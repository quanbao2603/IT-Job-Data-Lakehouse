import os
import uuid
import datetime
from typing import List, Dict, Any, Optional

from minio import Minio
from minio.commonconfig import CopySource

from src.storage.atomic_writer import AtomicWriter, load_env_file
from src.storage.state_manager import StateManager


class CompactionEngine:
    """
    Động cơ gom các tệp nhỏ tầng Bronze (Bronze Small-Files Compaction Engine).
    Hỗ trợ Retention-Managed Compaction Lifecycle: lưu trữ sang archive hoặc xóa theo chính sách.
    """

    def __init__(
        self,
        endpoint: Optional[str] = None,
        access_key: Optional[str] = None,
        secret_key: Optional[str] = None,
        secure: Optional[bool] = None,
        bronze_bucket: Optional[str] = None
    ):
        load_env_file()
        self.endpoint = endpoint or os.getenv("MINIO_ENDPOINT", "localhost:9000")
        self.access_key = access_key or os.getenv("MINIO_ROOT_USER", os.getenv("MINIO_ACCESS_KEY"))
        self.secret_key = secret_key or os.getenv("MINIO_ROOT_PASSWORD", os.getenv("MINIO_SECRET_KEY"))

        if not self.access_key or not self.secret_key:
            raise ValueError("Thiếu thông tin xác thực MinIO trong .env!")

        if secure is not None:
            self.secure = secure
        else:
            self.secure = os.getenv("MINIO_SECURE", "false").lower() == "true"

        self.bronze_bucket = bronze_bucket or os.getenv("MINIO_BRONZE_BUCKET", "bronze")

        self.client = Minio(
            endpoint=self.endpoint,
            access_key=self.access_key,
            secret_key=self.secret_key,
            secure=self.secure
        )
        self.writer = AtomicWriter(
            endpoint=self.endpoint,
            access_key=self.access_key,
            secret_key=self.secret_key,
            secure=self.secure,
            bronze_bucket=self.bronze_bucket
        )
        self.state_manager = StateManager()

    def compact_partition(
        self,
        partition_prefix: str,
        min_files_threshold: int = 2,
        purge_small_files: bool = True,
        archive_small_files: bool = False
    ) -> Dict[str, Any]:
        prefix = partition_prefix.strip("/")
        parts = prefix.split("/")
        source_name = parts[0] if parts else "unknown"

        objects = list(self.client.list_objects(self.bronze_bucket, prefix=prefix, recursive=True))
        small_files = [
            obj.object_name for obj in objects
            if obj.object_name.endswith(".jsonl.gz") and not obj.object_name.startswith("compacted_")
        ]

        if len(small_files) < min_files_threshold:
            return {
                "status": "SKIPPED",
                "message": f"Chỉ có {len(small_files)} tệp, chưa đạt ngưỡng gom ({min_files_threshold} tệp).",
                "files_count": len(small_files)
            }

        old_total_bytes = sum(obj.size for obj in objects if obj.object_name in small_files)

        def merged_records_stream():
            for obj_key in small_files:
                for record in self.writer.stream_bronze_records(obj_key):
                    yield record

        timestamp = datetime.datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        compacted_run_id = f"compacted_{timestamp}_{uuid.uuid4().hex[:6]}"

        write_result = self.writer.write_atomic(
            source=source_name,
            records=merged_records_stream(),
            run_id=compacted_run_id
        )

        total_records = write_result["records_count"]
        compacted_key = write_result["bronze_key"]

        self.state_manager.log_compaction(
            partition_prefix=prefix,
            files_merged_count=len(small_files),
            total_records=total_records,
            old_total_bytes=old_total_bytes,
            new_compacted_bytes=write_result["compressed_size_bytes"],
            compacted_key=compacted_key,
            compaction_id=compacted_run_id
        )

        self.state_manager.register_ingestion_object(
            object_key=compacted_key,
            run_id=compacted_run_id,
            source_name=source_name,
            file_size_bytes=write_result["compressed_size_bytes"],
            records_count=total_records,
            checksum_sha256=write_result.get("file_checksum_sha256")
        )

        self.state_manager.mark_objects_compacted(
            old_object_keys=small_files,
            compacted_into_key=compacted_key
        )

        if archive_small_files:
            for old_file in small_files:
                archive_key = f"archive/{old_file}"
                self.client.copy_object(self.bronze_bucket, archive_key, CopySource(self.bronze_bucket, old_file))
                self.client.remove_object(self.bronze_bucket, old_file)
        elif purge_small_files:
            for old_file in small_files:
                self.client.remove_object(self.bronze_bucket, old_file)

        return {
            "status": "COMPACTED",
            "partition": prefix,
            "files_merged_count": len(small_files),
            "total_records": total_records,
            "old_total_bytes": old_total_bytes,
            "new_compacted_bytes": write_result["compressed_size_bytes"],
            "bytes_saved": max(0, old_total_bytes - write_result["compressed_size_bytes"]),
            "compacted_key": compacted_key
        }

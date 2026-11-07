import os
import io
import gzip
import json
import uuid
import hashlib
import tempfile
from datetime import datetime
from typing import Iterable, Dict, Any, Optional, Generator, List

from minio import Minio
from minio.commonconfig import CopySource


def load_env_file(dotenv_path: Optional[str] = None) -> None:
    if dotenv_path is None:
        current_dir = os.path.dirname(os.path.abspath(__file__))
        candidates = [
            os.path.join(current_dir, "..", "..", ".env"),
            os.path.join(current_dir, "..", ".env"),
            os.path.join(os.getcwd(), ".env")
        ]
        for candidate in candidates:
            if os.path.isfile(candidate):
                dotenv_path = candidate
                break

    if not dotenv_path or not os.path.isfile(dotenv_path):
        return

    try:
        with open(dotenv_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, val = line.split("=", 1)
                key = key.strip()
                val = val.strip()
                if (val.startswith('"') and val.endswith('"')) or (val.startswith("'") and val.endswith("'")):
                    val = val[1:-1]
                if key and key not in os.environ:
                    os.environ[key] = val
    except Exception:
        pass


class AtomicWriter:
    """
    Động cơ ghi dữ liệu bất biến và nguyên tử (Atomic Writer) cho Tầng Bronze.
    - True-Streaming Disk Pipeline O(1) RAM.
    - Idempotent Retry: Nhận diện và tái sử dụng object đích nếu đã được copy trước khi crash.
    """

    def __init__(
        self,
        endpoint: Optional[str] = None,
        access_key: Optional[str] = None,
        secret_key: Optional[str] = None,
        secure: Optional[bool] = None,
        landing_bucket: Optional[str] = None,
        bronze_bucket: Optional[str] = None
    ):
        load_env_file()
        self.endpoint = endpoint or os.getenv("MINIO_ENDPOINT", "localhost:9000")
        self.access_key = access_key or os.getenv("MINIO_ROOT_USER", os.getenv("MINIO_ACCESS_KEY"))
        self.secret_key = secret_key or os.getenv("MINIO_ROOT_PASSWORD", os.getenv("MINIO_SECRET_KEY"))

        if not self.access_key or not self.secret_key:
            raise ValueError("Thiếu thông tin xác thực MinIO trong biến môi trường hoặc .env!")

        if secure is not None:
            self.secure = secure
        else:
            self.secure = os.getenv("MINIO_SECURE", "false").lower() == "true"

        self.landing_bucket = landing_bucket or os.getenv("MINIO_LANDING_BUCKET", "landing")
        self.bronze_bucket = bronze_bucket or os.getenv("MINIO_BRONZE_BUCKET", "bronze")

        self.client = Minio(
            endpoint=self.endpoint,
            access_key=self.access_key,
            secret_key=self.secret_key,
            secure=self.secure
        )
        self._ensure_buckets()

    def _ensure_buckets(self) -> None:
        try:
            if not self.client.bucket_exists(self.landing_bucket):
                self.client.make_bucket(self.landing_bucket)
            if not self.client.bucket_exists(self.bronze_bucket):
                self.client.make_bucket(self.bronze_bucket)
        except Exception:
            pass

    def clean_stale_landing_uploads(self, max_age_hours: int = 6) -> int:
        cleaned_count = 0
        now = datetime.utcnow()
        try:
            objects = self.client.list_objects(self.landing_bucket, recursive=True)
            for obj in objects:
                if obj.object_name.endswith(".uploading"):
                    age_seconds = (now - obj.last_modified.replace(tzinfo=None)).total_seconds()
                    if age_seconds > max_age_hours * 3600:
                        self.client.remove_object(self.landing_bucket, obj.object_name)
                        cleaned_count += 1
        except Exception:
            pass
        return cleaned_count

    def write_atomic(
        self,
        source: str,
        records: Iterable[Dict[str, Any]],
        run_id: Optional[str] = None
    ) -> Dict[str, Any]:
        self.clean_stale_landing_uploads()

        source_clean = source.lower().strip()
        active_run_id = run_id or f"run_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"

        dt = datetime.utcnow()
        year = dt.strftime("%Y")
        month = dt.strftime("%m")
        day = dt.strftime("%d")

        temp_landing_key = f"{source_clean}/temp_{active_run_id}.uploading"
        bronze_key = f"{source_clean}/ingest_year={year}/ingest_month={month}/ingest_day={day}/part_{active_run_id}.jsonl.gz"

        with tempfile.NamedTemporaryFile(suffix=".jsonl.gz", delete=False) as tmp_file:
            temp_path = tmp_file.name

        try:
            records_count = 0
            raw_size_bytes = 0

            with gzip.open(temp_path, mode="wt", encoding="utf-8") as gz_out:
                for rec in records:
                    line = json.dumps(rec, ensure_ascii=False) + "\n"
                    raw_size_bytes += len(line.encode("utf-8"))
                    gz_out.write(line)
                    records_count += 1

            if records_count == 0:
                raise ValueError("Không có bản ghi nào để ghi vào Bronze Lakehouse!")

            compressed_size_bytes = os.path.getsize(temp_path)

            hasher = hashlib.sha256()
            with open(temp_path, "rb") as f:
                while chunk := f.read(65536):
                    hasher.update(chunk)
            file_checksum_sha256 = hasher.hexdigest()

            # Upload Landing
            self.client.fput_object(
                bucket_name=self.landing_bucket,
                object_name=temp_landing_key,
                file_path=temp_path,
                content_type="application/gzip"
            )

            stat = self.client.stat_object(self.landing_bucket, temp_landing_key)
            if stat.size != compressed_size_bytes:
                self.client.remove_object(self.landing_bucket, temp_landing_key)
                raise IOError("Lỗi toàn vẹn file trên MinIO landing!")

            # Idempotency check: Kiểm tra nếu file đích đã tồn tại
            already_committed = False
            try:
                bronze_stat = self.client.stat_object(self.bronze_bucket, bronze_key)
                if bronze_stat.size == compressed_size_bytes:
                    already_committed = True
            except Exception:
                pass

            if not already_committed:
                copy_source = CopySource(self.landing_bucket, temp_landing_key)
                self.client.copy_object(
                    bucket_name=self.bronze_bucket,
                    object_name=bronze_key,
                    source=copy_source
                )

            bronze_stat = self.client.stat_object(self.bronze_bucket, bronze_key)
            if bronze_stat.size != compressed_size_bytes:
                raise IOError("Lỗi toàn vẹn Bronze: Dung lượng không khớp sau copy!")

            self.client.remove_object(self.landing_bucket, temp_landing_key)

            ratio = (1 - (compressed_size_bytes / raw_size_bytes)) * 100 if raw_size_bytes > 0 else 0

            return {
                "status": "ALREADY_COMMITTED" if already_committed else "COMMITTED",
                "source": source_clean,
                "bronze_s3_uri": f"s3://{self.bronze_bucket}/{bronze_key}",
                "bronze_key": bronze_key,
                "records_count": records_count,
                "raw_size_bytes": raw_size_bytes,
                "compressed_size_bytes": compressed_size_bytes,
                "compression_ratio_pct": round(ratio, 2),
                "file_checksum_sha256": file_checksum_sha256
            }

        finally:
            if os.path.exists(temp_path):
                try:
                    os.remove(temp_path)
                except OSError:
                    pass

import abc
import uuid
import inspect
from datetime import datetime
from typing import Dict, Any, Optional, Iterable

from src.storage.state_manager import StateManager
from src.storage.atomic_writer import AtomicWriter
from src.storage.metadata_envelope import MetadataEnvelope


class BaseCrawler(abc.ABC):
    """
    Lớp cơ sở chuẩn hóa cho toàn bộ Crawler sàn việc làm IT.
    - Quản lý vòng đời Ingestion qua State Store.
    - Cơ chế Concurrency Lock chống chạy đè giữa các tiến trình.
    - Kích hoạt Heartbeat định kỳ theo từng batch dữ liệu.
    - Tách biệt rõ ràng source_watermark và ingestion_watermark.
    """

    def __init__(self, source_name: str):
        self.source_name = source_name.lower().strip()
        self.state_manager = StateManager()
        self.writer = AtomicWriter()
        self._current_run_id: Optional[str] = None

    def heartbeat(self) -> bool:
        """Refresh the active run lease during fetch or write progress."""
        if not self._current_run_id:
            return False
        return self.state_manager.heartbeat(self._current_run_id)

    @abc.abstractmethod
    def fetch_jobs(
        self,
        mode: str,
        max_pages: int,
        watermark_ts: Optional[str] = None,
        last_record_id: Optional[str] = None
    ) -> Iterable[Dict[str, Any]]:
        pass

    def run(self, mode: str = "incremental", max_pages: int = 5) -> Dict[str, Any]:
        run_id = f"{self.source_name}_{mode}_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:4]}"
        print("=" * 75)
        print(f"🚀 BẮT ĐẦU PHIÊN CÀO [{self.source_name.upper()}] - CHẾ ĐỘ: {mode.upper()}")
        print(f"   - Mã phiên chạy: {run_id}")
        print(f"   - Quét tối đa   : {max_pages} trang")
        print("=" * 75)

        acquired_run_id = self.state_manager.acquire_run_lock(self.source_name, run_id=run_id, mode=mode)
        if not acquired_run_id:
            print(f"\n[⚠️] CHẶN TIẾN TRÌNH TRÙNG LẶP (CONCURRENCY LOCK):")
            print(f"   Sàn [{self.source_name.upper()}] đang có một phiên cào khác đang chạy (RUNNING).")
            print("   Tiến trình hiện tại tự động dừng để bảo toàn tính nhất quán.")
            return {
                "status": "LOCKED",
                "message": f"Concurrent run active for source {self.source_name}",
                "records_ingested": 0
            }

        self._current_run_id = run_id
        self.heartbeat()

        watermark_info = self.state_manager.get_watermark(self.source_name)
        watermark_ts = watermark_info.get("last_watermark_ts") if watermark_info else None
        last_record_id = watermark_info.get("last_record_id") if watermark_info else None

        if mode == "incremental":
            print(f"[*] Mốc Watermark ghi nhận: Timestamp = {watermark_ts or 'None'} | Last Job ID = {last_record_id or 'None'}")
        else:
            print("[*] Chế độ FULL: Bỏ qua Watermark, cào toàn diện.")

        try:
            fetch_kwargs = {"mode": mode, "max_pages": max_pages, "watermark_ts": watermark_ts}
            sig = inspect.signature(self.fetch_jobs)
            if "last_record_id" in sig.parameters:
                fetch_kwargs["last_record_id"] = last_record_id

            jobs_iterable = self.fetch_jobs(**fetch_kwargs)
            jobs_iter = iter(jobs_iterable)

            first_job = next(jobs_iter, None)

            if first_job is None:
                now_utc = datetime.utcnow().isoformat() + "Z"
                self.state_manager.commit_success(
                    source_name=self.source_name,
                    watermark_ts=now_utc,
                    last_record_id=last_record_id,
                    records_ingested=0,
                    run_id=run_id
                )
                print("\n[🎉] Không có tin việc làm mới nào hôm nay! Dữ liệu đã đồng bộ 100%.")
                return {"status": "SUCCESS", "records_ingested": 0, "run_id": run_id}

            newest_job_id = first_job.get("job_id") or last_record_id

            # 2. Generator Stream bọc Envelope & Schema Drift kèm Heartbeat định kỳ
            def enveloped_generator():
                yield MetadataEnvelope.wrap(source=self.source_name, raw_payload=first_job, run_id=run_id)
                self.state_manager.heartbeat(run_id)
                count = 1
                for job in jobs_iter:
                    count += 1
                    if count % 10 == 0:
                        self.state_manager.heartbeat(run_id)
                    yield MetadataEnvelope.wrap(source=self.source_name, raw_payload=job, run_id=run_id)
                self.state_manager.heartbeat(run_id)

            # 3. Ghi nguyên tử lên MinIO Bronze
            print(f"[*] Đang nén .jsonl.gz và ghi nguyên tử vào MinIO Bronze (True Streaming O(1) RAM)...")
            write_result = self.writer.write_atomic(source=self.source_name, records=enveloped_generator(), run_id=run_id)

            # 4. Ghi nhận Manifest tệp vào State Store
            try:
                self.state_manager.register_ingestion_object(
                    object_key=write_result["bronze_key"],
                    run_id=run_id,
                    source_name=self.source_name,
                    file_size_bytes=write_result["compressed_size_bytes"],
                    records_count=write_result["records_count"],
                    checksum_sha256=write_result.get("file_checksum_sha256")
                )
            except Exception as manifest_err:
                raise RuntimeError(f"Ghi nhận Manifest thất bại cho {write_result['bronze_key']}: {manifest_err}")

            # 5. Cập nhật Watermark mới (Tách rõ source-watermark và ingestion-watermark)
            new_watermark = datetime.utcnow().isoformat() + "Z"
            source_watermark = (
                first_job.get("posted_date_iso")
                or first_job.get("approvedOn")
                or first_job.get("posted_date")
                or first_job.get("scraped_at")
            )

            self.state_manager.commit_success(
                source_name=self.source_name,
                watermark_ts=new_watermark,
                last_record_id=newest_job_id,
                records_ingested=write_result["records_count"],
                run_id=run_id,
                bronze_uri=write_result['bronze_s3_uri'],
                source_watermark=str(source_watermark) if source_watermark else None,
                watermark_strategy="datetime_cursor" if source_watermark else "id_cursor"
            )

            print("\n" + "=" * 75)
            print(f"✅ HOÀN TẤT PHIÊN CÀO [{self.source_name.upper()}] THÀNH CÔNG:")
            print(f"   - Số tin đã nạp vào Bronze: {write_result['records_count']} tin")
            print(f"   - MinIO Bronze URI        : {write_result['bronze_s3_uri']}")
            print(f"   - Mã băm SHA-256 tệp     : {write_result.get('file_checksum_sha256')}")
            print(f"   - Dung lượng nén (.gz)    : {write_result['compressed_size_bytes']} bytes")
            print(f"   - Ingestion Watermark     : {new_watermark}")
            print(f"   - Source Watermark        : {source_watermark or newest_job_id}")
            print("=" * 75)

            return {
                "status": "SUCCESS",
                "run_id": run_id,
                "records_ingested": write_result["records_count"],
                "bronze_uri": write_result['bronze_s3_uri']
            }

        except Exception as e:
            self.state_manager.mark_failure(self.source_name, run_id=run_id, error_message=str(e))
            print(f"\n❌ LỖI TRONG QUÁ TRÌNH CÀO [{self.source_name.upper()}]: {e}")
            raise
        finally:
            self._current_run_id = None

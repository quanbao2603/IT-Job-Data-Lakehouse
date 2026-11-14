import os
import io
import gzip
import json
import unittest
import tempfile
import sqlite3
import multiprocessing as mp
from typing import Iterator, Dict, Any, Optional
from unittest.mock import MagicMock, patch

from src.storage.metadata_envelope import MetadataEnvelope
from src.storage.state_manager import StateManager
from src.storage.atomic_writer import AtomicWriter, load_env_file
from src.storage.compaction_engine import CompactionEngine
from src.crawlers.base_crawler import BaseCrawler


def _multiprocess_lock_worker(db_path: str, source_name: str, run_id: str, queue, barrier) -> None:
    mgr = StateManager(db_path=db_path)
    res = mgr.acquire_run_lock(source_name, run_id=run_id)
    queue.put(res)
    # Keep the winner alive while the other process finishes its lock attempt.
    barrier.wait(timeout=5)


class TestMetadataEnvelope(unittest.TestCase):
    def test_checksum_sha256_generation(self):
        sample = {"job_id": "123", "title": "Data Engineer"}
        wrapped = MetadataEnvelope.wrap("itviec", sample, run_id="test_run_1")
        self.assertIn("_metadata", wrapped)
        self.assertIn("payload_checksum", wrapped["_metadata"])
        checksum = wrapped["_metadata"]["payload_checksum"]
        self.assertEqual(len(checksum), 64)
        self.assertTrue(MetadataEnvelope.verify_integrity(wrapped))

    def test_schema_drift_rescues_unknown_fields(self):
        sample_with_drift = {
            "platform": "itviec",
            "job_id": "999",
            "job_title": "AI Architect",
            "salary": "3000 USD",
            "unknown_metric_x": 42,
            "new_scraping_tag": "experimental"
        }
        wrapped = MetadataEnvelope.wrap("itviec", sample_with_drift, run_id="test_drift_run")
        raw_payload = wrapped["raw_payload"]
        rescued_data = wrapped["_rescued_data"]

        self.assertNotIn("unknown_metric_x", raw_payload)
        self.assertNotIn("new_scraping_tag", raw_payload)
        self.assertIn("unknown_metric_x", rescued_data)
        self.assertIn("new_scraping_tag", rescued_data)
        self.assertEqual(rescued_data["unknown_metric_x"], 42)
        self.assertTrue(MetadataEnvelope.verify_integrity(wrapped))

    def test_topcv_schema_contract_consistency(self):
        sample_topcv = {
            "platform": "topcv",
            "job_id": "topcv_555",
            "job_title": "Data Engineer",
            "company_name": "Tech Corp",
            "company_industry": "Information Technology",
            "salary": "25 - 35 triệu"
        }
        wrapped = MetadataEnvelope.wrap("topcv", sample_topcv, run_id="test_topcv_contract")
        self.assertIn("company_industry", wrapped["raw_payload"])
        self.assertNotIn("company_industry", wrapped["_rescued_data"])


class TestStateManager(unittest.TestCase):
    def setUp(self):
        self.temp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.temp_db_path = self.temp_db.name
        self.temp_db.close()
        self.state_mgr = StateManager(db_path=self.temp_db_path)

    def tearDown(self):
        if os.path.exists(self.temp_db_path):
            try:
                os.remove(self.temp_db_path)
            except OSError:
                pass

    def test_watermark_lifecycle(self):
        self.state_mgr.mark_running("itviec", run_id="run_itviec_01", mode="incremental")
        state_running = self.state_mgr.get_watermark("itviec")
        self.assertEqual(state_running["last_run_status"], "RUNNING")

        now_iso = "2026-11-06T12:00:00Z"
        self.state_mgr.commit_success(
            source_name="itviec",
            watermark_ts=now_iso,
            last_record_id="job_9988",
            records_ingested=25,
            run_id="run_itviec_01",
            bronze_uri="s3://bronze/itviec/part_01.jsonl.gz"
        )

        state_committed = self.state_mgr.get_watermark("itviec")
        self.assertEqual(state_committed["last_run_status"], "SUCCESS")
        self.assertEqual(state_committed["last_record_id"], "job_9988")
        self.assertEqual(state_committed["records_ingested"], 25)

    def test_source_watermark_separation(self):
        self.state_mgr.commit_success(
            source_name="vietnamworks",
            watermark_ts="2026-11-06T15:00:00Z",
            source_watermark="2026-11-06T09:30:00Z",
            watermark_strategy="datetime_cursor",
            last_record_id="job_vnw_123",
            records_ingested=50
        )
        wm = self.state_mgr.get_watermark("vietnamworks")
        self.assertEqual(wm["last_watermark_ts"], "2026-11-06T15:00:00Z")
        self.assertEqual(wm["source_watermark"], "2026-11-06T09:30:00Z")
        self.assertEqual(wm["watermark_strategy"], "datetime_cursor")
        self.assertEqual(wm["last_record_id"], "job_vnw_123")

    def test_manifest_registration_and_compaction_tracking(self):
        self.state_mgr.register_ingestion_object(
            object_key="itviec/file1.jsonl.gz",
            run_id="run_01",
            source_name="itviec",
            file_size_bytes=500,
            records_count=10,
            checksum_sha256="hash1"
        )
        self.state_mgr.register_ingestion_object(
            object_key="itviec/file2.jsonl.gz",
            run_id="run_02",
            source_name="itviec",
            file_size_bytes=600,
            records_count=15,
            checksum_sha256="hash2"
        )

        active = self.state_mgr.list_active_bronze_objects("itviec")
        self.assertEqual(len(active), 2)

        self.state_mgr.mark_objects_compacted(["itviec/file1.jsonl.gz"], "itviec/file_compacted.jsonl.gz")

        active_after = self.state_mgr.list_active_bronze_objects("itviec")
        self.assertEqual(len(active_after), 1)
        self.assertEqual(active_after[0]["object_key"], "itviec/file2.jsonl.gz")

    def test_partial_unique_index_schema_exists(self):
        """Kiểm chứng Partial Unique Index bắt buộc phải tồn tại trong sqlite_master."""
        with self.state_mgr._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT sql FROM sqlite_master WHERE type='index' AND name='idx_active_running_per_source'")
            row = cursor.fetchone()
            self.assertIsNotNone(row, "Partial Unique Index idx_active_running_per_source không tồn tại!")
            self.assertIn("WHERE status = 'RUNNING'", row[0])

    def test_concurrency_exclusive_lock(self):
        lock1 = self.state_mgr.acquire_run_lock("topdev", run_id="run_topdev_01")
        self.assertIsNotNone(lock1)

        lock2 = self.state_mgr.acquire_run_lock("topdev", run_id="run_topdev_02")
        self.assertIsNone(lock2)

        self.state_mgr.commit_success("topdev", watermark_ts="2026-11-06T12:00:00Z", run_id=lock1)

        lock3 = self.state_mgr.acquire_run_lock("topdev", run_id="run_topdev_03")
        self.assertIsNotNone(lock3)

    def test_multiprocess_concurrency_race(self):
        ctx = mp.get_context("spawn" if os.name == "nt" else "fork")
        q = ctx.Queue()
        barrier = ctx.Barrier(2)

        p1 = ctx.Process(target=_multiprocess_lock_worker, args=(self.temp_db_path, "topcv", "run_p1", q, barrier))
        p2 = ctx.Process(target=_multiprocess_lock_worker, args=(self.temp_db_path, "topcv", "run_p2", q, barrier))

        p1.start()
        p2.start()
        p1.join(timeout=5)
        p2.join(timeout=5)

        results = [q.get(timeout=2), q.get(timeout=2)]
        successful = [r for r in results if r is not None]
        rejected = [r for r in results if r is None]

        self.assertEqual(len(successful), 1)
        self.assertEqual(len(rejected), 1)

        with self.state_mgr._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM ingestion_runs WHERE source_name = 'topcv' AND status = 'RUNNING'")
            running_count = cursor.fetchone()[0]
            self.assertEqual(running_count, 1)

    def test_stress_concurrency_race(self):
        """Stress-test 10 vòng đua liên tục: Trong mọi vòng, đúng 1 winner và đúng 1 dòng RUNNING."""
        ctx = mp.get_context("spawn" if os.name == "nt" else "fork")
        for iteration in range(10):
            with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp_db:
                iter_db_path = tmp_db.name
            try:
                # Khởi tạo schema & index
                _ = StateManager(db_path=iter_db_path)
                q = ctx.Queue()
                barrier = ctx.Barrier(2)

                p1 = ctx.Process(target=_multiprocess_lock_worker, args=(iter_db_path, "topdev", f"iter_{iteration}_p1", q, barrier))
                p2 = ctx.Process(target=_multiprocess_lock_worker, args=(iter_db_path, "topdev", f"iter_{iteration}_p2", q, barrier))
                p1.start()
                p2.start()
                p1.join(timeout=5)
                p2.join(timeout=5)

                results = [q.get(timeout=2), q.get(timeout=2)]
                winners = [r for r in results if r is not None]
                losers = [r for r in results if r is None]

                self.assertEqual(len(winners), 1, f"[Vòng {iteration}] Phải có đúng 1 winner.")
                self.assertEqual(len(losers), 1, f"[Vòng {iteration}] Phải có đúng 1 loser.")

                conn = sqlite3.connect(iter_db_path)
                running = conn.execute("SELECT COUNT(*) FROM ingestion_runs WHERE source_name = 'topdev' AND status = 'RUNNING'").fetchone()[0]
                conn.close()
                self.assertEqual(running, 1, f"[Vòng {iteration}] Chỉ có đúng 1 dòng RUNNING.")
            finally:
                if os.path.exists(iter_db_path):
                    try:
                        os.remove(iter_db_path)
                    except Exception:
                        pass

    def test_heartbeat_lifecycle(self):
        lock = self.state_mgr.acquire_run_lock("itviec", run_id="run_hb_01")
        self.assertIsNotNone(lock)
        updated = self.state_mgr.heartbeat("run_hb_01")
        self.assertTrue(updated)

        with self.state_mgr._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT heartbeat_at FROM ingestion_runs WHERE run_id = 'run_hb_01'")
            hb = cursor.fetchone()[0]
            self.assertIsNotNone(hb)

    def test_dead_pid_instant_recovery(self):
        with self.state_mgr._get_connection() as conn:
            conn.execute("""
                INSERT INTO ingestion_runs (run_id, source_name, mode, started_at, status, process_id)
                VALUES ('run_dead_01', 'vietnamworks', 'incremental', '2026-11-06T12:00:00Z', 'RUNNING', 999999999)
            """)
            conn.commit()

        new_lock = self.state_mgr.acquire_run_lock("vietnamworks", run_id="run_alive_02")
        self.assertIsNotNone(new_lock)


class TestAtomicWriterValidation(unittest.TestCase):
    def test_missing_credentials_rejection(self):
        with patch("src.storage.atomic_writer.load_env_file"):
            with patch.dict(os.environ, {}, clear=True):
                with self.assertRaises(ValueError):
                    AtomicWriter()

    def test_empty_records_rejection(self):
        writer = AtomicWriter(access_key="mock_key", secret_key="mock_secret")
        writer.client = MagicMock()
        writer.client.list_objects.return_value = []
        with self.assertRaises(ValueError):
            writer.write_atomic(source="itviec", records=[])

    def test_idempotent_retry_after_copy_crash(self):
        writer = AtomicWriter(access_key="mock_key", secret_key="mock_secret")
        writer.client = MagicMock()
        writer.client.list_objects.return_value = []
        mock_stat = MagicMock()
        mock_stat.size = 100
        writer.client.stat_object.return_value = mock_stat

        with patch("os.path.getsize", return_value=100):
            res = writer.write_atomic(source="itviec", records=[{"job_id": "1"}], run_id="run_retry_01")
            self.assertEqual(res["status"], "ALREADY_COMMITTED")
            writer.client.copy_object.assert_not_called()


class TestCrawlerGeneratorStreaming(unittest.TestCase):
    def setUp(self):
        self.temp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.temp_db_path = self.temp_db.name
        self.temp_db.close()

    def tearDown(self):
        if os.path.exists(self.temp_db_path):
            try:
                os.remove(self.temp_db_path)
            except OSError:
                pass

    def test_crawler_empty_stream_handled_gracefully(self):
        class EmptyCrawler(BaseCrawler):
            def __init__(self, db_path):
                self.source_name = "test_empty"
                self.state_manager = StateManager(db_path=db_path)
                self.writer = MagicMock()

            def fetch_jobs(self, **kwargs) -> Iterator[Dict[str, Any]]:
                return iter([])

        crawler = EmptyCrawler(self.temp_db_path)
        res = crawler.run(mode="incremental")
        self.assertEqual(res["status"], "SUCCESS")
        self.assertEqual(res["records_ingested"], 0)
        crawler.writer.write_atomic.assert_not_called()

    def test_crawler_generator_yields_stream_to_writer(self):
        mock_jobs = [
            {"job_id": "job_101", "job_title": "Backend Dev", "salary": "20M"},
            {"job_id": "job_102", "job_title": "Frontend Dev", "salary": "22M"}
        ]

        class YieldingCrawler(BaseCrawler):
            def __init__(self, db_path):
                self.source_name = "itviec"
                self.state_manager = StateManager(db_path=db_path)
                self.writer = MagicMock()
                self.writer.write_atomic.return_value = {
                    "status": "COMMITTED",
                    "source": "itviec",
                    "bronze_s3_uri": "s3://bronze/itviec/test.jsonl.gz",
                    "bronze_key": "itviec/test.jsonl.gz",
                    "records_count": 2,
                    "raw_size_bytes": 1000,
                    "compressed_size_bytes": 400,
                    "compression_ratio_pct": 60.0,
                    "file_checksum_sha256": "mock_file_sha256"
                }

            def fetch_jobs(self, **kwargs) -> Iterator[Dict[str, Any]]:
                for job in mock_jobs:
                    yield job

        crawler = YieldingCrawler(self.temp_db_path)
        res = crawler.run(mode="incremental")

        self.assertEqual(res["status"], "SUCCESS")
        self.assertEqual(res["records_ingested"], 2)
        crawler.writer.write_atomic.assert_called_once()

        wm = crawler.state_manager.get_watermark("itviec")
        self.assertEqual(wm["last_record_id"], "job_101")

        active_objs = crawler.state_manager.list_active_bronze_objects("itviec")
        self.assertEqual(len(active_objs), 1)
        self.assertEqual(active_objs[0]["checksum_sha256"], "mock_file_sha256")

    def test_manifest_failure_aborts_commit(self):
        class FailManifestCrawler(BaseCrawler):
            def __init__(self, db_path):
                self.source_name = "itviec"
                self.state_manager = StateManager(db_path=db_path)
                self.writer = MagicMock()
                self.writer.write_atomic.return_value = {
                    "status": "COMMITTED",
                    "source": "itviec",
                    "bronze_s3_uri": "s3://bronze/itviec/fail_manifest.jsonl.gz",
                    "bronze_key": "itviec/fail_manifest.jsonl.gz",
                    "records_count": 1,
                    "raw_size_bytes": 500,
                    "compressed_size_bytes": 200,
                    "compression_ratio_pct": 60.0,
                    "file_checksum_sha256": "hash_xyz"
                }

            def fetch_jobs(self, **kwargs) -> Iterator[Dict[str, Any]]:
                yield {"job_id": "job_fail_1", "job_title": "Tester"}

        crawler = FailManifestCrawler(self.temp_db_path)
        crawler.state_manager.register_ingestion_object = MagicMock(side_effect=sqlite3.OperationalError("disk I/O error"))

        with self.assertRaises(RuntimeError):
            crawler.run(mode="incremental")

        with crawler.state_manager._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT status, error_message FROM ingestion_runs WHERE source_name = 'itviec'")
            row = cursor.fetchone()
            self.assertEqual(row["status"], "FAILED")
            self.assertIn("Manifest thất bại", row["error_message"])

    def test_periodic_heartbeat_during_crawl(self):
        class HeartbeatCrawler(BaseCrawler):
            def __init__(self, db_path):
                self.source_name = "topcv"
                self.state_manager = StateManager(db_path=db_path)
                self.writer = MagicMock()
                self.writer.write_atomic.return_value = {
                    "status": "COMMITTED",
                    "source": "topcv",
                    "bronze_s3_uri": "s3://bronze/topcv/test.jsonl.gz",
                    "bronze_key": "topcv/test.jsonl.gz",
                    "records_count": 25,
                    "raw_size_bytes": 5000,
                    "compressed_size_bytes": 1000,
                    "compression_ratio_pct": 80.0,
                    "file_checksum_sha256": "mock_sha"
                }

            def fetch_jobs(self, **kwargs) -> Iterator[Dict[str, Any]]:
                for i in range(25):
                    yield {"job_id": f"job_{i}", "posted_date_iso": "2026-11-06T10:00:00Z"}

        class ConsumingWriter:
            def write_atomic(self, source, records, run_id=None):
                rows = list(records)
                return {
                    "status": "COMMITTED",
                    "source": source,
                    "bronze_s3_uri": f"s3://bronze/{source}/test.jsonl.gz",
                    "bronze_key": f"{source}/test.jsonl.gz",
                    "records_count": len(rows),
                    "compressed_size_bytes": 1000,
                    "file_checksum_sha256": "mock_sha",
                }

        crawler = HeartbeatCrawler(self.temp_db_path)
        crawler.writer = ConsumingWriter()
        crawler.state_manager.heartbeat = MagicMock(wraps=crawler.state_manager.heartbeat)
        res = crawler.run(mode="incremental")
        self.assertEqual(res["status"], "SUCCESS")
        self.assertGreaterEqual(crawler.state_manager.heartbeat.call_count, 3)

    def test_fetch_phase_heartbeat_progress(self):
        """Khẳng định heartbeat được gọi tại các chốt tiến độ của pha fetch."""
        class ConsumingFetchWriter:
            def write_atomic(self, source, records, run_id=None, target_bronze_key=None):
                rows = list(records)
                return {
                    "status": "COMMITTED",
                    "source": source,
                    "bronze_s3_uri": f"s3://bronze/{source}/test.jsonl.gz",
                    "bronze_key": f"{source}/test.jsonl.gz",
                    "records_count": len(rows),
                    "compressed_size_bytes": 100,
                    "compression_ratio_pct": 50.0,
                    "file_checksum_sha256": "mock_sha"
                }

        class FetchHeartbeatCrawler(BaseCrawler):
            def __init__(self, db_path):
                self.source_name = "itviec"
                self.state_manager = StateManager(db_path=db_path)
                self.writer = ConsumingFetchWriter()

            def fetch_jobs(self, **kwargs) -> Iterator[Dict[str, Any]]:
                # Mô phỏng duyệt qua 2 search pages và detail items
                for page in range(1, 3):
                    self.heartbeat()  # fetch-phase heartbeat per page
                    for j in range(2):
                        self.heartbeat()  # fetch-phase heartbeat per detail
                        yield {"job_id": f"job_p{page}_{j}", "posted_date_iso": "2026-11-06T10:00:00Z"}

        crawler = FetchHeartbeatCrawler(self.temp_db_path)
        crawler.state_manager.heartbeat = MagicMock(wraps=crawler.state_manager.heartbeat)
        res = crawler.run(mode="incremental")
        self.assertEqual(res["status"], "SUCCESS")
        # 2 page + 4 detail + 1 sau lock + 2 write-phase = 9 heartbeat; yêu cầu tối thiểu 4
        self.assertGreaterEqual(crawler.state_manager.heartbeat.call_count, 4)


class _FakeS3Response:
    """Giả lập response get_object của MinIO cho luồng đọc gzip."""

    def __init__(self, data: bytes):
        self._buf = io.BytesIO(data)

    def read(self, amt=-1):
        return self._buf.read(amt)

    def close(self):
        pass

    def release_conn(self):
        pass


def _gz_bytes(records):
    buf = io.BytesIO()
    with gzip.open(buf, "wt", encoding="utf-8") as gz:
        for rec in records:
            gz.write(json.dumps(rec) + "\n")
    return buf.getvalue()


class TestCompactionEngine(unittest.TestCase):
    PARTITION = "itviec/ingest_year=2026/ingest_month=11/ingest_day=05"

    def setUp(self):
        self.temp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.temp_db_path = self.temp_db.name
        self.temp_db.close()

        with patch("src.storage.compaction_engine.load_env_file"), \
             patch("src.storage.atomic_writer.load_env_file"), \
             patch("src.storage.compaction_engine.Minio", return_value=MagicMock()), \
             patch("src.storage.atomic_writer.Minio", return_value=MagicMock()):
            self.engine = CompactionEngine(access_key="mock_key", secret_key="mock_secret")
        self.engine.state_manager = StateManager(db_path=self.temp_db_path)

        self.file_a = f"{self.PARTITION}/part_itviec_incremental_20261105_100000_aaaa.jsonl.gz"
        self.file_b = f"{self.PARTITION}/part_itviec_incremental_20261105_120000_bbbb.jsonl.gz"
        self.file_compacted_old = f"{self.PARTITION}/part_compacted_20261105_090000_cccc.jsonl.gz"

        self.records_a = [{"job_id": f"a{i}", "_metadata": {"source": "itviec"}} for i in range(2)]
        self.records_b = [{"job_id": f"b{i}", "_metadata": {"source": "itviec"}} for i in range(3)]

        # Hai tệp nguồn phải có manifest hợp lệ trước khi được phép gom
        self.engine.state_manager.register_ingestion_object(self.file_a, "run_a", "itviec", 100, 2, "sha_a")
        self.engine.state_manager.register_ingestion_object(self.file_b, "run_b", "itviec", 100, 3, "sha_b")

        mock_client = MagicMock()
        def _list_objects(bucket, prefix=None, recursive=False):
            if bucket != "bronze":
                return iter([])
            objs = []
            for key, size in [(self.file_a, 100), (self.file_b, 100), (self.file_compacted_old, 500)]:
                if prefix is None or key.startswith(prefix):
                    obj = MagicMock()
                    obj.object_name = key
                    obj.size = size
                    objs.append(obj)
            return iter(objs)
        mock_client.list_objects.side_effect = _list_objects
        mock_client.stat_object.return_value = MagicMock(size=100)
        self.mock_client = mock_client
        self.engine.client = mock_client
        self.engine.writer.client = mock_client

    def tearDown(self):
        if os.path.exists(self.temp_db_path):
            try:
                os.remove(self.temp_db_path)
            except OSError:
                pass

    def _wire_get_object(self, verified_records=None):
        """get_object trả nội dung nguồn cho 2 tệp nhỏ; trả verified_records cho tệp compaction."""
        sources = {self.file_a: self.records_a, self.file_b: self.records_b}
        def _get_object(bucket, key):
            if key in sources:
                return _FakeS3Response(_gz_bytes(sources[key]))
            payload = verified_records if verified_records is not None else self.records_a + self.records_b
            return _FakeS3Response(_gz_bytes(payload))
        self.mock_client.get_object.side_effect = _get_object

    def test_stream_bronze_records_reads_jsonl_gz(self):
        self._wire_get_object()
        records = list(self.engine.writer.stream_bronze_records(self.file_a))
        self.assertEqual(len(records), 2)
        self.assertEqual(records[0]["job_id"], "a0")

    def test_compaction_skips_below_threshold(self):
        self._wire_get_object()
        res = self.engine.compact_partition(self.PARTITION, min_files_threshold=3)
        self.assertEqual(res["status"], "SKIPPED")
        self.assertEqual(res["files_count"], 2)

    def test_compaction_happy_path_archive_mode(self):
        self._wire_get_object()
        with patch("os.path.getsize", return_value=100):
            res = self.engine.compact_partition(self.PARTITION, min_files_threshold=2)

        self.assertEqual(res["status"], "COMPACTED")
        # Lọc đúng: chỉ 2 tệp nguồn, bỏ qua tệp part_compacted_ cũ
        self.assertEqual(res["files_merged_count"], 2)
        self.assertEqual(res["total_records"], 5)
        self.assertEqual(res["bytes_saved"], 100)
        # Tệp hợp nhất nằm ĐÚNG partition nguồn, không phải partition ngày hiện tại
        self.assertTrue(res["compacted_key"].startswith(self.PARTITION))
        self.assertIn("part_compacted_", res["compacted_key"])

        # Audit log có bytes_saved (bug TypeError cũ phải biến mất)
        with self.engine.state_manager._get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT bytes_saved, total_records, files_merged_count FROM compaction_audit_logs")
            row = cur.fetchone()
            self.assertEqual(row["bytes_saved"], 100)
            self.assertEqual(row["total_records"], 5)

        # Manifest: tệp mới active, 2 tệp nguồn marked compacted
        active = self.engine.state_manager.list_active_bronze_objects("itviec")
        self.assertEqual([o["object_key"] for o in active], [res["compacted_key"]])
        with self.engine.state_manager._get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) AS c FROM ingestion_objects WHERE is_compacted = 1")
            self.assertEqual(cur.fetchone()["c"], 2)

        # Lifecycle archive: copy từng tệp nguồn sang archive/ rồi mới remove
        archive_copies = [c.args[1] for c in self.mock_client.copy_object.call_args_list
                          if c.args[1].startswith("archive/")]
        self.assertEqual(sorted(archive_copies), sorted([f"archive/{self.file_a}", f"archive/{self.file_b}"]))
        removed = [c.args[1] for c in self.mock_client.remove_object.call_args_list]
        self.assertIn(self.file_a, removed)
        self.assertIn(self.file_b, removed)
        self.assertNotIn(res["compacted_key"], removed)

    def test_compaction_record_count_mismatch_fail_closed(self):
        # Tệp sau gom đọc lại chỉ có 3/5 bản ghi → phải xóa tệp gom và không ghi manifest
        self._wire_get_object(verified_records=self.records_a)
        with patch("os.path.getsize", return_value=100):
            with self.assertRaises(IOError):
                self.engine.compact_partition(self.PARTITION, min_files_threshold=2)

        with self.engine.state_manager._get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) AS c FROM compaction_audit_logs")
            self.assertEqual(cur.fetchone()["c"], 0)
            cur.execute("SELECT COUNT(*) AS c FROM ingestion_objects")
            self.assertEqual(cur.fetchone()["c"], 2)  # chỉ còn đúng 2 dòng manifest của tệp nguồn
            cur.execute("SELECT COUNT(*) AS c FROM ingestion_objects WHERE is_compacted = 1")
            self.assertEqual(cur.fetchone()["c"], 0)
        removed = [c.args[1] for c in self.mock_client.remove_object.call_args_list]
        self.assertTrue(any("part_compacted_" in k for k in removed))
        # Tệp nguồn KHÔNG bị xóa khi đối soát fail
        self.assertNotIn(self.file_a, removed)
        self.assertNotIn(self.file_b, removed)


if __name__ == "__main__":
    unittest.main()

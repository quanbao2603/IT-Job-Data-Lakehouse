import os
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


if __name__ == "__main__":
    unittest.main()

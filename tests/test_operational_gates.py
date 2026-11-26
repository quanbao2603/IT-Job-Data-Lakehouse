import hashlib
import io
import json
import os
import tempfile
import unittest

from src.storage.state_manager import StateManager
from types import SimpleNamespace
from unittest.mock import patch

from src.utils.bronze_audit_gate import AuditGateError, validate_source_results
from src.utils.operational import append_failure_event, redact_message
from src.utils.preflight import PreflightError, run_preflight


class TestPreflight(unittest.TestCase):
    def test_preflight_rejects_disk_below_fifteen_percent(self):
        with self.assertRaises(PreflightError):
            run_preflight(
                project_root="/workspace",
                python_executable="/workspace/.venv/bin/python",
                state_db_path="/workspace/data/state_store.db",
                disk_usage_fn=lambda _: SimpleNamespace(total=100, used=86, free=14),
                memory_available_mb_fn=lambda: 2048,
                network_check_fn=lambda: None,
                minio_check_fn=lambda: None,
                state_store_check_fn=lambda: None,
            )

    def test_preflight_returns_sanitized_runtime_contract(self):
        result = run_preflight(
            project_root="/workspace",
            python_executable="/workspace/.venv/bin/python",
            state_db_path="/workspace/data/state_store.db",
            disk_usage_fn=lambda _: SimpleNamespace(total=100, used=70, free=30),
            memory_available_mb_fn=lambda: 2048,
            network_check_fn=lambda: None,
            minio_check_fn=lambda: None,
            state_store_check_fn=lambda: None,
        )
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["disk_free_percent"], 30.0)
        self.assertEqual(result["memory_available_mb"], 2048)
        self.assertNotIn("password", json.dumps(result).lower())


class TestOperationalLedger(unittest.TestCase):
    def test_redaction_removes_credentials_and_html(self):
        message = (
            "password=hunter2 token=abc123 api_key=secret "
            "Cookie: sid=private <html>session</html>"
        )
        clean = redact_message(message)
        self.assertNotIn("hunter2", clean)
        self.assertNotIn("abc123", clean)
        self.assertNotIn("private", clean)
        self.assertNotIn("<html>", clean)

    def test_failure_event_is_append_only_jsonl_and_redacted(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "source_failures.jsonl")
            append_failure_event(
                path=path,
                dag_id="dag",
                run_id="run",
                task_id="crawl_topcv_incremental",
                source="topcv",
                failure_class="BLOCKED",
                http_status=403,
                attempt=3,
                message="token=do-not-store",
            )
            with open(path, encoding="utf-8") as fh:
                event = json.loads(fh.readline())
            self.assertEqual(event["failure_class"], "BLOCKED")
            self.assertEqual(event["http_status"], 403)
            self.assertNotIn("do-not-store", json.dumps(event))


class _FakeResponse:
    def __init__(self, payload):
        self.payload = io.BytesIO(payload)

    def read(self, amount=-1):
        return self.payload.read(amount)

    def close(self):
        pass

    def release_conn(self):
        pass


class _FakeMinio:
    def __init__(self, object_key, payload):
        self.object_key = object_key
        self.payload = payload

    def stat_object(self, bucket, object_key):
        self.assert_object(bucket, object_key)
        return SimpleNamespace(size=len(self.payload))

    def get_object(self, bucket, object_key):
        self.assert_object(bucket, object_key)
        return _FakeResponse(self.payload)

    def list_objects(self, bucket, recursive=True):
        if bucket != "landing":
            raise AssertionError(f"unexpected bucket: {bucket}")
        return []

    def assert_object(self, bucket, object_key):
        if bucket != "bronze" or object_key != self.object_key:
            raise AssertionError(f"unexpected object: {bucket}/{object_key}")


class TestAuditGate(unittest.TestCase):
    def test_audit_rejects_non_success_source(self):
        with self.assertRaises(AuditGateError):
            validate_source_results(
                {
                    "topcv": {"status": "FAILED"},
                    "vieclam24h": {"status": "SUCCESS"},
                },
                expected_sources=["topcv", "vieclam24h"],
            )

    def test_audit_accepts_all_success_including_valid_empty(self):
        result = validate_source_results(
            {
                "topcv": {"status": "SUCCESS", "records_ingested": 0},
                "vieclam24h": {"status": "SUCCESS", "records_ingested": 2},
            },
            expected_sources=["topcv", "vieclam24h"],
        )
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["source_count"], 2)

    def test_partial_audit_reports_failed_source_instead_of_aborting(self):
        """Eight healthy platforms must publish even when one source times out."""
        result = validate_source_results(
            {
                "devwork": {"status": "FAILED"},
                "topcv": {"status": "SUCCESS", "records_ingested": 91},
            },
            expected_sources=["devwork", "topcv"],
            allow_partial=True,
        )
        self.assertEqual(result["status"], "PARTIAL")
        self.assertEqual(result["failed_sources"], {"devwork": "FAILED"})
        self.assertEqual(result["success_count"], 1)

    def test_partial_audit_still_fails_when_every_source_failed(self):
        with self.assertRaises(AuditGateError):
            validate_source_results(
                {"a": {"status": "FAILED"}, "b": {"status": "FAILED"}},
                expected_sources=["a", "b"],
                allow_partial=True,
            )

    def test_partial_audit_still_fails_on_missing_source_entry(self):
        with self.assertRaises(AuditGateError):
            validate_source_results(
                {"a": {"status": "SUCCESS"}},
                expected_sources=["a", "b"],
                allow_partial=True,
            )

    def test_audit_defers_when_a_source_is_deferred(self):
        result = validate_source_results(
            {
                "topcv": {"status": "DEFERRED", "reason": "cooldown"},
                "vieclam24h": {"status": "SUCCESS", "records_ingested": 2},
            },
            expected_sources=["topcv", "vieclam24h"],
        )
        self.assertEqual(result["status"], "DEFERRED")
        self.assertEqual(result["deferred_sources"], {"topcv": "cooldown"})
        self.assertEqual(result["success_count"], 1)

    def test_audit_rechecks_object_checksum_envelope_and_record_count(self):
        from src.storage.metadata_envelope import MetadataEnvelope
        from src.utils.bronze_audit_gate import audit_bronze_run
        import gzip

        with tempfile.TemporaryDirectory() as tmp:
            db_path = os.path.join(tmp, "state.db")
            manager = StateManager(db_path=db_path)
            run_id = manager.acquire_run_lock("topcv", run_id="audit_run")
            record = MetadataEnvelope.wrap(
                source="topcv",
                raw_payload={"job_id": "job-1", "job_title": "Engineer"},
                run_id=run_id,
            )
            buffer = io.BytesIO()
            with gzip.GzipFile(fileobj=buffer, mode="wb") as gz:
                gz.write((json.dumps(record) + "\n").encode())
            payload = buffer.getvalue()
            object_key = "topcv/audit.jsonl.gz"
            manager.register_ingestion_object(
                object_key=object_key,
                run_id=run_id,
                source_name="topcv",
                file_size_bytes=len(payload),
                records_count=1,
                checksum_sha256=hashlib.sha256(payload).hexdigest(),
            )
            manager.commit_success(
                source_name="topcv",
                watermark_ts="2026-11-15T00:00:00Z",
                last_record_id="job-1",
                records_ingested=1,
                run_id=run_id,
                bronze_uri=f"s3://bronze/{object_key}",
            )
            result = audit_bronze_run(
                {"topcv": {"status": "SUCCESS", "run_id": run_id, "records_ingested": 1}},
                expected_sources=["topcv"],
                state_db_path=db_path,
                minio_client=_FakeMinio(object_key, payload),
            )
            self.assertEqual(result["status"], "PASS")
            self.assertEqual(result["checked_objects"], 1)

    def test_audit_rejects_corrupt_bronze_object(self):
        from src.storage.metadata_envelope import MetadataEnvelope
        from src.utils.bronze_audit_gate import audit_bronze_run

        with tempfile.TemporaryDirectory() as tmp:
            db_path = os.path.join(tmp, "state.db")
            manager = StateManager(db_path=db_path)
            run_id = manager.acquire_run_lock("topcv", run_id="corrupt_run")
            payload = b"not-a-valid-gzip"
            object_key = "topcv/corrupt.jsonl.gz"
            manager.register_ingestion_object(
                object_key=object_key,
                run_id=run_id,
                source_name="topcv",
                file_size_bytes=len(payload),
                records_count=1,
                checksum_sha256=hashlib.sha256(payload).hexdigest(),
            )
            manager.commit_success(
                source_name="topcv",
                watermark_ts="2026-11-15T00:00:00Z",
                last_record_id="job-1",
                records_ingested=1,
                run_id=run_id,
            )
            with self.assertRaises(AuditGateError):
                audit_bronze_run(
                    {"topcv": {"status": "SUCCESS", "run_id": run_id, "records_ingested": 1}},
                    expected_sources=["topcv"],
                    state_db_path=db_path,
                    minio_client=_FakeMinio(object_key, payload),
                )


if __name__ == "__main__":
    unittest.main()

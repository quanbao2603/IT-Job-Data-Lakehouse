import csv
import os
import tempfile
import unittest
from unittest import mock
from pathlib import Path


class TestOperationalWorkflows(unittest.TestCase):
    def test_compaction_prefixes_target_old_partitions(self):
        from src.storage.compaction_plan import target_partition_prefixes

        prefixes = target_partition_prefixes(
            "2026-11-19",
            ["itviec", "topcv"],
            age_days=2,
        )
        self.assertEqual(
            prefixes,
            [
                "itviec/ingest_year=2026/ingest_month=11/ingest_day=17",
                "topcv/ingest_year=2026/ingest_month=11/ingest_day=17",
            ],
        )

    def test_review_queue_sync_and_manual_acceptance(self):
        from src.etl.review_queue import resolve_review, sync_pending_review_queue
        from src.storage.entity_registry import EntityRegistry

        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "state.db")
            csv_path = Path(tmp) / "pending_review.csv"
            with csv_path.open("w", newline="", encoding="utf-8") as stream:
                writer = csv.DictWriter(
                    stream,
                    fieldnames=(
                        "observation_id", "platform", "source_job_id",
                        "candidate_entity_id", "match_score", "match_status", "review_reason",
                    ),
                )
                writer.writeheader()
                writer.writerow({
                    "observation_id": "obs-1", "platform": "itviec", "source_job_id": "job-1",
                    "candidate_entity_id": "uj-1", "match_score": "0.8",
                    "match_status": "REVIEW_REQUIRED", "review_reason": "needs review",
                })
            registry = EntityRegistry(db_path)
            registry.create_entity(
                unified_job_id="uj-1", platform="itviec", source_job_id="job-0",
                fingerprint="acme|backend|ha noi", title="Backend", company="Acme", city="Hà Nội",
                observed_at="2026-11-19T00:00:00Z",
            )
            registry.record_observation(
                observation_id="obs-1", platform="itviec", source_job_id="job-1",
                content_hash="h1", unified_job_id=None, resolution_version="v1",
                observed_at="2026-11-19T00:00:00Z",
            )
            registry.record_link(
                observation_id="obs-1", resolution_version="v1", unified_job_id=None,
                candidate_entity_id="uj-1", match_method="FUZZY_TITLE_COMPANY_LOCATION",
                match_score=0.8, match_status="REVIEW_REQUIRED", review_reason="needs review",
            )
            self.assertEqual(sync_pending_review_queue(csv_path, db_path), 1)
            result = resolve_review(
                db_path, "obs-1", decision="ACCEPT", reviewer="reviewer-1", candidate_entity_id="uj-1",
            )
            self.assertEqual(result["status"], "RESOLVED")
            self.assertEqual(result["decision"], "ACCEPT")
            self.assertEqual(registry.get_observation("obs-1")["unified_job_id"], "uj-1")
            self.assertEqual(registry.get_link("obs-1", "v1")["match_status"], "MANUAL_MATCH")

    def test_alert_configuration_reports_missing_delivery_secret(self):
        """With no env anywhere — including the on-disk env file — alerts are simply off."""
        from src.utils import operational

        with mock.patch.dict(os.environ, {}, clear=True), \
             mock.patch.object(operational, "_ALERT_ENV_CANDIDATES", ()):
            result = operational.alert_configuration_status()
        self.assertFalse(result["enabled"])
        self.assertEqual(result["reason"], "ALERT_ENABLED=false")

    def test_alert_reads_settings_written_after_the_process_started(self):
        """Airflow reads EnvironmentFile only at boot; a later token must still reach alerts."""
        import tempfile

        from src.utils import operational

        with tempfile.NamedTemporaryFile("w", suffix=".env", delete=False) as handle:
            handle.write("ALERT_ENABLED=true\nALERT_CHANNEL=telegram\n")
            handle.write("TELEGRAM_BOT_TOKEN=placeholder-token\nTELEGRAM_CHAT_ID=12345\n")
            path = handle.name
        try:
            with mock.patch.dict(os.environ, {}, clear=True), \
                 mock.patch.object(operational, "_ALERT_ENV_CANDIDATES", (path,)):
                result = operational.alert_configuration_status()
        finally:
            os.unlink(path)
        self.assertTrue(result["enabled"])
        self.assertTrue(result["ready"])
        self.assertEqual(result["channel"], "telegram")


if __name__ == "__main__":
    unittest.main()

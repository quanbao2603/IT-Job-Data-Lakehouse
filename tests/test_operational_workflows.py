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
        from src.utils.operational import alert_configuration_status

        with mock.patch.dict(os.environ, {}, clear=True):
            result = alert_configuration_status()
        self.assertFalse(result["enabled"])
        self.assertEqual(result["reason"], "ALERT_ENABLED=false")


if __name__ == "__main__":
    unittest.main()

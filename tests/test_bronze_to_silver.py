import json
import tempfile
import unittest
from pathlib import Path

from src.etl.bronze_to_silver import BronzeToSilverPipeline
from src.etl.entity_resolver import EntityResolver
from src.etl.silver_writer import SilverWriter
from src.storage.entity_registry import EntityRegistry
from src.storage.metadata_envelope import MetadataEnvelope


class TestBronzeToSilverPipeline(unittest.TestCase):
    def _envelope(self, job_id="job-1", title="Backend Engineer", company="Acme", content_hash="hash-1"):
        payload = {
            "platform": "itviec",
            "job_id": job_id,
            "job_url": "https://jobs.example.test/1?utm_source=test",
            "scraped_at": "2026-11-16T01:00:00Z",
            "content_hash": content_hash,
            "job_title": title,
            "company_name": company,
            "salary": "20 - 30 triệu",
            "address": "Hanoi",
            "job_description": "<p>Python backend service</p>",
            "requirements": "Python",
            "benefits": "Remote",
        }
        envelope = MetadataEnvelope.wrap("itviec", payload, run_id="run-test")
        envelope["_metadata"]["ingested_at"] = "2026-11-16T02:00:00Z"
        return envelope

    def test_processes_valid_and_quarantines_invalid_without_losing_observation(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "state.db")
            silver_root = str(Path(tmp) / "silver")
            registry = EntityRegistry(db_path)
            pipeline = BronzeToSilverPipeline(
                registry=registry,
                writer=SilverWriter(silver_root),
                run_id="run-test",
                pending_review_path=str(Path(tmp) / "pending_review.csv"),
            )
            invalid = self._envelope(job_id="job-2", title="", company="Acme", content_hash="hash-2")
            report = pipeline.process([self._envelope(), invalid])
            self.assertEqual(report["metrics"]["total_observations_processed"], 2)
            self.assertEqual(report["metrics"]["valid_observations"], 1)
            self.assertEqual(report["metrics"]["quarantine_count"], 1)
            self.assertEqual(report["metrics"]["new_entity_count"], 1)
            self.assertTrue(list((Path(silver_root) / "observations").rglob("*.parquet")))
            self.assertTrue(list((Path(silver_root) / "quarantine").rglob("*.parquet")))
            self.assertTrue((Path(silver_root) / "audit_logs" / "run_run-test.json").exists())

    def test_replay_is_idempotent_for_same_envelope(self):
        with tempfile.TemporaryDirectory() as tmp:
            registry = EntityRegistry(str(Path(tmp) / "state.db"))
            pipeline = BronzeToSilverPipeline(
                registry=registry,
                writer=SilverWriter(str(Path(tmp) / "silver")),
                run_id="run-test",
                pending_review_path=str(Path(tmp) / "pending_review.csv"),
            )
            envelope = self._envelope()
            first = pipeline.process([envelope])
            second = pipeline.process([envelope])
            self.assertEqual(first["metrics"]["new_entity_count"], 1)
            self.assertEqual(second["metrics"]["new_entity_count"], 0)
            self.assertEqual(len(registry.list_observations()), 1)

    def test_bronze_manifest_reader_is_bounded_and_uses_active_objects(self):
        with tempfile.TemporaryDirectory() as tmp:
            registry = EntityRegistry(str(Path(tmp) / "state.db"))
            pipeline = BronzeToSilverPipeline(
                registry=registry,
                writer=SilverWriter(str(Path(tmp) / "silver")),
                run_id="run-bounded",
                pending_review_path=str(Path(tmp) / "pending_review.csv"),
                max_records_per_batch=1,
            )
            class ManifestReader:
                def list_active_bronze_objects(self, platform):
                    self.platform = platform
                    return [{"object_key": "itviec/active.jsonl.gz"}]
            reader = ManifestReader()
            report = pipeline.process_bronze_objects(
                reader,
                lambda key: iter([self._envelope(job_id="job-bounded")]),
                platforms={"itviec"},
            )
            self.assertEqual(reader.platform, "itviec")
            self.assertEqual(report["metrics"]["total_observations_processed"], 1)
            self.assertEqual(report["metrics"]["valid_observations"], 1)

    def test_entities_partition_by_canonical_posted_date(self):
        with tempfile.TemporaryDirectory() as tmp:
            registry = EntityRegistry(str(Path(tmp) / "state.db"))
            pipeline = BronzeToSilverPipeline(
                registry=registry,
                writer=SilverWriter(str(Path(tmp) / "silver")),
                run_id="run-partition",
                pending_review_path=str(Path(tmp) / "pending_review.csv"),
            )
            envelope = self._envelope()
            envelope["raw_payload"]["posted_date_iso"] = "2026-11-10"
            envelope["_metadata"]["payload_checksum"] = MetadataEnvelope._compute_sha256(envelope["raw_payload"])
            pipeline.process([envelope])
            entity_files = list((Path(tmp) / "silver" / "entities").rglob("*.parquet"))
            self.assertTrue(entity_files)
            self.assertIn("year=2026/month=11/day=10", str(entity_files[0]))


if __name__ == "__main__":
    unittest.main()

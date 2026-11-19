import json
import tempfile
import unittest
from pathlib import Path

import pyarrow.parquet as pq

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
            observation_file = next((Path(silver_root) / "observations").rglob("*.parquet"))
            silver_row = pq.read_table(observation_file).to_pylist()[0]
            silver_metadata = json.loads(silver_row["platform_specific_attributes"])["_bronze_metadata"]
            self.assertEqual(silver_metadata["bronze_pipeline_run_id"], "run-test")
            self.assertEqual(silver_metadata["bronze_source"], "itviec")
            quarantine_file = next((Path(silver_root) / "quarantine").rglob("*.parquet"))
            quarantine_row = pq.read_table(quarantine_file).to_pylist()[0]
            quarantine_metadata = json.loads(
                quarantine_row["platform_specific_attributes"]
            )["_bronze_metadata"]
            self.assertEqual(quarantine_metadata["bronze_pipeline_run_id"], "run-test")

    def test_replay_does_not_append_duplicate_observation_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            registry = EntityRegistry(str(Path(tmp) / "state.db"))
            root = Path(tmp) / "silver"
            pipeline = BronzeToSilverPipeline(
                registry=registry,
                writer=SilverWriter(str(root)),
                run_id="run-test",
                pending_review_path=str(Path(tmp) / "pending_review.csv"),
            )
            envelope = self._envelope()
            pipeline.process([envelope])
            pipeline.process([envelope])
            observation_files = list((root / "observations").rglob("*.parquet"))
            self.assertEqual(sum(pq.read_metadata(path).num_rows for path in observation_files), 1)

    def test_schema_drift_is_exposed_in_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            registry = EntityRegistry(str(Path(tmp) / "state.db"))
            pipeline = BronzeToSilverPipeline(
                registry=registry,
                writer=SilverWriter(str(Path(tmp) / "silver")),
                run_id="run-drift",
                pending_review_path=str(Path(tmp) / "pending_review.csv"),
            )
            envelope = self._envelope()
            envelope["_rescued_data"] = {"new_source_field": "value"}
            envelope["_metadata"]["payload_checksum"] = MetadataEnvelope._compute_sha256(
                {**envelope["raw_payload"], **envelope["_rescued_data"]}
            )
            report = pipeline.process([envelope])
            self.assertTrue(report["schema_drift_warnings"])

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
            self.assertIn("year=2026/month=11/day=10", entity_files[0].as_posix())

    def test_incremental_manifest_batches_do_not_close_historical_entities(self):
        with tempfile.TemporaryDirectory() as tmp:
            registry = EntityRegistry(str(Path(tmp) / "state.db"))
            silver_root = str(Path(tmp) / "silver")
            pending = str(Path(tmp) / "pending_review.csv")
            first = self._envelope(job_id="old-job")
            first["raw_payload"]["scraped_at"] = "2026-10-01T00:00:00Z"
            first["_metadata"]["ingested_at"] = "2026-10-01T00:00:00Z"
            first["_metadata"]["payload_checksum"] = MetadataEnvelope._compute_sha256(first["raw_payload"])
            initial = BronzeToSilverPipeline(
                registry=registry,
                writer=SilverWriter(silver_root),
                run_id="initial",
                pending_review_path=pending,
            )
            initial.process([first])
            old_entity = registry.list_entities()[0]["unified_job_id"]

            incremental = BronzeToSilverPipeline(
                registry=registry,
                writer=SilverWriter(silver_root),
                run_id="incremental",
                pending_review_path=pending,
            )

            class ManifestReader:
                def list_active_bronze_objects(self, platform):
                    return [{"object_key": "itviec/delta.jsonl.gz"}]

            incremental.process_bronze_objects(
                ManifestReader(),
                lambda key: iter([self._envelope(job_id="new-job")]),
                platforms={"itviec"},
            )
            self.assertNotEqual(registry.get_entity(old_entity)["entity_status"], "CLOSED")

    def test_entity_output_counts_current_batch_observation(self):
        with tempfile.TemporaryDirectory() as tmp:
            registry = EntityRegistry(str(Path(tmp) / "state.db"))
            pipeline = BronzeToSilverPipeline(
                registry=registry,
                writer=SilverWriter(str(Path(tmp) / "silver")),
                run_id="run-counts",
                pending_review_path=str(Path(tmp) / "pending_review.csv"),
            )
            report = pipeline.process([self._envelope(job_id="new-counted")], partial_run=True)
            entity_files = list((Path(tmp) / "silver" / "entities").rglob("*.parquet"))
            rows = pq.read_table(entity_files[0]).to_pylist()
            self.assertEqual(report["metrics"]["valid_observations"], 1)
            self.assertEqual(rows[0]["observation_count"], 1)
            self.assertEqual(rows[0]["source_count"], 1)


if __name__ == "__main__":
    unittest.main()

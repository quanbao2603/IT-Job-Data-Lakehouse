"""Tests for the Silver fixes Gold depends on: domain coverage, release sealing, primary source."""
import json
import tempfile
import unittest
from pathlib import Path

import pyarrow.parquet as pq

from src.etl.bronze_to_silver import BronzeToSilverPipeline
from src.etl.silver_writer import SilverWriter
from src.etl.transformers import canonicalize_bronze_record, generate_observation_id, normalization_versions
from src.storage.entity_registry import EntityRegistry
from src.storage.metadata_envelope import MetadataEnvelope


class _ManifestReader:
    def __init__(self, keys):
        self._keys = keys

    def list_active_bronze_objects(self, platform):
        return [{"object_key": key} for key in self._keys.get(platform, [])]


def _envelope(platform, job_id, **extra):
    payload = {
        "platform": platform,
        "job_id": job_id,
        "job_url": f"https://jobs.example.test/{platform}/{job_id}",
        "scraped_at": "2026-11-16T01:00:00Z",
        "content_hash": f"hash-{platform}-{job_id}",
        "job_title": "Backend Engineer",
        "company_name": "Acme",
        "salary": "20 - 30 triệu",
        "job_description": "<p>Python backend service</p>",
    }
    payload.update(extra)
    envelope = MetadataEnvelope.wrap(platform, payload, run_id="run-test")
    envelope["_metadata"]["ingested_at"] = "2026-11-16T02:00:00Z"
    return envelope


class TestJobDomainCoverage(unittest.TestCase):
    """GAP-8: job_domain must be derivable for platforms that only expose `industries`."""

    def test_domain_from_job_domain_field(self):
        record = canonicalize_bronze_record(_envelope("itviec", "1", job_domain="Fintech"))
        self.assertEqual(record["job_domain"], "Fintech")

    def test_domain_from_industries_field(self):
        record = canonicalize_bronze_record(_envelope("careerviet", "2", industries="CNTT - Phần mềm"))
        self.assertEqual(record["job_domain"], "CNTT - Phần mềm")

    def test_domain_from_company_industry_fallback(self):
        record = canonicalize_bronze_record(_envelope("topdev", "3", company_industry="E-commerce"))
        self.assertEqual(record["job_domain"], "E-commerce")

    def test_domain_none_when_no_source_field(self):
        record = canonicalize_bronze_record(_envelope("topcv", "4"))
        self.assertIsNone(record["job_domain"])


class TestNormalizationVersions(unittest.TestCase):
    """GAP-6: versioned normalization contracts must be exposed for Gold lineage."""

    def test_versions_are_exposed(self):
        versions = normalization_versions()
        self.assertEqual(versions["skill_taxonomy_version"], "skill-taxonomy-v1")
        self.assertEqual(versions["company_normalization_version"], "company-normalization-v1")
        self.assertEqual(versions["city_normalization_version"], "city-normalization-v1")
        self.assertEqual(versions["transform_version"], "bronze-to-silver-transform-v1")


class TestSilverReleaseSealing(unittest.TestCase):
    """GAP-1: Gold must read a sealed release, never scan a day folder."""

    def _run(self, tmp, envelopes, run_id="run-release"):
        registry = EntityRegistry(str(Path(tmp) / "state.db"))
        pipeline = BronzeToSilverPipeline(
            registry=registry,
            writer=SilverWriter(str(Path(tmp) / "silver")),
            run_id=run_id,
            pending_review_path=str(Path(tmp) / "pending_review.csv"),
        )
        reader = _ManifestReader({"itviec": ["itviec/obj-1.jsonl.gz"]})
        report = pipeline.process_bronze_objects(
            reader,
            lambda key: iter(envelopes),
            platforms={"itviec"},
        )
        return pipeline, report

    def test_release_manifest_is_written_and_complete(self):
        with tempfile.TemporaryDirectory() as tmp:
            _, report = self._run(tmp, [_envelope("itviec", "job-1")])
            self.assertIn("silver_release_id", report)
            self.assertEqual(report["silver_release_status"], "COMPLETE")
            self.assertEqual(len(report["manifest_fingerprint"]), 64)

            release_path = Path(tmp) / "silver" / "releases" / f"{report['silver_release_id']}.json"
            self.assertTrue(release_path.exists())
            release = json.loads(release_path.read_text(encoding="utf-8"))
            self.assertEqual(release["status"], "COMPLETE")
            self.assertEqual(release["taxonomy_versions"]["skill_taxonomy_version"], "skill-taxonomy-v1")
            self.assertGreater(release["object_count"], 0)
            self.assertEqual(release["record_count"], sum(int(o["records"]) for o in release["objects"]))
            datasets = {o["dataset"] for o in release["objects"]}
            self.assertIn("observations", datasets)
            self.assertIn("entities", datasets)
            self.assertIn("entity_links", datasets)
            for obj in release["objects"]:
                self.assertEqual(obj["status"], "COMPLETE")
                self.assertEqual(len(obj["sha256"]), 64)

    def test_release_fingerprint_is_deterministic_for_same_input(self):
        with tempfile.TemporaryDirectory() as tmp_a, tempfile.TemporaryDirectory() as tmp_b:
            _, report_a = self._run(tmp_a, [_envelope("itviec", "job-1")], run_id="same-run")
            _, report_b = self._run(tmp_b, [_envelope("itviec", "job-1")], run_id="same-run")
            self.assertEqual(report_a["manifest_fingerprint"], report_b["manifest_fingerprint"])
            self.assertEqual(report_a["silver_release_id"], report_b["silver_release_id"])

    def test_primary_source_flags_the_canonical_observation(self):
        """GAP-5: is_primary_source marks the representative observation, not the entity creator.

        Links are immutable audit rows, so the flag is time-scoped: an older link keeps
        True if it was canonical when written. Consumers must therefore read the latest
        accepted link per entity to find the current representative.
        """
        with tempfile.TemporaryDirectory() as tmp:
            first = _envelope("itviec", "job-p", content_hash="h1")
            second = _envelope("itviec", "job-p", content_hash="h2")
            # observed_at is frozen into _metadata at Bronze wrap time and Silver prefers it,
            # so a later observation must update the metadata field, not just the payload.
            second["raw_payload"]["scraped_at"] = "2026-11-17T01:00:00Z"
            second["_metadata"]["observed_at"] = "2026-11-17T01:00:00Z"
            second["_metadata"]["ingested_at"] = "2026-11-17T02:00:00Z"
            second["_metadata"]["payload_checksum"] = MetadataEnvelope._compute_sha256(second["raw_payload"])
            self._run(tmp, [first, second], run_id="run-primary")

            first_observation_id = generate_observation_id(
                "itviec", "job-p", "2026-11-16T01:00:00Z", "h1"
            )
            second_observation_id = generate_observation_id(
                "itviec", "job-p", "2026-11-17T01:00:00Z", "h2"
            )
            self.assertNotEqual(first_observation_id, second_observation_id)

            link_files = list((Path(tmp) / "silver" / "entity_links").rglob("*.parquet"))
            self.assertTrue(link_files)
            rows = []
            for path in link_files:
                rows.extend(pq.read_table(path).to_pylist())
            self.assertEqual(len(rows), 2)
            by_observation = {row["observation_id"]: row for row in rows}
            self.assertIn(first_observation_id, by_observation)
            self.assertIn(second_observation_id, by_observation)

            # The newer observation is canonical, so its link must carry the flag.
            self.assertTrue(by_observation[second_observation_id]["is_primary_source"])
            self.assertEqual(by_observation[second_observation_id]["match_method"], "SOURCE_JOB_ID")
            # The creator link is no longer the representative for the current entity state.
            self.assertEqual(by_observation[first_observation_id]["match_status"], "DISTINCT_ENTITY")
            # Both links point at the same entity: content change must not create a new one.
            self.assertEqual(
                by_observation[first_observation_id]["unified_job_id"],
                by_observation[second_observation_id]["unified_job_id"],
            )


class TestObservedAtProvenance(unittest.TestCase):
    """observed_at is frozen at Bronze wrap time so Silver and Gold replays stay idempotent."""

    def test_wrap_captures_observed_at_from_payload(self):
        envelope = _envelope("itviec", "job-w")
        self.assertEqual(envelope["_metadata"]["observed_at"], "2026-11-16T01:00:00Z")

    def test_metadata_observed_at_wins_over_payload_scraped_at(self):
        envelope = _envelope("itviec", "job-t")
        envelope["_metadata"]["observed_at"] = "2026-11-10T00:00:00Z"
        envelope["raw_payload"]["scraped_at"] = "2026-11-16T01:00:00Z"
        envelope["_metadata"]["payload_checksum"] = MetadataEnvelope._compute_sha256(envelope["raw_payload"])
        record = canonicalize_bronze_record(envelope)
        self.assertEqual(record["observed_at"], "2026-11-10T00:00:00Z")
        self.assertEqual(
            record["observation_id"],
            generate_observation_id("itviec", "job-t", "2026-11-10T00:00:00Z", "hash-itviec-job-t"),
        )


if __name__ == "__main__":
    unittest.main()

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from src.gold.load_facts import build_change_events
from src.gold.release_controller import load_bundle
from src.gold.silver_release_reader import (
    SilverRelease,
    build_effective_link,
    load_historical_release_data,
)


class TestGoldReviewRegressions(unittest.TestCase):
    def test_load_bundle_deletes_dependent_facts_before_snapshot(self):
        class Cursor:
            def __init__(self):
                self.sql = []

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def execute(self, statement, params=None):
                self.sql.append(statement)

            def executemany(self, statement, rows):
                self.sql.append(statement)

        class Connection:
            def __init__(self):
                self.cursor_instance = Cursor()

            def cursor(self):
                return self.cursor_instance

            def commit(self):
                pass

        class Bundle:
            gold_release_id = "gold-test"
            silver_release_id = "silver-test"
            dim_job_closes = []
            dim_date = []
            dim_platform = []
            dim_company = []
            dim_skill = []
            dim_job = []
            snapshot = []
            platform = []
            skill = []
            change_event = []
            market = []
            company = []
            skill_pair = []
            effective_entity = []
            effective_link = []
            source_observation = []
            conflicts = []

        connection = Connection()
        load_bundle(connection, Bundle(), include_staging=False)
        deletes = [
            sql for sql in connection.cursor_instance.sql
            if sql.startswith("DELETE FROM gold.fact_")
        ]
        self.assertLess(
            deletes.index("DELETE FROM gold.fact_job_platform_daily WHERE gold_release_id = %s"),
            deletes.index("DELETE FROM gold.fact_job_daily_snapshot WHERE gold_release_id = %s"),
        )

    def test_resolution_version_is_numeric_when_timestamps_tie(self):
        links = [
            {
                "observation_id": "obs-1", "unified_job_id": "uj-v2",
                "platform": "itviec", "source_job_id": "1",
                "match_status": "AUTO_MATCH", "match_method": "SOURCE_JOB_ID",
                "matched_at": "2026-11-16T03:00:00Z", "resolution_version": "v2",
            },
            {
                "observation_id": "obs-1", "unified_job_id": "uj-v10",
                "platform": "itviec", "source_job_id": "1",
                "match_status": "AUTO_MATCH", "match_method": "SOURCE_JOB_ID",
                "matched_at": "2026-11-16T03:00:00Z", "resolution_version": "v10",
            },
        ]
        rows, conflicts = build_effective_link(links, cutoff="2026-11-16T04:00:00Z")
        self.assertEqual(rows[0]["unified_job_id"], "uj-v10")
        self.assertEqual(conflicts, [])

    def test_same_effective_link_key_with_different_decisions_is_a_conflict(self):
        base = {
            "observation_id": "obs-1", "platform": "itviec", "source_job_id": "1",
            "match_status": "AUTO_MATCH", "match_method": "SOURCE_JOB_ID",
            "matched_at": "2026-11-16T03:00:00Z", "resolution_version": "v1",
        }
        rows, conflicts = build_effective_link(
            [{**base, "unified_job_id": "uj-a"}, {**base, "unified_job_id": "uj-b"}],
            cutoff="2026-11-16T04:00:00Z",
        )
        self.assertEqual(len(rows), 1)
        self.assertIn("AMBIGUOUS_LINK", {row["conflict_type"] for row in conflicts})

    def test_historical_release_reader_finds_late_observation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            object_dir = root / "observations"
            object_dir.mkdir(parents=True)
            object_path = object_dir / "prior.parquet"
            prior_observation = {
                "observation_id": "obs-old", "platform": "itviec", "source_job_id": "1",
                "observed_at": "2026-11-15T02:00:00Z", "job_title_normalized": "backend",
            }
            pq.write_table(pa.Table.from_pylist([prior_observation]), object_path)
            digest = hashlib.sha256(object_path.read_bytes()).hexdigest()
            release_id = "silver_prior"
            manifest = {
                "silver_release_id": release_id, "status": "COMPLETE",
                "snapshot_cutoff_at": "2026-11-16T00:00:00Z", "resolution_version": "v1",
                "manifest_fingerprint": "f" * 64, "taxonomy_versions": {}, "metrics": {},
                "objects": [{"dataset": "observations", "object_key": "observations/prior.parquet",
                              "records": 1, "sha256": digest, "status": "COMPLETE"}],
                "partial_run": False, "coverage_warning": False, "failed_platforms": [],
            }
            releases = root / "releases"
            releases.mkdir()
            (releases / f"{release_id}.json").write_text(json.dumps(manifest), encoding="utf-8")
            current = SilverRelease(
                release_id="silver_current", status="COMPLETE",
                snapshot_cutoff_at="2026-11-17T00:00:00Z", resolution_version="v1",
                manifest_fingerprint="c" * 64, taxonomy_versions={}, metrics={}, objects=(),
                partial_run=False, coverage_warning=False, failed_platforms=(),
                path=releases / "silver_current.json",
            )
            late = {
                "observation_id": "obs-late", "platform": "itviec", "source_job_id": "1",
                "observed_at": "2026-11-15T03:00:00Z",
            }
            historical, links, late_ids, count, audit = load_historical_release_data(
                root, current, [late]
            )
            self.assertEqual(count, 1)
            self.assertEqual([row["observation_id"] for row in historical], ["obs-old"])
            self.assertEqual(links, [])
            self.assertEqual(late_ids, {"obs-late"})

    def test_late_observation_marks_recomputed_change_as_revised(self):
        def observation(observation_id, observed_at, salary):
            return {
                "observation_id": observation_id,
                "platform": "itviec", "source_job_id": "1",
                "observed_at": observed_at,
                "job_title_normalized": "backend engineer",
                "city_normalized": "Hà Nội", "work_mode": "Hybrid",
                "skills_standardized": ["python"],
                "salary_min_vnd": salary, "salary_max_vnd": salary + 1_000_000,
                "unified_job_id": "uj-1",
            }

        rows = build_change_events(
            [
                observation("obs-late", "2026-11-15T02:00:00Z", 20_000_000),
                observation("obs-new", "2026-11-16T02:00:00Z", 25_000_000),
            ],
            {}, gold_release_id="gold-1", silver_release_id="silver-1",
            cutoff_at="2026-11-16T04:00:00Z", late_observation_ids={"obs-late"},
        )
        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0]["is_revised"])


if __name__ == "__main__":
    unittest.main()

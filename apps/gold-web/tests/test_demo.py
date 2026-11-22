"""Demo fixture contract: labelled, filtered and never a live fallback."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from server.config import Settings  # noqa: E402
from server.demo import DEMO_GOLD_RELEASE, DemoRepository  # noqa: E402
from server.repository import GoldReleaseChanged  # noqa: E402
from server.schemas import FilterParams  # noqa: E402


def demo_settings() -> Settings:
    return Settings(
        web_env="development", demo_mode=True, root_path="/insights",
        stale_after_days=1, query_timeout_ms=5000, db_host="127.0.0.1",
        db_port=5432, db_name="lakehouse", reader_user="gold_reader",
        reader_password="", connect_timeout=5,
    )


class TestDemoRepository(unittest.TestCase):
    def setUp(self) -> None:
        self.repo = DemoRepository(demo_settings())

    def test_dashboard_matches_its_own_fixture(self):
        payload = self.repo.dashboard(FilterParams())
        summary = payload["summary"]
        self.assertEqual(summary["jobs"], 12)
        self.assertEqual(summary["salary_samples"], 10)
        self.assertEqual(summary["companies"], 11)  # UNKNOWN excluded from the KPI
        self.assertEqual(summary["missing_city"], 1)
        self.assertIsNotNone(summary["salary_median"])

    def test_filters_are_applied_to_the_cohort(self):
        python = self.repo.dashboard(FilterParams(skill="python"))
        self.assertEqual(python["summary"]["jobs"], 3)
        backend = self.repo.jobs(FilterParams(domain="Backend"))
        self.assertEqual(backend["total"], 3)
        self.assertTrue(all(row["job_domain"] == "Backend" for row in backend["items"]))

    def test_drill_through_company_key_is_exact(self):
        companies = self.repo.companies(FilterParams())
        first = companies["items"][0]
        jobs = self.repo.jobs(FilterParams(company_key=first["company_key"]))
        self.assertEqual(jobs["total"], first["job_count"])

    def test_skill_pairs_are_unordered_and_deduplicated(self):
        payload = self.repo.skills(FilterParams())
        keys = [(row["skill_a"], row["skill_b"]) for row in payload["pairs"]]
        self.assertEqual(len(keys), len(set(keys)))
        self.assertTrue(all(first < second for first, second in keys))

    def test_pagination_is_stable(self):
        first = self.repo.jobs(FilterParams(page=1, page_size=5))
        second = self.repo.jobs(FilterParams(page=2, page_size=5))
        self.assertEqual(len(first["items"]), 5)
        self.assertFalse({row["unified_job_id"] for row in first["items"]} & {row["unified_job_id"] for row in second["items"]})

    def test_missing_salary_is_null_not_zero(self):
        payload = self.repo.jobs(FilterParams(page_size=50))
        missing = [row for row in payload["items"] if not row["salary_sample_flag"]]
        self.assertTrue(missing)
        for row in missing:
            self.assertIsNone(row["salary_midpoint_vnd"])

    def test_demo_release_ids_are_explicit(self):
        context = self.repo.release_context()
        self.assertEqual(context.gold_release_id, DEMO_GOLD_RELEASE)
        self.assertIn("demo", context.gold_release_id)

    def test_expected_release_mismatch_is_rejected(self):
        with self.assertRaises(GoldReleaseChanged):
            self.repo.dashboard(FilterParams(expected_release_id="gold_live_release"))


if __name__ == "__main__":
    unittest.main()

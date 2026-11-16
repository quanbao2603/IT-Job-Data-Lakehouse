import unittest
from datetime import date

from src.etl.entity_lifecycle import choose_canonical, is_past_grace_period, next_entity_status
from src.etl.quality_gate import validate_observation


class TestSilverQualityLifecycle(unittest.TestCase):
    def test_hard_quality_failures(self):
        self.assertFalse(validate_observation({"job_title": "x", "company_name": "y"}).accepted)
        self.assertEqual(validate_observation({"source_job_id": "1", "job_title": "x", "company_name": "y", "salary_min_vnd": 5, "salary_max_vnd": 4}).quarantine_reason, "SALARY_MIN_GREATER_THAN_MAX")

    def test_soft_quality_rules_are_deterministic(self):
        internship = {"source_job_id": "1", "job_title": "x", "company_name": "y", "job_level": "Intern", "salary_min_vnd": 1_000_000}
        self.assertTrue(validate_observation(internship).accepted)
        old = {"source_job_id": "1", "job_title": "x", "company_name": "y", "posted_date": "2019-01-01"}
        self.assertEqual(validate_observation(old, date(2026, 11, 16)).quarantine_reason, "STALE_POSTED_DATE")

    def test_lifecycle_and_canonical_selection(self):
        rows = [{"observed_at": "2026-11-15", "job_description_clean": "short"}, {"observed_at": "2026-11-16", "job_description_clean": "long description"}]
        self.assertEqual(choose_canonical(rows), rows[1])
        self.assertEqual(next_entity_status("ACTIVE", content_changed=True), "UPDATED")
        self.assertEqual(next_entity_status("CLOSED"), "REOPENED")
        self.assertTrue(is_past_grace_period("itviec", "2026-11-01T00:00:00Z", date(2026, 11, 16)))


if __name__ == "__main__":
    unittest.main()

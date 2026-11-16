import unittest
from datetime import date

from src.etl.entity_lifecycle import choose_canonical, is_past_grace_period, next_entity_status
from src.etl.quality_gate import (
    enforce_publish_gate,
    validate_entity,
    validate_link,
    validate_observation,
)


class TestSilverQualityLifecycle(unittest.TestCase):
    def test_hard_quality_failures(self):
        self.assertFalse(validate_observation({"job_title": "x", "company_name": "y"}).accepted)
        record = {
            "source_job_id": "1",
            "job_title": "x",
            "company_name": "y",
            "platform": "itviec",
            "observation_id": "obs-1",
            "observed_at": "2026-11-16T00:00:00Z",
            "vacancies_count": 1,
            "salary_min_vnd": 5,
            "salary_max_vnd": 4,
        }
        self.assertEqual(validate_observation(record).quarantine_reason, "SALARY_MIN_GREATER_THAN_MAX")

    def test_soft_quality_rules_are_deterministic(self):
        internship = {
            "source_job_id": "1",
            "job_title": "x",
            "company_name": "y",
            "platform": "itviec",
            "observation_id": "obs-1",
            "observed_at": "2026-11-16T00:00:00Z",
            "vacancies_count": 1,
            "job_level": "Intern",
            "salary_min_vnd": 1_000_000,
        }
        self.assertTrue(validate_observation(internship).accepted)
        old = {
            "source_job_id": "1",
            "job_title": "x",
            "company_name": "y",
            "platform": "itviec",
            "observation_id": "obs-1",
            "observed_at": "2026-11-16T00:00:00Z",
            "vacancies_count": 1,
            "posted_date": "2019-01-01",
        }
        self.assertEqual(validate_observation(old, date(2026, 11, 16)).quarantine_reason, "STALE_POSTED_DATE")

    def test_lifecycle_and_canonical_selection(self):
        rows = [
            {"observed_at": "2026-11-15", "job_description_clean": "short"},
            {"observed_at": "2026-11-16", "job_description_clean": "long description"},
        ]
        self.assertEqual(choose_canonical(rows), rows[1])
        self.assertEqual(next_entity_status("ACTIVE", content_changed=True), "UPDATED")
        self.assertEqual(next_entity_status("CLOSED"), "REOPENED")
        self.assertTrue(is_past_grace_period("itviec", "2026-11-01T00:00:00Z", date(2026, 11, 16)))

    def test_entity_and_link_contracts_fail_closed(self):
        self.assertTrue(validate_entity({
            "unified_job_id": "uj_1",
            "canonical_title": "Backend",
            "canonical_company": "Acme",
            "first_seen_at": "2026-11-16T00:00:00Z",
            "last_seen_at": "2026-11-16T00:00:00Z",
            "resolution_version": "v1",
            "entity_status": "NEW",
            "observation_count": 1,
        }).accepted)
        self.assertEqual(
            validate_entity({"unified_job_id": "uj_1", "entity_status": "BROKEN"}).quarantine_reason,
            "MISSING_ENTITY_FIELD: canonical_title",
        )
        self.assertTrue(validate_link({
            "observation_id": "obs-1",
            "match_status": "REVIEW_REQUIRED",
            "match_method": "FUZZY_TITLE_COMPANY_LOCATION",
            "match_score": 0.8,
            "unified_job_id": None,
            "candidate_entity_id": "uj_1",
        }).accepted)
        self.assertEqual(validate_link({
            "observation_id": "obs-1",
            "match_status": "REVIEW_REQUIRED",
            "match_method": "FUZZY_TITLE_COMPANY_LOCATION",
            "match_score": 0.8,
            "unified_job_id": "uj_1",
            "candidate_entity_id": "uj_1",
        }).quarantine_reason, "REVIEW_LINK_MUST_NOT_HAVE_UNIFIED_ID")

    def test_publish_gate_blocks_only_above_five_percent(self):
        self.assertEqual(enforce_publish_gate({"metrics": {"quarantine_rate_pct": 5.0}})["status"], "READY")
        with self.assertRaises(ValueError):
            enforce_publish_gate({"metrics": {"quarantine_rate_pct": 5.01}})
        with self.assertRaises(ValueError):
            enforce_publish_gate({"metrics": {"quarantine_rate_pct": 0}, "schema_drift_warnings": ["new_field"]})
        with self.assertRaises(ValueError):
            enforce_publish_gate({"metrics": {"quarantine_rate_pct": 0}, "partial_run": True})
        with self.assertRaises(ValueError):
            enforce_publish_gate({"metrics": {"quarantine_rate_pct": 0}, "failed_platforms": ["topcv"]})

    def test_invalid_posted_date_is_quarantined_without_explicit_today(self):
        record = {
            "source_job_id": "1",
            "job_title": "x",
            "company_name": "y",
            "platform": "itviec",
            "observation_id": "obs-1",
            "observed_at": "2026-11-16T00:00:00Z",
            "vacancies_count": 1,
            "posted_date": "not-a-date",
        }
        self.assertEqual(validate_observation(record).quarantine_reason, "INVALID_POSTED_DATE")


if __name__ == "__main__":
    unittest.main()

import sqlite3
import unittest

from src.utils.publish_silver_for_gold import publish_entities


class TestSilverGoldPublish(unittest.TestCase):
    def _row(self):
        return {
            "unified_job_id": "uj-1",
            "canonical_title": "Backend Engineer",
            "canonical_company": "Acme",
            "canonical_location": "Hanoi",
            "canonical_salary_min_vnd": 20_000_000,
            "canonical_salary_max_vnd": 30_000_000,
            "canonical_currency": "VND",
            "canonical_description": "Build services",
            "canonical_posted_date": "2026-11-16",
            "source_count": 2,
            "observation_count": 3,
            "first_seen_at": "2026-11-15T00:00:00Z",
            "last_seen_at": "2026-11-16T00:00:00Z",
            "entity_status": "ACTIVE",
            "canonical_updated_at": "2026-11-16T00:00:00Z",
            "resolution_version": "v1",
        }

    def test_publish_upserts_and_reads_back_exact_entity(self):
        connection = sqlite3.connect(":memory:")
        result = publish_entities(
            [self._row()],
            connection,
            {"metrics": {"quarantine_rate_pct": 0.0}, "schema_drift_warnings": []},
        )
        self.assertEqual(result["status"], "COMPLETE")
        self.assertEqual(result["published_count"], 1)
        row = connection.execute(
            "SELECT unified_job_id, canonical_title, entity_status FROM gold_entities"
        ).fetchone()
        self.assertEqual(row, ("uj-1", "Backend Engineer", "ACTIVE"))

    def test_publish_gate_blocks_before_any_write(self):
        connection = sqlite3.connect(":memory:")
        with self.assertRaises(ValueError):
            publish_entities(
                [self._row()],
                connection,
                {"metrics": {"quarantine_rate_pct": 5.01}, "schema_drift_warnings": []},
            )
        self.assertEqual(connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='gold_entities'"
        ).fetchone(), None)


if __name__ == "__main__":
    unittest.main()

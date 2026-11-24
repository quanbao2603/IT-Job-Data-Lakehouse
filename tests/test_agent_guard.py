import unittest

from src.agent.guard import GuardError, validate
from src.agent.schema import ALLOWED_RELATIONS


def _ok(sql: str, max_rows: int = 100):
    return validate(sql, allowed_relations=ALLOWED_RELATIONS, max_rows=max_rows)


class TestSqlGuard(unittest.TestCase):
    def test_allows_a_plain_select_and_adds_a_limit(self):
        result = _ok("SELECT COUNT(*) FROM gold.v_job_snapshot")
        self.assertIn("LIMIT 100", result.sql)
        self.assertEqual(result.relations, ("gold.v_job_snapshot",))

    def test_clamps_an_oversized_limit(self):
        result = _ok("SELECT 1 FROM gold.v_job_snapshot LIMIT 99999", max_rows=50)
        self.assertIn("LIMIT 50", result.sql)
        self.assertNotIn("99999", result.sql)
        self.assertTrue(result.warnings)

    def test_keeps_a_limit_within_bounds(self):
        result = _ok("SELECT 1 FROM gold.v_job_snapshot LIMIT 10", max_rows=50)
        self.assertIn("LIMIT 10", result.sql)

    def test_rejects_writes(self):
        for statement in (
            "UPDATE gold.dim_job SET canonical_title = 'x'",
            "DELETE FROM gold.dim_job",
            "INSERT INTO gold.dim_job VALUES (1)",
            "DROP TABLE gold.dim_job",
            "ALTER TABLE gold.dim_job ADD COLUMN x int",
            "TRUNCATE gold.dim_job",
            "GRANT SELECT ON gold.dim_job TO public",
            "SELECT * INTO gold.copy_job FROM gold.dim_job",
        ):
            with self.subTest(statement=statement):
                with self.assertRaises(GuardError):
                    _ok(statement)

    def test_rejects_multiple_statements(self):
        with self.assertRaises(GuardError):
            _ok("SELECT 1 FROM gold.dim_date; DROP TABLE gold.dim_job")

    def test_rejects_other_schemas(self):
        for statement in (
            "SELECT * FROM information_schema.tables",
            "SELECT * FROM gold_control.active_release",
            "SELECT * FROM public.some_table",
            "SELECT pg_sleep(10)",
        ):
            with self.subTest(statement=statement):
                with self.assertRaises(GuardError):
                    _ok(statement)

    def test_rejects_unlisted_relations(self):
        with self.assertRaises(GuardError):
            _ok("SELECT * FROM gold.dim_unknown_table")

    def test_rejects_unqualified_table_names(self):
        with self.assertRaises(GuardError):
            _ok("SELECT * FROM dim_job")

    def test_allows_a_cte_over_allowed_relations(self):
        result = _ok(
            "WITH per_company AS (SELECT company_key, COUNT(*) AS n FROM gold.v_job_snapshot "
            "GROUP BY company_key) SELECT * FROM per_company ORDER BY n DESC"
        )
        self.assertIn("LIMIT 100", result.sql)

    def test_keywords_inside_string_literals_are_ignored(self):
        result = _ok("SELECT 'delete from gold.dim_job' AS note FROM gold.dim_date")
        self.assertIn("gold.dim_date", result.relations)

    def test_comments_cannot_smuggle_a_write(self):
        with self.assertRaises(GuardError):
            _ok("SELECT 1 FROM gold.dim_date -- harmless\n; DELETE FROM gold.dim_job")

    def test_empty_sql_is_rejected(self):
        with self.assertRaises(GuardError):
            _ok("   ")


if __name__ == "__main__":
    unittest.main()

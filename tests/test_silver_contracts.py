import unittest

from src.etl.transformers import (
    clean_company_name,
    generate_observation_id,
    generate_unified_job_id,
    make_canonical_url,
    parse_salary,
    strip_html,
)
from src.schemas.entity_links_schema import SILVER_ENTITY_LINKS_SCHEMA
from src.schemas.entity_schema import SILVER_ENTITY_SCHEMA
from src.schemas.silver_schema import QUARANTINE_SCHEMA, SILVER_OBSERVATIONS_SCHEMA


class TestSilverContracts(unittest.TestCase):
    def test_schema_field_counts_and_nullability(self):
        self.assertEqual(len(SILVER_OBSERVATIONS_SCHEMA), 46)
        self.assertEqual(len(SILVER_ENTITY_SCHEMA), 16)
        self.assertEqual(len(SILVER_ENTITY_LINKS_SCHEMA), 16)
        self.assertEqual(len(QUARANTINE_SCHEMA), 50)
        self.assertTrue(SILVER_ENTITY_LINKS_SCHEMA.field("unified_job_id").nullable)

    def test_ids_are_deterministic_and_delimited(self):
        first = generate_observation_id("itviec", "12", "2026-11-16", "abc")
        second = generate_observation_id("itviec", "12", "2026-11-16", "abc")
        self.assertEqual(first, second)
        self.assertEqual(len(first), 64)
        self.assertTrue(generate_unified_job_id("itviec|12|" + first).startswith("uj_"))

    def test_normalization_and_url_tracking_removal(self):
        self.assertEqual(clean_company_name("Công ty TNHH Example JSC"), "example")
        self.assertEqual(strip_html("<p>Backend&nbsp;Engineer</p>"), "Backend Engineer")
        self.assertEqual(
            make_canonical_url("HTTPS://Example.com/jobs/12/?utm_source=x&ref=abc&lang=vi"),
            "https://example.com/jobs/12?lang=vi",
        )

    def test_salary_parser_is_deterministic(self):
        self.assertEqual(parse_salary("15 - 20 triệu"), (15000000, 20000000, False, "VND"))
        self.assertEqual(parse_salary("2,000 - 3,000 USD"), (50800000, 76200000, False, "USD"))
        self.assertEqual(parse_salary("Thỏa thuận"), (None, None, True, None))


if __name__ == "__main__":
    unittest.main()

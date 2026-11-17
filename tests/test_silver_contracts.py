import unittest

from src.etl.transformers import (
    canonicalize_bronze_record,
    clean_company_name,
    generate_observation_id,
    generate_unified_job_id,
    make_canonical_url,
    parse_salary,
    standardize_skills,
    strip_html,
)
from src.schemas.entity_links_schema import SILVER_ENTITY_LINKS_SCHEMA
from src.schemas.entity_schema import SILVER_ENTITY_SCHEMA
from src.schemas.silver_schema import QUARANTINE_SCHEMA, SILVER_OBSERVATIONS_SCHEMA
from src.storage.metadata_envelope import MetadataEnvelope


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

    def test_transformer_applies_stable_observation_contract(self):
        envelope = {
            "_metadata": {"source": "itviec", "ingested_at": "2026-11-16T02:00:00Z"},
            "raw_payload": {
                "platform": "itviec", "job_id": "job-1", "job_url": "https://example.test/jobs/1?utm_source=x",
                "content_hash": "hash-1", "scraped_at": "2026-11-16T01:00:00Z", "job_title": "Urgent Backend Engineer",
                "company_name": "Công ty TNHH Example JSC", "salary": "120 triệu/năm", "job_location": "Hanoi",
                "job_description": "<p>Python and JavaScript</p>", "requirements": "Python", "benefits": "Remote",
                "working_time": ["FULL_TIME"],
            },
        }
        raw_payload = dict(envelope.get("raw_payload") or {})
        raw_payload["observed_at"] = "2026-11-16T02:00:00Z"
        envelope["raw_payload"] = raw_payload
        envelope["_metadata"]["payload_checksum"] = MetadataEnvelope._compute_sha256(raw_payload)
        envelope["_metadata"]["observed_at"] = "2026-11-16T02:00:00Z"
        record = canonicalize_bronze_record(envelope)

        self.assertEqual(len(record), 46)
        self.assertEqual(record["observation_id"], generate_observation_id("itviec", "job-1", "2026-11-16T02:00:00Z", "hash-1"))
        self.assertEqual(record["company_name"], "example")
        self.assertEqual(record["city_normalized"], "Hà Nội")
        self.assertEqual(record["salary_min_vnd"], 10_000_000)
        self.assertEqual(record["working_time"], "FULL_TIME")

    def test_skills_use_boundaries_and_do_not_confuse_java_script(self):
        skills = standardize_skills("JavaScript, Java, Python")
        self.assertEqual(set(skills), {"javascript", "java", "python"})
        self.assertEqual(len(skills), 3)


if __name__ == "__main__":
    unittest.main()

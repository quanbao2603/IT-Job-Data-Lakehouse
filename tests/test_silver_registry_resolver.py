import tempfile
import unittest

from src.etl.entity_resolver import EntityResolver
from src.etl.transformers import generate_observation_id
from src.storage.entity_registry import EntityRegistry


def observation(platform="itviec", source_job_id="1", title="Backend Engineer", company="Acme", city="Hanoi"):
    content_hash = "hash-1"
    observed_at = "2026-11-16T02:00:00Z"
    return {
        "platform": platform,
        "source_job_id": source_job_id,
        "observation_id": generate_observation_id(platform, source_job_id, observed_at, content_hash),
        "job_title": title,
        "job_title_normalized": title.casefold(),
        "company_name": company,
        "company_name_normalized": company.casefold(),
        "city": city,
        "city_normalized": city.casefold(),
        "salary_min_vnd": 20_000_000,
        "salary_max_vnd": 30_000_000,
    }


class TestSilverRegistryResolver(unittest.TestCase):
    def setUp(self):
        self.db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.db.close()
        self.registry = EntityRegistry(self.db.name)
        self.resolver = EntityResolver(self.registry)

    def tearDown(self):
        import os
        self.registry = None
        self.resolver = None
        try:
            os.remove(self.db.name)
        except PermissionError:
            pass

    def test_new_entity_and_source_mapping_are_stable(self):
        first = observation()
        result = self.resolver.resolve(first, "2026-11-16T02:00:00Z")
        self.assertEqual(result.match_status, "DISTINCT_ENTITY")
        again = self.resolver.resolve(first, "2026-11-17T02:00:00Z")
        self.assertEqual(again.match_status, "AUTO_MATCH")
        self.assertEqual(again.unified_job_id, result.unified_job_id)

    def test_exact_identity_matches_across_platforms(self):
        first = observation(platform="itviec", source_job_id="1")
        result = self.resolver.resolve(first, "2026-11-16T02:00:00Z")
        second = observation(platform="topdev", source_job_id="2")
        matched = self.resolver.resolve(second, "2026-11-16T02:00:00Z")
        self.assertEqual(matched.match_method, "EXACT_NORMALIZED_IDENTITY")
        self.assertEqual(matched.unified_job_id, result.unified_job_id)

    def test_review_required_keeps_canonical_id_null(self):
        self.resolver.resolve(observation(), "2026-11-16T02:00:00Z")
        candidate = observation(platform="topdev", source_job_id="2", title="Backend Developer", company="Acme", city="Hanoi")
        result = self.resolver.resolve(candidate, "2026-11-16T02:00:00Z")
        self.assertEqual(result.match_status, "REVIEW_REQUIRED")
        self.assertIsNone(result.unified_job_id)
        self.assertIsNotNone(result.candidate_entity_id)


if __name__ == "__main__":
    unittest.main()

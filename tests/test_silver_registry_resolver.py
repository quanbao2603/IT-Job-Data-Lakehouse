import tempfile
import unittest

from src.etl.entity_resolver import EntityResolver
from src.etl.transformers import generate_observation_id
from src.storage.entity_registry import EntityRegistry


def observation(platform="itviec", source_job_id="1", title="Backend Engineer", company="Acme", city="Hanoi", content_hash="hash-1", job_level=None):
    observed_at = "2026-11-16T02:00:00Z"
    return {
        "platform": platform,
        "source_job_id": source_job_id,
        "observation_id": generate_observation_id(platform, source_job_id, observed_at, content_hash),
        "content_hash": content_hash,
        "source_url": None,
        "canonical_url": None,
        "job_title": title,
        "job_title_normalized": title.casefold(),
        "job_level": job_level,
        "company_name": company,
        "company_name_normalized": company.casefold(),
        "city": city,
        "city_normalized": city.casefold(),
        "work_mode": None,
        "job_description_clean": "Build reliable services with a strong engineering team.",
        "description_normalized": "build reliable services with a strong engineering team",
        "salary_min_vnd": 20_000_000,
        "salary_max_vnd": 30_000_000,
    }


class TestSilverRegistryResolver(unittest.TestCase):
    def setUp(self):
        self.db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.db.close()
        self.registry = EntityRegistry(self.db.name)
        self.pending_review = self.db.name + ".pending.csv"
        self.resolver = EntityResolver(self.registry, pending_review_path=self.pending_review)

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
        candidate = observation(platform="topdev", source_job_id="2", title="Platform Engineer", company="Acme", city="Hanoi")
        result = self.resolver.resolve(candidate, "2026-11-16T02:00:00Z")
        self.assertEqual(result.match_status, "REVIEW_REQUIRED")
        self.assertIsNone(result.unified_job_id)
        self.assertIsNotNone(result.candidate_entity_id)
        with open(self.pending_review, encoding="utf-8") as pending:
            self.assertIn(candidate["observation_id"], pending.read())

    def test_canonical_url_matches_before_fuzzy_rules(self):
        first = observation(source_job_id="1")
        first["source_url"] = "https://jobs.example.test/posting/1?utm_source=x"
        first["canonical_url"] = "https://jobs.example.test/posting/1"
        created = self.resolver.resolve(first, "2026-11-16T02:00:00Z")
        second = observation(platform="topdev", source_job_id="2", title="Completely Different Title", company="Other Company")
        second["source_url"] = "https://jobs.example.test/posting/1?ref=copy"
        second["canonical_url"] = "https://jobs.example.test/posting/1"
        matched = self.resolver.resolve(second, "2026-11-16T02:00:00Z")
        self.assertEqual(matched.match_method, "CANONICAL_URL")
        self.assertEqual(matched.unified_job_id, created.unified_job_id)

    def test_conflicting_level_does_not_auto_merge(self):
        senior = observation(job_level="Senior")
        created = self.resolver.resolve(senior, "2026-11-16T02:00:00Z")
        intern = observation(platform="topdev", source_job_id="2", job_level="Intern")
        result = self.resolver.resolve(intern, "2026-11-16T02:00:00Z")
        self.assertNotEqual(result.unified_job_id, created.unified_job_id)
        self.assertEqual(result.match_status, "DISTINCT_ENTITY")

    def test_changed_content_keeps_id_and_marks_updated(self):
        first = observation()
        created = self.resolver.resolve(first, "2026-11-16T02:00:00Z")
        changed = observation(content_hash="hash-2")
        result = self.resolver.resolve(changed, "2026-11-17T02:00:00Z")
        self.assertEqual(result.unified_job_id, created.unified_job_id)
        row = self.registry.list_entities()[0]
        self.assertEqual(row["entity_status"], "UPDATED")

    def test_review_csv_is_idempotent_on_replay(self):
        self.resolver.resolve(observation(), "2026-11-16T02:00:00Z")
        candidate = observation(platform="topdev", source_job_id="2", title="Platform Engineer", company="Acme", city="Hanoi")
        self.resolver.resolve(candidate, "2026-11-16T02:00:00Z")
        self.resolver.resolve(candidate, "2026-11-16T02:00:00Z")
        with open(self.pending_review, encoding="utf-8") as pending:
            lines = pending.read().splitlines()
        self.assertEqual(sum(candidate["observation_id"] in line for line in lines), 1)

    def test_work_mode_conflict_does_not_auto_merge(self):
        first = observation()
        first["work_mode"] = "Full-time"
        created = self.resolver.resolve(first, "2026-11-16T02:00:00Z")
        second = observation(platform="topdev", source_job_id="2")
        second["work_mode"] = "Part-time"
        result = self.resolver.resolve(second, "2026-11-16T02:00:00Z")
        self.assertEqual(result.match_status, "DISTINCT_ENTITY")
        self.assertNotEqual(result.unified_job_id, created.unified_job_id)

    def test_disjoint_salary_does_not_auto_merge(self):
        first = observation()
        created = self.resolver.resolve(first, "2026-11-16T02:00:00Z")
        second = observation(platform="topdev", source_job_id="2")
        second["salary_min_vnd"], second["salary_max_vnd"] = 80_000_000, 90_000_000
        result = self.resolver.resolve(second, "2026-11-16T02:00:00Z")
        self.assertNotEqual(result.unified_job_id, created.unified_job_id)
        self.assertEqual(result.match_status, "DISTINCT_ENTITY")

    def test_generic_title_alone_does_not_auto_merge(self):
        first = observation(title="Developer", company="Acme")
        created = self.resolver.resolve(first, "2026-11-16T02:00:00Z")
        second = observation(platform="topdev", source_job_id="2", title="Developer", company="Other Corp")
        result = self.resolver.resolve(second, "2026-11-16T02:00:00Z")
        self.assertEqual(result.match_status, "DISTINCT_ENTITY")
        self.assertNotEqual(result.unified_job_id, created.unified_job_id)


if __name__ == "__main__":
    unittest.main()

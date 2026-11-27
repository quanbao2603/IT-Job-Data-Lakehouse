"""Replaying the same posting must not mint a new job every time.

_create_distinct created the entity but never linked source_job_id to it, so the next replay
re-entered the same path, derived a new unified_job_id from the new observation_id, and grew the
cohort — 4.853 -> 4.997 -> 5.017 across three replays with no new source data behind the growth.
"""
import tempfile
import unittest

from src.etl.entity_resolver import EntityResolver
from src.storage.entity_registry import EntityRegistry

BASE = {
    "platform": "topcv",
    "source_job_id": "123456",
    "job_title": "kỹ sư phần mềm",
    "job_title_normalized": "ky su phan mem",
    "company_name": "công ty riêng biệt",
    "company_name_normalized": "cong ty rieng biet",
    "city_normalized": "ha noi",
    "canonical_url": "",
    "content_hash": "a" * 64,
    "observed_at": "2026-11-27T00:00:00Z",
}


class TestDistinctEntityIsStable(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.registry = EntityRegistry(f"{self.dir.name}/state.db")
        self.resolver = EntityResolver(self.registry, resolution_version="v1")

    def tearDown(self):
        self.dir.cleanup()

    def test_second_observation_of_the_same_posting_reuses_the_entity(self):
        first = self.resolver.resolve(dict(BASE, observation_id="obs-1"), BASE["observed_at"])
        second = self.resolver.resolve(dict(BASE, observation_id="obs-2"), BASE["observed_at"])
        self.assertEqual(first.unified_job_id, second.unified_job_id)
        self.assertEqual(len(list(self.registry.list_entities())), 1)

    def test_three_replays_do_not_inflate_the_cohort(self):
        for index in range(3):
            self.resolver.resolve(dict(BASE, observation_id=f"obs-{index}"), BASE["observed_at"])
        self.assertEqual(len(list(self.registry.list_entities())), 1)

    def test_a_different_source_job_is_still_its_own_entity(self):
        self.resolver.resolve(dict(BASE, observation_id="obs-1"), BASE["observed_at"])
        other = dict(BASE, source_job_id="999999", observation_id="obs-2",
                     job_title="kế toán", job_title_normalized="ke toan")
        result = self.resolver.resolve(other, BASE["observed_at"])
        self.assertNotEqual(result.unified_job_id, None)
        self.assertEqual(len(list(self.registry.list_entities())), 2)


if __name__ == "__main__":
    unittest.main()

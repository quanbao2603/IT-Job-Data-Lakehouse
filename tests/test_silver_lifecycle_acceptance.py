import tempfile
import unittest
from datetime import date

from src.etl.entity_lifecycle import (
    close_stale_entities,
    expire_entities,
    lifecycle_transition,
    mark_stale_entities,
)
from src.etl.entity_resolver import EntityResolver
from src.etl.transformers import generate_observation_id
from src.storage.entity_registry import EntityRegistry


def observation(platform="itviec", source_job_id="1", title="Backend Engineer", company="Acme", city="Hanoi", content_hash="hash-1", job_level=None, salary=(20_000_000, 30_000_000)):
    observed_at = "2026-11-01T02:00:00Z"
    return {
        "platform": platform, "source_job_id": source_job_id,
        "observation_id": generate_observation_id(platform, source_job_id, observed_at, content_hash),
        "content_hash": content_hash, "source_url": None, "canonical_url": None,
        "job_title": title, "job_title_normalized": title.casefold(), "job_level": job_level,
        "company_name": company, "company_name_normalized": company.casefold(),
        "city": city, "city_normalized": city.casefold(), "work_mode": None,
        "job_description_clean": "Build reliable services with Python and APIs.",
        "description_normalized": "build reliable services with python and apis",
        "salary_min_vnd": salary[0], "salary_max_vnd": salary[1],
    }


class TestSilverLifecycleAcceptance(unittest.TestCase):
    def setUp(self):
        self.db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.db.close()
        self.registry = EntityRegistry(self.db.name)
        self.resolver = EntityResolver(self.registry, pending_review_path=self.db.name + ".pending.csv")

    def tearDown(self):
        import os
        for path in (self.db.name, self.db.name + ".pending.csv"):
            try:
                os.remove(path)
            except FileNotFoundError:
                pass

    def test_t01_t09_source_identity_and_content_update(self):
        first = observation()
        created = self.resolver.resolve(first, first["observed_at"] if "observed_at" in first else "2026-11-01T02:00:00Z")
        changed = observation(content_hash="hash-2")
        updated = self.resolver.resolve(changed, "2026-11-02T02:00:00Z")
        self.assertEqual(updated.unified_job_id, created.unified_job_id)
        self.assertEqual(self.registry.get_entity(created.unified_job_id)["entity_status"], "UPDATED")

    def test_t10_t11_partial_run_protects_closed_state_and_reopen_transition(self):
        self.assertEqual(lifecycle_transition("ACTIVE", missing_beyond_grace=True), "CLOSED")
        self.assertEqual(lifecycle_transition("CLOSED", reappeared=True), "REOPENED")
        self.assertEqual(lifecycle_transition("ACTIVE", missing_beyond_grace=True, reappeared=True), "CLOSED")
        self.assertEqual(lifecycle_transition("ACTIVE", missing_beyond_grace=True), "CLOSED")

    def test_stale_close_and_expire_are_explicit_and_partial_safe(self):
        created = self.resolver.resolve(observation(), "2026-11-01T02:00:00Z")
        self.assertEqual(mark_stale_entities(
            self.registry,
            platform="itviec",
            observed_source_job_ids=set(),
            as_of=date(2026, 11, 5),
            partial_run=False,
        ), 1)
        self.assertEqual(self.registry.get_entity(created.unified_job_id)["entity_status"], "STALE")
        self.assertEqual(close_stale_entities(
            self.registry,
            platform="itviec",
            observed_source_job_ids=set(),
            as_of=date(2026, 11, 20),
            partial_run=False,
        ), 1)
        self.assertEqual(expire_entities(self.registry, expired_entity_ids={created.unified_job_id}), 1)
        self.assertEqual(self.registry.get_entity(created.unified_job_id)["entity_status"], "EXPIRED")
        self.assertEqual(mark_stale_entities(
            self.registry,
            platform="itviec",
            observed_source_job_ids=set(),
            as_of=date(2026, 11, 5),
            partial_run=True,
        ), 0)

    def test_unknown_platform_lifecycle_fails_closed(self):
        with self.assertRaises(ValueError):
            close_stale_entities(
                self.registry,
                platform="unknown",
                observed_source_job_ids=set(),
                as_of=date(2026, 11, 16),
            )
        with self.assertRaises(ValueError):
            mark_stale_entities(
                self.registry,
                platform="unknown",
                observed_source_job_ids=set(),
                as_of=date(2026, 11, 16),
            )

    def test_t15_version_change_records_previous_entity_id_and_keeps_history(self):
        first = observation()
        v1_result = self.resolver.resolve(first, "2026-11-01T02:00:00Z")
        v2 = EntityResolver(
            self.registry,
            resolution_version="v2",
            pending_review_path=self.db.name + ".pending.csv",
        )
        second = observation(content_hash="hash-v2")
        v2_result = v2.resolve(second, "2026-11-02T02:00:00Z")
        self.assertEqual(v2_result.unified_job_id, v1_result.unified_job_id)
        links = self.registry.list_links(second["observation_id"])
        self.assertEqual(links[-1]["resolution_version"], "v2")
        self.assertEqual(links[-1]["previous_unified_job_id"], v1_result.unified_job_id)
        self.assertEqual(len(self.registry.list_observations(v1_result.unified_job_id)), 2)
        self.assertEqual(self.registry.list_observations(v1_result.unified_job_id)[0]["resolution_version"], "v1")
        self.assertEqual(self.registry.list_observations(v1_result.unified_job_id)[1]["resolution_version"], "v2")

    def test_t15_version_change_can_record_split_id(self):
        first = observation()
        v1_result = self.resolver.resolve(first, "2026-11-01T02:00:00Z")
        v2 = EntityResolver(self.registry, resolution_version="v2", pending_review_path=self.db.name + ".pending.csv")
        split = observation(platform="topdev", source_job_id="split-2", company="Other Corp")
        v2_result = v2.resolve(split, "2026-11-02T02:00:00Z")
        self.assertNotEqual(v2_result.unified_job_id, v1_result.unified_job_id)
        self.assertIsNone(self.registry.get_link(split["observation_id"], "v2")["previous_unified_job_id"])

    def test_t16_history_and_t17_multiple_platforms(self):
        first = observation()
        created = self.resolver.resolve(first, "2026-11-01T02:00:00Z")
        second = observation(platform="topdev", source_job_id="2")
        matched = self.resolver.resolve(second, "2026-11-02T02:00:00Z")
        self.assertEqual(matched.unified_job_id, created.unified_job_id)
        self.assertEqual(len(self.registry.list_observations(created.unified_job_id)), 2)
        self.assertEqual({row["platform"] for row in self.registry.list_observations(created.unified_job_id)}, {"itviec", "topdev"})

    def test_t18_guarded_distinct_entity(self):
        created = self.resolver.resolve(observation(company="Acme"), "2026-11-01T02:00:00Z")
        distinct = self.resolver.resolve(observation(platform="topdev", source_job_id="2", company="Other Corp"), "2026-11-01T02:00:00Z")
        self.assertEqual(distinct.match_status, "DISTINCT_ENTITY")
        self.assertNotEqual(created.unified_job_id, distinct.unified_job_id)


if __name__ == "__main__":
    unittest.main()

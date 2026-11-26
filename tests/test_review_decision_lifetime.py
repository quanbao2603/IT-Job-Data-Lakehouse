"""A review decision must outlive the replay that follows it.

The resolver used to re-derive REVIEW_REQUIRED from the same fuzzy score every run, so an answered
queue refilled itself nightly — which is why 688 links sat unanswered. ACCEPT must bind to the
chosen entity, REJECT must keep the observation separate, and neither may come back as a question.
"""
import unittest

from src.etl.entity_resolver import EntityResolver
from src.storage.entity_registry import EntityRegistry

OBSERVATION = {
    "observation_id": "obs-decide-1",
    "platform": "topcv",
    "source_job_id": "900001",
    "job_title": "business analyst senior",
    "company_name": "fpt software",
    # the scorer reads the normalised fields, not the raw ones
    "job_title_normalized": "business analyst senior",
    "company_name_normalized": "fpt software",
    "city_normalized": "hà nội",
    "canonical_url": "https://example.test/job/900001",
    "content_hash": "a" * 64,
    "observed_at": "2026-11-26T00:00:00Z",
}


class TestReviewDecisionLifetime(unittest.TestCase):
    def setUp(self):
        self.dir = __import__("tempfile").TemporaryDirectory()
        self.db = f"{self.dir.name}/state.db"
        self.registry = EntityRegistry(self.db)
        self.resolver = EntityResolver(self.registry, resolution_version="v1")
        # a look-alike entity so the fuzzy scorer lands in the review band
        self.registry.create_entity(
            unified_job_id="uj_candidate",
            platform="careerviet",
            source_job_id="700001",
            # deliberately not the observation's own fingerprint: this is a near-duplicate,
            # so it must reach the fuzzy band instead of the exact-identity path
            fingerprint="near|duplicate|ha noi",
            title="business analyst",
            company="fpt software",
            city="hà nội",
            observed_at="2026-11-20T00:00:00Z",
            content_hash="b" * 64,
        )

    def tearDown(self):
        self.dir.cleanup()

    def test_accept_decision_binds_and_stops_asking(self):
        first = self.resolver.resolve(dict(OBSERVATION), OBSERVATION["observed_at"])
        self.assertEqual(first.match_status, "REVIEW_REQUIRED")
        self.registry.resolve_review(
            observation_id=OBSERVATION["observation_id"],
            resolution_version="v1",
            decision="ACCEPT",
            reviewer="test",
            candidate_entity_id=first.candidate_entity_id,
        )
        again = self.resolver.resolve(dict(OBSERVATION), OBSERVATION["observed_at"])
        # Either the decision path (MANUAL_MATCH) or an exact-identity re-match (AUTO_MATCH) is
        # fine: what matters is that the job lands on the accepted entity and is not asked again.
        self.assertNotEqual(again.match_status, "REVIEW_REQUIRED")
        self.assertEqual(again.unified_job_id, first.candidate_entity_id)

    def test_reject_decision_keeps_the_job_separate_without_asking_again(self):
        first = self.resolver.resolve(dict(OBSERVATION), OBSERVATION["observed_at"])
        self.assertEqual(first.match_status, "REVIEW_REQUIRED")
        self.registry.resolve_review(
            observation_id=OBSERVATION["observation_id"],
            resolution_version="v1",
            decision="REJECT",
            reviewer="test",
        )
        again = self.resolver.resolve(dict(OBSERVATION), OBSERVATION["observed_at"])
        self.assertNotEqual(again.match_status, "REVIEW_REQUIRED")
        self.assertNotEqual(again.unified_job_id, first.candidate_entity_id)


if __name__ == "__main__":
    unittest.main()

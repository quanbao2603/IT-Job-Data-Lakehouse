"""Gold acceptance tests (TG-01..TG-18) over the pure bundle builder.

These run without PostgreSQL: every business rule lives in pure functions, and the SQL
layer is a thin executor. That keeps the gate testable on Windows CI and on the VPS.
"""
import tempfile
import unittest
from pathlib import Path

from src.gold.contract import NON_OPEN_ENTITY_STATUSES, OPEN_ENTITY_STATUSES
from src.gold.data_quality import blocking_failures, run_checks, summarize
from src.gold.load_aggregates import percentile_cont
from src.gold.pipeline import build_bundle
from src.gold.silver_release_reader import (
    SilverRelease,
    build_effective_entity,
    build_effective_link,
    latest_release,
)

CUTOFF = "2026-11-16T10:00:00Z"
SILVER_RELEASE = "silver_release_test"


def _entity(unified_job_id, title="Backend Engineer", company="acme", status="ACTIVE",
            salary=(20_000_000, 30_000_000), **extra):
    row = {
        "unified_job_id": unified_job_id,
        "canonical_title": title,
        "canonical_company": company,
        "canonical_location": "Hà Nội",
        "canonical_salary_min_vnd": salary[0],
        "canonical_salary_max_vnd": salary[1],
        "canonical_currency": "VND",
        "canonical_description": "Build services",
        "canonical_posted_date": "2026-11-10",
        "source_count": 1,
        "observation_count": 1,
        "first_seen_at": "2026-11-10T02:00:00Z",
        "last_seen_at": "2026-11-16T02:00:00Z",
        "entity_status": status,
        "canonical_updated_at": "2026-11-16T02:00:00Z",
        "resolution_version": "v1",
    }
    row.update(extra)
    return row


def _observation(observation_id, platform="itviec", source_job_id="1", unified_job_id=None,
                 observed_at="2026-11-16T02:00:00Z", skills=("python", "docker"),
                 domain="Fintech", city="Hà Nội", level="Senior", work_mode="Hybrid",
                 salary=(20_000_000, 30_000_000), title="backend engineer",
                 company="acme", accepted=True, **extra):
    row = {
        "observation_id": observation_id,
        "platform": platform,
        "source_job_id": source_job_id,
        "observed_at": observed_at,
        "job_title_normalized": title,
        "company_name": company.title(),
        "company_name_normalized": company,
        "company_name_raw": company.title(),
        "company_industry": "Software",
        "city_normalized": city,
        "job_domain": domain,
        "job_level": level,
        "work_mode": work_mode,
        "salary_min_vnd": salary[0],
        "salary_max_vnd": salary[1],
        "skills_standardized": list(skills),
        "quality_accepted": True,
    }
    row.update(extra)
    if unified_job_id:
        row["unified_job_id"] = unified_job_id
    row["accepted_for_gold"] = accepted
    return row


def _link(observation_id, unified_job_id, platform="itviec", source_job_id="1",
          status="AUTO_MATCH", method="SOURCE_JOB_ID", matched_at="2026-11-16T03:00:00Z",
          primary=True, score=1.0):
    return {
        "observation_id": observation_id, "unified_job_id": unified_job_id,
        "candidate_entity_id": None, "platform": platform, "source_job_id": source_job_id,
        "match_method": method, "match_score": score, "match_status": status,
        "matched_at": matched_at, "resolution_version": "v1", "is_primary_source": primary,
    }


def _bundle(entities, links, observations, **kwargs):
    params = {
        "entities": entities, "links": links, "observations": observations,
        "cutoff_at": CUTOFF, "silver_release_id": SILVER_RELEASE,
        "manifest_fingerprint": "f" * 64,
    }
    params.update(kwargs)
    return build_bundle(**params)


def _simple_fixture():
    entities = [_entity("uj_a"), _entity("uj_b", title="Frontend Engineer", company="beta")]
    links = [_link("obs_a1", "uj_a"), _link("obs_b1", "uj_b", platform="topcv", source_job_id="9")]
    observations = [
        _observation("obs_a1", unified_job_id="uj_a"),
        _observation("obs_b1", platform="topcv", source_job_id="9", unified_job_id="uj_b",
                     company="beta", skills=("react",), domain="E-commerce", city="Hồ Chí Minh"),
    ]
    return entities, links, observations


class TestSnapshotFact(unittest.TestCase):
    def test_tg01_row_count_equals_effective_entities_and_pk_unique(self):
        entities, links, observations = _simple_fixture()
        bundle = _bundle(entities, links, observations)
        self.assertEqual(len(bundle.snapshot), 2)
        keys = [(row["snapshot_date"], row["unified_job_id"]) for row in bundle.snapshot]
        self.assertEqual(len(keys), len(set(keys)))

    def test_tg02_rebuild_is_idempotent(self):
        entities, links, observations = _simple_fixture()
        first = _bundle(entities, links, observations, gold_release_id="gold_fixed")
        second = _bundle(entities, links, observations, gold_release_id="gold_fixed")
        self.assertEqual(first.row_counts(), second.row_counts())
        self.assertEqual(
            sorted(row["unified_job_id"] for row in first.snapshot),
            sorted(row["unified_job_id"] for row in second.snapshot),
        )
        self.assertEqual(first.manifest_fingerprint, second.manifest_fingerprint)

    def test_newly_seen_is_null_on_baseline_and_counted_after(self):
        entities, links, observations = _simple_fixture()
        baseline = _bundle(entities, links, observations)
        self.assertTrue(all(row["newly_seen_job_count"] is None for row in baseline.snapshot))
        following = _bundle(entities, links, observations, previous_snapshot_ids=["uj_a"])
        by_id = {row["unified_job_id"]: row["newly_seen_job_count"] for row in following.snapshot}
        self.assertEqual(by_id["uj_a"], 0)
        self.assertEqual(by_id["uj_b"], 1)

    def test_dg05_open_job_count_follows_entity_status(self):
        rows = []
        for index, status in enumerate(OPEN_ENTITY_STATUSES + NON_OPEN_ENTITY_STATUSES):
            rows.append(_entity(f"uj_{index}", status=status))
        links = [_link(f"obs_{index}", f"uj_{index}", source_job_id=str(index))
                 for index in range(len(rows))]
        observations = [_observation(f"obs_{index}", source_job_id=str(index),
                                     unified_job_id=f"uj_{index}") for index in range(len(rows))]
        bundle = _bundle(rows, links, observations)
        by_id = {row["unified_job_id"]: row["open_job_count"] for row in bundle.snapshot}
        for index, status in enumerate(OPEN_ENTITY_STATUSES):
            self.assertEqual(by_id[f"uj_{index}"], 1, f"{status} must count as open")
        for offset, status in enumerate(NON_OPEN_ENTITY_STATUSES):
            index = len(OPEN_ENTITY_STATUSES) + offset
            self.assertEqual(by_id[f"uj_{index}"], 0, f"{status} must not count as open")

    def test_tg12_and_tg13_salary_sample_rules(self):
        entities = [_entity("uj_bad", salary=(30_000_000, 20_000_000)),
                    _entity("uj_none", salary=(None, None))]
        links = [_link("obs_bad", "uj_bad"), _link("obs_none", "uj_none", source_job_id="2")]
        observations = [
            _observation("obs_bad", unified_job_id="uj_bad", salary=(30_000_000, 20_000_000)),
            _observation("obs_none", source_job_id="2", unified_job_id="uj_none", salary=(None, None)),
        ]
        bundle = _bundle(entities, links, observations)
        by_id = {row["unified_job_id"]: row for row in bundle.snapshot}
        self.assertFalse(by_id["uj_bad"]["salary_sample_flag"])
        self.assertIsNone(by_id["uj_bad"]["salary_midpoint_vnd"])
        self.assertFalse(by_id["uj_none"]["salary_sample_flag"])
        for row in bundle.market:
            self.assertEqual(row["salary_sample_count"], 0)
            self.assertIsNone(row["salary_p50_vnd"])


class TestPlatformAndSkillFacts(unittest.TestCase):
    def test_tg11_one_job_three_platforms_yields_three_rows_and_one_snapshot_row(self):
        entities = [_entity("uj_a")]
        links = [
            _link("obs_1", "uj_a", platform="itviec", source_job_id="1"),
            _link("obs_2", "uj_a", platform="topcv", source_job_id="2"),
            _link("obs_3", "uj_a", platform="topdev", source_job_id="3"),
        ]
        observations = [
            _observation("obs_1", platform="itviec", source_job_id="1", unified_job_id="uj_a"),
            _observation("obs_2", platform="topcv", source_job_id="2", unified_job_id="uj_a"),
            _observation("obs_3", platform="topdev", source_job_id="3", unified_job_id="uj_a"),
        ]
        bundle = _bundle(entities, links, observations)
        self.assertEqual(len(bundle.snapshot), 1)
        self.assertEqual(len(bundle.platform), 3)
        self.assertEqual(len({row["platform_key"] for row in bundle.platform}), 3)

    def test_tg10_recrawl_does_not_inflate_skill_job_count(self):
        entities = [_entity("uj_a")]
        links = [_link("obs_1", "uj_a", source_job_id="1"),
                 _link("obs_1", "uj_a", source_job_id="1", matched_at="2026-11-16T04:00:00Z")]
        observations = [
            _observation("obs_1", source_job_id="1", unified_job_id="uj_a"),
            _observation("obs_1", source_job_id="1", unified_job_id="uj_a",
                         observed_at="2026-11-16T05:00:00Z"),
        ]
        bundle = _bundle(entities, links, observations)
        per_skill = {}
        for row in bundle.skill:
            per_skill.setdefault(row["skill_key"], []).append(row["unified_job_id"])
        for skill, jobs in per_skill.items():
            self.assertEqual(len(jobs), len(set(jobs)), f"{skill} inflated by recrawl")
        self.assertEqual(len(bundle.effective_link), 1, "one effective decision per observation")

    def test_tg17_four_skills_produce_six_ordered_pairs(self):
        entities = [_entity("uj_a")]
        links = [_link("obs_1", "uj_a")]
        observations = [_observation("obs_1", unified_job_id="uj_a",
                                     skills=("python", "docker", "kubernetes", "aws"))]
        bundle = _bundle(entities, links, observations)
        self.assertEqual(len(bundle.skill), 4)
        self.assertEqual(len(bundle.skill_pair), 6)
        for row in bundle.skill_pair:
            self.assertLess(row["skill_a_key"], row["skill_b_key"])
            self.assertEqual(row["co_mentioned_job_count"], 1)

    def test_tg05_review_required_link_never_reaches_a_fact(self):
        entities = [_entity("uj_a")]
        links = [_link("obs_review", None, status="REVIEW_REQUIRED", method="FUZZY_TITLE_COMPANY_LOCATION",
                       primary=False, score=0.81),
                 _link("obs_a1", "uj_a")]
        observations = [
            _observation("obs_review", source_job_id="77", accepted=False),
            _observation("obs_a1", unified_job_id="uj_a"),
        ]
        bundle = _bundle(entities, links, observations)
        self.assertEqual(len(bundle.snapshot), 1)
        review_rows = [row for row in bundle.source_observation if row["observation_id"] == "obs_review"]
        self.assertIsNone(review_rows[0]["unified_job_id"])
        self.assertFalse(review_rows[0]["accepted_for_gold"])
        self.assertNotIn("obs_review", {row["observation_id"] for row in bundle.change_event})


class TestChangeEvents(unittest.TestCase):
    def test_tg14_first_observation_is_not_a_change(self):
        entities = [_entity("uj_a")]
        links = [_link("obs_1", "uj_a")]
        observations = [_observation("obs_1", unified_job_id="uj_a")]
        bundle = _bundle(entities, links, observations)
        self.assertEqual(bundle.change_event, [])

    def test_tg15_salary_change_produces_one_event_with_delta(self):
        entities = [_entity("uj_a")]
        links = [_link("obs_1", "uj_a"), _link("obs_2", "uj_a", matched_at="2026-11-17T03:00:00Z")]
        observations = [
            _observation("obs_1", unified_job_id="uj_a", observed_at="2026-11-16T02:00:00Z",
                         salary=(20_000_000, 30_000_000)),
            _observation("obs_2", unified_job_id="uj_a", observed_at="2026-11-17T02:00:00Z",
                         salary=(25_000_000, 35_000_000)),
        ]
        bundle = _bundle(entities, links, observations, cutoff_at="2026-11-17T10:00:00Z")
        self.assertEqual(len(bundle.change_event), 1)
        event = bundle.change_event[0]
        self.assertTrue(event["salary_changed"])
        self.assertEqual(event["salary_old_vnd"], 20_000_000)
        self.assertEqual(event["salary_new_vnd"], 25_000_000)
        self.assertEqual(event["salary_delta_vnd"], 5_000_000)
        self.assertEqual(event["previous_observation_id"], "obs_1")
        self.assertNotEqual(event["observation_id"], event["previous_observation_id"])

    def test_cosmetic_hash_change_without_business_field_change_creates_no_event(self):
        entities = [_entity("uj_a")]
        links = [_link("obs_1", "uj_a"), _link("obs_2", "uj_a", matched_at="2026-11-17T03:00:00Z")]
        observations = [
            _observation("obs_1", unified_job_id="uj_a", observed_at="2026-11-16T02:00:00Z",
                         content_hash="aaa"),
            _observation("obs_2", unified_job_id="uj_a", observed_at="2026-11-17T02:00:00Z",
                         content_hash="bbb"),
        ]
        bundle = _bundle(entities, links, observations, cutoff_at="2026-11-17T10:00:00Z")
        self.assertEqual(bundle.change_event, [])


class TestDimJobSCD2(unittest.TestCase):
    def test_tg08_title_change_opens_version_two_and_closes_version_one(self):
        entities, links, observations = _simple_fixture()
        first = _bundle(entities, links, observations, gold_release_id="gold_r1")
        current = {row["unified_job_id"]: row for row in first.dim_job if row["is_current"]}
        self.assertEqual(current["uj_a"]["version_seq"], 1)

        changed_entity = _entity("uj_a", title="Staff Backend Engineer")
        second = _bundle([changed_entity, entities[1]], links, observations,
                         gold_release_id="gold_r2", current_dim_job=current)
        versions = {row["unified_job_id"]: row for row in second.dim_job}
        self.assertEqual(versions["uj_a"]["version_seq"], 2)
        self.assertNotEqual(versions["uj_a"]["job_key"], current["uj_a"]["job_key"])
        self.assertEqual(versions["uj_b"]["version_seq"], 1, "unchanged job keeps its version")
        closed = {row["job_key"] for row in second.dim_job_closes}
        self.assertIn(current["uj_a"]["job_key"], closed)
        self.assertNotIn(current["uj_b"]["job_key"], closed)

    def test_tg09_unchanged_job_keeps_the_same_key_across_releases(self):
        entities, links, observations = _simple_fixture()
        first = _bundle(entities, links, observations, gold_release_id="gold_r1")
        current = {row["unified_job_id"]: row for row in first.dim_job}
        second = _bundle(entities, links, observations, gold_release_id="gold_r2",
                         current_dim_job=current)
        self.assertEqual(
            {row["unified_job_id"]: row["job_key"] for row in second.dim_job},
            {row["unified_job_id"]: row["job_key"] for row in first.dim_job},
        )
        self.assertEqual(second.dim_job_closes, [])


class TestDataQualityGate(unittest.TestCase):
    def test_tg18_clean_bundle_passes_and_publish_is_allowed(self):
        entities, links, observations = _simple_fixture()
        bundle = _bundle(entities, links, observations)
        results = run_checks(bundle)
        self.assertEqual(blocking_failures(results), [])
        self.assertTrue(summarize(results)["publish_allowed"])
        self.assertGreaterEqual(len(results), 26)

    def test_tg06_future_observation_blocks_publish(self):
        entities = [_entity("uj_a")]
        links = [_link("obs_future", "uj_a", matched_at="2026-11-20T03:00:00Z")]
        observations = [_observation("obs_future", unified_job_id="uj_a",
                                     observed_at="2026-11-20T02:00:00Z")]
        bundle = _bundle(entities, links, observations)
        results = run_checks(bundle)
        codes = {row["check_code"] for row in blocking_failures(results)}
        self.assertIn("DQ-06", codes)
        self.assertFalse(summarize(results)["publish_allowed"])

    def test_tg07_duplicate_entity_blocks_publish(self):
        entities = [_entity("uj_a"), _entity("uj_a")]
        links = [_link("obs_a1", "uj_a")]
        observations = [_observation("obs_a1", unified_job_id="uj_a")]
        rows, conflicts = build_effective_entity(entities)
        self.assertEqual(len(rows), 1)
        self.assertIn("DUP_ENTITY", {row["conflict_type"] for row in conflicts})
        bundle = _bundle(entities, links, observations)
        codes = {row["check_code"] for row in blocking_failures(run_checks(bundle))}
        self.assertIn("DQ-27", codes)

    def test_dq24_source_divergence_blocks_publish(self):
        entities = [_entity("uj_a", canonical_title="Backend Engineer")]
        registry = [dict(entities[0], canonical_title="Different Title",
                         canonical_job_level="Senior", canonical_work_mode="Hybrid",
                         canonical_description="x")]
        links = [_link("obs_a1", "uj_a")]
        observations = [_observation("obs_a1", unified_job_id="uj_a")]
        bundle = _bundle(entities, links, observations, registry_entities=registry)
        codes = {row["check_code"] for row in blocking_failures(run_checks(bundle))}
        self.assertIn("DQ-24", codes)

    def test_dq23_open_job_source_is_entity_status(self):
        entities, links, observations = _simple_fixture()
        bundle = _bundle(entities, links, observations)
        self.assertEqual(bundle.open_job_source, "entity_status")
        results = {row["check_code"]: row for row in run_checks(bundle)}
        self.assertTrue(results["DQ-23"]["passed"])
        self.assertTrue(results["DQ-23b"]["passed"])

    def test_dq09_and_dq11_aggregates_reconcile_to_snapshot(self):
        entities, links, observations = _simple_fixture()
        bundle = _bundle(entities, links, observations)
        self.assertEqual(sum(row["observed_job_count"] for row in bundle.market), len(bundle.snapshot))
        self.assertEqual(sum(row["observed_job_count"] for row in bundle.company), len(bundle.snapshot))


class TestReleaseConsumability(unittest.TestCase):
    def _release(self, tmp, release_id, **overrides):
        payload = {
            "silver_release_id": release_id, "status": "COMPLETE",
            "snapshot_cutoff_at": CUTOFF, "resolution_version": "v1",
            "manifest_fingerprint": "f" * 64, "taxonomy_versions": {},
            "metrics": {}, "objects": [], "partial_run": False,
            "coverage_warning": False, "failed_platforms": [],
        }
        payload.update(overrides)
        path = Path(tmp) / "releases" / f"{release_id}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(__import__("json").dumps(payload), encoding="utf-8")
        return payload

    def test_tg04_partial_silver_release_is_not_consumable(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._release(tmp, "silver_partial", partial_run=True, failed_platforms=["topcv"])
            self.assertIsNone(latest_release(tmp))

    def test_tg03_day_without_valid_release_yields_none_not_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._release(tmp, "silver_failed", status="FAILED")
            self.assertIsNone(latest_release(tmp))

    def test_complete_release_is_selected(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._release(tmp, "silver_ok")
            release = latest_release(tmp)
            self.assertIsInstance(release, SilverRelease)
            self.assertEqual(release.release_id, "silver_ok")
            self.assertTrue(release.is_consumable)


class TestPercentile(unittest.TestCase):
    def test_percentile_cont_matches_linear_interpolation(self):
        self.assertIsNone(percentile_cont([], 0.5))
        self.assertEqual(percentile_cont([10], 0.5), 10.0)
        self.assertEqual(percentile_cont([1, 2, 3, 4], 0.5), 2.5)
        self.assertEqual(percentile_cont([1, 2, 3, 4], 0.25), 1.75)
        self.assertEqual(percentile_cont([1, 2, 3, 4], 0.75), 3.25)


class TestEffectiveLink(unittest.TestCase):
    def test_latest_resolution_version_wins_per_observation(self):
        links = [
            _link("obs_1", "uj_old", matched_at="2026-11-15T03:00:00Z"),
            dict(_link("obs_1", "uj_new", matched_at="2026-11-16T03:00:00Z"), resolution_version="v2"),
        ]
        rows, conflicts = build_effective_link(links, cutoff=CUTOFF)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["unified_job_id"], "uj_new")
        self.assertEqual(rows[0]["resolution_version"], "v2")
        self.assertEqual(conflicts, [])

    def test_rejected_status_with_job_is_a_conflict(self):
        links = [_link("obs_1", "uj_a", status="REVIEW_REQUIRED", primary=False, score=0.8)]
        rows, conflicts = build_effective_link(links, cutoff=CUTOFF)
        self.assertIsNone(rows[0]["unified_job_id"])
        self.assertIn("REJECTED_LINK_HAS_JOB", {row["conflict_type"] for row in conflicts})


class TestEndToEndFromSealedSilverRelease(unittest.TestCase):
    """TG-01 end to end: real Silver output on disk feeds a real Gold bundle."""

    def _envelope(self, job_id, platform="itviec", title="Backend Engineer", company="Acme",
                  scraped_at="2026-11-16T01:00:00Z", content_hash="h1", **extra):
        from src.storage.metadata_envelope import MetadataEnvelope

        payload = {
            "platform": platform, "job_id": job_id,
            "job_url": f"https://jobs.example.test/{platform}/{job_id}",
            "scraped_at": scraped_at, "content_hash": content_hash,
            "job_title": title, "company_name": company,
            "salary": "20 - 30 triệu", "job_location": "Hanoi",
            "job_description": "<p>Python and Docker backend service</p>",
            "requirements": "Python, Docker", "benefits": "Remote friendly",
        }
        payload.update(extra)
        envelope = MetadataEnvelope.wrap(platform, payload, run_id="run-e2e")
        envelope["_metadata"]["ingested_at"] = scraped_at
        envelope["_metadata"]["observed_at"] = scraped_at
        return envelope

    def test_sealed_release_is_consumable_and_builds_a_bundle(self):
        from src.etl.bronze_to_silver import BronzeToSilverPipeline
        from src.etl.silver_writer import SilverWriter
        from src.storage.entity_registry import EntityRegistry

        class _Reader:
            """Only careerviet owns an object, so each envelope is processed exactly once."""

            def list_active_bronze_objects(self, platform):
                if platform == "careerviet":
                    return [{"object_key": "careerviet/obj-1.jsonl.gz"}]
                return []

        with tempfile.TemporaryDirectory() as tmp:
            root = str(Path(tmp) / "silver")
            registry = EntityRegistry(str(Path(tmp) / "state.db"))
            pipeline = BronzeToSilverPipeline(
                registry=registry,
                writer=SilverWriter(root),
                run_id="run-e2e",
                pending_review_path=str(Path(tmp) / "pending_review.csv"),
            )
            envelopes = [
                # careerviet's schema contract carries `industries`; GAP-8 maps it to job_domain.
                self._envelope("job-1", platform="careerviet", industries="CNTT - Phần mềm"),
                # topcv's contract carries `company_industry`, the last fallback of the chain.
                self._envelope("job-2", platform="topcv", title="Frontend Engineer",
                               company="Beta", scraped_at="2026-11-16T02:00:00Z",
                               content_hash="h2", company_industry="E-commerce"),
            ]
            report = pipeline.process_bronze_objects(
                _Reader(), lambda key: iter(envelopes), platforms={"careerviet", "topcv"}
            )
            self.assertEqual(report["status"], "SUCCESS")
            # The internal batching flag must not make the sealed release look partial.
            self.assertFalse(report["partial_run"])
            self.assertEqual(report["silver_release_status"], "COMPLETE")
            self.assertTrue(report["lifecycle_deferred"])

            release = latest_release(root)
            self.assertIsNotNone(release, "a COMPLETE release must be consumable by Gold")
            self.assertEqual(release.release_id, report["silver_release_id"])

            bundle = build_bundle(root=root, registry_entities=registry.list_entities())
            self.assertEqual(len(bundle.snapshot), 2)
            self.assertEqual(len(bundle.effective_entity), 2)
            self.assertEqual(blocking_failures(run_checks(bundle)), [])
            # GAP-8 fix must reach Gold; both fixtures now normalize into the
            # dashboard taxonomy instead of leaking raw industry strings.
            domains = {row["job_domain"] for row in bundle.dim_job}
            self.assertIn("Phát triển phần mềm", domains)
            self.assertNotIn(None, domains)
            # Every snapshot row carries lineage back to the sealed Silver release.
            for row in bundle.snapshot:
                self.assertEqual(row["silver_release_id"], report["silver_release_id"])
                self.assertEqual(row["gold_release_id"], bundle.gold_release_id)


class TestSqlScripts(unittest.TestCase):
    """Static validation of the DDL and views, so a broken script fails before deploy."""

    def _statements(self, filename):
        from src.gold.connection import split_sql_script

        path = Path(__file__).resolve().parent.parent / "src" / "gold" / filename
        return split_sql_script(path.read_text(encoding="utf-8"))

    def test_ddl_has_every_table_and_no_comment_fragments(self):
        import re

        statements = self._statements("ddl.sql")
        self.assertTrue(statements)
        self.assertFalse(any(s.startswith("--") for s in statements))
        self.assertTrue(all(len(s) > 10 for s in statements))
        self.assertEqual(len([s for s in statements if s.upper().startswith("CREATE SCHEMA")]), 3)
        creates = [s for s in statements if s.upper().startswith("CREATE TABLE")]
        # 5 dimensions + 7 facts + 4 control + 4 staging.
        self.assertEqual(len(creates), 20)
        # Match on the created object name only; REFERENCES clauses also mention dim tables.
        names = [re.match(r"CREATE TABLE IF NOT EXISTS ([\w.]+)", s).group(1) for s in creates]
        self.assertEqual(len([n for n in names if n.startswith("gold.dim_")]), 5)
        self.assertEqual(len([n for n in names if n.startswith("gold.fact_")]), 7)
        self.assertEqual(len([n for n in names if n.startswith("gold_control.")]), 4)
        self.assertEqual(len([n for n in names if n.startswith("gold_staging.")]), 4)
        self.assertEqual(len(set(names)), 20, "table names must be unique")

    def test_ddl_encodes_the_business_decisions(self):
        script = (Path(__file__).resolve().parent.parent / "src" / "gold" / "ddl.sql").read_text(encoding="utf-8")
        # D-G05: the open-job status set is enforced by the database, not only by code.
        self.assertIn("'NEW', 'ACTIVE', 'UPDATED', 'REOPENED', 'STALE'", script)
        self.assertIn("chk_snapshot_closed_not_open", script)
        # GAP-9: the city axis is a closed set of five explicit groups.
        self.assertIn("chk_market_city", script)
        # Fact 7: ordered pairs exclude mirrors and self joins at the database level.
        self.assertIn("chk_pair_ordered", script)
        # D-G01: every fact and aggregate is addressable by release for idempotent retry.
        fact_blocks = [
            block for block in script.split("CREATE TABLE IF NOT EXISTS")
            if "gold.fact_" in block.split("(")[0]
        ]
        self.assertEqual(len(fact_blocks), 7)
        for block in fact_blocks:
            self.assertIn("gold_release_id", block)
            self.assertIn("silver_release_id", block)
            self.assertIn("PRIMARY KEY (gold_release_id", block)

    def test_semantic_views_are_bound_to_active_release(self):
        statements = self._statements("semantic_views.sql")
        views = [s for s in statements if "CREATE OR REPLACE VIEW" in s.upper()]
        # 10 release-bound views plus v_job_source_url, which joins the already-pinned
        # platform view instead of re-reading the active_release pointer itself.
        self.assertEqual(len(views), 11)
        fact_views = [s for s in views if "gold.fact_" in s]
        self.assertEqual(len(fact_views), 7)
        for view in fact_views:
            self.assertIn("gold_control.active_release", view)
        url_view = [s for s in views if "v_job_source_url" in s]
        self.assertEqual(len(url_view), 1)
        # The URL view must be pinned too, transitively, through the platform view.
        self.assertIn("gold.v_job_platform", url_view[0])


class TestDimensionKeyClosure(unittest.TestCase):
    """Reproduces the production ForeignKeyViolation Hermes hit on the VPS."""

    def test_entity_without_observation_in_this_release_still_gets_a_company_member(self):
        # uj_history exists in the registry but none of its observations are in this
        # release, so dim_company built from observations alone would miss its company.
        entities = [
            _entity("uj_a"),
            _entity("uj_history", title="Legacy Data Engineer", company="legacycorp"),
        ]
        links = [_link("obs_a1", "uj_a")]
        observations = [_observation("obs_a1", unified_job_id="uj_a")]
        bundle = _bundle(entities, links, observations)

        company_keys = {row["company_key"] for row in bundle.dim_company}
        self.assertTrue(
            all(row["company_key"] in company_keys for row in bundle.dim_job),
            "every dim_job company_key must exist in dim_company",
        )
        self.assertEqual(blocking_failures(run_checks(bundle)), [])

    def test_dq02b_blocks_when_a_company_member_is_missing(self):
        entities, links, observations = _simple_fixture()
        bundle = _bundle(entities, links, observations)
        self.assertEqual(blocking_failures(run_checks(bundle)), [])
        # Drop the member a dim_job row actually references; the UNKNOWN row is never
        # referenced by these jobs, so removing it would prove nothing.
        referenced = bundle.dim_job[0]["company_key"]
        bundle.dim_company = [row for row in bundle.dim_company
                              if row["company_key"] != referenced]
        codes = {row["check_code"] for row in blocking_failures(run_checks(bundle))}
        self.assertIn("DQ-02b", codes)

    def test_dq02c_and_02e_block_on_missing_skill_and_date_members(self):
        entities, links, observations = _simple_fixture()
        bundle = _bundle(entities, links, observations)
        self.assertTrue(bundle.skill and bundle.dim_date)
        bundle.dim_skill = []
        codes = {row["check_code"] for row in blocking_failures(run_checks(bundle))}
        self.assertIn("DQ-02c", codes)
        bundle.dim_skill = [{"skill_key": row["skill_key"], "taxonomy_version": "t",
                             "skill_code": "x", "skill_name": "X", "skill_group": None,
                             "is_unknown": False} for row in bundle.skill]
        bundle.dim_date = []
        codes = {row["check_code"] for row in blocking_failures(run_checks(bundle))}
        self.assertIn("DQ-02e", codes)


if __name__ == "__main__":
    unittest.main()

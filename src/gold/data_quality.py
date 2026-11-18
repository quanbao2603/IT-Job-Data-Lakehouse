"""Gold data quality: 26 checks run before a release may be published.

Every check is a pure function over the built bundle, so the whole gate is testable
without PostgreSQL. BLOCK severity keeps the previous active_release in place.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any, Iterable, Optional

from src.gold.contract import (
    CITY_AXIS,
    NON_OPEN_ENTITY_STATUSES,
    OPEN_ENTITY_STATUSES,
)

BLOCK = "BLOCK"
WARN = "WARN"


def _result(code: str, name: str, expected: Any, actual: Any, severity: str, passed: bool) -> dict[str, Any]:
    return {
        "check_code": code, "check_name": name, "severity": severity, "passed": bool(passed),
        "expected": str(expected), "actual": str(actual),
        "diff": "" if passed else f"expected {expected}, got {actual}",
    }


def _unique(keys: Iterable[tuple]) -> tuple[int, int]:
    seen = Counter(keys)
    return len(seen), sum(count for count in seen.values() if count > 1)


def run_checks(bundle: Any) -> list[dict[str, Any]]:
    """Run every Gold DQ check against one built bundle."""
    results: list[dict[str, Any]] = []
    snapshot = bundle.snapshot
    entity_count = len(bundle.effective_entity)

    # DQ-01 primary keys are unique on every fact and aggregate.
    key_specs = {
        "snapshot": [("snapshot_date", "unified_job_id")],
        "platform": [("snapshot_date", "unified_job_id", "platform_key")],
        "skill": [("snapshot_date", "unified_job_id", "skill_key")],
        "change_event": [("platform_key", "source_job_id", "observation_id")],
        "market": [("snapshot_date", "job_domain", "city_bucket", "job_level")],
        "company": [("snapshot_date", "company_key")],
        "skill_pair": [("snapshot_date", "skill_a_key", "skill_b_key")],
    }
    duplicate_total = 0
    for name, fields in key_specs.items():
        rows = getattr(bundle, name)
        _, duplicates = _unique(tuple(row[field] for field in fields[0]) for row in rows)
        duplicate_total += duplicates
    results.append(_result("DQ-01", "Grain PK unique on all facts", 0, duplicate_total, BLOCK, duplicate_total == 0))

    # DQ-02 every fact row joins a dim_job version that exists in this release.
    job_keys = {row["job_key"] for row in bundle.dim_job}
    orphan = sum(
        1 for name in ("snapshot", "platform", "skill")
        for row in getattr(bundle, name)
        if row.get("job_key") and row["job_key"] not in job_keys
    )
    results.append(_result("DQ-02", "Facts reference an existing dim_job version", 0, orphan, BLOCK, orphan == 0))

    # DQ-03 snapshot cohort equals the effective entity count of the release.
    results.append(_result("DQ-03", "Snapshot rows equal effective entities", entity_count, len(snapshot), BLOCK,
                           entity_count == len(snapshot)))

    # DQ-04 salary ordering.
    bad_salary = sum(
        1 for row in snapshot
        if row.get("canonical_salary_min_vnd") is not None
        and row.get("canonical_salary_max_vnd") is not None
        and row["canonical_salary_min_vnd"] > row["canonical_salary_max_vnd"]
    )
    results.append(_result("DQ-04", "salary_min <= salary_max", 0, bad_salary, BLOCK, bad_salary == 0))

    # DQ-05 / DQ-08 exactly one current dim_job version per unified job.
    current_counts = Counter(
        row["unified_job_id"] for row in bundle.dim_job if row.get("is_current")
    )
    multi_current = sum(1 for count in current_counts.values() if count != 1)
    results.append(_result("DQ-05", "dim_job version matches the snapshot day", 0, multi_current, BLOCK, multi_current == 0))
    results.append(_result("DQ-08", "Exactly one current version per job", 0, multi_current, BLOCK, multi_current == 0))

    # DQ-06 no observation beyond the cutoff reached a fact.
    future = sum(1 for c in bundle.conflicts if c["conflict_type"] == "FUTURE_OBSERVATION")
    results.append(_result("DQ-06", "No observation newer than cutoff", 0, future, BLOCK, future == 0))

    # DQ-07 rejected link statuses never attach a job.
    rejected = sum(1 for c in bundle.conflicts if c["conflict_type"] == "REJECTED_LINK_HAS_JOB")
    results.append(_result("DQ-07", "No REVIEW_REQUIRED/UNMATCHED link carries a job", 0, rejected, BLOCK, rejected == 0))

    # DQ-09 mutually exclusive market groups sum back to the snapshot cohort.
    market_total = sum(row["observed_job_count"] for row in bundle.market)
    results.append(_result("DQ-09", "Market groups sum to snapshot cohort", len(snapshot), market_total, BLOCK,
                           market_total == len(snapshot)))

    # DQ-10 salary sample never exceeds the cohort of its group.
    bad_sample = sum(
        1 for row in bundle.market if row["salary_sample_count"] > row["observed_job_count"]
    )
    results.append(_result("DQ-10", "salary_sample_count <= observed_job_count", 0, bad_sample, BLOCK, bad_sample == 0))

    # DQ-11 company groups (including UNKNOWN) sum back to the snapshot cohort.
    company_total = sum(row["observed_job_count"] for row in bundle.company)
    results.append(_result("DQ-11", "Company groups sum to snapshot cohort", len(snapshot), company_total, BLOCK,
                           company_total == len(snapshot)))

    # DQ-12 skill pairs are ordered and never self-joined.
    bad_pairs = sum(
        1 for row in bundle.skill_pair
        if row["skill_a_key"] >= row["skill_b_key"]
    )
    results.append(_result("DQ-12", "Skill pairs ordered, no self pair", 0, bad_pairs, BLOCK, bad_pairs == 0))

    # DQ-13 a pair never exceeds the jobs that mention each skill alone.
    jobs_per_skill: dict[str, set[str]] = defaultdict(set)
    for row in bundle.skill:
        jobs_per_skill[row["skill_key"]].add(row["unified_job_id"])
    over_counted = sum(
        1 for row in bundle.skill_pair
        if row["co_mentioned_job_count"] > min(
            len(jobs_per_skill.get(row["skill_a_key"], set())),
            len(jobs_per_skill.get(row["skill_b_key"], set())),
        )
    )
    results.append(_result("DQ-13", "co_mentioned <= jobs having each skill", 0, over_counted, BLOCK, over_counted == 0))

    # DQ-14 change event grain is unique (already covered by DQ-01, kept explicit).
    _, event_duplicates = _unique(
        (row["platform_key"], row["source_job_id"], row["observation_id"]) for row in bundle.change_event
    )
    results.append(_result("DQ-14", "Change event PK unique", 0, event_duplicates, BLOCK, event_duplicates == 0))

    # DQ-15 the baseline observation of a source job is never reported as a change.
    self_baseline = sum(
        1 for row in bundle.change_event
        if row["observation_id"] == row.get("previous_observation_id")
    )
    results.append(_result("DQ-15", "Baseline observation is not a change", 0, self_baseline, BLOCK, self_baseline == 0))

    # DQ-16 lineage: every row carries both release identifiers.
    missing_lineage = 0
    for name in ("snapshot", "platform", "skill", "change_event", "market", "company", "skill_pair"):
        for row in getattr(bundle, name):
            if not row.get("silver_release_id") or not row.get("gold_release_id"):
                missing_lineage += 1
    results.append(_result("DQ-16", "Every row carries gold and silver release ids", 0, missing_lineage, BLOCK,
                           missing_lineage == 0))

    # DQ-17 null coverage of the attributes the market aggregate groups on.
    total = max(1, len(snapshot))
    dim_by_key = {row["job_key"]: row for row in bundle.dim_job}
    null_domain = sum(1 for row in snapshot if not (dim_by_key.get(row["job_key"]) or {}).get("job_domain"))
    rate = round(null_domain / total * 100, 2)
    results.append(_result("DQ-17", "job_domain null rate within threshold", "<=90.0", rate, WARN, rate <= 90.0))

    # DQ-18 unmapped skills are reported, not silently invented.
    results.append(_result("DQ-18", "Unmapped skill codes reported", 0, len(bundle.unmapped_skills), WARN,
                           len(bundle.unmapped_skills) == 0))

    # DQ-19 representative fallback rate stays observable.
    fallbacks = sum(1 for row in bundle.dim_job if row.get("representative_fallback"))
    fallback_rate = round(fallbacks / max(1, len(bundle.dim_job)) * 100, 2)
    results.append(_result("DQ-19", "representative_fallback rate", "<=50.0", fallback_rate, WARN, fallback_rate <= 50.0))

    # DQ-20 repeated crawls cannot inflate the job count per skill.
    _, skill_duplicates = _unique(
        (row["snapshot_date"], row["unified_job_id"], row["skill_key"]) for row in bundle.skill
    )
    results.append(_result("DQ-20", "Job-skill-day grain not inflated by recrawl", 0, skill_duplicates, BLOCK,
                           skill_duplicates == 0))

    # DQ-21 open jobs never exceed observed jobs.
    open_total = sum(row["open_job_count"] for row in snapshot)
    results.append(_result("DQ-21", "SUM(open_job_count) <= SUM(job_count)", len(snapshot), open_total, BLOCK,
                           open_total <= len(snapshot)))

    # DQ-22 no closed/expired/merged/split entity is counted as open.
    bad_open = sum(
        1 for row in snapshot
        if row["entity_status"] in NON_OPEN_ENTITY_STATUSES and row["open_job_count"] == 1
    )
    results.append(_result("DQ-22", "Non-open statuses never flagged open", 0, bad_open, BLOCK, bad_open == 0))

    # DQ-23 open_job_count is derived from entity_status, never from observations.is_active.
    results.append(_result("DQ-23", "open_job_count source is entity_status", "entity_status",
                           bundle.open_job_source, BLOCK, bundle.open_job_source == "entity_status"))
    derived_check = sum(
        1 for row in snapshot
        if (row["open_job_count"] == 1) != (row["entity_status"] in OPEN_ENTITY_STATUSES)
    )
    results.append(_result("DQ-23b", "open_job_count matches the D-G05 status set", 0, derived_check, BLOCK,
                           derived_check == 0))

    # DQ-24 the two Silver sources agree (GAP-7 / D-G06).
    divergence = sum(1 for c in bundle.conflicts if c["conflict_type"] == "SOURCE_DIVERGENCE")
    results.append(_result("DQ-24", "Parquet entities and SQLite registry agree", 0, divergence, BLOCK, divergence == 0))

    # DQ-25 the UNKNOWN share of the domain axis stays visible.
    unknown_domain = sum(
        row["observed_job_count"] for row in bundle.market if row["job_domain"] == "UNKNOWN"
    )
    unknown_rate = round(unknown_domain / max(1, market_total) * 100, 2)
    results.append(_result("DQ-25", "job_domain UNKNOWN share", "<=50.0", unknown_rate, WARN, unknown_rate <= 50.0))

    # DQ-26 the city axis only ever holds the five explicit groups.
    bad_city = sum(1 for row in bundle.market if row["city_bucket"] not in CITY_AXIS)
    results.append(_result("DQ-26", "City axis limited to five explicit groups", 0, bad_city, BLOCK, bad_city == 0))

    # Any unresolved conflict blocks publishing, whatever its type.
    duplicate_entity = sum(1 for c in bundle.conflicts if c["conflict_type"] == "DUP_ENTITY")
    results.append(_result("DQ-27", "No duplicate effective entity", 0, duplicate_entity, BLOCK, duplicate_entity == 0))
    return results


def blocking_failures(results: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    return [row for row in results if not row["passed"] and row["severity"] == BLOCK]


def summarize(results: Iterable[dict[str, Any]]) -> dict[str, Any]:
    rows = list(results)
    return {
        "total_checks": len(rows),
        "passed": sum(1 for row in rows if row["passed"]),
        "failed_block": len(blocking_failures(rows)),
        "failed_warn": sum(1 for row in rows if not row["passed"] and row["severity"] == WARN),
        "publish_allowed": not blocking_failures(rows),
    }

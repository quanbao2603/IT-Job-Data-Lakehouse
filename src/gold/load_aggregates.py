"""BI aggregate builders: derived from the base facts of the same gold_release_id.

These tables add no new business events. They exist so a dashboard query stays cheap,
and they must never be summed across overlapping groups to produce a job total.
"""
from __future__ import annotations

import math
from collections import defaultdict
from typing import Any, Iterable, Optional

from src.gold.contract import UNKNOWN
from src.gold.timeutil import date_key, parse_utc


def percentile_cont(values: Iterable[float], quantile: float) -> Optional[float]:
    """Linear-interpolation percentile, matching PostgreSQL percentile_cont semantics."""
    data = sorted(float(value) for value in values)
    if not data:
        return None
    if len(data) == 1:
        return data[0]
    position = (len(data) - 1) * float(quantile)
    low = int(math.floor(position))
    high = int(math.ceil(position))
    if low == high:
        return data[low]
    return data[low] + (data[high] - data[low]) * (position - low)


def _group_label(value: Any) -> str:
    text = str(value or "").strip()
    return text or UNKNOWN


def build_market_daily(
    snapshot_rows: Iterable[dict[str, Any]],
    dim_job_by_key: dict[str, dict[str, Any]],
    *,
    gold_release_id: str,
    silver_release_id: str,
    snapshot_date: Any,
    cutoff_at: str,
) -> list[dict[str, Any]]:
    """Fact 5: day x domain x city x level. Groups are mutually exclusive by construction."""
    buckets: dict[tuple[str, str, str], dict[str, Any]] = defaultdict(
        lambda: {"jobs": 0, "newly": 0, "newly_null": False, "open": 0, "salaries": []}
    )
    for row in snapshot_rows:
        dim_job = dim_job_by_key.get(row["job_key"]) or {}
        key = (
            _group_label(dim_job.get("job_domain")),
            _group_label(dim_job.get("city_bucket") or row.get("city_bucket")),
            _group_label(dim_job.get("job_level")),
        )
        bucket = buckets[key]
        bucket["jobs"] += 1
        bucket["open"] += int(row.get("open_job_count") or 0)
        if row.get("newly_seen_job_count") is None:
            bucket["newly_null"] = True
        else:
            bucket["newly"] += int(row["newly_seen_job_count"])
        if row.get("salary_sample_flag") and row.get("salary_midpoint_vnd") is not None:
            bucket["salaries"].append(float(row["salary_midpoint_vnd"]))

    rows = []
    for (domain, city, level), bucket in sorted(buckets.items()):
        salaries = bucket["salaries"]
        rows.append({
            "gold_release_id": gold_release_id,
            "snapshot_date": snapshot_date,
            "date_key": date_key(snapshot_date),
            "job_domain": domain,
            "city_bucket": city,
            "job_level": level,
            "observed_job_count": bucket["jobs"],
            "newly_seen_job_count": None if bucket["newly_null"] else bucket["newly"],
            "open_job_count": bucket["open"],
            "salary_sample_count": len(salaries),
            "salary_p25_vnd": percentile_cont(salaries, 0.25),
            "salary_p50_vnd": percentile_cont(salaries, 0.50),
            "salary_p75_vnd": percentile_cont(salaries, 0.75),
            "salary_avg_vnd": (sum(salaries) / len(salaries)) if salaries else None,
            "snapshot_cutoff_at": parse_utc(cutoff_at),
            "silver_release_id": silver_release_id,
        })
    return rows


def build_company_hiring_daily(
    snapshot_rows: Iterable[dict[str, Any]],
    dim_job_by_key: dict[str, dict[str, Any]],
    *,
    gold_release_id: str,
    silver_release_id: str,
    snapshot_date: Any,
    cutoff_at: str,
) -> list[dict[str, Any]]:
    """Fact 6: day x company_key. Observation counts are never used as job counts."""
    buckets: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"jobs": 0, "newly": 0, "newly_null": False, "open": 0,
                 "cities": set(), "domains": set(), "salaries": 0}
    )
    for row in snapshot_rows:
        dim_job = dim_job_by_key.get(row["job_key"]) or {}
        bucket = buckets[row["company_key"]]
        bucket["jobs"] += 1
        bucket["open"] += int(row.get("open_job_count") or 0)
        if row.get("newly_seen_job_count") is None:
            bucket["newly_null"] = True
        else:
            bucket["newly"] += int(row["newly_seen_job_count"])
        bucket["cities"].add(_group_label(dim_job.get("city_bucket")))
        bucket["domains"].add(_group_label(dim_job.get("job_domain")))
        if row.get("salary_sample_flag"):
            bucket["salaries"] += 1

    rows = []
    for company, bucket in sorted(buckets.items()):
        rows.append({
            "gold_release_id": gold_release_id,
            "snapshot_date": snapshot_date,
            "date_key": date_key(snapshot_date),
            "company_key": company,
            "observed_job_count": bucket["jobs"],
            "newly_seen_job_count": None if bucket["newly_null"] else bucket["newly"],
            "open_job_count": bucket["open"],
            "distinct_city_count": len(bucket["cities"]),
            "distinct_domain_count": len(bucket["domains"]),
            "salary_sample_count": bucket["salaries"],
            "snapshot_cutoff_at": parse_utc(cutoff_at),
            "silver_release_id": silver_release_id,
        })
    return rows


def build_skill_pair_daily(
    skill_rows: Iterable[dict[str, Any]],
    *,
    gold_release_id: str,
    silver_release_id: str,
    snapshot_date: Any,
    cutoff_at: str,
    taxonomy_version: str,
    max_skills_per_job: int = 40,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Fact 7: unordered skill pairs. `skill_a_key < skill_b_key` removes mirrors and self-pairs.

    Jobs carrying an abnormal number of skills are reported before their combinations are
    generated, because k skills produce k(k-1)/2 pairs and can explode the table.
    """
    skills_by_job: dict[str, set[str]] = defaultdict(set)
    for row in skill_rows:
        skills_by_job[row["unified_job_id"]].add(row["skill_key"])

    outliers = sorted(
        job for job, skills in skills_by_job.items() if len(skills) > max_skills_per_job
    )
    pairs: dict[tuple[str, str], set[str]] = defaultdict(set)
    for job, skills in skills_by_job.items():
        ordered = sorted(skills)
        for index, first in enumerate(ordered):
            for second in ordered[index + 1:]:
                pairs[(first, second)].add(job)

    rows = [
        {
            "gold_release_id": gold_release_id,
            "snapshot_date": snapshot_date,
            "date_key": date_key(snapshot_date),
            "skill_a_key": first,
            "skill_b_key": second,
            "taxonomy_version": taxonomy_version,
            "co_mentioned_job_count": len(jobs),
            "snapshot_cutoff_at": parse_utc(cutoff_at),
            "silver_release_id": silver_release_id,
        }
        for (first, second), jobs in sorted(pairs.items())
    ]
    return rows, outliers

"""Base fact builders: pure functions producing rows for the four foundational facts."""
from __future__ import annotations

from typing import Any, Iterable, Optional

from src.gold.contract import EVIDENCE_WINDOW_DAYS, is_open_status
from src.gold.load_dimensions import platform_key
from src.gold.timeutil import date_key, days_between, parse_utc, report_date, within_window


def salary_midpoint(minimum: Any, maximum: Any) -> Optional[float]:
    """Only a complete, ordered, non-negative VND range qualifies as a salary sample."""
    if minimum is None or maximum is None:
        return None
    try:
        low, high = int(minimum), int(maximum)
    except (TypeError, ValueError):
        return None
    if low < 0 or high < 0 or low > high:
        return None
    return (low + high) / 2


def build_snapshot_facts(
    entities: Iterable[dict[str, Any]],
    dim_job_by_entity: dict[str, dict[str, Any]],
    *,
    gold_release_id: str,
    silver_release_id: str,
    snapshot_date: Any,
    cutoff_at: str,
    previous_snapshot_ids: Optional[Iterable[str]] = None,
) -> list[dict[str, Any]]:
    """Fact 1: one row per unified job. Never multiplied by joining links or observations."""
    baseline = previous_snapshot_ids is None
    previous = set(previous_snapshot_ids or ())
    rows: list[dict[str, Any]] = []
    for entity in entities:
        unified_job_id = str(entity["unified_job_id"])
        dim_job = dim_job_by_entity.get(unified_job_id)
        if dim_job is None:
            # A snapshot row must always join a dim_job version; skipping would understate
            # the cohort silently, so this is a hard error for the caller to catch.
            raise KeyError(f"dim_job version missing for {unified_job_id}")
        minimum = entity.get("canonical_salary_min_vnd")
        maximum = entity.get("canonical_salary_max_vnd")
        midpoint = salary_midpoint(minimum, maximum)
        posted = dim_job.get("canonical_posted_date")
        status = str(entity.get("entity_status") or "")
        rows.append({
            "gold_release_id": gold_release_id,
            "snapshot_date": snapshot_date,
            "date_key": date_key(snapshot_date),
            "unified_job_id": unified_job_id,
            "job_key": dim_job["job_key"],
            "company_key": dim_job["company_key"],
            "posted_date_key": date_key(posted) if posted else None,
            "job_count": 1,
            "open_job_count": 1 if is_open_status(status) else 0,
            "newly_seen_job_count": None if baseline else (0 if unified_job_id in previous else 1),
            "entity_status": status,
            "canonical_salary_min_vnd": minimum,
            "canonical_salary_max_vnd": maximum,
            "salary_midpoint_vnd": midpoint,
            "salary_sample_flag": midpoint is not None,
            "source_count": int(entity.get("source_count") or 0),
            "observation_count": int(entity.get("observation_count") or 0),
            "last_seen_at": parse_utc(entity.get("last_seen_at")),
            "snapshot_cutoff_at": parse_utc(cutoff_at),
            "silver_release_id": silver_release_id,
        })
    return rows


def _accepted_observations(
    observations: Iterable[dict[str, Any]],
    cutoff_at: str,
    window_days: int = EVIDENCE_WINDOW_DAYS,
) -> list[dict[str, Any]]:
    """Observations that both belong to an accepted job and are recent enough to be evidence."""
    rows = []
    for observation in observations:
        if not observation.get("accepted_for_gold"):
            continue
        if not observation.get("unified_job_id"):
            continue
        if not within_window(observation.get("observed_at"), cutoff_at, window_days):
            continue
        rows.append(observation)
    return rows


def build_platform_facts(
    observations: Iterable[dict[str, Any]],
    dim_job_by_entity: dict[str, dict[str, Any]],
    snapshot_job_ids: set[str],
    *,
    gold_release_id: str,
    silver_release_id: str,
    snapshot_date: Any,
    cutoff_at: str,
    window_days: int = EVIDENCE_WINDOW_DAYS,
) -> list[dict[str, Any]]:
    """Fact 2: evidence that one unified job was seen on one platform inside the window."""
    grouped: dict[tuple[str, str], dict[str, Any]] = {}
    for observation in _accepted_observations(observations, cutoff_at, window_days):
        unified_job_id = str(observation["unified_job_id"])
        if unified_job_id not in snapshot_job_ids:
            continue
        platform = str(observation.get("platform") or "").strip().casefold()
        bucket = grouped.setdefault((unified_job_id, platform), {
            "source_job_ids": set(), "observation_count": 0, "last_observed_at": None,
        })
        bucket["source_job_ids"].add(str(observation.get("source_job_id") or ""))
        bucket["observation_count"] += 1
        observed_at = parse_utc(observation.get("observed_at"))
        if bucket["last_observed_at"] is None or (
            observed_at is not None and observed_at > bucket["last_observed_at"]
        ):
            bucket["last_observed_at"] = observed_at

    rows: list[dict[str, Any]] = []
    for (unified_job_id, platform), bucket in sorted(grouped.items()):
        dim_job = dim_job_by_entity.get(unified_job_id)
        if dim_job is None:
            continue
        last_observed = bucket["last_observed_at"]
        freshness = days_between(
            last_observed.isoformat().replace("+00:00", "Z") if last_observed else None,
            cutoff_at,
        )
        rows.append({
            "gold_release_id": gold_release_id,
            "snapshot_date": snapshot_date,
            "date_key": date_key(snapshot_date),
            "unified_job_id": unified_job_id,
            "job_key": dim_job["job_key"],
            "platform_key": platform_key(platform),
            "company_key": dim_job["company_key"],
            "job_platform_count": 1,
            "source_listing_count": len(bucket["source_job_ids"]),
            "observation_count_in_window": bucket["observation_count"],
            "last_observed_at": last_observed,
            "freshness_days": max(0, int(freshness or 0)),
            "evidence_window_days": window_days,
            "snapshot_cutoff_at": parse_utc(cutoff_at),
            "silver_release_id": silver_release_id,
        })
    return rows


def build_skill_facts(
    observations: Iterable[dict[str, Any]],
    dim_job_by_entity: dict[str, dict[str, Any]],
    snapshot_job_ids: set[str],
    *,
    gold_release_id: str,
    silver_release_id: str,
    snapshot_date: Any,
    cutoff_at: str,
    taxonomy_version: str,
    window_days: int = EVIDENCE_WINDOW_DAYS,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Fact 3: one row per job per standardized skill; repeated crawls must not inflate it."""
    from src.gold.load_dimensions import skill_key

    grouped: dict[tuple[str, str], dict[str, Any]] = {}
    unmapped: set[str] = set()
    for observation in _accepted_observations(observations, cutoff_at, window_days):
        unified_job_id = str(observation["unified_job_id"])
        if unified_job_id not in snapshot_job_ids:
            continue
        skills = observation.get("skills_standardized") or []
        if isinstance(skills, str):
            skills = [skills]
        platform = str(observation.get("platform") or "").strip().casefold()
        observed_at = parse_utc(observation.get("observed_at"))
        for raw_skill in skills:
            skill = str(raw_skill or "").strip().casefold()
            if not skill:
                continue
            bucket = grouped.setdefault((unified_job_id, skill), {
                "platforms": set(), "latest_observed_at": None,
            })
            bucket["platforms"].add(platform)
            if bucket["latest_observed_at"] is None or (
                observed_at is not None and observed_at > bucket["latest_observed_at"]
            ):
                bucket["latest_observed_at"] = observed_at

    rows: list[dict[str, Any]] = []
    for (unified_job_id, skill), bucket in sorted(grouped.items()):
        dim_job = dim_job_by_entity.get(unified_job_id)
        if dim_job is None:
            continue
        rows.append({
            "gold_release_id": gold_release_id,
            "snapshot_date": snapshot_date,
            "date_key": date_key(snapshot_date),
            "unified_job_id": unified_job_id,
            "job_key": dim_job["job_key"],
            "skill_key": skill_key(skill, taxonomy_version),
            "taxonomy_version": taxonomy_version,
            "job_skill_count": 1,
            "supporting_platform_count": len(bucket["platforms"]),
            "latest_observed_at": bucket["latest_observed_at"],
            "snapshot_cutoff_at": parse_utc(cutoff_at),
            "silver_release_id": silver_release_id,
        })
    return rows, sorted(unmapped)


def _change_flags(previous: dict[str, Any], current: dict[str, Any]) -> dict[str, Any]:
    """Compare tracked business fields; content_hash is deliberately ignored."""
    previous_skills = set(previous.get("skills_standardized") or [])
    current_skills = set(current.get("skills_standardized") or [])
    previous_salary = (previous.get("salary_min_vnd"), previous.get("salary_max_vnd"))
    current_salary = (current.get("salary_min_vnd"), current.get("salary_max_vnd"))
    return {
        "title_changed": str(previous.get("job_title_normalized") or "") != str(current.get("job_title_normalized") or ""),
        "salary_changed": previous_salary != current_salary,
        "location_changed": str(previous.get("city_normalized") or "") != str(current.get("city_normalized") or ""),
        "work_mode_changed": str(previous.get("work_mode") or "") != str(current.get("work_mode") or ""),
        "skills_changed": previous_skills != current_skills,
        "skill_added_count": len(current_skills - previous_skills),
        "skill_removed_count": len(previous_skills - current_skills),
        "_previous_salary": previous_salary,
        "_current_salary": current_salary,
    }


def _representative_salary(minimum: Any, maximum: Any) -> Optional[int]:
    for value in (minimum, maximum):
        if value is not None:
            return int(value)
    return None


def build_change_events(
    observations: Iterable[dict[str, Any]],
    dim_job_by_entity: dict[str, dict[str, Any]],
    *,
    gold_release_id: str,
    silver_release_id: str,
    cutoff_at: str,
) -> list[dict[str, Any]]:
    """Fact 4: one event per observation that changed a business field of a source job.

    The first observation of a source job is the baseline, never a change. Cosmetic edits
    that only move content_hash do not create events because content_hash is not compared.
    """
    by_source: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for observation in observations:
        platform = str(observation.get("platform") or "").strip().casefold()
        source_job_id = str(observation.get("source_job_id") or "")
        if not platform or not source_job_id:
            continue
        by_source.setdefault((platform, source_job_id), []).append(observation)

    rows: list[dict[str, Any]] = []
    for (platform, source_job_id), group in sorted(by_source.items()):
        ordered = sorted(
            group,
            key=lambda item: (
                parse_utc(item.get("observed_at")) or parse_utc(cutoff_at),
                str(item.get("observation_id") or ""),
            ),
        )
        previous = ordered[0]
        for current in ordered[1:]:
            observed_at = parse_utc(current.get("observed_at"))
            if observed_at is None or observed_at > parse_utc(cutoff_at):
                previous = current
                continue
            flags = _change_flags(previous, current)
            if not any(flags[field] for field in (
                "title_changed", "salary_changed", "location_changed",
                "work_mode_changed", "skills_changed",
            )):
                previous = current
                continue
            unified_job_id = current.get("unified_job_id")
            dim_job = dim_job_by_entity.get(str(unified_job_id)) if unified_job_id else None
            old_salary = _representative_salary(*flags["_previous_salary"])
            new_salary = _representative_salary(*flags["_current_salary"])
            event_date = report_date(current.get("observed_at"))
            rows.append({
                "gold_release_id": gold_release_id,
                "event_date": event_date,
                "event_date_key": date_key(event_date),
                "platform_key": platform_key(platform),
                "source_job_id": source_job_id,
                "observation_id": str(current.get("observation_id")),
                "previous_observation_id": str(previous.get("observation_id")),
                "unified_job_id": unified_job_id,
                "job_key": (dim_job or {}).get("job_key"),
                "company_key": (dim_job or {}).get("company_key"),
                "change_event_count": 1,
                "title_changed": flags["title_changed"],
                "salary_changed": flags["salary_changed"],
                "location_changed": flags["location_changed"],
                "work_mode_changed": flags["work_mode_changed"],
                "skills_changed": flags["skills_changed"],
                "salary_old_vnd": old_salary,
                "salary_new_vnd": new_salary,
                "salary_delta_vnd": (
                    new_salary - old_salary if old_salary is not None and new_salary is not None else None
                ),
                "skill_added_count": flags["skill_added_count"],
                "skill_removed_count": flags["skill_removed_count"],
                "link_resolved": bool(unified_job_id),
                "is_revised": False,
                "observed_at": observed_at,
                "snapshot_cutoff_at": parse_utc(cutoff_at),
                "silver_release_id": silver_release_id,
            })
            previous = current
    return rows

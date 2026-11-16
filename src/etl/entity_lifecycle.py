"""MVP lifecycle and canonical observation selection helpers."""
from __future__ import annotations

from datetime import date, datetime
from typing import Any

SOURCE_GRACE_PERIOD_DAYS = {
    "careerviet": 14, "upzi": 14, "vietnamdevs": 14, "vieclam24h": 14,
    "devwork": 14, "itviec": 7, "topcv": 14, "topdev": 10, "vietnamworks": 21,
}
SOURCE_PRIORITY = {"itviec": 1, "vietnamworks": 2, "topcv": 3}


def choose_canonical(observations: list[dict[str, Any]]) -> dict[str, Any]:
    if not observations:
        raise ValueError("Cannot choose canonical observation from empty input")
    eligible = [row for row in observations if row.get("quality_accepted", True)]
    if not eligible:
        raise ValueError("No quality-accepted observations available")
    return max(eligible, key=lambda row: (
        row.get("observed_at") or "",
        len(row.get("job_description_clean") or ""),
        row.get("salary_min_vnd") is not None,
        row.get("expired_date") is not None,
        row.get("city_normalized") is not None,
        -SOURCE_PRIORITY.get(str(row.get("platform") or "").casefold(), 99),
        row.get("observation_id") or "",
    ))


def next_entity_status(previous: str | None, *, content_changed: bool = False, closed: bool = False) -> str:
    if closed:
        return "CLOSED"
    if previous == "CLOSED":
        return "REOPENED"
    if content_changed:
        return "UPDATED"
    if previous in {None, "NEW"}:
        return "NEW"
    return "ACTIVE"


def is_past_grace_period(platform: str, last_seen_at: str, as_of: date) -> bool:
    seen = datetime.fromisoformat(last_seen_at.replace("Z", "+00:00")).date()
    if platform not in SOURCE_GRACE_PERIOD_DAYS:
        raise ValueError(f"Unknown Silver platform: {platform}")
    return (as_of - seen).days > SOURCE_GRACE_PERIOD_DAYS[platform]


def mark_stale_entities(
    registry: Any,
    *,
    platform: str,
    observed_source_job_ids: set[str],
    as_of: date,
    partial_run: bool = False,
) -> int:
    """Mark missing entities STALE while they remain inside the source grace period."""
    if partial_run:
        return 0
    if platform not in SOURCE_GRACE_PERIOD_DAYS:
        raise ValueError(f"Unknown Silver platform: {platform}")
    changed = 0
    for entity in registry.list_entities():
        source_rows = registry.list_source_jobs(entity["unified_job_id"], platform=platform)
        if not source_rows or any(row["source_job_id"] in observed_source_job_ids for row in source_rows):
            continue
        if any(not is_past_grace_period(platform, row["last_observed_at"], as_of) for row in source_rows):
            if registry.set_entity_status(entity["unified_job_id"], "STALE"):
                changed += 1
    return changed


def close_stale_entities(
    registry: Any,
    *,
    platform: str,
    observed_source_job_ids: set[str],
    as_of: date,
    partial_run: bool = False,
) -> int:
    """Close only entities safely absent beyond grace; partial runs never close."""
    if partial_run:
        return 0
    if platform not in SOURCE_GRACE_PERIOD_DAYS:
        raise ValueError(f"Unknown Silver platform: {platform}")
    changed = 0
    for entity in registry.list_entities():
        source_rows = registry.list_source_jobs(entity["unified_job_id"], platform=platform)
        if not source_rows or any(row["source_job_id"] in observed_source_job_ids for row in source_rows):
            continue
        if all(is_past_grace_period(platform, row["last_observed_at"], as_of) for row in source_rows):
            if registry.set_entity_status(entity["unified_job_id"], "CLOSED"):
                changed += 1
    return changed


def expire_entities(
    registry: Any,
    *,
    expired_entity_ids: set[str],
    partial_run: bool = False,
) -> int:
    """Mark only explicitly expired entities; never infer expiry from absence."""
    if partial_run:
        return 0
    changed = 0
    for unified_job_id in sorted(expired_entity_ids):
        entity = registry.get_entity(unified_job_id)
        if entity is not None and entity["entity_status"] in {"CLOSED", "STALE"}:
            if registry.set_entity_status(unified_job_id, "EXPIRED"):
                changed += 1
    return changed


def lifecycle_transition(previous: str | None, *, content_changed: bool = False,
                         missing_beyond_grace: bool = False, reappeared: bool = False) -> str:
    if missing_beyond_grace:
        return "CLOSED"
    if reappeared and previous == "CLOSED":
        return "REOPENED"
    if content_changed:
        return "UPDATED"
    return previous or "NEW"

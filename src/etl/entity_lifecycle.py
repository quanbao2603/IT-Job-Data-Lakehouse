"""MVP lifecycle and canonical observation selection helpers."""
from __future__ import annotations

from datetime import date, datetime
from typing import Any

SOURCE_GRACE_PERIOD_DAYS = {
    "careerviet": 14, "upzi": 14, "vietnamdevs": 14, "vieclam24h": 14,
    "devwork": 14, "itviec": 7, "topcv": 14, "topdev": 10, "vietnamworks": 21,
}


def choose_canonical(observations: list[dict[str, Any]]) -> dict[str, Any]:
    if not observations:
        raise ValueError("Cannot choose canonical observation from empty input")
    return max(observations, key=lambda row: (
        row.get("observed_at") or "",
        len(row.get("job_description_clean") or ""),
        row.get("salary_min_vnd") is not None,
        row.get("expired_date") is not None,
        row.get("city_normalized") is not None,
        row.get("observed_at") or "",
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
    return (as_of - seen).days > SOURCE_GRACE_PERIOD_DAYS.get(platform, 14)

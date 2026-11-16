"""Deterministic Silver quality gate and quarantine classification."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any, Optional


@dataclass(frozen=True)
class QualityResult:
    accepted: bool
    quarantine_reason: Optional[str] = None
    warning: Optional[str] = None


def validate_observation(record: dict[str, Any], today: Optional[date] = None) -> QualityResult:
    for field, code in (
        ("source_job_id", "SCHEMA_DRIFT_MISSING_JOB_ID"),
        ("job_title", "MISSING_MANDATORY_FIELD: job_title"),
        ("company_name", "MISSING_MANDATORY_FIELD: company_name"),
    ):
        if not str(record.get(field) or "").strip():
            return QualityResult(False, code)
    if record.get("salary_min_vnd") is not None and record.get("salary_max_vnd") is not None:
        if record["salary_min_vnd"] > record["salary_max_vnd"]:
            return QualityResult(False, "SALARY_MIN_GREATER_THAN_MAX")
    if record.get("salary_min_vnd") is not None and record["salary_min_vnd"] < 3_000_000:
        if str(record.get("job_level") or "").casefold() in {"intern", "fresher"}:
            return QualityResult(True, warning="SALARY_TOO_LOW_OUTLIER")
        return QualityResult(False, "SALARY_TOO_LOW_OUTLIER")
    if record.get("salary_max_vnd") is not None and record["salary_max_vnd"] > 300_000_000:
        return QualityResult(False, "SALARY_TOO_HIGH_OUTLIER")
    posted_date = record.get("posted_date")
    if posted_date and today:
        try:
            parsed = date.fromisoformat(posted_date)
        except ValueError:
            parsed = None
        if parsed and parsed > today:
            return QualityResult(False, "FUTURE_POSTED_DATE")
        if parsed and parsed < date(2020, 1, 1):
            return QualityResult(False, "STALE_POSTED_DATE")
    return QualityResult(True)

"""Fail-closed Silver quality gates and Gold publish gate."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any, Optional

VALID_ENTITY_STATUSES = {
    "NEW", "ACTIVE", "UPDATED", "STALE", "EXPIRED", "CLOSED", "REOPENED", "MERGED", "SPLIT",
}
VALID_MATCH_STATUSES = {"AUTO_MATCH", "REVIEW_REQUIRED", "DISTINCT_ENTITY", "UNMATCHED", "MANUAL_MATCH"}
VALID_MATCH_METHODS = {
    "SOURCE_JOB_ID", "CANONICAL_URL", "EXACT_NORMALIZED_IDENTITY",
    "FUZZY_TITLE_COMPANY_LOCATION", "MANUAL_REVIEW", "NONE",
}


@dataclass(frozen=True)
class QualityResult:
    accepted: bool
    quarantine_reason: Optional[str] = None
    warning: Optional[str] = None


def validate_observation(record: dict[str, Any], today: Optional[date] = None) -> QualityResult:
    today = today or datetime.now(timezone.utc).date()
    for field, code in (
        ("source_job_id", "SCHEMA_DRIFT_MISSING_JOB_ID"),
        ("job_title", "MISSING_MANDATORY_FIELD: job_title"),
        ("company_name", "MISSING_MANDATORY_FIELD: company_name"),
        ("platform", "MISSING_MANDATORY_FIELD: platform"),
        ("observation_id", "MISSING_MANDATORY_FIELD: observation_id"),
        ("observed_at", "MISSING_MANDATORY_FIELD: observed_at"),
    ):
        if not str(record.get(field) or "").strip():
            return QualityResult(False, code)
    if not isinstance(record.get("vacancies_count"), int) or record["vacancies_count"] < 1:
        return QualityResult(False, "INVALID_VACANCIES_COUNT")
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
    if posted_date:
        try:
            parsed = date.fromisoformat(posted_date)
        except ValueError:
            return QualityResult(False, "INVALID_POSTED_DATE")
        if parsed > today:
            return QualityResult(False, "FUTURE_POSTED_DATE")
        if parsed < date(2020, 1, 1):
            return QualityResult(False, "STALE_POSTED_DATE")
    return QualityResult(True)


def validate_entity(entity: dict[str, Any]) -> QualityResult:
    for field in ("unified_job_id", "canonical_title", "canonical_company", "first_seen_at", "last_seen_at", "resolution_version"):
        if not str(entity.get(field) or "").strip():
            return QualityResult(False, f"MISSING_ENTITY_FIELD: {field}")
    if entity.get("entity_status") not in VALID_ENTITY_STATUSES:
        return QualityResult(False, "INVALID_ENTITY_STATUS")
    if int(entity.get("observation_count", 0)) < 1:
        return QualityResult(False, "ENTITY_WITHOUT_OBSERVATION")
    return QualityResult(True)


def validate_link(link: dict[str, Any]) -> QualityResult:
    if not str(link.get("observation_id") or "").strip():
        return QualityResult(False, "MISSING_LINK_OBSERVATION_ID")
    if link.get("match_status") not in VALID_MATCH_STATUSES:
        return QualityResult(False, "INVALID_MATCH_STATUS")
    if link.get("match_method") not in VALID_MATCH_METHODS:
        return QualityResult(False, "INVALID_MATCH_METHOD")
    score = link.get("match_score")
    if score is not None and not 0.0 <= float(score) <= 1.0:
        return QualityResult(False, "INVALID_MATCH_SCORE")
    status = link["match_status"]
    if status in {"REVIEW_REQUIRED", "UNMATCHED"} and link.get("unified_job_id") is not None:
        return QualityResult(False, "REVIEW_LINK_MUST_NOT_HAVE_UNIFIED_ID")
    if status == "REVIEW_REQUIRED" and not link.get("candidate_entity_id"):
        return QualityResult(False, "REVIEW_LINK_MISSING_CANDIDATE")
    return QualityResult(True)


def enforce_publish_gate(summary_report: dict[str, Any]) -> dict[str, Any]:
    metrics = summary_report.get("metrics") or {}
    quarantine_rate = float(metrics.get("quarantine_rate_pct", 0.0))
    reasons = []
    if quarantine_rate > 5.0:
        reasons.append("Quarantine rate > 5% — block Gold publish")
    if summary_report.get("schema_drift_warnings"):
        reasons.append("Schema drift detected — block Gold publish")
    if summary_report.get("coverage_warning") or summary_report.get("partial_run") or summary_report.get("failed_platforms"):
        reasons.append("Partial or failed source coverage — block Gold publish")
    if reasons:
        raise ValueError("; ".join(reasons))
    return {"status": "READY", "quarantine_rate_pct": quarantine_rate}

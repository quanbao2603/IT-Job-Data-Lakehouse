"""Dimension builders: pure functions that produce rows, no database access.

Keeping these pure means the business rules are testable on any machine, including
Windows CI without PostgreSQL, and the SQL layer stays a thin executor.
"""
from __future__ import annotations

import hashlib
from collections import Counter
from datetime import date
from typing import Any, Iterable, Optional

from src.etl.transformers import (
    CITY_NORMALIZATION_VERSION,
    COMPANY_NORMALIZATION_VERSION,
    SKILL_TAXONOMY,
    SKILL_TAXONOMY_VERSION,
    clean_company_name,
)
from src.gold.contract import CITY_AXIS, PLATFORMS, UNKNOWN
from src.gold.timeutil import date_key, daterange


def _digest(prefix: str, *parts: Any) -> str:
    raw = "|".join(str(part if part is not None else "") for part in parts)
    return f"{prefix}_{hashlib.sha256(raw.encode('utf-8')).hexdigest()}"


def company_key(company_name_normalized: str, version: str = COMPANY_NORMALIZATION_VERSION) -> str:
    name = str(company_name_normalized or "").strip()
    if not name:
        return unknown_company_key(version)
    return _digest("co", name, version)


def unknown_company_key(version: str = COMPANY_NORMALIZATION_VERSION) -> str:
    return _digest("co", UNKNOWN, version)


def skill_key(skill_code: str, taxonomy_version: str = SKILL_TAXONOMY_VERSION) -> str:
    return _digest("sk", str(skill_code).strip().casefold(), taxonomy_version)


def job_key(unified_job_id: str, version_seq: int) -> str:
    return _digest("jk", unified_job_id, version_seq)


def platform_key(platform_code: str) -> int:
    """Stable 1..N key over the known platform list; unknown codes land after it."""
    code = str(platform_code or "").strip().casefold()
    if code in PLATFORMS:
        return PLATFORMS.index(code) + 1
    return len(PLATFORMS) + 1


def build_dim_date(start: date, end: date) -> list[dict[str, Any]]:
    """Calendar dimension, independent of Silver."""
    rows = []
    for day in daterange(start, end):
        rows.append({
            "date_key": date_key(day),
            "full_date": day,
            "year": day.year,
            "quarter": (day.month - 1) // 3 + 1,
            "month": day.month,
            "week_of_year": int(day.strftime("%V")),
            "day_of_week": day.isoweekday(),
            "is_weekend": day.isoweekday() >= 6,
            "timezone_name": "Asia/Ho_Chi_Minh",
        })
    return rows


def build_dim_platform(observed: Optional[Iterable[str]] = None) -> list[dict[str, Any]]:
    """Nine known sources plus an unknown member so a new code is visible, not dropped."""
    codes = list(PLATFORMS)
    for code in observed or ():
        normalized = str(code or "").strip().casefold()
        if normalized and normalized not in codes:
            codes.append(normalized)
    rows = [
        {"platform_key": index + 1, "platform_code": code, "platform_name": code.title(),
         "is_active": True, "is_unknown": False}
        for index, code in enumerate(codes)
    ]
    rows.append({
        "platform_key": len(codes) + 1, "platform_code": UNKNOWN, "platform_name": "Unknown",
        "is_active": True, "is_unknown": True,
    })
    return rows


def build_dim_skill(
    observed_skills: Optional[Iterable[str]] = None,
    taxonomy_version: str = SKILL_TAXONOMY_VERSION,
) -> tuple[list[dict[str, Any]], list[str]]:
    """One key per taxonomy code; unmapped observations are reported, never invented."""
    rows = [
        {"skill_key": skill_key(code, taxonomy_version), "taxonomy_version": taxonomy_version,
         "skill_code": code, "skill_name": code.title(), "skill_group": None, "is_unknown": False}
        for code in SKILL_TAXONOMY
    ]
    known = {str(code).casefold() for code in SKILL_TAXONOMY}
    unmapped = sorted({
        str(skill).strip().casefold() for skill in (observed_skills or ())
        if str(skill).strip() and str(skill).strip().casefold() not in known
    })
    return rows, unmapped


def build_dim_company(
    observations: Iterable[dict[str, Any]],
    normalization_version: str = COMPANY_NORMALIZATION_VERSION,
    canonical_companies: Iterable[Optional[str]] = (),
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Group by normalized name only; never fuzzy-merge two similar names.

    `canonical_companies` must carry the canonical_company of EVERY effective entity.
    An entity whose observations all belong to earlier Silver releases has no
    representative observation here, so its company would otherwise be missing from
    this dimension and dim_job would break its foreign key on insert.
    """
    grouped: dict[str, dict[str, Any]] = {}
    for observation in observations:
        normalized = str(observation.get("company_name_normalized") or "").strip()
        display = str(observation.get("company_name") or "").strip()
        raw = str(observation.get("company_name_raw") or "").strip()
        bucket = grouped.setdefault(normalized, {
            "normalized": normalized,
            "display": Counter(),
            "raw": Counter(),
            "industry": Counter(),
            "size": Counter(),
        })
        if display:
            bucket["display"][display] += 1
        if raw:
            bucket["raw"][raw] += 1
        for field, column in (("company_industry", "industry"), ("company_size", "size")):
            value = observation.get(field)
            if value:
                bucket[column][str(value).strip()] += 1

    def _most_common(counter: Counter) -> Optional[str]:
        if not counter:
            return None
        # Deterministic: highest count, then alphabetical.
        return sorted(counter.items(), key=lambda item: (-item[1], item[0]))[0][0]

    rows = [{
        "company_key": unknown_company_key(normalization_version),
        "company_name_normalized": UNKNOWN,
        "company_display_name": "Unknown",
        "company_alias_raw": None,
        "company_industry": None,
        "company_size": None,
        "normalization_version": normalization_version,
        "is_unknown": True,
    }]
    ambiguous = []
    for normalized, bucket in sorted(grouped.items()):
        if not normalized:
            continue
        display = _most_common(bucket["display"]) or normalized.title()
        raw_alias = _most_common(bucket["raw"])
        rows.append({
            "company_key": company_key(normalized, normalization_version),
            "company_name_normalized": normalized,
            "company_display_name": display,
            "company_alias_raw": raw_alias,
            "company_industry": _most_common(bucket["industry"]),
            "company_size": _most_common(bucket["size"]),
            "normalization_version": normalization_version,
            "is_unknown": False,
        })
        distinct_raw = list(bucket["raw"])
        if len(distinct_raw) > 1:
            ambiguous.append({
                "company_name_normalized": normalized,
                "distinct_raw_spellings": sorted(distinct_raw),
            })

    # Closure: every entity company needs a dimension member, even when this release
    # contains no observation for it. The key uses the same expression as
    # resolve_company_key so both sides always agree.
    covered = {str(row["company_name_normalized"]) for row in rows}
    for canonical in canonical_companies:
        normalized = clean_company_name(canonical) if canonical else ""
        if not normalized or normalized in covered:
            continue
        covered.add(normalized)
        rows.append({
            "company_key": company_key(normalized, normalization_version),
            "company_name_normalized": normalized,
            "company_display_name": str(canonical).strip(),
            "company_alias_raw": None,
            "company_industry": None,
            "company_size": None,
            "normalization_version": normalization_version,
            "is_unknown": False,
        })
    return rows, ambiguous


def resolve_company_key(
    observation: Optional[dict[str, Any]],
    canonical_company: Optional[str] = None,
    normalization_version: str = COMPANY_NORMALIZATION_VERSION,
) -> str:
    """Company key for one job: prefer the normalized observation value, else clean the canonical."""
    normalized = str((observation or {}).get("company_name_normalized") or "").strip()
    if not normalized and canonical_company:
        normalized = clean_company_name(canonical_company)
    # Silver maps an unparseable employer to the literal marker "UNKNOWN"; without
    # this guard the marker becomes a real company dimension row that tops the
    # "companies with the most jobs" chart and is not flagged is_unknown.
    if normalized.casefold() in {"", "unknown", "n/a", "chưa xác định"}:
        return unknown_company_key(normalization_version)
    return company_key(normalized, normalization_version)


def city_bucket_of(observation: Optional[dict[str, Any]]) -> str:
    """GAP-9: five explicit groups; 'Khác' is a real province, UNKNOWN is missing data."""
    value = str((observation or {}).get("city_normalized") or "").strip()
    if not value:
        return UNKNOWN
    return value if value in CITY_AXIS else UNKNOWN

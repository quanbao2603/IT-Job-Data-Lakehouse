"""dim_job SCD2 planning: reuse Silver's canonical choice, never invent a second rule.

Silver already decides which observation represents an entity via
`src.etl.entity_lifecycle.choose_canonical`. Gold must apply the identical ordering,
otherwise dim_job and entities.canonical_* would disagree about the same job.
"""
from __future__ import annotations

from typing import Any, Iterable, Optional

from src.etl.entity_lifecycle import choose_canonical
from src.etl.transformers import COMPANY_NORMALIZATION_VERSION
from src.gold.contract import UNKNOWN
from src.gold.contract import city_bucket
from src.gold.load_dimensions import city_bucket_of, job_key, resolve_company_key
from src.gold.timeutil import parse_date, parse_utc

# Attributes whose change closes an SCD2 version and opens a new one.
TRACKED_ATTRIBUTES = (
    "canonical_title", "company_key", "canonical_location", "city_bucket",
    "job_domain", "job_level", "work_mode",
    "canonical_salary_min_vnd", "canonical_salary_max_vnd", "entity_status",
)


def pick_representative(
    observations: Iterable[dict[str, Any]],
    links_by_observation: Optional[dict[str, dict[str, Any]]] = None,
) -> tuple[Optional[dict[str, Any]], bool]:
    """Return the representative observation and whether it was a fallback choice.

    The fallback flag is raised when the chosen observation is not the one Silver marked
    as primary, which is expected: is_primary_source is time-scoped on immutable audit
    links, so it identifies the creator, not the best current record (GAP-5).
    """
    rows = [row for row in observations if row.get("quality_accepted", True)]
    if not rows:
        return None, False
    canonical = choose_canonical(rows)
    fallback = True
    if links_by_observation:
        link = links_by_observation.get(str(canonical.get("observation_id") or ""))
        if link is not None:
            fallback = not bool(link.get("is_primary_source", False))
    return canonical, fallback


def build_dim_job_row(
    entity: dict[str, Any],
    observations: list[dict[str, Any]],
    *,
    version_seq: int,
    release_id: str,
    valid_from_at: str,
    links_by_observation: Optional[dict[str, dict[str, Any]]] = None,
    normalization_version: str = COMPANY_NORMALIZATION_VERSION,
) -> dict[str, Any]:
    """One dim_job version row for one unified_job_id."""
    representative, fallback = pick_representative(observations, links_by_observation)
    unified_job_id = str(entity["unified_job_id"])
    company = resolve_company_key(
        representative,
        canonical_company=entity.get("canonical_company"),
        normalization_version=normalization_version,
    )
    # Entity attributes come from the registry-backed staging row (D-G06); the
    # representative observation supplies what no entity table carries (GAP-4).
    job_level = (
        entity.get("canonical_job_level")
        or (representative or {}).get("job_level")
    )
    work_mode = (
        entity.get("canonical_work_mode")
        or (representative or {}).get("work_mode")
    )
    job_domain = (representative or {}).get("job_domain")
    # An entity can outlive its observations (the current release may not include a
    # fresh observation for it). Falling back to the entity's canonical city keeps a
    # known location instead of overwriting it with UNKNOWN.
    city_value = city_bucket_of(representative)
    if city_value == UNKNOWN:
        canonical_city = str(entity.get("canonical_location") or "").strip()
        if canonical_city:
            city_value = city_bucket(canonical_city)
    return {
        "job_key": job_key(unified_job_id, version_seq),
        "unified_job_id": unified_job_id,
        "version_seq": version_seq,
        "canonical_title": str(entity.get("canonical_title") or UNKNOWN),
        "company_key": company,
        "canonical_location": entity.get("canonical_location"),
        "city_bucket": city_value,
        "job_domain": job_domain,
        "job_level": job_level,
        "work_mode": work_mode,
        "canonical_salary_min_vnd": entity.get("canonical_salary_min_vnd"),
        "canonical_salary_max_vnd": entity.get("canonical_salary_max_vnd"),
        "canonical_currency": entity.get("canonical_currency"),
        "canonical_posted_date": parse_date(entity.get("canonical_posted_date")),
        "entity_status": str(entity.get("entity_status") or UNKNOWN),
        "representative_observation_id": (representative or {}).get("observation_id"),
        "representative_fallback": bool(fallback),
        "valid_from_release": release_id,
        "valid_to_release": None,
        "valid_from_at": parse_utc(valid_from_at),
        "valid_to_at": None,
        "is_current": True,
    }


def plan_dim_job(
    entities: Iterable[dict[str, Any]],
    observations_by_job: dict[str, list[dict[str, Any]]],
    *,
    release_id: str,
    valid_from_at: str,
    current_versions: Optional[dict[str, dict[str, Any]]] = None,
    links_by_observation: Optional[dict[str, dict[str, Any]]] = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    """Plan dim_job upserts and version closures for one release.

    Returns (upsert_rows, close_rows, report). A first run has no current versions, so
    every entity opens version 1. History before Gold go-live is never backfilled.
    """
    current = current_versions or {}
    upserts: list[dict[str, Any]] = []
    closes: list[dict[str, Any]] = []
    report = {"new_version_count": 0, "unchanged_count": 0, "fallback_count": 0,
              "missing_observation_count": 0}
    for entity in entities:
        unified_job_id = str(entity["unified_job_id"])
        observations = observations_by_job.get(unified_job_id, [])
        if not observations:
            report["missing_observation_count"] += 1
        existing = current.get(unified_job_id)
        candidate = build_dim_job_row(
            entity, observations,
            version_seq=(int(existing["version_seq"]) + 1) if existing else 1,
            release_id=release_id, valid_from_at=valid_from_at,
            links_by_observation=links_by_observation,
        )
        if candidate["representative_fallback"]:
            report["fallback_count"] += 1
        if existing is None:
            upserts.append(candidate)
            report["new_version_count"] += 1
            continue
        changed = any(
            candidate.get(attribute) != existing.get(attribute) for attribute in TRACKED_ATTRIBUTES
        )
        if not changed:
            # Keep the open version; facts must join the version that was current that day.
            upserts.append({**existing, "job_key": existing["job_key"]})
            report["unchanged_count"] += 1
            continue
        closes.append({
            "job_key": existing["job_key"],
            "valid_to_release": release_id,
            "valid_to_at": parse_utc(valid_from_at),
            "is_current": False,
        })
        upserts.append(candidate)
        report["new_version_count"] += 1
    return upserts, closes, report

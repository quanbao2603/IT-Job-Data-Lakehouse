"""Gold bundle builder: pure orchestration from one sealed Silver release.

Nothing here touches PostgreSQL. The function returns a fully built bundle that the
release controller can persist, and that tests can assert on directly.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Callable, Iterable, Optional

from src.etl.transformers import normalization_versions
from src.gold.contract import EVIDENCE_WINDOW_DAYS
from src.gold.data_quality import run_checks
from src.gold.load_aggregates import (
    build_company_hiring_daily,
    build_market_daily,
    build_skill_pair_daily,
)
from src.gold.load_dim_job import plan_dim_job
from src.gold.load_dimensions import (
    build_dim_company,
    build_dim_date,
    build_dim_platform,
    build_dim_skill,
)
from src.gold.silver_release_reader import (
    SilverRelease,
    build_effective_entity,
    build_effective_link,
    build_source_observation,
    load_historical_release_data,
    latest_release,
    read_dataset,
    verify_release,
)
from src.gold.timeutil import report_date


@dataclass
class GoldBundle:
    """Everything one Gold release contains, before it is written anywhere."""

    gold_release_id: str
    silver_release_id: str
    snapshot_date: date
    cutoff_at: str
    manifest_fingerprint: str
    taxonomy_versions: dict[str, str]
    evidence_window_days: int
    effective_entity: list[dict[str, Any]] = field(default_factory=list)
    effective_link: list[dict[str, Any]] = field(default_factory=list)
    source_observation: list[dict[str, Any]] = field(default_factory=list)
    conflicts: list[dict[str, Any]] = field(default_factory=list)
    unmapped_skills: list[str] = field(default_factory=list)
    skill_outliers: list[str] = field(default_factory=list)
    dim_date: list[dict[str, Any]] = field(default_factory=list)
    dim_platform: list[dict[str, Any]] = field(default_factory=list)
    dim_company: list[dict[str, Any]] = field(default_factory=list)
    dim_skill: list[dict[str, Any]] = field(default_factory=list)
    dim_job: list[dict[str, Any]] = field(default_factory=list)
    dim_job_closes: list[dict[str, Any]] = field(default_factory=list)
    snapshot: list[dict[str, Any]] = field(default_factory=list)
    platform: list[dict[str, Any]] = field(default_factory=list)
    skill: list[dict[str, Any]] = field(default_factory=list)
    change_event: list[dict[str, Any]] = field(default_factory=list)
    market: list[dict[str, Any]] = field(default_factory=list)
    company: list[dict[str, Any]] = field(default_factory=list)
    skill_pair: list[dict[str, Any]] = field(default_factory=list)
    reports: dict[str, Any] = field(default_factory=dict)
    # DQ-23 asserts this: the open-job metric must come from canonical entity_status.
    open_job_source: str = "entity_status"

    def row_counts(self) -> dict[str, int]:
        return {
            "effective_entity": len(self.effective_entity),
            "effective_link": len(self.effective_link),
            "source_observation": len(self.source_observation),
            "dim_date": len(self.dim_date),
            "dim_platform": len(self.dim_platform),
            "dim_company": len(self.dim_company),
            "dim_skill": len(self.dim_skill),
            "dim_job": len(self.dim_job),
            "fact_job_daily_snapshot": len(self.snapshot),
            "fact_job_platform_daily": len(self.platform),
            "fact_job_skill_daily": len(self.skill),
            "fact_job_change_event": len(self.change_event),
            "fact_job_market_daily": len(self.market),
            "fact_company_hiring_daily": len(self.company),
            "fact_skill_pair_daily": len(self.skill_pair),
            "conflicts": len(self.conflicts),
        }


def _collect_dates(bundle: GoldBundle) -> tuple[date, date]:
    """Every date any fact references, so dim_date always satisfies the foreign keys."""
    dates: list[date] = [bundle.snapshot_date]
    for row in bundle.dim_job:
        if row.get("canonical_posted_date"):
            dates.append(row["canonical_posted_date"])
    for row in bundle.change_event:
        dates.append(row["event_date"])
    return min(dates), max(dates)


def build_bundle(
    *,
    root: Optional[str] = None,
    release: Optional[SilverRelease] = None,
    entities: Optional[Iterable[dict[str, Any]]] = None,
    links: Optional[Iterable[dict[str, Any]]] = None,
    observations: Optional[Iterable[dict[str, Any]]] = None,
    registry_entities: Optional[Iterable[dict[str, Any]]] = None,
    historical_observations: Optional[Iterable[dict[str, Any]]] = None,
    gold_release_id: Optional[str] = None,
    previous_snapshot_ids: Optional[Iterable[str]] = None,
    current_dim_job: Optional[dict[str, dict[str, Any]]] = None,
    evidence_window_days: int = EVIDENCE_WINDOW_DAYS,
    object_reader: Optional[Callable[[str], Iterable[dict[str, Any]]]] = None,
    cutoff_at: Optional[str] = None,
    silver_release_id: Optional[str] = None,
    manifest_fingerprint: str = "",
    taxonomy_versions: Optional[dict[str, str]] = None,
) -> GoldBundle:
    """Build one Gold release from one Silver release.

    Either pass `root`/`release` to read sealed Silver output, or pass `entities`,
    `links` and `observations` together with an explicit `cutoff_at` and
    `silver_release_id` for tests and backfills.
    """
    conflicts: list[dict[str, Any]] = []
    reports: dict[str, Any] = {}

    if release is None and root is not None:
        release = latest_release(root)
        if release is None:
            raise FileNotFoundError(f"No consumable Silver release found under {root}/releases")

    release_history_observations: list[dict[str, Any]] = []
    historical_links: list[dict[str, Any]] = []
    late_observation_ids: set[str] = set()
    historical_release_count = 0
    if release is not None:
        if not release.is_consumable:
            raise ValueError(
                f"Silver release {release.release_id} is not consumable "
                f"(status={release.status}, partial_run={release.partial_run}, "
                f"failed_platforms={list(release.failed_platforms)})"
            )
        problems = verify_release(release, root)
        if problems:
            raise ValueError(f"Silver release verification failed: {problems[:5]}")
        entities = read_dataset(release, root, "entities", object_reader=object_reader)
        links = read_dataset(release, root, "entity_links", object_reader=object_reader)
        observations = read_dataset(release, root, "observations", object_reader=object_reader)
        (
            release_history_observations,
            historical_links,
            late_observation_ids,
            historical_release_count,
        ) = load_historical_release_data(root, release, observations)
        resolved_cutoff = release.snapshot_cutoff_at
        resolved_silver_release = release.release_id
        resolved_fingerprint = release.manifest_fingerprint
        resolved_taxonomy = dict(release.taxonomy_versions)
        reports["release_verification"] = "PASS"
    else:
        if not cutoff_at or not silver_release_id:
            raise ValueError(
                "build_bundle needs either a Silver release/root, or both an explicit "
                "cutoff_at and silver_release_id"
            )
        resolved_cutoff = cutoff_at
        resolved_silver_release = silver_release_id
        resolved_fingerprint = manifest_fingerprint
        resolved_taxonomy = dict(taxonomy_versions or normalization_versions())
        reports["release_verification"] = "INJECTED"

    snapshot_date = report_date(resolved_cutoff)
    active_release_id = gold_release_id or f"gold_{snapshot_date:%Y%m%d}_{resolved_silver_release}"

    entity_rows, entity_conflicts = build_effective_entity(
        entities or (), object_key="entities", registry_entities=registry_entities
    )
    conflicts.extend(entity_conflicts)
    link_rows, link_conflicts = build_effective_link(
        [*(historical_links or ()), *(links or ())], cutoff=resolved_cutoff
    )
    conflicts.extend(link_conflicts)
    observation_rows, observation_conflicts = build_source_observation(
        [*(release_history_observations or ()), *(observations or ())],
        cutoff=resolved_cutoff,
        effective_links=link_rows,
    )
    conflicts.extend(observation_conflicts)

    links_by_observation = {row["observation_id"]: row for row in link_rows}
    observations_by_job: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in observation_rows:
        if row.get("accepted_for_gold") and row.get("unified_job_id"):
            observations_by_job[str(row["unified_job_id"])].append(row)

    taxonomy_version = resolved_taxonomy.get("skill_taxonomy_version", "skill-taxonomy-v1")
    observed_skills = {
        str(skill).strip().casefold()
        for row in observation_rows
        for skill in (row.get("skills_standardized") or [])
    }
    dim_skill, unmapped_skills = build_dim_skill(observed_skills, taxonomy_version)
    dim_company, company_ambiguity = build_dim_company(
        observation_rows,
        canonical_companies=[row.get("canonical_company") for row in entity_rows],
    )
    observed_platforms = {str(row.get("platform") or "").casefold() for row in observation_rows}
    dim_platform = build_dim_platform(observed_platforms)

    dim_job_upserts, dim_job_closes, dim_job_report = plan_dim_job(
        entity_rows, observations_by_job,
        release_id=active_release_id,
        valid_from_at=resolved_cutoff,
        current_versions=current_dim_job,
        links_by_observation=links_by_observation,
    )
    dim_job_by_entity = {row["unified_job_id"]: row for row in dim_job_upserts}

    bundle = GoldBundle(
        gold_release_id=active_release_id,
        silver_release_id=resolved_silver_release,
        snapshot_date=snapshot_date,
        cutoff_at=resolved_cutoff,
        manifest_fingerprint=resolved_fingerprint,
        taxonomy_versions=resolved_taxonomy,
        evidence_window_days=evidence_window_days,
        effective_entity=entity_rows,
        effective_link=link_rows,
        source_observation=observation_rows,
        conflicts=conflicts,
        unmapped_skills=unmapped_skills,
        dim_platform=dim_platform,
        dim_company=dim_company,
        dim_skill=dim_skill,
        dim_job=dim_job_upserts,
        dim_job_closes=dim_job_closes,
        reports=reports,
    )

    from src.gold.load_facts import (
        build_change_events,
        build_platform_facts,
        build_skill_facts,
        build_snapshot_facts,
    )

    bundle.snapshot = build_snapshot_facts(
        entity_rows, dim_job_by_entity,
        gold_release_id=active_release_id, silver_release_id=resolved_silver_release,
        snapshot_date=snapshot_date, cutoff_at=resolved_cutoff,
        previous_snapshot_ids=previous_snapshot_ids,
    )
    snapshot_job_ids = {row["unified_job_id"] for row in bundle.snapshot}
    dim_job_by_key = {row["job_key"]: row for row in dim_job_upserts}

    bundle.platform = build_platform_facts(
        observation_rows, dim_job_by_entity, snapshot_job_ids,
        gold_release_id=active_release_id, silver_release_id=resolved_silver_release,
        snapshot_date=snapshot_date, cutoff_at=resolved_cutoff,
        window_days=evidence_window_days,
    )
    bundle.skill, skill_unmapped = build_skill_facts(
        observation_rows, dim_job_by_entity, snapshot_job_ids,
        gold_release_id=active_release_id, silver_release_id=resolved_silver_release,
        snapshot_date=snapshot_date, cutoff_at=resolved_cutoff,
        taxonomy_version=taxonomy_version, window_days=evidence_window_days,
    )
    bundle.unmapped_skills = sorted(set(unmapped_skills) | set(skill_unmapped))

    event_observations = list(observation_rows)
    if historical_observations:
        seen = {row["observation_id"] for row in event_observations}
        for row in historical_observations:
            if row.get("observation_id") not in seen:
                event_observations.append({**row, "accepted_for_gold": True})
        reports["change_event_scope"] = "release_plus_history"
    else:
        reports["change_event_scope"] = "release_only"
    reports["historical_release_count"] = historical_release_count
    reports["late_observation_count"] = len(late_observation_ids)
    bundle.change_event = build_change_events(
        event_observations, dim_job_by_entity,
        gold_release_id=active_release_id, silver_release_id=resolved_silver_release,
        cutoff_at=resolved_cutoff, late_observation_ids=late_observation_ids,
    )

    bundle.market = build_market_daily(
        bundle.snapshot, dim_job_by_key,
        gold_release_id=active_release_id, silver_release_id=resolved_silver_release,
        snapshot_date=snapshot_date, cutoff_at=resolved_cutoff,
    )
    bundle.company = build_company_hiring_daily(
        bundle.snapshot, dim_job_by_key,
        gold_release_id=active_release_id, silver_release_id=resolved_silver_release,
        snapshot_date=snapshot_date, cutoff_at=resolved_cutoff,
    )
    bundle.skill_pair, bundle.skill_outliers = build_skill_pair_daily(
        bundle.skill,
        gold_release_id=active_release_id, silver_release_id=resolved_silver_release,
        snapshot_date=snapshot_date, cutoff_at=resolved_cutoff, taxonomy_version=taxonomy_version,
    )

    start, end = _collect_dates(bundle)
    bundle.dim_date = build_dim_date(start, end + timedelta(days=1))
    bundle.reports["dim_job"] = dim_job_report
    bundle.reports["company_ambiguity"] = company_ambiguity
    bundle.reports["row_counts"] = bundle.row_counts()
    return bundle


def build_and_check(**kwargs: Any) -> tuple[GoldBundle, list[dict[str, Any]]]:
    """Convenience wrapper used by the DAG: build the bundle, then run the DQ gate."""
    bundle = build_bundle(**kwargs)
    return bundle, run_checks(bundle)

"""Deterministic MVP entity matching rules."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from rapidfuzz import fuzz

from src.etl.transformers import generate_unified_job_id, normalize_text
from src.storage.entity_registry import EntityRegistry


@dataclass(frozen=True)
class MatchResult:
    unified_job_id: Optional[str]
    candidate_entity_id: Optional[str]
    match_method: str
    match_score: Optional[float]
    match_status: str
    review_reason: Optional[str] = None


class EntityResolver:
    def __init__(self, registry: EntityRegistry, resolution_version: str = "v1") -> None:
        self.registry = registry
        self.resolution_version = resolution_version

    @staticmethod
    def fingerprint(observation: dict[str, Any]) -> str:
        return "|".join((
            str(observation.get("company_name_normalized") or ""),
            str(observation.get("job_title_normalized") or ""),
            str(observation.get("city_normalized") or ""),
        ))

    @staticmethod
    def _salary_overlap(left: dict[str, Any], right: Any) -> float:
        left_min, left_max = left.get("salary_min_vnd"), left.get("salary_max_vnd")
        right_min = right["canonical_salary_min_vnd"] if "canonical_salary_min_vnd" in right.keys() else None
        right_max = right["canonical_salary_max_vnd"] if "canonical_salary_max_vnd" in right.keys() else None
        if left_min is None or left_max is None or right_min is None or right_max is None:
            return 0.5
        return 1.0 if max(left_min, right_min) <= min(left_max, right_max) else 0.0

    def resolve(self, observation: dict[str, Any], observed_at: str) -> MatchResult:
        platform = str(observation.get("platform") or "").strip().lower()
        source_job_id = str(observation.get("source_job_id") or "").strip()
        if not platform or not source_job_id:
            return MatchResult(None, None, "NONE", None, "UNMATCHED", "Missing platform or source_job_id")

        existing = self.registry.get_source_job(platform, source_job_id)
        if existing:
            self.registry.touch_source_job(platform, source_job_id, observed_at)
            return MatchResult(existing["unified_job_id"], None, "SOURCE_JOB_ID", 1.0, "AUTO_MATCH")

        fingerprint = self.fingerprint(observation)
        candidates = self.registry.find_candidates(fingerprint)
        if candidates:
            candidate = candidates[0]
            return MatchResult(candidate["unified_job_id"], None, "EXACT_NORMALIZED_IDENTITY", 0.98, "AUTO_MATCH")

        # MVP candidate search over the bounded registry; later ETL batches can index this lookup.
        all_candidates = self.registry.list_entities()
        if not all_candidates:
            identity_seed = f"{platform}|{source_job_id}|{observation['observation_id']}"
            unified_id = generate_unified_job_id(identity_seed)
            self.registry.create_entity(
                unified_job_id=unified_id, platform=platform, source_job_id=source_job_id,
                fingerprint=fingerprint, title=observation["job_title"],
                company=observation["company_name"], city=observation.get("city"), observed_at=observed_at,
                resolution_version=self.resolution_version,
            )
            return MatchResult(unified_id, None, "NONE", None, "DISTINCT_ENTITY")

        candidate = all_candidates[0]
        title_score = fuzz.token_set_ratio(observation.get("job_title_normalized", ""), normalize_text(candidate["canonical_title"])) / 100
        company_score = fuzz.token_sort_ratio(observation.get("company_name_normalized", ""), normalize_text(candidate["canonical_company"])) / 100
        location_score = fuzz.ratio(observation.get("city_normalized", ""), normalize_text(candidate["canonical_city"] or "")) / 100
        score = round(0.40 * title_score + 0.25 * company_score + 0.15 * location_score + 0.20 * 0.5, 6)
        if score >= 0.90:
            return MatchResult(candidate["unified_job_id"], None, "FUZZY_TITLE_COMPANY_LOCATION", score, "AUTO_MATCH")
        if score >= 0.75:
            return MatchResult(None, candidate["unified_job_id"], "FUZZY_TITLE_COMPANY_LOCATION", score, "REVIEW_REQUIRED", "Fuzzy score requires human review")
        identity_seed = f"{platform}|{source_job_id}|{observation['observation_id']}"
        unified_id = generate_unified_job_id(identity_seed)
        self.registry.create_entity(
            unified_job_id=unified_id, platform=platform, source_job_id=source_job_id,
            fingerprint=fingerprint, title=observation["job_title"],
            company=observation["company_name"], city=observation.get("city"), observed_at=observed_at,
            resolution_version=self.resolution_version,
        )
        return MatchResult(unified_id, None, "FUZZY_TITLE_COMPANY_LOCATION", score, "DISTINCT_ENTITY")

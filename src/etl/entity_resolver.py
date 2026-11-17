"""Deterministic Silver entity matching with guarded candidate resolution."""
from __future__ import annotations

import csv
import os
from dataclasses import dataclass
from typing import Any, Optional

from rapidfuzz import fuzz

from src.etl.transformers import generate_unified_job_id, make_canonical_url, normalize_text
from src.storage.entity_registry import EntityRegistry, utc_now

REVIEW_REASON = "Fuzzy score in [0.75, 0.90) requires human review"
SEVERE_LEVEL_PAIRS = {
    frozenset(("intern", "senior")),
    frozenset(("intern", "lead")),
    frozenset(("intern", "director")),
    frozenset(("fresher", "senior")),
    frozenset(("fresher", "lead")),
    frozenset(("fresher", "director")),
}
WORK_MODE_CONFLICTS = {frozenset(("full-time", "part-time")), frozenset(("full time", "part time"))}
GENERIC_TITLES = {"developer", "engineer", "manager", "software developer", "software engineer"}


@dataclass(frozen=True)
class MatchResult:
    unified_job_id: Optional[str]
    candidate_entity_id: Optional[str]
    match_method: str
    match_score: Optional[float]
    match_status: str
    review_reason: Optional[str] = None


class EntityResolver:
    def __init__(
        self,
        registry: EntityRegistry,
        resolution_version: str = "v1",
        pending_review_path: str = "data/operational/pending_review.csv",
    ) -> None:
        self.registry = registry
        self.resolution_version = resolution_version
        self.pending_review_path = pending_review_path

    @staticmethod
    def fingerprint(observation: dict[str, Any]) -> str:
        return "|".join(
            (
                str(observation.get("company_name_normalized") or ""),
                str(observation.get("job_title_normalized") or ""),
                str(observation.get("city_normalized") or ""),
            )
        )

    @staticmethod
    def _salary_overlap(left: dict[str, Any], right: Any) -> float:
        left_min, left_max = left.get("salary_min_vnd"), left.get("salary_max_vnd")
        right_min = right["canonical_salary_min_vnd"] if "canonical_salary_min_vnd" in right.keys() else None
        right_max = right["canonical_salary_max_vnd"] if "canonical_salary_max_vnd" in right.keys() else None
        if left_min is None or left_max is None or right_min is None or right_max is None:
            return 0.5
        return 1.0 if max(left_min, right_min) <= min(left_max, right_max) else 0.0

    @staticmethod
    def _level_conflict(left: Any, right: Any) -> bool:
        left_text = normalize_text(left)
        right_text = normalize_text(right)
        if not left_text or not right_text or left_text == right_text:
            return False
        return frozenset((left_text, right_text)) in SEVERE_LEVEL_PAIRS

    @classmethod
    def _guard_allows(cls, observation: dict[str, Any], candidate: Any) -> bool:
        if cls._level_conflict(observation.get("job_level"), candidate["canonical_job_level"]):
            return False
        left_mode = normalize_text(observation.get("work_mode"))
        right_mode = normalize_text(candidate["canonical_work_mode"])
        if left_mode and right_mode and frozenset((left_mode, right_mode)) in WORK_MODE_CONFLICTS:
            return False
        company = normalize_text(observation.get("company_name_normalized"))
        candidate_company = normalize_text(candidate["canonical_company"])
        if company and candidate_company and fuzz.token_sort_ratio(company, candidate_company) < 55:
            return False
        location = normalize_text(observation.get("city_normalized"))
        candidate_location = normalize_text(candidate["canonical_city"])
        remote_left = "remote" in left_mode
        remote_right = "remote" in right_mode
        if location and candidate_location and location != candidate_location and not (remote_left or remote_right):
            return False
        left_salary = observation.get("salary_min_vnd"), observation.get("salary_max_vnd")
        right_salary = candidate["canonical_salary_min_vnd"], candidate["canonical_salary_max_vnd"]
        if all(value is not None for value in left_salary + right_salary) and cls._salary_overlap(observation, candidate) == 0.0:
            return False
        left_title = normalize_text(observation.get("job_title_normalized"))
        right_title = normalize_text(candidate["canonical_title"])
        if left_title in GENERIC_TITLES and right_title in GENERIC_TITLES and left_title != right_title:
            return False
        return True

    @staticmethod
    def _score(observation: dict[str, Any], candidate: Any) -> float:
        title_score = fuzz.token_set_ratio(
            normalize_text(observation.get("job_title_normalized")),
            normalize_text(candidate["canonical_title"]),
        ) / 100
        company_score = fuzz.token_sort_ratio(
            normalize_text(observation.get("company_name_normalized")),
            normalize_text(candidate["canonical_company"]),
        ) / 100
        location_score = fuzz.ratio(
            normalize_text(observation.get("city_normalized")),
            normalize_text(candidate["canonical_city"]),
        ) / 100
        description_score = fuzz.token_set_ratio(
            normalize_text(observation.get("description_normalized") or observation.get("job_description_clean")),
            normalize_text(candidate["canonical_description"]),
        ) / 100
        salary_score = EntityResolver._salary_overlap(observation, candidate)
        return round(
            0.40 * title_score
            + 0.25 * company_score
            + 0.15 * location_score
            + 0.10 * salary_score
            + 0.10 * description_score,
            6,
        )

    def _write_pending_review(self, observation: dict[str, Any], result: MatchResult) -> None:
        directory = os.path.dirname(self.pending_review_path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        fields = (
            "observation_id", "platform", "source_job_id", "candidate_entity_id",
            "match_score", "match_status", "review_reason",
        )
        existing_ids: set[str] = set()
        if os.path.exists(self.pending_review_path):
            with open(self.pending_review_path, newline="", encoding="utf-8") as stream:
                existing_ids = {row.get("observation_id", "") for row in csv.DictReader(stream)}
        observation_id = str(observation.get("observation_id") or "")
        if observation_id in existing_ids:
            return
        exists = os.path.exists(self.pending_review_path)
        with open(self.pending_review_path, "a", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=fields)
            if not exists:
                writer.writeheader()
            writer.writerow(
                {
                    "observation_id": observation_id,
                    "platform": observation.get("platform"),
                    "source_job_id": observation.get("source_job_id"),
                    "candidate_entity_id": result.candidate_entity_id,
                    "match_score": result.match_score,
                    "match_status": result.match_status,
                    "review_reason": result.review_reason,
                }
            )

    def _record_observation(
        self,
        observation: dict[str, Any],
        unified_job_id: Optional[str],
        canonical_url: Optional[str],
    ) -> None:
        self.registry.record_observation(
            observation_id=observation["observation_id"],
            platform=str(observation["platform"]).strip().lower(),
            source_job_id=str(observation["source_job_id"]).strip(),
            content_hash=str(observation.get("content_hash") or "unknown"),
            unified_job_id=unified_job_id,
            resolution_version=self.resolution_version,
            canonical_url=canonical_url,
            observed_at=observation.get("observed_at"),
            posted_date=observation.get("posted_date"),
            expired_date=observation.get("expired_date"),
            job_description_clean=observation.get("job_description_clean"),
            salary_min_vnd=observation.get("salary_min_vnd"),
            salary_max_vnd=observation.get("salary_max_vnd"),
            salary_currency=observation.get("salary_currency"),
            city_normalized=observation.get("city_normalized"),
            quality_accepted=True,
        )

    def _record_link(self, observation: dict[str, Any], result: MatchResult, *, previous_unified_job_id: Optional[str] = None) -> None:
        self.registry.record_link(
            observation_id=observation["observation_id"],
            resolution_version=self.resolution_version,
            unified_job_id=result.unified_job_id,
            candidate_entity_id=result.candidate_entity_id,
            match_method=result.match_method,
            match_score=result.match_score,
            match_status=result.match_status,
            review_reason=result.review_reason,
            previous_unified_job_id=previous_unified_job_id,
        )

    def _create_distinct(
        self,
        observation: dict[str, Any],
        observed_at: str,
        fingerprint: str,
        canonical_url: Optional[str],
        method: str,
        score: Optional[float],
        previous_unified_id: Optional[str] = None,
    ) -> MatchResult:
        platform = str(observation["platform"]).strip().lower()
        source_job_id = str(observation["source_job_id"]).strip()
        unified_id = generate_unified_job_id(
            f"{platform}|{source_job_id}|{observation['observation_id']}"
        )
        unified_id, created = self.registry.get_or_create_entity(
            unified_job_id=unified_id,
            platform=platform,
            source_job_id=source_job_id,
            fingerprint=fingerprint,
            title=observation["job_title"],
            company=observation["company_name"],
            city=observation.get("city"),
            observed_at=observed_at,
            resolution_version=self.resolution_version,
            job_level=observation.get("job_level"),
            work_mode=observation.get("work_mode"),
            salary_min_vnd=observation.get("salary_min_vnd"),
            salary_max_vnd=observation.get("salary_max_vnd"),
            description=observation.get("job_description_clean") or observation.get("description_normalized"),
            content_hash=observation.get("content_hash"),
        )
        result = MatchResult(unified_id, None, method, score, "DISTINCT_ENTITY")
        self._record_observation(observation, unified_id, canonical_url)
        self._record_link(observation, result, previous_unified_job_id=previous_unified_id)
        return result

    def resolve(self, observation: dict[str, Any], observed_at: str) -> MatchResult:
        platform = str(observation.get("platform") or "").strip().lower()
        source_job_id = str(observation.get("source_job_id") or "").strip()
        required = ("observation_id", "job_title", "company_name")
        if not platform or not source_job_id:
            result = MatchResult(None, None, "NONE", None, "UNMATCHED", "Missing platform or source_job_id")
            if observation.get("observation_id"):
                self._record_observation(observation, None, None)
                self._record_link(observation, result)
            return result
        if any(not str(observation.get(field) or "").strip() for field in required):
            result = MatchResult(None, None, "NONE", None, "UNMATCHED", "Missing mandatory resolver input")
            self._record_observation(observation, None, None)
            self._record_link(observation, result)
            return result

        prior_links = self.registry.list_links(observation["observation_id"])
        previous_version_id = next(
            (row["unified_job_id"] for row in reversed(prior_links) if row["unified_job_id"]),
            None,
        )
        existing = self.registry.get_source_job(platform, source_job_id)
        if existing:
            previous_id = existing["unified_job_id"]
            prior_entity_links = self.registry.list_entity_links(previous_id)
            previous_for_audit = previous_id if prior_entity_links and any(
                row["resolution_version"] != self.resolution_version for row in prior_entity_links
            ) else None
            unified_id = self.registry.update_entity_observation(
                platform=platform,
                source_job_id=source_job_id,
                observed_at=observed_at,
                content_hash=str(observation.get("content_hash") or "unknown"),
                title=observation.get("job_title"),
                job_level=observation.get("job_level"),
                work_mode=observation.get("work_mode"),
                salary_min_vnd=observation.get("salary_min_vnd"),
                salary_max_vnd=observation.get("salary_max_vnd"),
                description=observation.get("job_description_clean") or observation.get("description_normalized"),
            )
            unified_id = unified_id or previous_id
            canonical_url = make_canonical_url(observation.get("canonical_url") or observation.get("source_url"))
            self._record_observation(observation, unified_id, canonical_url)
            result = MatchResult(unified_id, None, "SOURCE_JOB_ID", 1.0, "AUTO_MATCH")
            self._record_link(observation, result, previous_unified_job_id=previous_for_audit)
            return result

        previous_link = self.registry.get_link(observation["observation_id"], self.resolution_version)
        previous_unified_id = previous_link["unified_job_id"] if previous_link else None

        fingerprint = self.fingerprint(observation)
        canonical_url = make_canonical_url(observation.get("canonical_url") or observation.get("source_url"))
        if canonical_url:
            candidate = self.registry.find_by_canonical_url(canonical_url)
            if candidate:
                self.registry.bind_source_job(platform, source_job_id, candidate["unified_job_id"], observed_at)
                self._record_observation(observation, candidate["unified_job_id"], canonical_url)
                result = MatchResult(candidate["unified_job_id"], None, "CANONICAL_URL", 1.0, "AUTO_MATCH")
                self._record_link(observation, result, previous_unified_job_id=previous_unified_id)
                return result

        candidates = self.registry.find_candidates(fingerprint) if all(fingerprint.split("|")) else []
        for candidate in candidates:
            if self._guard_allows(observation, candidate):
                self.registry.bind_source_job(platform, source_job_id, candidate["unified_job_id"], observed_at)
                self._record_observation(observation, candidate["unified_job_id"], canonical_url)
                result = MatchResult(candidate["unified_job_id"], None, "EXACT_NORMALIZED_IDENTITY", 0.98, "AUTO_MATCH")
                self._record_link(observation, result, previous_unified_job_id=previous_unified_id)
                return result

        eligible = [candidate for candidate in self.registry.list_entities() if self._guard_allows(observation, candidate)]
        if not eligible:
            return self._create_distinct(observation, observed_at, fingerprint, canonical_url, "NONE", None)
        scored = sorted(
            ((self._score(observation, candidate), candidate) for candidate in eligible),
            key=lambda item: (-item[0], item[1]["unified_job_id"]),
        )
        score, candidate = scored[0]
        if score >= 0.90:
            self.registry.bind_source_job(platform, source_job_id, candidate["unified_job_id"], observed_at)
            self._record_observation(observation, candidate["unified_job_id"], canonical_url)
            result = MatchResult(candidate["unified_job_id"], None, "FUZZY_TITLE_COMPANY_LOCATION", score, "AUTO_MATCH")
            self._record_link(observation, result, previous_unified_job_id=previous_unified_id)
            return result
        if score >= 0.75:
            result = MatchResult(None, candidate["unified_job_id"], "FUZZY_TITLE_COMPANY_LOCATION", score, "REVIEW_REQUIRED", REVIEW_REASON)
            self._record_observation(observation, None, canonical_url)
            self._record_link(observation, result, previous_unified_job_id=previous_unified_id)
            self._write_pending_review(observation, result)
            return result
        return self._create_distinct(
            observation,
            observed_at,
            fingerprint,
            canonical_url,
            "FUZZY_TITLE_COMPANY_LOCATION",
            score,
            previous_unified_id,
        )

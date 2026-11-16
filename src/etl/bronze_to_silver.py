"""Bounded Bronze-envelope to local Silver pipeline.

The pipeline is deliberately small: Bronze reading remains owned by AtomicWriter;
this module owns validation, resolution, lifecycle metadata, and Silver outputs.
"""
from __future__ import annotations

import hashlib
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

from src.etl.entity_lifecycle import choose_canonical
from src.etl.quality_gate import validate_entity, validate_link

SUPPORTED_PLATFORMS = {
    "careerviet", "upzi", "vietnamdevs", "vieclam24h", "devwork",
    "itviec", "topcv", "topdev", "vietnamworks",
}
from typing import Any, Iterable, Optional

from src.etl.entity_resolver import EntityResolver, MatchResult
from src.etl.quality_gate import validate_observation
from src.etl.silver_writer import SilverWriter
from src.etl.transformers import canonicalize_bronze_record
from src.schemas.silver_schema import SILVER_OBSERVATIONS_SCHEMA
from src.storage.entity_registry import EntityRegistry, utc_now
from src.storage.metadata_envelope import MetadataEnvelope


class BronzeToSilverPipeline:
    """Process verified Bronze envelopes in bounded batches without deleting Bronze."""

    def __init__(
        self,
        registry: EntityRegistry,
        writer: SilverWriter,
        run_id: str,
        resolution_version: str = "v1",
        pending_review_path: str = "data/operational/pending_review.csv",
        max_records_per_batch: int = 5_000,
    ) -> None:
        self.registry = registry
        self.writer = writer
        self.run_id = run_id
        self.resolution_version = resolution_version
        self.max_records_per_batch = max_records_per_batch
        self.resolver = EntityResolver(
            registry,
            resolution_version=resolution_version,
            pending_review_path=pending_review_path,
        )

    @staticmethod
    def _blank_observation(envelope: dict[str, Any]) -> dict[str, Any]:
        """Build a schema-shaped quarantine row even when canonicalization fails."""
        values: dict[str, Any] = {}
        for field in SILVER_OBSERVATIONS_SCHEMA:
            if field.nullable:
                values[field.name] = None
            elif field.type == "bool":
                values[field.name] = False
            elif field.type == "int64":
                values[field.name] = 1
            else:
                values[field.name] = ""
        metadata = envelope.get("_metadata") or {}
        payload = envelope.get("raw_payload") or {}
        observed_at = str(
            metadata.get("ingested_at")
            or payload.get("scraped_at")
            or "1970-01-01T00:00:00Z"
        )
        values.update(
            {
                "platform": str(payload.get("platform") or metadata.get("source") or "unknown"),
                "source_job_id": str(payload.get("job_id") or payload.get("source_job_id") or ""),
                "observation_id": hashlib.sha256(
                    json.dumps(envelope, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
                ).hexdigest(),
                "content_hash": payload.get("content_hash") or "quarantine",
                "crawled_at": str(payload.get("scraped_at") or observed_at),
                "observed_at": observed_at,
                "ingested_at": observed_at,
                "silver_updated_at": observed_at,
                "job_title": str(payload.get("job_title") or ""),
                "company_name": str(payload.get("company_name") or ""),
                "company_name_raw": payload.get("company_name"),
                "is_active": True,
                "is_negotiable": False,
                "vacancies_count": 1,
                "platform_specific_attributes": json.dumps(payload, ensure_ascii=False, sort_keys=True),
            }
        )
        return values

    @staticmethod
    def _partition(timestamp: str, *, platform: Optional[str] = None) -> str:
        text = str(timestamp or "")
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError(f"Invalid Silver partition timestamp: {timestamp}") from exc
        prefix = f"platform={platform}/" if platform else ""
        return f"{prefix}year={parsed:%Y}/month={parsed:%m}/day={parsed:%d}"

    def _quarantine(self, record: dict[str, Any], reason: str) -> dict[str, Any]:
        result = dict(record)
        result.update(
            {
                "rejection_reason": reason,
                "quarantined_at": utc_now(),
                "audit_run_id": self.run_id,
                "raw_payload_snippet": json.dumps(record, ensure_ascii=False, sort_keys=True)[:300],
            }
        )
        return result

    def _entity_row(self, entity_id: str, record: dict[str, Any]) -> dict[str, Any]:
        entity = self.registry.get_entity(entity_id)
        if entity is None:
            raise ValueError(f"Resolved entity not found in registry: {entity_id}")
        observations = self.registry.list_observations(entity_id)
        platforms = {row["platform"] for row in observations}
        canonical = choose_canonical(
            [record] + [dict(row) for row in observations if row["observation_id"] != record["observation_id"]]
        )
        canonical_posted_date = canonical.get("posted_date") or record.get("posted_date") or entity["first_seen_at"][:10]
        return {
            "unified_job_id": entity_id,
            "canonical_title": entity["canonical_title"],
            "canonical_company": entity["canonical_company"],
            "canonical_location": entity["canonical_city"],
            "canonical_salary_min_vnd": entity["canonical_salary_min_vnd"],
            "canonical_salary_max_vnd": entity["canonical_salary_max_vnd"],
            "canonical_currency": canonical.get("salary_currency") or record.get("salary_currency"),
            "canonical_description": entity["canonical_description"] or canonical.get("job_description_clean") or record.get("job_description_clean"),
            "canonical_posted_date": canonical_posted_date,
            "source_count": len(platforms),
            "observation_count": len(observations),
            "first_seen_at": entity["first_seen_at"],
            "last_seen_at": entity["last_seen_at"],
            "entity_status": entity["entity_status"],
            "canonical_updated_at": entity["updated_at"],
            "resolution_version": entity["resolution_version"],
        }

    def _link_row(self, record: dict[str, Any], result: MatchResult) -> dict[str, Any]:
        return {
            "unified_job_id": result.unified_job_id,
            "candidate_entity_id": result.candidate_entity_id,
            "observation_id": record["observation_id"],
            "platform": record["platform"],
            "source_job_id": record["source_job_id"],
            "source_url": record.get("source_url"),
            "canonical_url": record.get("canonical_url"),
            "match_method": result.match_method,
            "match_score": result.match_score,
            "match_status": result.match_status,
            "matched_at": utc_now(),
            "matcher_version": self.resolution_version,
            "review_reason": result.review_reason,
            "previous_unified_job_id": None,
            "resolution_version": self.resolution_version,
            "is_primary_source": result.match_status == "DISTINCT_ENTITY",
        }

    def process_bronze_objects(
        self,
        manifest_reader: Any,
        object_reader: Any,
        *,
        platforms: Optional[set[str]] = None,
    ) -> dict[str, Any]:
        """Read active Bronze manifest objects incrementally in bounded batches."""
        selected = set(platforms or SUPPORTED_PLATFORMS)
        unknown = selected - SUPPORTED_PLATFORMS
        if unknown:
            raise ValueError(f"Unknown Silver platforms: {sorted(unknown)}")
        aggregate = Counter()
        observed_by_platform: dict[str, set[str]] = {}
        last_report: dict[str, Any] = {
            "run_id": self.run_id,
            "pipeline": "bronze_to_silver_v1",
            "execution_mode": "batch",
            "resolution_version": self.resolution_version,
            "failed_platforms": [],
            "partial_run": False,
            "coverage_warning": False,
            "metrics": {},
            "schema_drift_warnings": [],
            "status": "SUCCESS",
        }
        for platform in sorted(selected):
            for obj in manifest_reader.list_active_bronze_objects(platform):
                batch: list[dict[str, Any]] = []
                for envelope in object_reader(obj["object_key"]):
                    payload = envelope.get("raw_payload") or {}
                    observed_by_platform.setdefault(platform, set()).add(
                        str(payload.get("job_id") or payload.get("source_job_id") or "")
                    )
                    batch.append(envelope)
                    if len(batch) >= self.max_records_per_batch:
                        report = self.process(batch)
                        aggregate.update(report["metrics"])
                        last_report = report
                        batch = []
                if batch:
                    report = self.process(batch)
                    aggregate.update(report["metrics"])
                    last_report = report
        last_report["metrics"] = dict(aggregate)
        last_report["metrics"]["quarantine_rate_pct"] = round(
            (aggregate["quarantine_count"] / aggregate["total_observations_processed"] * 100), 4
        ) if aggregate["total_observations_processed"] else 0.0
        last_report["metrics"]["canonical_entity_count"] = last_report["metrics"].get("canonical_entity_count", 0)
        last_report["metrics"]["entity_link_count"] = last_report["metrics"].get("entity_link_count", 0)
        last_report["metrics"]["source_platform_count"] = len(observed_by_platform)
        last_report["observed_source_job_ids"] = {
            platform: sorted(ids) for platform, ids in sorted(observed_by_platform.items())
        }
        self.writer.write_audit_log(last_report, self.run_id)
        return last_report

    def process(
        self,
        envelopes: Iterable[dict[str, Any]],
        *,
        failed_platforms: Optional[set[str]] = None,
        partial_run: bool = False,
    ) -> dict[str, Any]:
        failed_platforms = failed_platforms or set()
        observations: list[dict[str, Any]] = []
        quarantine: list[dict[str, Any]] = []
        links: list[dict[str, Any]] = []
        entities: dict[str, dict[str, Any]] = {}
        metrics = Counter(
            total_observations_processed=0,
            valid_observations=0,
            quarantine_count=0,
            new_entity_count=0,
            updated_entity_count=0,
            auto_match_count=0,
            review_required_count=0,
            distinct_entity_count=0,
            unmatched_count=0,
        )
        for envelope in envelopes:
            metrics["total_observations_processed"] += 1
            if not MetadataEnvelope.verify_integrity(envelope):
                record = self._blank_observation(envelope)
                quarantine.append(self._quarantine(record, "BRONZE_ENVELOPE_CHECKSUM_MISMATCH"))
                metrics["quarantine_count"] += 1
                continue
            try:
                record = canonicalize_bronze_record(envelope)
                quality = validate_observation(record)
                if not quality.accepted:
                    raise ValueError(quality.quarantine_reason or "QUALITY_GATE_REJECTED")
                metrics["valid_observations"] += 1
                result = self.resolver.resolve(record, record["observed_at"])
                links.append(self._link_row(record, result))
                observations.append(record)
                if result.match_status == "DISTINCT_ENTITY":
                    metrics["new_entity_count"] += 1
                    metrics["distinct_entity_count"] += 1
                elif result.match_status == "REVIEW_REQUIRED":
                    metrics["review_required_count"] += 1
                elif result.match_status == "UNMATCHED":
                    metrics["unmatched_count"] += 1
                else:
                    metrics["auto_match_count"] += 1
                if result.unified_job_id:
                    entities[result.unified_job_id] = self._entity_row(result.unified_job_id, record)
                    entity = self.registry.get_entity(result.unified_job_id)
                    if result.match_method == "SOURCE_JOB_ID" and entity is not None and entity["entity_status"] == "UPDATED":
                        metrics["updated_entity_count"] += 1

            except (KeyError, TypeError, ValueError) as exc:
                quarantine.append(self._quarantine(self._blank_observation(envelope), str(exc)))
                metrics["quarantine_count"] += 1

        if observations:
            by_partition: dict[str, list[dict[str, Any]]] = {}
            for record in observations:
                by_partition.setdefault(
                    self._partition(record["observed_at"], platform=record["platform"]), []
                ).append(record)
            for partition, rows in by_partition.items():
                self.writer.write("observations", rows, partition)
        if quarantine:
            by_partition = {}
            for row in quarantine:
                by_partition.setdefault(
                    self._partition(row["observed_at"], platform=row["platform"]), []
                ).append(row)
            for partition, rows in by_partition.items():
                self.writer.write("quarantine", rows, partition)

        # Lifecycle closure is deliberately kept outside resolver decisions so a
        # partial source run can never close entities from a missing platform.
        lifecycle_metrics = {"stale_entity_count": 0, "closed_entity_count": 0, "expired_entity_count": 0}
        if not partial_run and not failed_platforms:
            from src.etl.entity_lifecycle import close_stale_entities, mark_stale_entities
            from datetime import date
            observed_by_platform = {}
            for record in observations:
                observed_by_platform.setdefault(record["platform"], set()).add(record["source_job_id"])
            as_of = date.today()
            for platform, source_ids in observed_by_platform.items():
                lifecycle_metrics["stale_entity_count"] += mark_stale_entities(
                    self.registry, platform=platform, observed_source_job_ids=source_ids, as_of=as_of
                )
                lifecycle_metrics["closed_entity_count"] += close_stale_entities(
                    self.registry, platform=platform, observed_source_job_ids=source_ids, as_of=as_of
                )
        metrics.update(lifecycle_metrics)
        if links:
            for link in links:
                quality = validate_link(link)
                if not quality.accepted:
                    raise ValueError(f"Silver entity link rejected: {quality.quarantine_reason}")
            by_partition = {}
            for link in links:
                by_partition.setdefault(self._partition(link["matched_at"]), []).append(link)
            for partition, rows in by_partition.items():
                self.writer.write("entity_links", rows, partition)
        if entities:
            entity_rows = list(entities.values())
            for entity in entity_rows:
                entity_observations = self.registry.list_observations(entity["unified_job_id"])
                canonical = choose_canonical(
                    [dict(row) for row in entity_observations if row["quality_accepted"]]
                ) if entity_observations else None
                if canonical:
                    entity["canonical_posted_date"] = canonical.get("posted_date") or entity["canonical_posted_date"]
                    entity["canonical_description"] = canonical.get("job_description_clean") or entity["canonical_description"]
                    entity["canonical_currency"] = canonical.get("salary_currency") or entity["canonical_currency"]
                quality = validate_entity(entity)
                if not quality.accepted:
                    raise ValueError(f"Silver entity rejected: {quality.quarantine_reason}")
            by_partition = {}
            for entity in entity_rows:
                # Business partition: canonical posted date, with first-seen fallback.
                partition_timestamp = entity.get("canonical_posted_date") or entity["first_seen_at"]
                by_partition.setdefault(self._partition(partition_timestamp), []).append(entity)
            for partition, rows in by_partition.items():
                self.writer.write("entities", rows, partition)

        # Keep every successful batch auditable and expose the explicit publish gate input.
        report_metrics = dict(metrics)
        report_metrics["canonical_entity_count"] = len(entities)
        report_metrics["entity_link_count"] = len(links)
        total = metrics["total_observations_processed"]
        report_metrics["quarantine_rate_pct"] = round(
            (metrics["quarantine_count"] / total) * 100, 4
        ) if total else 0.0
        report_metrics["stale_entity_count"] = lifecycle_metrics["stale_entity_count"]
        report_metrics["closed_entity_count"] = lifecycle_metrics["closed_entity_count"]
        report_metrics["expired_entity_count"] = lifecycle_metrics["expired_entity_count"]
        report = {
            "run_id": self.run_id,
            "pipeline": "bronze_to_silver_v1",
            "execution_mode": "partial" if partial_run else "batch",
            "resolution_version": self.resolution_version,
            "failed_platforms": sorted(failed_platforms),
            "partial_run": partial_run,
            "coverage_warning": bool(failed_platforms),
            "metrics": report_metrics,
            "schema_drift_warnings": [],
            "status": "SUCCESS" if not metrics["quarantine_count"] or metrics["valid_observations"] else "FAILED",
        }
        self.writer.write_audit_log(report, self.run_id)
        return report

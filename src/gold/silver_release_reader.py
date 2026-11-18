"""Read one sealed Silver release and build the effective E/L/O staging sets.

Gold never lists a day folder to guess what is current: it reads the release manifest
written by BronzeToSilverPipeline.seal_silver_release(), verifies every object, and
only then derives the effective sets. Anything ambiguous becomes a conflict that
blocks publishing instead of being silently resolved.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

from src.gold.contract import REJECTED_MATCH_STATUSES, accepted_link
from src.gold.timeutil import parse_utc


@dataclass(frozen=True)
class SilverRelease:
    """One sealed, addressable Silver release."""

    release_id: str
    status: str
    snapshot_cutoff_at: str
    resolution_version: str
    manifest_fingerprint: str
    taxonomy_versions: dict[str, str]
    metrics: dict[str, Any]
    objects: tuple[dict[str, Any], ...]
    partial_run: bool
    coverage_warning: bool
    failed_platforms: tuple[str, ...]
    path: Path

    @property
    def is_consumable(self) -> bool:
        """A release may only feed Gold when it sealed cleanly and covered all sources."""
        return (
            self.status == "COMPLETE"
            and not self.partial_run
            and not self.coverage_warning
            and not self.failed_platforms
        )

    def objects_for(self, dataset: str) -> tuple[dict[str, Any], ...]:
        return tuple(obj for obj in self.objects if obj.get("dataset") == dataset)


def _read_manifest(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def load_release(root: str | Path, release_id: str) -> SilverRelease:
    """Load one release manifest by id."""
    path = Path(root) / "releases" / f"{release_id}.json"
    if not path.exists():
        raise FileNotFoundError(f"Silver release manifest not found: {path}")
    payload = _read_manifest(path)
    return SilverRelease(
        release_id=str(payload["silver_release_id"]),
        status=str(payload.get("status", "")),
        snapshot_cutoff_at=str(payload.get("snapshot_cutoff_at", "")),
        resolution_version=str(payload.get("resolution_version", "")),
        manifest_fingerprint=str(payload.get("manifest_fingerprint", "")),
        taxonomy_versions=dict(payload.get("taxonomy_versions") or {}),
        metrics=dict(payload.get("metrics") or {}),
        objects=tuple(payload.get("objects") or ()),
        partial_run=bool(payload.get("partial_run", False)),
        coverage_warning=bool(payload.get("coverage_warning", False)),
        failed_platforms=tuple(payload.get("failed_platforms") or ()),
        path=path,
    )


def latest_release(root: str | Path, *, require_consumable: bool = True) -> Optional[SilverRelease]:
    """Newest sealed release, preferring one Gold is allowed to consume."""
    release_dir = Path(root) / "releases"
    if not release_dir.exists():
        return None
    candidates = []
    for path in sorted(release_dir.glob("*.json")):
        try:
            # release_dir is <root>/releases, so its parent is the Silver root.
            release = load_release(release_dir.parent, path.stem)
        except (KeyError, json.JSONDecodeError, FileNotFoundError):
            continue
        candidates.append(release)
    if not candidates:
        return None
    candidates.sort(key=lambda item: (item.snapshot_cutoff_at, item.release_id))
    if require_consumable:
        consumable = [item for item in candidates if item.is_consumable]
        return consumable[-1] if consumable else None
    return candidates[-1]


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_release(release: SilverRelease, root: str | Path) -> list[str]:
    """Verify every object of a release against its recorded checksum and row count.

    Returns a list of human-readable problems; an empty list means the release is intact.
    """
    import pyarrow.parquet as pq

    problems: list[str] = []
    if release.status != "COMPLETE":
        problems.append(f"release status is {release.status}, expected COMPLETE")
    if not release.objects:
        problems.append("release manifest lists no objects")
    root_path = Path(root)
    for obj in release.objects:
        object_key = str(obj.get("object_key") or "")
        local = root_path / object_key
        if not local.exists():
            problems.append(f"missing object: {object_key}")
            continue
        expected_sha = str(obj.get("sha256") or "")
        if expected_sha and _sha256_file(local) != expected_sha:
            problems.append(f"checksum mismatch: {object_key}")
        try:
            actual_rows = pq.read_metadata(local).num_rows
        except Exception as exc:  # noqa: BLE001 - report, never crash the audit
            problems.append(f"unreadable parquet {object_key}: {exc}")
            continue
        if int(obj.get("records") or 0) != actual_rows:
            problems.append(f"record count mismatch: {object_key}")
    return problems


def read_dataset(
    release: SilverRelease,
    root: str | Path,
    dataset: str,
    *,
    object_reader: Optional[Callable[[str], Iterable[dict[str, Any]]]] = None,
) -> list[dict[str, Any]]:
    """Read every verified object of one dataset in one release.

    `object_reader` allows a MinIO-backed read; the default reads the local mirror
    that SilverWriter always produces alongside the upload.
    """
    import pyarrow.parquet as pq

    rows: list[dict[str, Any]] = []
    root_path = Path(root)
    for obj in release.objects_for(dataset):
        object_key = str(obj.get("object_key") or "")
        if object_reader is not None:
            rows.extend(dict(row) for row in object_reader(object_key))
            continue
        local = root_path / object_key
        if not local.exists():
            raise FileNotFoundError(f"Silver object not found for {dataset}: {local}")
        table = pq.read_table(local)
        rows.extend(table.to_pylist())
    return rows


def _conflict(conflict_type: str, identifier: str, detail: dict[str, Any]) -> dict[str, Any]:
    return {"conflict_type": conflict_type, "identifier": str(identifier), "detail": detail}


def build_effective_link(
    links: Iterable[dict[str, Any]],
    *,
    cutoff: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Collapse link history to one effective decision per observation_id.

    Links are immutable audit rows written per resolution_version, so several versions
    can exist for one observation. The effective decision is the latest by
    (matched_at, resolution_version). Rejected statuses never attach a job.
    """
    limit = parse_utc(cutoff)
    best: dict[str, dict[str, Any]] = {}
    conflicts: list[dict[str, Any]] = []
    for link in links:
        observation_id = str(link.get("observation_id") or "")
        if not observation_id:
            conflicts.append(_conflict("MISSING_OBSERVATION_ID", "unknown", dict(link)))
            continue
        matched_at = parse_utc(link.get("matched_at"))
        if matched_at is None:
            conflicts.append(_conflict("UNPARSEABLE_MATCHED_AT", observation_id, dict(link)))
            continue
        if limit is not None and matched_at > limit:
            # A decision made after the cutoff must not rewrite an older business day.
            continue
        status = str(link.get("match_status") or "")
        unified_job_id = link.get("unified_job_id")
        if status in REJECTED_MATCH_STATUSES and unified_job_id:
            conflicts.append(
                _conflict("REJECTED_LINK_HAS_JOB", observation_id,
                          {"match_status": status, "unified_job_id": unified_job_id})
            )
        row = {
            "observation_id": observation_id,
            "unified_job_id": unified_job_id if accepted_link(status, unified_job_id) else None,
            "platform": str(link.get("platform") or ""),
            "source_job_id": str(link.get("source_job_id") or ""),
            "match_method": str(link.get("match_method") or ""),
            "match_status": status,
            "match_score": link.get("match_score"),
            "matched_at": str(link.get("matched_at") or ""),
            "resolution_version": str(link.get("resolution_version") or ""),
            "is_primary_source": bool(link.get("is_primary_source", False)),
            "accepted_for_gold": accepted_link(status, unified_job_id),
        }
        current = best.get(observation_id)
        if current is None or (row["matched_at"], row["resolution_version"]) >= (
            current["matched_at"], current["resolution_version"]
        ):
            best[observation_id] = row
    return sorted(best.values(), key=lambda item: item["observation_id"]), conflicts


def build_effective_entity(
    entities: Iterable[dict[str, Any]],
    *,
    object_key: str = "",
    registry_entities: Optional[Iterable[dict[str, Any]]] = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """One effective entity per unified_job_id, reconciled against the SQLite registry.

    D-G06: the SQLite entity_registry wins for entity-level attributes because it is
    richer and is written by the resolver inside the same transaction. The Parquet
    entities dataset is the published copy used for reconciliation and lineage.
    """
    rows: dict[str, dict[str, Any]] = {}
    conflicts: list[dict[str, Any]] = []
    for entity in entities:
        unified_job_id = str(entity.get("unified_job_id") or "")
        if not unified_job_id:
            conflicts.append(_conflict("MISSING_UNIFIED_JOB_ID", "unknown", dict(entity)))
            continue
        if unified_job_id in rows:
            conflicts.append(_conflict("DUP_ENTITY", unified_job_id, {"source_object_key": object_key}))
            continue
        rows[unified_job_id] = {**dict(entity), "source_object_key": object_key}

    if registry_entities is not None:
        registry_by_id = {str(row["unified_job_id"]): row for row in registry_entities}
        for unified_job_id, row in rows.items():
            registry_row = registry_by_id.get(unified_job_id)
            if registry_row is None:
                conflicts.append(_conflict("SOURCE_DIVERGENCE", unified_job_id,
                                           {"reason": "present in Parquet, absent in registry"}))
                continue
            for parquet_field, registry_field in (
                ("canonical_title", "canonical_title"),
                ("entity_status", "entity_status"),
                ("canonical_salary_min_vnd", "canonical_salary_min_vnd"),
                ("canonical_salary_max_vnd", "canonical_salary_max_vnd"),
            ):
                if row.get(parquet_field) != registry_row[registry_field]:
                    conflicts.append(_conflict("SOURCE_DIVERGENCE", unified_job_id, {
                        "field": parquet_field,
                        "parquet": row.get(parquet_field),
                        "registry": registry_row[registry_field],
                    }))
            # D-G06: registry values win.
            row["canonical_job_level"] = registry_row["canonical_job_level"]
            row["canonical_work_mode"] = registry_row["canonical_work_mode"]
            row["canonical_description"] = (
                registry_row["canonical_description"] or row.get("canonical_description")
            )
    return sorted(rows.values(), key=lambda item: item["unified_job_id"]), conflicts


def build_source_observation(
    observations: Iterable[dict[str, Any]],
    *,
    cutoff: str,
    effective_links: Optional[Iterable[dict[str, Any]]] = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Observations up to cutoff, each attached to at most one accepted unified_job_id."""
    limit = parse_utc(cutoff)
    link_by_observation = {
        str(link["observation_id"]): link for link in (effective_links or [])
    }
    rows: list[dict[str, Any]] = []
    conflicts: list[dict[str, Any]] = []
    seen: set[str] = set()
    for observation in observations:
        observation_id = str(observation.get("observation_id") or "")
        if not observation_id:
            conflicts.append(_conflict("MISSING_OBSERVATION_ID", "unknown", {}))
            continue
        if observation_id in seen:
            continue
        seen.add(observation_id)
        observed_at = parse_utc(observation.get("observed_at"))
        if observed_at is None:
            conflicts.append(_conflict("UNPARSEABLE_OBSERVED_AT", observation_id, {}))
            continue
        if limit is not None and observed_at > limit:
            conflicts.append(_conflict("FUTURE_OBSERVATION", observation_id,
                                       {"observed_at": str(observation.get("observed_at"))}))
            continue
        link = link_by_observation.get(observation_id)
        rows.append({
            **dict(observation),
            "unified_job_id": (link or {}).get("unified_job_id"),
            "accepted_for_gold": bool((link or {}).get("accepted_for_gold", False)),
        })
    return sorted(rows, key=lambda item: item["observation_id"]), conflicts

"""Bounded, deterministic local/MinIO Parquet writer for Silver."""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any, Iterable, Optional

import pyarrow as pa
import pyarrow.parquet as pq

from src.schemas.entity_links_schema import SILVER_ENTITY_LINKS_SCHEMA
from src.schemas.entity_schema import SILVER_ENTITY_SCHEMA
from src.schemas.silver_schema import QUARANTINE_SCHEMA, SILVER_OBSERVATIONS_SCHEMA

SCHEMAS = {
    "observations": SILVER_OBSERVATIONS_SCHEMA,
    "entities": SILVER_ENTITY_SCHEMA,
    "entity_links": SILVER_ENTITY_LINKS_SCHEMA,
    "quarantine": QUARANTINE_SCHEMA,
}


class SilverWriter:
    """Write validated batches with deterministic files and reconciliation manifests.

    Local filesystem is the default. An optional MinIO client must implement
    ``put_object(bucket, key, stream, length, content_type=...)`` and
    ``stat_object(bucket, key)``; no production endpoint is contacted by default.
    """

    def __init__(
        self,
        root: str = "data/silver",
        max_records_per_batch: int = 5_000,
        *,
        minio_client: Any = None,
        bucket: str = "silver",
    ) -> None:
        self.root = Path(root)
        self.max_records_per_batch = max_records_per_batch
        self.minio_client = minio_client
        self.bucket = bucket

    def _manifest_path(self, dataset: str, partition: str, identity_digest: str) -> Path:
        return self.root / dataset / partition / f"manifest-{identity_digest}.json"

    @staticmethod
    def _identity_digest(dataset: str, rows: list[dict[str, Any]]) -> str:
        key_fields = {
            "observations": ("observation_id",),
            "entities": ("unified_job_id",),
            "entity_links": ("observation_id", "resolution_version"),
            "quarantine": ("observation_id",),
        }.get(dataset, ())
        identity = sorted(tuple(row.get(field) for field in key_fields) for row in rows)
        payload = json.dumps(identity, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]

    @staticmethod
    def _partition_values(partition: str) -> dict[str, Optional[str]]:
        values: dict[str, Optional[str]] = {"year": None, "month": None, "day": None}
        for component in str(partition).strip("/").split("/"):
            if "=" in component:
                key, value = component.split("=", 1)
                if key in values:
                    values[key] = value
        return values

    @classmethod
    def _schema_for_partition(cls, dataset: str, partition: str) -> pa.Schema:
        schema = SCHEMAS[dataset]
        return schema.append(pa.field("year", pa.string(), nullable=True)).append(
            pa.field("month", pa.string(), nullable=True)
        ).append(pa.field("day", pa.string(), nullable=True))

    @classmethod
    def _rows_for_partition(cls, dataset: str, rows: list[dict[str, Any]], partition: str) -> list[dict[str, Any]]:
        values = cls._partition_values(partition)
        return [{**row, **values} for row in rows]

    @staticmethod
    def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False, dir=path.parent) as tmp:
            json.dump(payload, tmp, ensure_ascii=False, indent=2, sort_keys=True)
            tmp.write("\n")
            temp_path = Path(tmp.name)
        os.replace(temp_path, path)

    def _upload_minio(self, path: Path, key: str, expected_sha256: str) -> None:
        if self.minio_client is None:
            return
        with path.open("rb") as stream:
            self.minio_client.put_object(
                self.bucket,
                key,
                stream,
                path.stat().st_size,
                content_type="application/octet-stream",
            )
        self._verify_minio_object(key, path.stat().st_size, expected_sha256)

    def _verify_minio_object(self, key: str, expected_size: int, expected_sha256: str) -> None:
        stat = self.minio_client.stat_object(self.bucket, key)
        if int(stat.size) != expected_size:
            raise ValueError(f"MinIO size mismatch for {key}: {stat.size} != {expected_size}")
        if not hasattr(self.minio_client, "get_object"):
            return
        response = self.minio_client.get_object(self.bucket, key)
        try:
            remote_digest = hashlib.sha256()
            for chunk in iter(lambda: response.read(65536), b""):
                remote_digest.update(chunk)
        finally:
            response.close()
            if hasattr(response, "release_conn"):
                response.release_conn()
        if remote_digest.hexdigest() != expected_sha256:
            raise ValueError(f"MinIO checksum mismatch for {key}")

    def _upload_manifest(self, manifest_path: Path, dataset: str, partition: str, identity_digest: str) -> None:
        if self.minio_client is None:
            return
        key = f"audit_logs/manifests/{dataset}/{partition}/manifest-{identity_digest}.json"
        checksum = _sha256(manifest_path)
        with manifest_path.open("rb") as stream:
            self.minio_client.put_object(
                self.bucket,
                key,
                stream,
                manifest_path.stat().st_size,
                content_type="application/json",
            )
        self._verify_minio_object(key, manifest_path.stat().st_size, checksum)

    def _write_batch(self, dataset: str, rows: list[dict[str, Any]], partition: str, batch_index: int) -> dict[str, Any]:
        schema = self._schema_for_partition(dataset, partition)
        rows = self._rows_for_partition(dataset, rows, partition)
        destination = self.root / dataset / partition
        destination.mkdir(parents=True, exist_ok=True)
        identity_digest = self._identity_digest(dataset, [
            {k: v for k, v in row.items() if k not in {"year", "month", "day"}} for row in rows
        ])
        digest = hashlib.sha256((dataset + partition).encode()).hexdigest()[:16]
        filename = f"part-{identity_digest}-{digest}.parquet"
        final_path = destination / filename
        manifest_path = self._manifest_path(dataset, partition, identity_digest)
        object_key = f"{dataset}/{partition}/{filename}"
        pending = {
            "status": "PENDING",
            "dataset": dataset,
            "partition": partition,
            "path": str(final_path),
            "object_key": object_key,
            "bucket": self.bucket,
            "records": len(rows),
        }
        self._write_json_atomic(manifest_path, pending)
        temp_path: Optional[Path] = None
        try:
            table = pa.Table.from_pylist(rows, schema=schema)
            with tempfile.NamedTemporaryFile(suffix=".parquet", delete=False, dir=destination) as tmp:
                temp_path = Path(tmp.name)
            pq.write_table(table, temp_path, compression="snappy")
            os.replace(temp_path, final_path)
            checksum = _sha256(final_path)
            self._upload_minio(final_path, object_key, checksum)
            verified_records = pq.read_metadata(final_path).num_rows
            if verified_records != len(rows):
                raise ValueError(f"Parquet record count mismatch: expected {len(rows)}, got {verified_records}")
            complete = {
                **pending,
                "status": "COMPLETE",
                "sha256": checksum,
                "records": verified_records,
            }
            self._write_json_atomic(manifest_path, complete)
            self._upload_manifest(manifest_path, dataset, partition, identity_digest)
            return {
                "path": str(final_path),
                "object_key": object_key,
                "records": verified_records,
                "sha256": checksum,
                "manifest_path": str(manifest_path),
                "status": "COMPLETE",
            }
        except Exception as exc:
            failed = {**pending, "status": "FAILED", "error": type(exc).__name__ + ": " + str(exc)}
            self._write_json_atomic(manifest_path, failed)
            raise
        finally:
            if temp_path is not None and temp_path.exists():
                temp_path.unlink()

    def write(self, dataset: str, records: Iterable[dict[str, Any]], partition: str) -> list[dict[str, Any]]:
        if dataset not in SCHEMAS:
            raise ValueError(f"Unknown Silver dataset: {dataset}")
        key_fields = {
            "observations": ("observation_id",),
            "entities": ("unified_job_id",),
            "entity_links": ("observation_id", "resolution_version"),
            "quarantine": ("observation_id",),
        }.get(dataset)
        if key_fields:
            records = self._dedupe(records, key_fields)
        results = []
        batch: list[dict[str, Any]] = []
        batch_index = 0
        for record in records:
            batch.append(record)
            if len(batch) >= self.max_records_per_batch:
                results.append(self._write_batch(dataset, batch, partition, batch_index))
                batch_index += 1
                batch = []
        if batch:
            results.append(self._write_batch(dataset, batch, partition, batch_index))
        return results

    @staticmethod
    def _dedupe(records: Iterable[dict[str, Any]], key_fields: tuple[str, ...]) -> Iterable[dict[str, Any]]:
        unique: dict[tuple[Any, ...], dict[str, Any]] = {}
        for record in records:
            key = tuple(record.get(field) for field in key_fields)
            if any(value in (None, "") for value in key):
                raise ValueError(f"Missing Silver idempotency key: {key_fields}")
            unique[key] = record
        return [unique[key] for key in sorted(unique)]

    def write_audit_log(self, report: dict[str, Any], run_id: str) -> str:
        audit_dir = self.root / "audit_logs"
        audit_dir.mkdir(parents=True, exist_ok=True)
        path = audit_dir / f"run_{run_id}.json"
        self._write_json_atomic(path, report)
        return str(path)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()

"""Bounded, deterministic Parquet writer for Silver MVP."""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any, Iterable

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
    def __init__(self, root: str = "data/silver", max_records_per_batch: int = 5_000) -> None:
        self.root = Path(root)
        self.max_records_per_batch = max_records_per_batch

    def _write_batch(self, dataset: str, rows: list[dict[str, Any]], partition: str, batch_index: int) -> dict[str, Any]:
        schema = SCHEMAS[dataset]
        table = pa.Table.from_pylist(rows, schema=schema)
        destination = self.root / dataset / partition
        destination.mkdir(parents=True, exist_ok=True)
        filename = f"part-{batch_index:05d}-{hashlib.sha256((dataset + partition).encode()).hexdigest()[:16]}.parquet"
        final_path = destination / filename
        with tempfile.NamedTemporaryFile(suffix=".parquet", delete=False, dir=destination) as tmp:
            temp_path = Path(tmp.name)
        try:
            pq.write_table(table, temp_path, compression="snappy")
            os.replace(temp_path, final_path)
        finally:
            if temp_path.exists():
                temp_path.unlink()
        return {"path": str(final_path), "records": len(rows), "sha256": _sha256(final_path)}

    def write(self, dataset: str, records: Iterable[dict[str, Any]], partition: str) -> list[dict[str, Any]]:
        if dataset not in SCHEMAS:
            raise ValueError(f"Unknown Silver dataset: {dataset}")
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

    def write_audit_log(self, report: dict[str, Any], run_id: str) -> str:
        audit_dir = self.root / "audit_logs"
        audit_dir.mkdir(parents=True, exist_ok=True)
        path = audit_dir / f"run_{run_id}.json"
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
        return str(path)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()

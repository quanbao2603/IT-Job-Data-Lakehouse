"""Fail-closed post-crawl gate for the Bronze orchestration DAG."""
from __future__ import annotations

import gzip
import hashlib
import io
import json
import os
import sqlite3
from typing import Any, Dict, Iterable, Optional

from src.storage.metadata_envelope import MetadataEnvelope


class AuditGateError(RuntimeError):
    """Raised when source results or Bronze metadata are not safe to accept."""


def latest_source_results(
    state_db_path: str,
    *,
    expected_sources: Iterable[str],
) -> Dict[str, Dict[str, Any]]:
    """Read the latest terminal State Store run for each expected source."""
    expected = list(expected_sources)
    if not os.path.isfile(state_db_path):
        raise AuditGateError(f"State Store is missing: {state_db_path}")
    results: Dict[str, Dict[str, Any]] = {}
    with sqlite3.connect(state_db_path) as connection:
        connection.row_factory = sqlite3.Row
        for source in expected:
            row = connection.execute(
                """
                SELECT run_id, source_name, status, records_ingested, bronze_s3_uri
                FROM ingestion_runs
                WHERE source_name = ?
                ORDER BY started_at DESC
                LIMIT 1
                """,
                (source,),
            ).fetchone()
            if row is None:
                results[source] = {"status": "MISSING", "records_ingested": 0}
            else:
                results[source] = dict(row)
    return results


def validate_source_results(
    source_results: Dict[str, Dict[str, Any]],
    *,
    expected_sources: Iterable[str],
    allow_partial: bool = False,
) -> Dict[str, Any]:
    """Check every source reported a result.

    `allow_partial=True` turns a failed source into a reported fact instead of an exception. One
    platform timing out used to abort the whole nightly run: the audit raised, Silver never ran,
    Gold was never published, and eight healthy sources produced nothing (devwork, 26/11).
    """
    expected = list(expected_sources)
    missing = [source for source in expected if source not in source_results]
    if missing:
        raise AuditGateError(f"Missing source results: {', '.join(missing)}")

    failures = {
        source: result.get("status")
        for source, result in source_results.items()
        if source in expected and result.get("status") not in {"SUCCESS", "DEFERRED"}
    }
    deferred = {
        source: result.get("reason") or "deferred"
        for source, result in source_results.items()
        if source in expected and result.get("status") == "DEFERRED"
    }
    if failures and not allow_partial:
        details = ", ".join(f"{source}={status}" for source, status in failures.items())
        raise AuditGateError(f"Non-success source result: {details}")

    successes = sum(1 for source in expected if source_results[source].get("status") == "SUCCESS")
    if allow_partial and failures and successes == 0:
        details = ", ".join(f"{source}={status}" for source, status in failures.items())
        raise AuditGateError(f"Every source failed, nothing to publish: {details}")

    return {
        "status": "PARTIAL" if failures else ("DEFERRED" if deferred else "PASS"),
        "failed_sources": {source: status for source, status in failures.items()},
        "source_count": len(expected),
        "success_count": successes,
        "deferred_sources": deferred,
        "empty_success_count": sum(
            1 for source in expected if int(source_results[source].get("records_ingested", 0)) == 0
        ),
        "records_ingested": sum(
            int(source_results[source].get("records_ingested", 0)) for source in expected
        ),
    }


def _state_rows(state_db_path: str, run_ids: Iterable[str]) -> Dict[str, sqlite3.Row]:
    ids = list(run_ids)
    if not ids:
        return {}
    placeholders = ",".join("?" for _ in ids)
    with sqlite3.connect(state_db_path) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            f"SELECT * FROM ingestion_runs WHERE run_id IN ({placeholders})",
            ids,
        ).fetchall()
    return {row["run_id"]: row for row in rows}


def audit_bronze_run(
    source_results: Dict[str, Dict[str, Any]],
    *,
    expected_sources: Iterable[str],
    state_db_path: str,
    minio_client: Optional[Any] = None,
    bronze_bucket: str = "bronze",
    allow_partial: bool = True,
) -> Dict[str, Any]:
    """Validate source status plus State Store/manifest/object evidence."""
    summary = validate_source_results(
        source_results, expected_sources=expected_sources, allow_partial=allow_partial
    )
    if summary.get("status") == "DEFERRED":
        summary.update({"checked_objects": 0, "state_store": "DEFERRED"})
        return summary
    audited_sources = [
        source for source in expected_sources if source_results[source].get("status") == "SUCCESS"
    ]
    raw_run_ids = [source_results[source].get("run_id") for source in audited_sources]
    if any(not run_id for run_id in raw_run_ids):
        raise AuditGateError("A successful source result is missing run_id")
    run_ids = [str(run_id) for run_id in raw_run_ids]
    if not os.path.isfile(state_db_path):
        raise AuditGateError(f"State Store is missing: {state_db_path}")

    runs = _state_rows(state_db_path, run_ids)
    missing_runs = [run_id for run_id in run_ids if run_id not in runs]
    if missing_runs:
        raise AuditGateError(f"Missing State Store runs: {', '.join(missing_runs)}")

    checked_objects = 0
    with sqlite3.connect(state_db_path) as connection:
        connection.row_factory = sqlite3.Row
        for source in audited_sources:
            result = source_results[source]
            run_id = result["run_id"]
            run = runs[run_id]
            if run["status"] != "SUCCESS":
                raise AuditGateError(f"State Store run {run_id} is {run['status']}")
            expected_count = int(result.get("records_ingested", 0))
            object_rows = connection.execute(
                "SELECT * FROM ingestion_objects WHERE run_id = ?",
                (run_id,),
            ).fetchall()
            if expected_count > 0 and not object_rows:
                raise AuditGateError(f"Missing manifest for successful run {run_id}")
            registered_count = sum(int(row["records_count"]) for row in object_rows)
            if registered_count != expected_count:
                raise AuditGateError(
                    f"Manifest count mismatch for {source}: {registered_count} != {expected_count}"
                )
            for row in object_rows:
                expected_checksum = row["checksum_sha256"]
                if not expected_checksum:
                    raise AuditGateError(f"Manifest checksum missing for {row['object_key']}")
                if minio_client is None:
                    raise AuditGateError("MinIO client is required for a Bronze audit")
                try:
                    stat = minio_client.stat_object(bronze_bucket, row["object_key"])
                    response = minio_client.get_object(bronze_bucket, row["object_key"])
                    try:
                        hasher = hashlib.sha256()
                        compressed = bytearray()
                        while True:
                            chunk = response.read(1024 * 1024)
                            if not chunk:
                                break
                            hasher.update(chunk)
                            compressed.extend(chunk)
                        if stat.size != len(compressed):
                            raise AuditGateError(
                                f"Bronze size mismatch: {row['object_key']}"
                            )
                        if hasher.hexdigest() != expected_checksum:
                            raise AuditGateError(
                                f"Bronze checksum mismatch: {row['object_key']}"
                            )
                        records_count = 0
                        with gzip.GzipFile(fileobj=io.BytesIO(compressed), mode="rb") as gz:
                            with io.TextIOWrapper(gz, encoding="utf-8") as text_stream:
                                for line in text_stream:
                                    if not line.strip():
                                        continue
                                    record = json.loads(line)
                                    if not MetadataEnvelope.verify_integrity(record):
                                        raise AuditGateError(
                                            f"Envelope checksum mismatch: {row['object_key']}"
                                        )
                                    records_count += 1
                        if records_count != int(row["records_count"]):
                            raise AuditGateError(
                                f"Bronze record count mismatch: {row['object_key']}"
                            )
                    finally:
                        response.close()
                        response.release_conn()
                except AuditGateError:
                    raise
                except Exception as exc:
                    raise AuditGateError(
                        f"Bronze object unavailable: {row['object_key']}"
                    ) from exc
                checked_objects += 1

        dangling_landing = []
        if minio_client is not None:
            dangling_landing = [
                obj.object_name
                for obj in minio_client.list_objects("landing", recursive=True)
                if obj.object_name.endswith(".uploading")
            ]
        if dangling_landing:
            raise AuditGateError(
                f"Temporary landing objects remain: {len(dangling_landing)}"
            )

    summary.update({"checked_objects": checked_objects, "state_store": "PASS"})
    return summary

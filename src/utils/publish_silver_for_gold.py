"""Explicit, gated Silver-entity publication adapter for Gold."""
from __future__ import annotations

import sqlite3
from typing import Any, Iterable

from src.etl.quality_gate import enforce_publish_gate

ENTITY_COLUMNS = (
    "unified_job_id",
    "canonical_title",
    "canonical_company",
    "canonical_location",
    "canonical_salary_min_vnd",
    "canonical_salary_max_vnd",
    "canonical_currency",
    "canonical_description",
    "canonical_posted_date",
    "source_count",
    "observation_count",
    "first_seen_at",
    "last_seen_at",
    "entity_status",
    "canonical_updated_at",
    "resolution_version",
)


def publish_entities(
    entities: Iterable[dict[str, Any]],
    connection: sqlite3.Connection,
    summary_report: dict[str, Any],
) -> dict[str, Any]:
    """Upsert canonical entities only after the fail-closed Silver gate."""
    enforce_publish_gate(summary_report)
    connection.execute(
        """CREATE TABLE IF NOT EXISTS gold_entities (
            unified_job_id TEXT PRIMARY KEY,
            canonical_title TEXT NOT NULL,
            canonical_company TEXT NOT NULL,
            canonical_location TEXT,
            canonical_salary_min_vnd INTEGER,
            canonical_salary_max_vnd INTEGER,
            canonical_currency TEXT,
            canonical_description TEXT,
            canonical_posted_date TEXT,
            source_count INTEGER NOT NULL,
            observation_count INTEGER NOT NULL,
            first_seen_at TEXT NOT NULL,
            last_seen_at TEXT NOT NULL,
            entity_status TEXT NOT NULL,
            canonical_updated_at TEXT NOT NULL,
            resolution_version TEXT NOT NULL
        )"""
    )
    rows = list(entities)
    placeholders = ", ".join("?" for _ in ENTITY_COLUMNS)
    updates = ", ".join(
        f"{column}=excluded.{column}" for column in ENTITY_COLUMNS if column != "unified_job_id"
    )
    connection.executemany(
        f"INSERT INTO gold_entities ({', '.join(ENTITY_COLUMNS)}) VALUES ({placeholders}) "
        f"ON CONFLICT(unified_job_id) DO UPDATE SET {updates}",
        [tuple(row.get(column) for column in ENTITY_COLUMNS) for row in rows],
    )
    connection.commit()
    for row in rows:
        read_back = connection.execute(
            "SELECT " + ", ".join(ENTITY_COLUMNS) + " FROM gold_entities WHERE unified_job_id = ?",
            (row["unified_job_id"],),
        ).fetchone()
        if read_back is None or tuple(read_back) != tuple(row.get(column) for column in ENTITY_COLUMNS):
            raise RuntimeError(f"Gold read-back mismatch for {row.get('unified_job_id')}")
    return {"status": "COMPLETE", "published_count": len(rows)}

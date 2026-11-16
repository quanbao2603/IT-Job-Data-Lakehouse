"""SQLite registry for stable Silver entity identity."""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager, closing
from datetime import datetime, timezone
from typing import Iterator, Optional


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


class EntityRegistry:
    def __init__(self, db_path: str = "data/state_store.db") -> None:
        self.db_path = db_path
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def _init_db(self) -> None:
        with closing(self._connect()) as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS entity_registry (
                    unified_job_id TEXT PRIMARY KEY,
                    candidate_identity_fingerprint TEXT NOT NULL,
                    canonical_title TEXT NOT NULL,
                    canonical_company TEXT NOT NULL,
                    canonical_city TEXT,
                    entity_status TEXT NOT NULL DEFAULT 'NEW',
                    first_seen_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL,
                    resolution_version TEXT NOT NULL DEFAULT 'v1',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_entity_registry_fingerprint
                    ON entity_registry(candidate_identity_fingerprint);
                CREATE INDEX IF NOT EXISTS idx_entity_registry_status
                    ON entity_registry(entity_status);
                CREATE TABLE IF NOT EXISTS source_job_registry (
                    platform TEXT NOT NULL,
                    source_job_id TEXT NOT NULL,
                    unified_job_id TEXT NOT NULL,
                    first_observed_at TEXT NOT NULL,
                    last_observed_at TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (platform, source_job_id),
                    FOREIGN KEY (unified_job_id) REFERENCES entity_registry(unified_job_id)
                );
                CREATE INDEX IF NOT EXISTS idx_source_job_registry_entity
                    ON source_job_registry(unified_job_id);
            """)
            conn.commit()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def get_source_job(self, platform: str, source_job_id: str) -> Optional[sqlite3.Row]:
        with closing(self._connect()) as conn:
            return conn.execute(
                "SELECT * FROM source_job_registry WHERE platform = ? AND source_job_id = ?",
                (platform, source_job_id),
            ).fetchone()

    def find_candidates(self, fingerprint: str) -> list[sqlite3.Row]:
        with closing(self._connect()) as conn:
            return conn.execute(
                "SELECT * FROM entity_registry WHERE candidate_identity_fingerprint = ? ORDER BY updated_at DESC",
                (fingerprint,),
            ).fetchall()

    def list_entities(self) -> list[sqlite3.Row]:
        with closing(self._connect()) as conn:
            return conn.execute("SELECT * FROM entity_registry ORDER BY updated_at DESC").fetchall()

    def create_entity(
        self,
        *,
        unified_job_id: str,
        platform: str,
        source_job_id: str,
        fingerprint: str,
        title: str,
        company: str,
        city: Optional[str],
        observed_at: str,
        resolution_version: str = "v1",
    ) -> None:
        now = utc_now()
        with self.transaction() as conn:
            conn.execute(
                """INSERT INTO entity_registry
                (unified_job_id, candidate_identity_fingerprint, canonical_title,
                 canonical_company, canonical_city, first_seen_at, last_seen_at,
                 resolution_version, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (unified_job_id, fingerprint, title, company, city, observed_at,
                 observed_at, resolution_version, now, now),
            )
            conn.execute(
                """INSERT INTO source_job_registry
                (platform, source_job_id, unified_job_id, first_observed_at,
                 last_observed_at, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (platform, source_job_id, unified_job_id, observed_at,
                 observed_at, now, now),
            )

    def touch_source_job(self, platform: str, source_job_id: str, observed_at: str) -> None:
        now = utc_now()
        with self.transaction() as conn:
            row = conn.execute(
                "SELECT unified_job_id FROM source_job_registry WHERE platform = ? AND source_job_id = ?",
                (platform, source_job_id),
            ).fetchone()
            if not row:
                raise KeyError(f"Unknown source job: {platform}/{source_job_id}")
            conn.execute(
                "UPDATE source_job_registry SET last_observed_at = ?, updated_at = ? "
                "WHERE platform = ? AND source_job_id = ?",
                (observed_at, now, platform, source_job_id),
            )
            conn.execute(
                "UPDATE entity_registry SET last_seen_at = ?, updated_at = ? WHERE unified_job_id = ?",
                (observed_at, now, row["unified_job_id"]),
            )

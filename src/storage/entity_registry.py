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
                    canonical_job_level TEXT,
                    canonical_work_mode TEXT,
                    canonical_salary_min_vnd INTEGER,
                    canonical_salary_max_vnd INTEGER,
                    canonical_description TEXT,
                    last_content_hash TEXT,
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
                CREATE TABLE IF NOT EXISTS silver_observation_registry (
                    observation_id TEXT PRIMARY KEY,
                    platform TEXT NOT NULL,
                    source_job_id TEXT NOT NULL,
                    content_hash TEXT NOT NULL,
                    canonical_url TEXT,
                    unified_job_id TEXT,
                    resolution_version TEXT NOT NULL,
                    observed_at TEXT,
                    posted_date TEXT,
                    expired_date TEXT,
                    job_description_clean TEXT,
                    salary_min_vnd INTEGER,
                    salary_max_vnd INTEGER,
                    salary_currency TEXT,
                    city_normalized TEXT,
                    quality_accepted INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (unified_job_id) REFERENCES entity_registry(unified_job_id)
                );
                CREATE TABLE IF NOT EXISTS silver_entity_links (
                    observation_id TEXT NOT NULL,
                    resolution_version TEXT NOT NULL,
                    unified_job_id TEXT,
                    candidate_entity_id TEXT,
                    match_method TEXT NOT NULL,
                    match_score REAL,
                    match_status TEXT NOT NULL,
                    review_reason TEXT,
                    previous_unified_job_id TEXT,
                    matched_at TEXT NOT NULL,
                    PRIMARY KEY (observation_id, resolution_version),
                    FOREIGN KEY (unified_job_id) REFERENCES entity_registry(unified_job_id)
                );
                CREATE INDEX IF NOT EXISTS idx_silver_obs_url
                    ON silver_observation_registry(platform, source_job_id);
            """)
            existing_columns = {
                row["name"] for row in conn.execute("PRAGMA table_info(entity_registry)").fetchall()
            }
            migrations = {
                "canonical_job_level": "TEXT",
                "canonical_work_mode": "TEXT",
                "canonical_salary_min_vnd": "INTEGER",
                "canonical_salary_max_vnd": "INTEGER",
                "canonical_description": "TEXT",
                "last_content_hash": "TEXT",
            }
            observation_columns = {
                row["name"] for row in conn.execute("PRAGMA table_info(silver_observation_registry)").fetchall()
            }
            observation_migrations = {
                "observed_at": "TEXT", "posted_date": "TEXT", "expired_date": "TEXT",
                "job_description_clean": "TEXT", "salary_min_vnd": "INTEGER",
                "salary_max_vnd": "INTEGER", "salary_currency": "TEXT",
                "city_normalized": "TEXT", "quality_accepted": "INTEGER NOT NULL DEFAULT 1",
            }
            for column, sql_type in observation_migrations.items():
                if column not in observation_columns:
                    conn.execute(f"ALTER TABLE silver_observation_registry ADD COLUMN {column} {sql_type}")
            link_columns = {
                row["name"] for row in conn.execute("PRAGMA table_info(silver_entity_links)").fetchall()
            }
            if "previous_unified_job_id" not in link_columns:
                conn.execute("ALTER TABLE silver_entity_links ADD COLUMN previous_unified_job_id TEXT")
            for column, sql_type in migrations.items():
                if column not in existing_columns:
                    conn.execute(f"ALTER TABLE entity_registry ADD COLUMN {column} {sql_type}")
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_silver_obs_canonical_url "
                "ON silver_observation_registry(canonical_url)"
            )
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

    def find_by_canonical_url(self, canonical_url: str) -> Optional[sqlite3.Row]:
        with closing(self._connect()) as conn:
            return conn.execute(
                """SELECT e.* FROM entity_registry e
                   JOIN silver_observation_registry o ON o.unified_job_id = e.unified_job_id
                   WHERE o.canonical_url = ? ORDER BY e.updated_at DESC LIMIT 1""",
                (canonical_url,),
            ).fetchone()

    def bind_source_job(self, platform: str, source_job_id: str, unified_job_id: str, observed_at: str) -> None:
        now = utc_now()
        with self.transaction() as conn:
            existing = conn.execute(
                "SELECT unified_job_id FROM source_job_registry WHERE platform = ? AND source_job_id = ?",
                (platform, source_job_id),
            ).fetchone()
            if existing and existing["unified_job_id"] != unified_job_id:
                raise ValueError(
                    f"Source job already bound to another entity: {platform}/{source_job_id}"
                )
            if existing:
                conn.execute(
                    "UPDATE source_job_registry SET last_observed_at = ?, updated_at = ? "
                    "WHERE platform = ? AND source_job_id = ?",
                    (observed_at, now, platform, source_job_id),
                )
                return
            conn.execute(
                """INSERT INTO source_job_registry
                (platform, source_job_id, unified_job_id, first_observed_at, last_observed_at, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (platform, source_job_id, unified_job_id, observed_at, observed_at, now, now),
            )

    def get_entity(self, unified_job_id: str) -> Optional[sqlite3.Row]:
        with closing(self._connect()) as conn:
            return conn.execute(
                "SELECT * FROM entity_registry WHERE unified_job_id = ?",
                (unified_job_id,),
            ).fetchone()

    def list_source_jobs(self, unified_job_id: str, platform: Optional[str] = None) -> list[sqlite3.Row]:
        with closing(self._connect()) as conn:
            if platform is None:
                return conn.execute(
                    "SELECT * FROM source_job_registry WHERE unified_job_id = ? ORDER BY platform, source_job_id",
                    (unified_job_id,),
                ).fetchall()
            return conn.execute(
                "SELECT * FROM source_job_registry WHERE unified_job_id = ? AND platform = ? ORDER BY source_job_id",
                (unified_job_id, platform),
            ).fetchall()

    def set_entity_status(self, unified_job_id: str, status: str) -> bool:
        now = utc_now()
        with self.transaction() as conn:
            result = conn.execute(
                "UPDATE entity_registry SET entity_status = ?, updated_at = ? WHERE unified_job_id = ? AND entity_status != ?",
                (status, now, unified_job_id, status),
            )
            return result.rowcount == 1

    def get_observation(self, observation_id: str) -> Optional[sqlite3.Row]:
        with closing(self._connect()) as conn:
            return conn.execute(
                "SELECT * FROM silver_observation_registry WHERE observation_id = ?",
                (observation_id,),
            ).fetchone()

    def list_observations(self, unified_job_id: Optional[str] = None) -> list[sqlite3.Row]:
        with closing(self._connect()) as conn:
            if unified_job_id is None:
                return conn.execute(
                    "SELECT * FROM silver_observation_registry ORDER BY created_at"
                ).fetchall()
            return conn.execute(
                "SELECT * FROM silver_observation_registry WHERE unified_job_id = ? ORDER BY created_at",
                (unified_job_id,),
            ).fetchall()

    def record_observation(self, *, observation_id: str, platform: str, source_job_id: str,
                           content_hash: str, unified_job_id: Optional[str],
                           resolution_version: str, canonical_url: Optional[str] = None,
                           observed_at: Optional[str] = None, posted_date: Optional[str] = None,
                           expired_date: Optional[str] = None, job_description_clean: Optional[str] = None,
                           salary_min_vnd: Optional[int] = None, salary_max_vnd: Optional[int] = None,
                           salary_currency: Optional[str] = None, city_normalized: Optional[str] = None,
                           quality_accepted: bool = True) -> bool:
        now = utc_now()
        with self.transaction() as conn:
            exists = conn.execute(
                "SELECT 1 FROM silver_observation_registry WHERE observation_id = ?",
                (observation_id,),
            ).fetchone()
            if exists:
                return False
            conn.execute(
                """INSERT INTO silver_observation_registry
                (observation_id, platform, source_job_id, content_hash, canonical_url, unified_job_id,
                 resolution_version, observed_at, posted_date, expired_date, job_description_clean,
                 salary_min_vnd, salary_max_vnd, salary_currency, city_normalized, quality_accepted, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (observation_id, platform, source_job_id, content_hash, canonical_url, unified_job_id,
                 resolution_version, observed_at, posted_date, expired_date, job_description_clean,
                 salary_min_vnd, salary_max_vnd, salary_currency, city_normalized,
                 int(quality_accepted), now),
            )
            return True

    def record_link(self, *, observation_id: str, resolution_version: str, unified_job_id: Optional[str],
                    candidate_entity_id: Optional[str], match_method: str, match_score: Optional[float],
                    match_status: str, review_reason: Optional[str],
                    previous_unified_job_id: Optional[str] = None) -> None:
        with self.transaction() as conn:
            conn.execute(
                """INSERT INTO silver_entity_links
                (observation_id, resolution_version, unified_job_id, candidate_entity_id, match_method,
                 match_score, match_status, review_reason, previous_unified_job_id, matched_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(observation_id, resolution_version) DO UPDATE SET
                  unified_job_id=excluded.unified_job_id, candidate_entity_id=excluded.candidate_entity_id,
                  match_method=excluded.match_method, match_score=excluded.match_score,
                  match_status=excluded.match_status, review_reason=excluded.review_reason,
                  previous_unified_job_id=excluded.previous_unified_job_id,
                  matched_at=excluded.matched_at""",
                (observation_id, resolution_version, unified_job_id, candidate_entity_id,
                 match_method, match_score, match_status, review_reason,
                 previous_unified_job_id, utc_now()),
            )

    def get_link(self, observation_id: str, resolution_version: str) -> Optional[sqlite3.Row]:
        with closing(self._connect()) as conn:
            return conn.execute(
                "SELECT * FROM silver_entity_links WHERE observation_id = ? AND resolution_version = ?",
                (observation_id, resolution_version),
            ).fetchone()

    def list_links(self, observation_id: str) -> list[sqlite3.Row]:
        with closing(self._connect()) as conn:
            return conn.execute(
                "SELECT * FROM silver_entity_links WHERE observation_id = ? ORDER BY matched_at, resolution_version",
                (observation_id,),
            ).fetchall()

    def list_entity_links(self, unified_job_id: str) -> list[sqlite3.Row]:
        with closing(self._connect()) as conn:
            return conn.execute(
                "SELECT * FROM silver_entity_links WHERE unified_job_id = ? ORDER BY matched_at, resolution_version",
                (unified_job_id,),
            ).fetchall()

    def update_entity_observation(self, *, platform: str, source_job_id: str,
                                  observed_at: str, content_hash: str,
                                  title: Optional[str] = None,
                                  job_level: Optional[str] = None,
                                  work_mode: Optional[str] = None,
                                  salary_min_vnd: Optional[int] = None,
                                  salary_max_vnd: Optional[int] = None,
                                  description: Optional[str] = None) -> Optional[str]:
        now = utc_now()
        with self.transaction() as conn:
            row = conn.execute(
                """SELECT s.unified_job_id, e.last_content_hash, e.entity_status
                   FROM source_job_registry s JOIN entity_registry e
                     ON e.unified_job_id = s.unified_job_id
                   WHERE s.platform = ? AND s.source_job_id = ?""",
                (platform, source_job_id),
            ).fetchone()
            if not row:
                return None
            changed = row["last_content_hash"] not in (None, content_hash)
            if row["entity_status"] == "CLOSED":
                status = "REOPENED"
            else:
                status = "UPDATED" if changed else row["entity_status"]
            assignments = ["last_seen_at = ?", "updated_at = ?", "last_content_hash = ?", "entity_status = ?"]
            values: list[object] = [observed_at, now, content_hash, status]
            optional = {
                "canonical_title": title,
                "canonical_job_level": job_level,
                "canonical_work_mode": work_mode,
                "canonical_salary_min_vnd": salary_min_vnd,
                "canonical_salary_max_vnd": salary_max_vnd,
                "canonical_description": description,
            }
            for column, value in optional.items():
                if value is not None:
                    assignments.append(f"{column} = ?")
                    values.append(value)
            values.append(row["unified_job_id"])
            conn.execute(
                f"UPDATE entity_registry SET {', '.join(assignments)} WHERE unified_job_id = ?",
                values,
            )
            conn.execute(
                "UPDATE source_job_registry SET last_observed_at = ?, updated_at = ? "
                "WHERE platform = ? AND source_job_id = ?",
                (observed_at, now, platform, source_job_id),
            )
            return row["unified_job_id"]

    def get_or_create_entity(
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
        job_level: Optional[str] = None,
        work_mode: Optional[str] = None,
        salary_min_vnd: Optional[int] = None,
        salary_max_vnd: Optional[int] = None,
        description: Optional[str] = None,
        content_hash: Optional[str] = None,
    ) -> tuple[str, bool]:
        """Atomically re-check identity and create at most one entity."""
        now = utc_now()
        with self.transaction() as conn:
            source = conn.execute(
                "SELECT unified_job_id FROM source_job_registry WHERE platform = ? AND source_job_id = ?",
                (platform, source_job_id),
            ).fetchone()
            if source:
                return source["unified_job_id"], False
            conn.execute(
                """INSERT INTO entity_registry
                (unified_job_id, candidate_identity_fingerprint, canonical_title,
                 canonical_company, canonical_city, canonical_job_level,
                 canonical_work_mode, canonical_salary_min_vnd,
                 canonical_salary_max_vnd, canonical_description, last_content_hash,
                 first_seen_at, last_seen_at, resolution_version, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (unified_job_id, fingerprint, title, company, city, job_level,
                 work_mode, salary_min_vnd, salary_max_vnd, description, content_hash,
                 observed_at, observed_at, resolution_version, now, now),
            )
            conn.execute(
                """INSERT INTO source_job_registry
                (platform, source_job_id, unified_job_id, first_observed_at,
                 last_observed_at, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (platform, source_job_id, unified_job_id, observed_at,
                 observed_at, now, now),
            )
            return unified_job_id, True

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
        job_level: Optional[str] = None,
        work_mode: Optional[str] = None,
        salary_min_vnd: Optional[int] = None,
        salary_max_vnd: Optional[int] = None,
        description: Optional[str] = None,
        content_hash: Optional[str] = None,
    ) -> None:
        now = utc_now()
        with self.transaction() as conn:
            conn.execute(
                """INSERT INTO entity_registry
                (unified_job_id, candidate_identity_fingerprint, canonical_title,
                 canonical_company, canonical_city, canonical_job_level,
                 canonical_work_mode, canonical_salary_min_vnd,
                 canonical_salary_max_vnd, canonical_description, last_content_hash,
                 first_seen_at, last_seen_at, resolution_version, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (unified_job_id, fingerprint, title, company, city, job_level,
                 work_mode, salary_min_vnd, salary_max_vnd, description, content_hash,
                 observed_at, observed_at, resolution_version, now, now),
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

import os
import sqlite3
import uuid
from datetime import datetime
from typing import Optional, Dict, Any, List


class StateManager:
    """
    Sổ cái trạng thái (State Store) cho Tầng Bronze.
    Chịu trách nhiệm theo dõi Watermark (source-native & ingestion-time), lịch sử chạy,
    sổ cái kiểm toán gom tệp, quản lý Atomic Concurrency Lock và Dead PID / Stale Recovery.
    """

    STALE_LEASE_SECONDS = 7200  # 2 giờ

    def __init__(self, db_path: str = "data/state_store.db"):
        self.db_path = db_path
        os.makedirs(os.path.dirname(os.path.abspath(self.db_path)), exist_ok=True)
        self._active_runs: Dict[str, str] = {}
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._get_connection() as conn:
            cursor = conn.cursor()

            # 1. Bảng lưu trạng thái watermark
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS ingestion_state (
                    source_name         TEXT PRIMARY KEY,
                    last_watermark_ts   TEXT,
                    source_watermark    TEXT,
                    watermark_strategy  TEXT DEFAULT 'id_cursor',
                    last_record_id      TEXT,
                    last_run_status     TEXT,
                    records_ingested    INTEGER DEFAULT 0,
                    updated_at          TEXT NOT NULL
                )
            """)

            # 2. Bảng lưu lịch sử kiểm toán
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS ingestion_runs (
                    run_id              TEXT PRIMARY KEY,
                    source_name         TEXT NOT NULL,
                    mode                TEXT NOT NULL,
                    started_at          TEXT NOT NULL,
                    finished_at         TEXT,
                    status              TEXT NOT NULL,
                    records_ingested    INTEGER DEFAULT 0,
                    bronze_s3_uri       TEXT,
                    duration_sec        REAL DEFAULT 0.0,
                    error_message       TEXT,
                    process_id          INTEGER,
                    heartbeat_at        TEXT
                )
            """)

            # 3. Bảng lưu kiểm toán gom tệp nhỏ
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS compaction_audit_logs (
                    compaction_id       TEXT PRIMARY KEY,
                    partition_prefix    TEXT NOT NULL,
                    files_merged_count  INTEGER NOT NULL,
                    total_records       INTEGER NOT NULL,
                    old_total_bytes     INTEGER NOT NULL,
                    new_compacted_bytes INTEGER NOT NULL,
                    bytes_saved         INTEGER NOT NULL,
                    compacted_key       TEXT NOT NULL,
                    executed_at         TEXT NOT NULL
                )
            """)

            # 4. Bảng lưu Sổ cái tệp tin Manifest
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS ingestion_objects (
                    object_key          TEXT PRIMARY KEY,
                    run_id              TEXT NOT NULL,
                    source_name         TEXT NOT NULL,
                    file_size_bytes     INTEGER NOT NULL,
                    records_count       INTEGER NOT NULL,
                    checksum_sha256     TEXT,
                    committed_at        TEXT NOT NULL,
                    is_compacted        INTEGER DEFAULT 0,
                    compacted_into_key  TEXT
                )
            """)
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_obj_run_id ON ingestion_objects(run_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_obj_source ON ingestion_objects(source_name)")

            # Migrations
            try:
                cursor.execute("ALTER TABLE ingestion_runs ADD COLUMN process_id INTEGER")
            except Exception:
                pass

            try:
                cursor.execute("ALTER TABLE ingestion_runs ADD COLUMN heartbeat_at TEXT")
            except Exception:
                pass

            try:
                cursor.execute("ALTER TABLE ingestion_state ADD COLUMN source_watermark TEXT")
            except Exception:
                pass

            try:
                cursor.execute("ALTER TABLE ingestion_state ADD COLUMN watermark_strategy TEXT DEFAULT 'id_cursor'")
            except Exception:
                pass

            try:
                cursor.execute("""
                    CREATE UNIQUE INDEX IF NOT EXISTS idx_active_running_per_source
                    ON ingestion_runs(source_name)
                    WHERE status = 'RUNNING'
                """)
            except sqlite3.IntegrityError:
                cursor.execute("""
                    UPDATE ingestion_runs
                    SET status = 'ABANDONED',
                        error_message = 'Cleaned duplicate RUNNING before index creation'
                    WHERE status = 'RUNNING'
                """)
                cursor.execute("""
                    CREATE UNIQUE INDEX IF NOT EXISTS idx_active_running_per_source
                    ON ingestion_runs(source_name)
                    WHERE status = 'RUNNING'
                """)

            conn.commit()

    @staticmethod
    def _is_pid_alive(pid: Optional[int]) -> bool:
        if not pid:
            return False
        try:
            os.kill(pid, 0)
            return True
        except (OSError, ProcessLookupError):
            return False
        except Exception:
            return False

    def _recover_stale_runs(self, source_name: str) -> None:
        now_dt = datetime.utcnow()
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT run_id, started_at, process_id, heartbeat_at
                FROM ingestion_runs
                WHERE source_name = ? AND status = 'RUNNING'
            """, (source_name,))
            running_runs = cursor.fetchall()

            for row in running_runs:
                r_id = row["run_id"]
                s_at = row["started_at"]
                hb_at = row["heartbeat_at"] if "heartbeat_at" in row.keys() else None
                pid = row["process_id"] if "process_id" in row.keys() else None

                should_abandon = False
                reason = ""

                if pid and not self._is_pid_alive(pid):
                    should_abandon = True
                    reason = f"Process dead: PID {pid} is no longer running"
                else:
                    try:
                        last_active_str = hb_at or s_at
                        s_dt = datetime.fromisoformat(last_active_str.replace("Z", ""))
                        elapsed = (now_dt - s_dt).total_seconds()
                        if elapsed > self.STALE_LEASE_SECONDS:
                            should_abandon = True
                            reason = f"Stale run: Exceeded lease timeout ({self.STALE_LEASE_SECONDS}s)"
                    except Exception:
                        pass

                if should_abandon:
                    try:
                        s_dt = datetime.fromisoformat(s_at.replace("Z", ""))
                        elapsed_sec = round((now_dt - s_dt).total_seconds(), 2)
                    except Exception:
                        elapsed_sec = 0.0

                    cursor.execute("""
                        UPDATE ingestion_runs
                        SET status = 'ABANDONED',
                            finished_at = ?,
                            duration_sec = ?,
                            error_message = ?
                        WHERE run_id = ?
                    """, (now_dt.isoformat() + "Z", elapsed_sec, reason, r_id))
            conn.commit()

    def acquire_run_lock(
        self,
        source_name: str,
        run_id: Optional[str] = None,
        mode: str = "incremental"
    ) -> Optional[str]:
        source_clean = source_name.lower().strip()
        now_dt = datetime.utcnow()
        now_str = now_dt.isoformat() + "Z"
        current_pid = os.getpid()

        self._recover_stale_runs(source_clean)
        active_run_id = run_id or f"{source_clean}_{mode}_{now_dt.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:4]}"

        conn = sqlite3.connect(self.db_path, timeout=3.0)
        conn.row_factory = sqlite3.Row
        try:
            cursor = conn.cursor()
            cursor.execute("BEGIN IMMEDIATE")

            # The partial unique index is the lock decision. Do not use a
            # SELECT-then-INSERT check: two processes can observe the same gap.
            cursor.execute("""
                INSERT INTO ingestion_runs (run_id, source_name, mode, started_at, status, process_id, heartbeat_at)
                VALUES (?, ?, ?, ?, 'RUNNING', ?, ?)
            """, (active_run_id, source_clean, mode, now_str, current_pid, now_str))

            cursor.execute("""
                INSERT INTO ingestion_state (source_name, last_run_status, updated_at)
                VALUES (?, 'RUNNING', ?)
                ON CONFLICT(source_name) DO UPDATE SET
                    last_run_status = 'RUNNING',
                    updated_at = excluded.updated_at
            """, (source_clean, now_str))

            conn.commit()
            self._active_runs[source_clean] = active_run_id
            return active_run_id

        except sqlite3.IntegrityError:
            # Another process owns the source lease. The transaction is
            # rolled back, so this attempt leaves no RUNNING row behind.
            try:
                conn.rollback()
            except Exception:
                pass
            return None
        except sqlite3.OperationalError:
            try:
                conn.rollback()
            except Exception:
                pass
            return None
        finally:
            conn.close()

    def heartbeat(self, run_id: str) -> bool:
        now_str = datetime.utcnow().isoformat() + "Z"
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE ingestion_runs
                SET heartbeat_at = ?
                WHERE run_id = ? AND status = 'RUNNING'
            """, (now_str, run_id))
            conn.commit()
            return cursor.rowcount > 0

    def get_watermark(self, source_name: str) -> Optional[Dict[str, Any]]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM ingestion_state WHERE source_name = ?", (source_name,))
            row = cursor.fetchone()
            return dict(row) if row else None

    def mark_running(
        self,
        source_name: str,
        run_id: Optional[str] = None,
        mode: str = "incremental"
    ) -> str:
        """Compatibility wrapper that uses the same exclusive lock path."""
        active_run_id = self.acquire_run_lock(
            source_name=source_name,
            run_id=run_id,
            mode=mode,
        )
        if not active_run_id:
            raise RuntimeError(f"Concurrent run active for source {source_name}")
        return active_run_id

    def commit_success(
        self,
        source_name: str,
        watermark_ts: str,
        last_record_id: Optional[str] = None,
        records_ingested: int = 0,
        run_id: Optional[str] = None,
        bronze_uri: Optional[str] = None,
        source_watermark: Optional[str] = None,
        watermark_strategy: str = "id_cursor"
    ) -> None:
        now_dt = datetime.utcnow()
        now_str = now_dt.isoformat() + "Z"
        source_clean = source_name.lower().strip()

        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO ingestion_state (
                    source_name, last_watermark_ts, source_watermark, watermark_strategy,
                    last_record_id, last_run_status, records_ingested, updated_at
                ) VALUES (?, ?, ?, ?, ?, 'SUCCESS', ?, ?)
                ON CONFLICT(source_name) DO UPDATE SET
                    last_watermark_ts = excluded.last_watermark_ts,
                    source_watermark = COALESCE(excluded.source_watermark, ingestion_state.source_watermark),
                    watermark_strategy = excluded.watermark_strategy,
                    last_record_id = excluded.last_record_id,
                    last_run_status = 'SUCCESS',
                    records_ingested = excluded.records_ingested,
                    updated_at = excluded.updated_at
            """, (source_clean, watermark_ts, source_watermark, watermark_strategy, last_record_id, records_ingested, now_str))

            active_run_id = run_id or self._active_runs.get(source_clean)
            if active_run_id:
                cursor.execute("""
                    SELECT started_at FROM ingestion_runs WHERE run_id = ?
                """, (active_run_id,))
                run_row = cursor.fetchone()
                duration = 0.0
                if run_row and run_row["started_at"]:
                    try:
                        s_dt = datetime.fromisoformat(run_row["started_at"].replace("Z", ""))
                        duration = round((now_dt - s_dt).total_seconds(), 2)
                    except Exception:
                        pass

                cursor.execute("""
                    UPDATE ingestion_runs
                    SET finished_at = ?,
                        status = 'SUCCESS',
                        records_ingested = ?,
                        bronze_s3_uri = ?,
                        duration_sec = ?
                    WHERE run_id = ?
                """, (now_str, records_ingested, bronze_uri, duration, active_run_id))
            conn.commit()

    def mark_failure(
        self,
        source_name: str,
        error_message: str,
        run_id: Optional[str] = None
    ) -> None:
        now_dt = datetime.utcnow()
        now_str = now_dt.isoformat() + "Z"
        source_clean = source_name.lower().strip()

        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO ingestion_state (source_name, last_run_status, updated_at)
                VALUES (?, 'FAILED', ?)
                ON CONFLICT(source_name) DO UPDATE SET
                    last_run_status = 'FAILED',
                    updated_at = excluded.updated_at
            """, (source_clean, now_str))

            active_run_id = run_id or self._active_runs.get(source_clean)
            if active_run_id:
                cursor.execute("""
                    SELECT started_at FROM ingestion_runs WHERE run_id = ?
                """, (active_run_id,))
                run_row = cursor.fetchone()
                duration = 0.0
                if run_row and run_row["started_at"]:
                    try:
                        s_dt = datetime.fromisoformat(run_row["started_at"].replace("Z", ""))
                        duration = round((now_dt - s_dt).total_seconds(), 2)
                    except Exception:
                        pass

                cursor.execute("""
                    UPDATE ingestion_runs
                    SET finished_at = ?,
                        status = 'FAILED',
                        duration_sec = ?,
                        error_message = ?
                    WHERE run_id = ?
                """, (now_str, duration, str(error_message), active_run_id))
            conn.commit()

    def register_ingestion_object(
        self,
        object_key: str,
        run_id: str,
        source_name: str,
        file_size_bytes: int,
        records_count: int,
        checksum_sha256: Optional[str] = None
    ) -> None:
        now_str = datetime.utcnow().isoformat() + "Z"
        source_clean = source_name.lower().strip()
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO ingestion_objects (
                    object_key, run_id, source_name, file_size_bytes,
                    records_count, checksum_sha256, committed_at, is_compacted
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 0)
                ON CONFLICT(object_key) DO UPDATE SET
                    file_size_bytes = excluded.file_size_bytes,
                    records_count = excluded.records_count,
                    checksum_sha256 = excluded.checksum_sha256,
                    committed_at = excluded.committed_at
            """, (object_key, run_id, source_clean, file_size_bytes, records_count, checksum_sha256, now_str))
            conn.commit()

    def mark_objects_compacted(self, old_object_keys: List[str], compacted_into_key: str) -> None:
        if not old_object_keys:
            return
        with self._get_connection() as conn:
            cursor = conn.cursor()
            placeholders = ",".join("?" for _ in old_object_keys)
            params = [compacted_into_key] + old_object_keys
            cursor.execute(f"""
                UPDATE ingestion_objects
                SET is_compacted = 1,
                    compacted_into_key = ?
                WHERE object_key IN ({placeholders})
            """, params)
            conn.commit()

    def list_active_bronze_objects(self, source_name: str) -> List[Dict[str, Any]]:
        source_clean = source_name.lower().strip()
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT * FROM ingestion_objects
                WHERE source_name = ? AND is_compacted = 0
                ORDER BY committed_at ASC
            """, (source_clean,))
            return [dict(row) for row in cursor.fetchall()]

    def log_compaction(
        self,
        compaction_id: str,
        partition_prefix: str,
        files_merged_count: int,
        total_records: int,
        old_total_bytes: int,
        new_compacted_bytes: int,
        bytes_saved: int,
        compacted_key: str
    ) -> None:
        now_str = datetime.utcnow().isoformat() + "Z"
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO compaction_audit_logs (
                    compaction_id, partition_prefix, files_merged_count, total_records,
                    old_total_bytes, new_compacted_bytes, bytes_saved, compacted_key, executed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                compaction_id, partition_prefix, files_merged_count, total_records,
                old_total_bytes, new_compacted_bytes, bytes_saved, compacted_key, now_str
            ))
            conn.commit()

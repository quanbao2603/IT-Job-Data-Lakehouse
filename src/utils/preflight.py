"""Read-only Bronze pipeline preflight checks."""
from __future__ import annotations

import os
import shutil
import sqlite3
import urllib.request
from typing import Any, Callable, Dict, Optional

from minio import Minio

from src.storage.atomic_writer import load_env_file


class PreflightError(RuntimeError):
    """Raised when the runtime is not safe to start a Bronze crawl."""


def _memory_available_mb() -> int:
    with open("/proc/meminfo", encoding="utf-8") as handle:
        for line in handle:
            if line.startswith("MemAvailable:"):
                return int(line.split()[1]) // 1024
    raise PreflightError("MemAvailable is unavailable")


def _network_check() -> None:
    endpoint = os.getenv("LAKEHOUSE_PREFLIGHT_HTTPS_URL", "https://www.google.com/generate_204")
    with urllib.request.urlopen(endpoint, timeout=10) as response:
        if response.status >= 400:
            raise PreflightError(f"HTTPS preflight returned HTTP {response.status}")


def _minio_check() -> None:
    load_env_file()
    endpoint = os.getenv("MINIO_ENDPOINT", "localhost:9000")
    access_key = os.getenv("MINIO_ROOT_USER", os.getenv("MINIO_ACCESS_KEY"))
    secret_key = os.getenv("MINIO_ROOT_PASSWORD", os.getenv("MINIO_SECRET_KEY"))
    bucket = os.getenv("MINIO_BRONZE_BUCKET", "bronze")
    if not access_key or not secret_key:
        raise PreflightError("MinIO credentials are not configured")
    client = Minio(
        endpoint=endpoint,
        access_key=access_key,
        secret_key=secret_key,
        secure=os.getenv("MINIO_SECURE", "false").lower() == "true",
    )
    if not client.bucket_exists(bucket):
        raise PreflightError(f"MinIO bucket is unavailable: {bucket}")


def _state_store_check(state_db_path: str) -> None:
    if not os.path.isfile(state_db_path):
        raise PreflightError(f"State Store is missing: {state_db_path}")
    parent = os.path.dirname(os.path.abspath(state_db_path)) or "."
    if not os.access(parent, os.W_OK):
        raise PreflightError(f"State Store directory is not writable: {parent}")
    with sqlite3.connect(state_db_path, timeout=5) as connection:
        result = connection.execute("PRAGMA integrity_check").fetchone()
        if not result or result[0] != "ok":
            raise PreflightError("State Store integrity check failed")
        connection.execute("SELECT 1 FROM ingestion_state LIMIT 1").fetchone()
        running = connection.execute(
            "SELECT source_name FROM ingestion_runs WHERE status = 'RUNNING' LIMIT 1"
        ).fetchone()
        if running:
            raise PreflightError(
                f"State Store has an active RUNNING source: {running[0]}"
            )


def run_preflight(
    project_root: str,
    python_executable: str,
    state_db_path: str,
    *,
    disk_usage_fn: Callable[[str], Any] = shutil.disk_usage,
    memory_available_mb_fn: Callable[[], int] = _memory_available_mb,
    network_check_fn: Callable[[], None] = _network_check,
    minio_check_fn: Callable[[], None] = _minio_check,
    state_store_check_fn: Optional[Callable[[], None]] = None,
) -> Dict[str, Any]:
    """Run all preflight checks without creating, deleting, or changing data."""
    usage = disk_usage_fn(project_root)
    if usage.total <= 0:
        raise PreflightError("Disk usage returned an invalid total")
    disk_free_percent = round((usage.free / usage.total) * 100, 2)
    if disk_free_percent < float(os.getenv("LAKEHOUSE_MIN_FREE_DISK_PERCENT", "15")):
        raise PreflightError(f"Disk free space is below threshold: {disk_free_percent}%")

    memory_available_mb = int(memory_available_mb_fn())
    min_memory_mb = int(os.getenv("LAKEHOUSE_MIN_AVAILABLE_MEMORY_MB", "1024"))
    if memory_available_mb < min_memory_mb:
        raise PreflightError(f"MemAvailable is below threshold: {memory_available_mb}MB")

    network_check_fn()
    minio_check_fn()
    (state_store_check_fn or (lambda: _state_store_check(state_db_path)))()

    load_env_file()
    return {
        "status": "PASS",
        "project_root": project_root,
        "python_executable": python_executable,
        "disk_free_percent": disk_free_percent,
        "memory_available_mb": memory_available_mb,
        "minio_configured": bool(os.getenv("MINIO_ENDPOINT", "localhost:9000")),
        "state_db_path": state_db_path,
    }

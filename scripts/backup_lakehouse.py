"""Back up what cannot be rebuilt: the Gold database and the ingestion state store.

Bronze technically allows a rebuild, but Bronze lives on the same disk as everything else, the
Silver releases are pruned, and nothing had been dumped since 07/11. This writes a restorable
export to `backups/` and proves it is readable before declaring success.

  * `pg_dump --format=custom` of Gold (schema + data), verified with `pg_restore --list`
  * a consistent copy of `data/state_store.db` (sqlite backup API, not `cp`), verified with
    `pragma integrity_check`
  * `manifest.json` with sizes and sha256 so a truncated file is visible

    lakehouse_env/bin/python scripts/backup_lakehouse.py            # write + verify + prune
    lakehouse_env/bin/python scripts/backup_lakehouse.py --keep 2
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from src.storage.atomic_writer import load_env_file  # noqa: E402


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _dump_gold(target: Path) -> Dict[str, Any]:
    env = dict(os.environ)
    env["PGPASSWORD"] = os.environ["GOLD_DB_PASSWORD"]
    command = [
        "pg_dump",
        "--format=custom",
        "--compress=6",
        "--no-owner",
        "--no-privileges",
        "-h", os.getenv("GOLD_DB_HOST", "127.0.0.1"),
        "-p", os.getenv("GOLD_DB_PORT", "5432"),
        "-U", os.environ["GOLD_DB_USER"],
        "-d", os.environ["GOLD_DB_NAME"],
        "-f", str(target),
    ]
    completed = subprocess.run(command, env=env, capture_output=True, text=True, timeout=1800)
    if completed.returncode != 0:
        raise RuntimeError(f"pg_dump failed: {(completed.stderr or '').strip()[:400]}")
    listing = subprocess.run(
        ["pg_restore", "--list", str(target)], capture_output=True, text=True, timeout=300
    )
    if listing.returncode != 0:
        target.unlink(missing_ok=True)
        raise RuntimeError(f"pg_restore --list rejected the dump: {(listing.stderr or '').strip()[:300]}")
    lines = listing.stdout.splitlines()
    tables = sum(1 for line in lines if " TABLE " in line and "TABLE DATA" not in line)
    data_entries = sum(1 for line in lines if " TABLE DATA " in line)
    if not any("TABLE DATA" in line and " dim_job " in line for line in lines):
        target.unlink(missing_ok=True)
        raise RuntimeError("dump has no data entry for gold.dim_job — refusing to keep an empty backup")
    return {
        "file": target.name,
        "bytes": target.stat().st_size,
        "tables": tables,
        "data_entries": data_entries,
        "sha256": _sha256(target),
    }


def _copy_state_db(target: Path) -> Dict[str, Any]:
    source = PROJECT_ROOT / "data" / "state_store.db"
    if not source.exists():
        return {"skipped": "state store missing"}
    # A plain cp of a live SQLite file can capture a torn page; the backup API takes a snapshot.
    with sqlite3.connect(str(source)) as origin, sqlite3.connect(str(target)) as copy:
        origin.backup(copy)
    with sqlite3.connect(str(target)) as check:
        integrity = check.execute("pragma integrity_check").fetchone()[0]
    if integrity != "ok":
        target.unlink(missing_ok=True)
        raise RuntimeError(f"state store backup is corrupt: {integrity}")
    return {"file": target.name, "bytes": target.stat().st_size, "sha256": _sha256(target)}


def prune_backups(root: Path, keep: int) -> List[str]:
    backups = sorted(
        (entry for entry in root.iterdir() if entry.is_dir() and entry.name.startswith("lakehouse_backup_")),
        key=lambda entry: entry.name,
        reverse=True,
    )
    removed = []
    for stale in backups[keep:]:
        shutil.rmtree(stale, ignore_errors=True)
        removed.append(stale.name)
    return removed


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Back up Gold and the ingestion state store")
    parser.add_argument("--root", default=str(PROJECT_ROOT / "backups"))
    parser.add_argument("--keep", type=int, default=int(os.getenv("LAKEHOUSE_BACKUP_KEEP", "4")))
    args = parser.parse_args(argv)

    load_env_file()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    target = Path(args.root) / f"lakehouse_backup_{stamp}"
    target.mkdir(parents=True, exist_ok=True)

    manifest: Dict[str, Any] = {"created_at": datetime.now(timezone.utc).isoformat(), "directory": target.name}
    manifest["gold"] = _dump_gold(target / "gold.dump")
    manifest["state_store"] = _copy_state_db(target / "state_store.db")
    manifest["pruned"] = prune_backups(Path(args.root), max(1, args.keep))

    (target / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1))
    print(json.dumps({"status": "SUCCESS", **manifest}, ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())

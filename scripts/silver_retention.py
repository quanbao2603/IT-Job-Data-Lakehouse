"""Bound Silver storage: drop release directories nobody can still need.

Every replay writes a full copy of observations/entities/links (25-36 MB), and 20 of them had
piled up to 501 MB. This keeps:

  * every release a Gold load_run points at (deleting one would break a published release),
  * the newest `--keep-last` releases whatever their status,
  * anything younger than `--keep-days`.

Everything else is listed, and only removed with --apply.

    lakehouse_env/bin/python scripts/silver_retention.py --dry-run
    lakehouse_env/bin/python scripts/silver_retention.py --apply
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Dict, Iterable, List, Tuple

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))


def _dir_size(path: Path) -> int:
    total = 0
    for root, _dirs, files in os.walk(path):
        for name in files:
            try:
                total += (Path(root) / name).stat().st_size
            except OSError:
                continue
    return total


def select_prunable(
    releases: Dict[str, datetime],
    *,
    protected: Iterable[str],
    keep_last: int,
    keep_days: int,
    today: datetime,
) -> List[str]:
    """Return release names that may be deleted, oldest first.

    `releases` maps release directory name -> last-modified time. `protected` holds names Gold
    still references. Guard rails: the newest release is never pruned, and neither is anything
    inside the age window.
    """
    if not releases:
        return []
    keep = set(protected)
    ordered = sorted(releases.items(), key=lambda kv: kv[1], reverse=True)
    keep.update(name for name, _ts in ordered[: max(1, keep_last)])
    cutoff = today - timedelta(days=max(0, keep_days))
    keep.update(name for name, ts in releases.items() if ts >= cutoff)
    if ordered:
        keep.add(ordered[0][0])

    prunable = [name for name in releases if name not in keep]
    prunable.sort(key=lambda name: releases[name])
    return prunable


def _gold_referenced_releases(protect_days: int = 7) -> set[str]:
    """Silver releases a Gold release still depends on.

    Only load_runs from the last `protect_days` are counted, plus the active release. Anything
    older is history: its Gold output is already published and verified, so keeping a 30 MB copy
    of its Silver forever is what made retention useless (12 of 14 releases were "protected").
    """
    import psycopg2

    conn = psycopg2.connect(
        host=os.getenv("GOLD_DB_HOST", "127.0.0.1"),
        port=os.getenv("GOLD_DB_PORT", "5432"),
        dbname=os.environ["GOLD_DB_NAME"],
        user=os.environ["GOLD_DB_USER"],
        password=os.environ["GOLD_DB_PASSWORD"],
    )
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                select distinct silver_release_id from gold_control.load_run
                where silver_release_id is not null
                  and (started_at >= now() - make_interval(days => %s)
                       or gold_release_id in (select gold_release_id from gold_control.active_release))
                """,
                (max(0, protect_days),),
            )
            refs = {row[0] for row in cur.fetchall()}
    finally:
        conn.close()
    # Gold stores 'silver_release_<run_id>' while the directory is '<run_id>'.
    return {r[len("silver_release_"):] if r.startswith("silver_release_") else r for r in refs}


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Prune old Silver releases")
    parser.add_argument("--silver-root", default=os.getenv("LAKEHOUSE_SILVER_ROOT", str(PROJECT_ROOT / "data/silver")))
    parser.add_argument("--keep-last", type=int, default=int(os.getenv("LAKEHOUSE_SILVER_KEEP_LAST", "4")))
    parser.add_argument("--keep-days", type=int, default=int(os.getenv("LAKEHOUSE_SILVER_KEEP_DAYS", "3")))
    parser.add_argument(
        "--protect-gold-days",
        type=int,
        default=int(os.getenv("LAKEHOUSE_SILVER_PROTECT_GOLD_DAYS", "3")),
        help="how long a Gold release keeps its Silver copy alive",
    )
    parser.add_argument("--apply", action="store_true", help="actually delete; default is a dry run")
    parser.add_argument("--skip-gold-check", action="store_true", help="do not protect releases Gold references")
    args = parser.parse_args(argv)

    root = Path(args.silver_root)
    # Shared silver roots, not releases: deleting these would destroy every release at once.
    reserved = {"releases", "audit_logs", "entities", "entity_links", "observations", "quarantine"}
    releases = {
        entry.name: datetime.fromtimestamp(entry.stat().st_mtime)
        for entry in root.iterdir()
        if entry.is_dir() and entry.name not in reserved
    }
    protected = set() if args.skip_gold_check else _gold_referenced_releases(args.protect_gold_days)
    prunable = select_prunable(
        releases,
        protected=protected,
        keep_last=args.keep_last,
        keep_days=args.keep_days,
        today=datetime.now(),
    )

    freed = 0
    removed: List[Tuple[str, int]] = []
    for name in prunable:
        path = root / name
        size = _dir_size(path)
        if args.apply:
            shutil.rmtree(path, ignore_errors=False)
            manifest = root / "releases" / f"silver_release_{name}.json"
            if manifest.exists():
                manifest.unlink()
        freed += size
        removed.append((name, size))

    summary = {
        "status": "APPLIED" if args.apply else "DRY_RUN",
        "releases_total": len(releases),
        "protected_by_gold": len(protected & set(releases)),
        "kept_last": args.keep_last,
        "kept_days": args.keep_days,
        "protect_gold_days": args.protect_gold_days,
        "pruned_count": len(removed),
        "freed_bytes": freed,
        "pruned": [{"release": name, "bytes": size} for name, size in removed],
    }
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Run Bronze compaction outside Airflow.

The daily DAG only looked at a single day and required seven files, so with one or two files per
source per day it merged nothing for weeks and wrote no audit rows at all ('compaction never ran').
This command compacts a window of days, thresholds at two files, and prints what it did.

    lakehouse_env/bin/python scripts/compact_bronze.py --window-days 30 --min-files 2 --dry-run
    lakehouse_env/bin/python scripts/compact_bronze.py --window-days 30
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from src.storage.compaction_engine import CompactionEngine  # noqa: E402
from src.storage.compaction_plan import target_partition_prefixes  # noqa: E402

SOURCES = (
    "careerviet", "devwork", "itviec", "topcv", "topdev",
    "upzi", "vieclam24h", "vietnamdevs", "vietnamworks",
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Compact Bronze partitions across a day window")
    parser.add_argument("--as-of", default=date.today().isoformat())
    parser.add_argument("--age-days", type=int, default=int(os.getenv("LAKEHOUSE_COMPACTION_AGE_DAYS", "2")))
    parser.add_argument("--window-days", type=int, default=int(os.getenv("LAKEHOUSE_COMPACTION_WINDOW_DAYS", "14")))
    parser.add_argument("--min-files", type=int, default=int(os.getenv("LAKEHOUSE_COMPACTION_MIN_FILES", "2")))
    parser.add_argument("--dry-run", action="store_true", help="list the partitions without touching Bronze")
    parser.add_argument("--source", action="append", help="limit to one source (repeatable)")
    args = parser.parse_args(argv)

    sources = tuple(args.source) if args.source else SOURCES
    prefixes = target_partition_prefixes(
        args.as_of, sources, age_days=args.age_days, window_days=args.window_days
    )
    if args.dry_run:
        print(json.dumps({"status": "DRY_RUN", "partitions": len(prefixes), "first": prefixes[:5]}, ensure_ascii=False))
        return 0

    engine = CompactionEngine(state_db_path=os.getenv("LAKEHOUSE_STATE_DB_PATH", "data/state_store.db"))
    results = []
    for prefix in prefixes:
        try:
            result = engine.compact_partition(
                prefix,
                min_files_threshold=args.min_files,
                purge_small_files=False,
                archive_small_files=True,
            )
        except Exception as exc:  # keep going; one bad partition must not hide the rest
            result = {"status": "ERROR", "message": f"{type(exc).__name__}: {exc}"}
        result["partition"] = prefix
        results.append(result)

    compacted = [r for r in results if r.get("status") == "COMPACTED"]
    summary = {
        "status": "SUCCESS",
        "partitions_scanned": len(results),
        "compacted": len(compacted),
        "skipped": sum(1 for r in results if r.get("status") == "SKIPPED"),
        "errors": [r for r in results if r.get("status") == "ERROR"],
        "files_merged": sum(r.get("files_merged_count", 0) for r in compacted),
        "records": sum(r.get("total_records", 0) for r in compacted),
        "bytes_before": sum(r.get("old_total_bytes", 0) for r in compacted),
        "bytes_after": sum(r.get("new_compacted_bytes", 0) for r in compacted),
        "bytes_saved": sum(r.get("bytes_saved", 0) for r in compacted),
    }
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0 if not summary["errors"] else 1


if __name__ == "__main__":
    sys.exit(main())

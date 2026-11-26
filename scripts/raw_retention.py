"""Expire raw response copies — a debugging window, not an archive.

`bronze/raw/` costs roughly 70 KB per page (gzipped). Keeping a week is enough to reproduce a
parser bug on a live posting; older copies are dead weight. Default 7 days.

    lakehouse_env/bin/python scripts/raw_retention.py            # dry run
    lakehouse_env/bin/python scripts/raw_retention.py --apply
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from minio import Minio  # noqa: E402

from src.storage.atomic_writer import load_env_file  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Delete raw response copies past their window")
    parser.add_argument("--days", type=int, default=int(os.getenv("LAKEHOUSE_RAW_RETENTION_DAYS", "7")))
    parser.add_argument("--apply", action="store_true", help="actually delete; default is a dry run")
    args = parser.parse_args(argv)

    load_env_file()
    client = Minio(
        os.getenv("MINIO_ENDPOINT", "localhost:9000").replace("http://", "").replace("https://", ""),
        access_key=os.getenv("MINIO_ROOT_USER", os.getenv("MINIO_ACCESS_KEY")),
        secret_key=os.getenv("MINIO_ROOT_PASSWORD", os.getenv("MINIO_SECRET_KEY")),
        secure=os.getenv("MINIO_SECURE", "false").lower() == "true",
    )
    bucket = os.getenv("MINIO_BRONZE_BUCKET", "bronze")
    cutoff = datetime.now(timezone.utc) - timedelta(days=max(1, args.days))

    expired, kept_bytes = [], 0
    for obj in client.list_objects(bucket, prefix="raw/", recursive=True):
        modified = obj.last_modified
        if modified is not None and modified.tzinfo is None:
            modified = modified.replace(tzinfo=timezone.utc)
        if modified and modified < cutoff:
            expired.append((obj.object_name, obj.size))
        else:
            kept_bytes += obj.size or 0

    freed = sum(size for _key, size in expired)
    if args.apply:
        for key, _size in expired:
            client.remove_object(bucket, key)

    print(
        json.dumps(
            {
                "status": "APPLIED" if args.apply else "DRY_RUN",
                "retention_days": args.days,
                "expired_objects": len(expired),
                "freed_bytes": freed,
                "kept_bytes": kept_bytes,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())

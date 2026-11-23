"""Replay Silver from the committed Bronze objects.

Use after a parser/normalization change: Bronze is unchanged, so a replay re-derives
observations, entities and links and seals a new Silver release without touching any
external source.

    lakehouse_env/bin/python scripts/replay_silver.py [--run-id manual_facet_fix_<stamp>]

The run id becomes the release id (`silver_release_<run_id>`), which Gold then consumes.
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from minio import Minio  # noqa: E402

from src.etl.bronze_to_silver import BronzeToSilverPipeline  # noqa: E402
from src.etl.silver_writer import SilverWriter  # noqa: E402
from src.storage.atomic_writer import AtomicWriter, load_env_file  # noqa: E402
from src.storage.entity_registry import EntityRegistry  # noqa: E402
from src.storage.state_manager import StateManager  # noqa: E402

PLATFORMS = {
    "careerviet", "upzi", "vietnamdevs", "vieclam24h", "devwork",
    "itviec", "topcv", "topdev", "vietnamworks",
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Replay Silver from Bronze without crawling")
    parser.add_argument("--run-id", default=None, help="defaults to manual_replay_<stamp>")
    parser.add_argument("--state-db", default=os.getenv("LAKEHOUSE_STATE_DB_PATH", str(PROJECT_ROOT / "data/state_store.db")))
    args = parser.parse_args(argv)

    load_env_file()
    run_id = args.run_id or f"manual_replay_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    print("run_id:", run_id)

    client = Minio(
        endpoint=os.getenv("MINIO_ENDPOINT", "localhost:9000"),
        access_key=os.getenv("MINIO_ROOT_USER", os.getenv("MINIO_ACCESS_KEY")),
        secret_key=os.getenv("MINIO_ROOT_PASSWORD", os.getenv("MINIO_SECRET_KEY")),
        secure=os.getenv("MINIO_SECURE", "false").lower() == "true",
    )
    bucket = os.getenv("MINIO_SILVER_BUCKET", "silver")
    if not client.bucket_exists(bucket):
        client.make_bucket(bucket)

    registry = EntityRegistry(args.state_db)
    state = StateManager(args.state_db)
    pipeline = BronzeToSilverPipeline(
        registry=registry,
        writer=SilverWriter(
            os.getenv("LAKEHOUSE_SILVER_ROOT", str(PROJECT_ROOT / "data/silver")),
            max_records_per_batch=int(os.getenv("LAKEHOUSE_SILVER_MAX_RECORDS_PER_BATCH", "5000")),
            minio_client=client,
            bucket=bucket,
            object_namespace=run_id,
        ),
        run_id=run_id,
        resolution_version=os.getenv("LAKEHOUSE_SILVER_RESOLUTION_VERSION", "v1"),
        pending_review_path=os.getenv(
            "LAKEHOUSE_PENDING_REVIEW_PATH", str(PROJECT_ROOT / "data/operational/pending_review.csv")
        ),
    )

    def reader(object_key: str):
        atomic = AtomicWriter(
            endpoint=os.getenv("MINIO_ENDPOINT", "localhost:9000"),
            access_key=os.getenv("MINIO_ROOT_USER", os.getenv("MINIO_ACCESS_KEY")),
            secret_key=os.getenv("MINIO_ROOT_PASSWORD", os.getenv("MINIO_SECRET_KEY")),
            secure=os.getenv("MINIO_SECURE", "false").lower() == "true",
        )
        yield from atomic.stream_bronze_records(object_key)

    report = pipeline.process_bronze_objects(state, reader, platforms=PLATFORMS)
    print("status:", report.get("status"))
    print("silver_release_id:", report.get("silver_release_id"))
    return 0 if report.get("status") == "SUCCESS" else 1


if __name__ == "__main__":
    sys.exit(main())

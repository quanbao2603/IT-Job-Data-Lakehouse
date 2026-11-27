"""Re-fetch a handful of postings whose stored title has two words glued together.

The full re-crawl fixed eight platforms; TopCV tripped its rate limit after 24 pages, leaving 44
titles like "senhior productdeveloperexecutive". Those are few enough that fetching them one by
one at the pace TopCV tolerates (15-19s) costs a quarter of an hour instead of five hours, and the
run stops cleanly on the first block instead of burning the cooldown.

    lakehouse_env/bin/python scripts/repair_glued_titles.py --dry-run --limit 2
    lakehouse_env/bin/python scripts/repair_glued_titles.py
"""
from __future__ import annotations

import argparse
import datetime
import os
import random
import sys
import time
import uuid
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from src.storage.metadata_envelope import MetadataEnvelope  # noqa: E402

# A single "word" longer than 16 letters in a Vietnamese title is the fingerprint of two fragments
# glued by the old parser; real words that long do not occur in this corpus.
GLUED_SQL = r"""
    select distinct u.unified_job_id, u.source_url
    from gold.v_job_snapshot s
    join gold.dim_job j on j.job_key = s.job_key
    join gold.v_job_source_url u on u.unified_job_id = s.unified_job_id
    where s.gold_release_id = (select gold_release_id from gold_control.active_release)
      and u.platform_code = 'topcv'
      and j.canonical_title ~ '\m[a-zà-ỹ]{16,}\M'
      and u.source_url is not null
"""


def glued_targets(limit: int | None) -> list[tuple[str, str]]:
    import psycopg2

    conn = psycopg2.connect(
        host=os.getenv("GOLD_DB_HOST", "127.0.0.1"),
        port=os.getenv("GOLD_DB_PORT", "5432"),
        dbname=os.environ["GOLD_DB_NAME"],
        user=os.environ["GOLD_DB_READER_USER"],
        password=os.environ["GOLD_DB_READER_PASSWORD"],
    )
    try:
        conn.set_session(readonly=True)
        with conn.cursor() as cur:
            cur.execute(GLUED_SQL)
            rows = [(row[0], row[1]) for row in cur.fetchall()]
    finally:
        conn.close()
    rows.sort(key=lambda item: item[1])
    return rows[:limit] if limit else rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Re-fetch postings with glued titles")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--delay-min", type=float, default=float(os.getenv("TOPCV_DELAY_MIN", "15")))
    parser.add_argument("--delay-max", type=float, default=float(os.getenv("TOPCV_DELAY_MAX", "19")))
    parser.add_argument("--dry-run", action="store_true", help="fetch and parse, write nothing")
    args = parser.parse_args(argv)

    targets = glued_targets(args.limit)
    print(f"cần cào lại: {len(targets)} tin TopCV", flush=True)
    if not targets:
        return 0

    from src.crawlers.topcv_crawler import TopCVCrawler

    crawler = TopCVCrawler()
    run_id = f"topcv_title_repair_{datetime.datetime.utcnow():%Y%m%d_%H%M%S}_{uuid.uuid4().hex[:4]}"
    records, stopped_early = [], False

    for index, (unified_id, url) in enumerate(targets, 1):
        try:
            html = crawler._make_request(url)
        except Exception as exc:  # a block must end the pass, not the day
            print(f"  [{index}/{len(targets)}] dừng: {type(exc).__name__} — {str(exc)[:80]}", flush=True)
            stopped_early = True
            break
        if not html:
            print(f"  [{index}/{len(targets)}] không tải được (tin có thể đã bị xoá)", flush=True)
            continue
        try:
            record = crawler._parse_job_detail_raw(html, url)
            records.append(record)
            print(f"  [{index}/{len(targets)}] {record.get('job_title', '')[:56]!r}", flush=True)
        except Exception as exc:
            print(f"  [{index}/{len(targets)}] lỗi parse: {type(exc).__name__}", flush=True)
        if index < len(targets):
            time.sleep(random.uniform(args.delay_min, args.delay_max))

    print(f"lấy được {len(records)} bản ghi" + (" (dừng sớm vì bị chặn)" if stopped_early else ""), flush=True)
    if args.dry_run or not records:
        return 0 if not stopped_early else 2

    from src.storage.state_manager import StateManager

    state = StateManager(os.getenv("LAKEHOUSE_STATE_DB_PATH", str(PROJECT_ROOT / "data/state_store.db")))
    active_run = state.acquire_run_lock("topcv", run_id=run_id, mode="repair")

    def enveloped():
        for record in records:
            yield MetadataEnvelope.wrap(source="topcv", raw_payload=record, run_id=active_run)

    result = crawler.writer.write_atomic(source="topcv", records=enveloped(), run_id=active_run)
    state.register_ingestion_object(
        object_key=result["bronze_key"],
        run_id=active_run,
        source_name="topcv",
        file_size_bytes=result["compressed_size_bytes"],
        records_count=result["records_count"],
        checksum_sha256=result.get("file_checksum_sha256"),
    )
    state.commit_success(
        source_name="topcv",
        watermark_ts=datetime.datetime.utcnow().isoformat() + "Z",
        records_ingested=result["records_count"],
        run_id=active_run,
        bronze_uri=result["bronze_s3_uri"],
        watermark_strategy="id_cursor",
    )
    print(f"đã ghi Bronze: {result['bronze_key']} ({result['records_count']} bản ghi)", flush=True)
    return 0 if not stopped_early else 2


if __name__ == "__main__":
    sys.exit(main())

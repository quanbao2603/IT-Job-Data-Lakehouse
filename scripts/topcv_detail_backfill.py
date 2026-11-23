"""Re-fetch TopCV job detail pages and append them to Bronze, with resume support.

TopCV throttles a full crawl (HTTP 429/403) after a few dozen requests, so the normal
crawler cannot finish 1.100 detail pages in one pass. This script:

  * takes the TopCV job URLs already known from the current Silver release,
  * fetches them slowly (default 9-12s apart) with the crawler's own parser,
  * checkpoints every record to a local JSONL so a block loses nothing,
  * on HTTP 403 waits 15 minutes and retries; after two consecutive blocks it stops
    cleanly so it can be resumed later,
  * commits everything collected so far as one Bronze object and registers it in the
    state store, so the next Silver replay picks it up.

Usage:
    lakehouse_env/bin/python scripts/topcv_detail_backfill.py --limit 50
    lakehouse_env/bin/python scripts/topcv_detail_backfill.py            # all URLs
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from src.crawlers.topcv_crawler import TopCVCrawler  # noqa: E402
from src.gold.silver_release_reader import latest_release, read_dataset  # noqa: E402
from src.storage.atomic_writer import AtomicWriter, load_env_file  # noqa: E402
from src.storage.metadata_envelope import MetadataEnvelope  # noqa: E402
from src.storage.state_manager import StateManager  # noqa: E402

STATE_DB = PROJECT_ROOT / "data/state_store.db"
OPERATIONAL = PROJECT_ROOT / "data/operational"
RECORDS_PATH = OPERATIONAL / "topcv_backfill_records.jsonl"
CHECKPOINT_PATH = OPERATIONAL / "topcv_backfill_checkpoint.json"


def known_urls(limit: int | None) -> list[str]:
    release = latest_release(str(PROJECT_ROOT / "data/silver"))
    if release is None:
        raise SystemExit("no Silver release found")
    observations = read_dataset(release, str(PROJECT_ROOT / "data/silver"), "observations")
    urls = sorted({o["source_url"] for o in observations if o.get("platform") == "topcv" and o.get("source_url")})
    print(f"topcv urls in {release.release_id}: {len(urls)}")
    return urls[:limit] if limit else urls


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--delay-min", type=float, default=float(os.getenv("TOPCV_DELAY_MIN", "9")))
    parser.add_argument("--delay-max", type=float, default=float(os.getenv("TOPCV_DELAY_MAX", "12")))
    parser.add_argument("--dry-run", action="store_true", help="fetch and parse but do not write Bronze")
    args = parser.parse_args(argv)

    load_env_file()
    OPERATIONAL.mkdir(parents=True, exist_ok=True)

    done: set[str] = set()
    if CHECKPOINT_PATH.exists():
        done = set(json.loads(CHECKPOINT_PATH.read_text()).get("done", []))
    records_path = RECORDS_PATH
    urls = [u for u in known_urls(args.limit) if u not in done]
    print(f"remaining urls: {len(urls)} (already done: {len(done)})")

    crawler = TopCVCrawler()
    state = StateManager(str(STATE_DB))
    run_id = f"topcv_backfill_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"

    collected: list[dict] = []
    blocks = 0
    for index, url in enumerate(urls, 1):
        try:
            html = crawler._make_request(url)
        except Exception as exc:  # noqa: BLE001
            print(f"[{index}/{len(urls)}] stop: {type(exc).__name__}: {str(exc)[:120]}")
            break
        if not html:
            print(f"[{index}/{len(urls)}] skip (no html): {url}")
            continue
        try:
            item = crawler._parse_job_detail_raw(html, url)
        except Exception as exc:  # noqa: BLE001
            print(f"[{index}/{len(urls)}] parse error: {exc} | {url}")
            continue
        if not item.get("company_name"):
            print(f"[{index}/{len(urls)}] no company parsed: {url}")
            continue
        collected.append(item)
        done.add(url)
        with records_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(item, ensure_ascii=False) + "\n")
        CHECKPOINT_PATH.write_text(json.dumps({"done": sorted(done)}, ensure_ascii=False))
        blocks = 0
        print(f"[{index}/{len(urls)}] {item['company_name'][:45]} | {item['job_title'][:35]}")
        time.sleep(args.delay_min if args.delay_min == args.delay_max else __import__("random").uniform(args.delay_min, args.delay_max))

    print(f"collected this pass: {len(collected)} | total done: {len(done)}")
    if args.dry_run or not collected:
        return 0

    writer = AtomicWriter()
    def enveloped():
        for item in collected:
            yield MetadataEnvelope.wrap(source="topcv", raw_payload=item, run_id=run_id)

    state.mark_running(source_name="topcv", run_id=run_id, mode="incremental")
    write_result = writer.write_atomic(source="topcv", records=enveloped(), run_id=run_id)
    state.register_ingestion_object(
        object_key=write_result["bronze_key"],
        run_id=run_id,
        source_name="topcv",
        file_size_bytes=write_result["compressed_size_bytes"],
        records_count=write_result["records_count"],
        checksum_sha256=write_result.get("file_checksum_sha256"),
    )
    state.commit_success(
        source_name="topcv",
        watermark_ts=datetime.now(timezone.utc).isoformat(),
        last_record_id=None,
        records_ingested=write_result["records_count"],
        run_id=run_id,
        bronze_uri=write_result["bronze_s3_uri"],
        source_watermark=None,
        watermark_strategy="none",
    )
    print("bronze_key:", write_result["bronze_key"], "records:", write_result["records_count"])
    return 0


if __name__ == "__main__":
    sys.exit(main())

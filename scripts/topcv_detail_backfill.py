"""Re-fetch TopCV job detail pages into Bronze, slowly, with resume support.

TopCV throttles aggressive crawls (HTTP 429 then 403). The search listing already
carries title/company/city/experience, but jobs whose stored observation still has no
company (older crawls that stored the unrendered Angular template) can only be fixed
from their detail page, so this script re-fetches them one at a time:

  * 6-9s between jobs, a 30s pause every 20 jobs (about 6 requests/minute),
  * one checkpointed JSONL record per job, so a block never loses work,
  * Bronze is committed every 40 records; the checkpoint remembers how many records
    were already committed, so a killed run resumes without duplicating,
  * two consecutive blocks stop the run and leave the checkpoint intact.

Usage:
    lakehouse_env/bin/python scripts/topcv_detail_backfill.py --only-missing
    lakehouse_env/bin/python scripts/topcv_detail_backfill.py --only-missing --limit 20 --dry-run
"""
from __future__ import annotations

import argparse
import json
import os
import random
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

BLANK_COMPANY = {"", "UNKNOWN", "N/A", "CHƯA XÁC ĐỊNH"}


def load_checkpoint() -> dict:
    if not CHECKPOINT_PATH.exists():
        return {"done": [], "committed": 0}
    data = json.loads(CHECKPOINT_PATH.read_text())
    data.setdefault("done", [])
    data.setdefault("committed", 0)
    return data


def save_checkpoint(state: dict) -> None:
    CHECKPOINT_PATH.write_text(json.dumps(state, ensure_ascii=False))


def target_urls(limit: int | None, only_missing: bool) -> list[str]:
    release = latest_release(str(PROJECT_ROOT / "data/silver"))
    if release is None:
        raise SystemExit("no Silver release found")
    observations = read_dataset(release, str(PROJECT_ROOT / "data/silver"), "observations")
    # A job is only worth fetching when *no* observation of it carries a company:
    # the 23/11 listing crawl already filled in most of them, and re-fetching those
    # would burn the rate limit for nothing.
    clean_jobs = {
        row.get("source_job_id")
        for row in observations
        if row.get("platform") == "topcv"
        and str(row.get("company_name") or "").strip().upper() not in BLANK_COMPANY
    }
    urls: set[str] = set()
    for row in observations:
        if row.get("platform") != "topcv":
            continue
        url = row.get("source_url")
        if not url or row.get("source_job_id") in clean_jobs:
            continue
        urls.add(url)
    ordered = sorted(urls)
    print(f"release {release.release_id}: {len(ordered)} topcv urls to fetch "
          f"(only_missing={only_missing})", flush=True)
    return ordered[:limit] if limit else ordered


def pending_records(committed: int) -> list[dict]:
    if not RECORDS_PATH.exists():
        return []
    records: list[dict] = []
    with RECORDS_PATH.open(encoding="utf-8") as handle:
        for index, line in enumerate(handle):
            if index < committed:
                continue
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records


def flush(state: StateManager, records: list[dict]) -> int:
    if not records:
        return 0
    run_id = f"topcv_backfill_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"
    writer = AtomicWriter()
    state.mark_running(source_name="topcv", run_id=run_id, mode="incremental")
    write_result = writer.write_atomic(
        source="topcv",
        records=(MetadataEnvelope.wrap(source="topcv", raw_payload=item, run_id=run_id) for item in records),
        run_id=run_id,
    )
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
    print(f"  -> committed {write_result['records_count']} records: {write_result['bronze_key']}", flush=True)
    return len(records)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--only-missing", action="store_true",
                        help="only jobs whose stored observation has no company")
    parser.add_argument("--delay-min", type=float, default=float(os.getenv("TOPCV_BACKFILL_DELAY_MIN", "6")))
    parser.add_argument("--delay-max", type=float, default=float(os.getenv("TOPCV_BACKFILL_DELAY_MAX", "9")))
    parser.add_argument("--pause-every", type=int, default=20)
    parser.add_argument("--pause-min", type=float, default=30.0)
    parser.add_argument("--pause-max", type=float, default=35.0)
    parser.add_argument("--flush-every", type=int, default=40)
    parser.add_argument("--dry-run", action="store_true", help="fetch and parse but do not write Bronze")
    args = parser.parse_args(argv)

    load_env_file()
    OPERATIONAL.mkdir(parents=True, exist_ok=True)

    checkpoint = load_checkpoint()
    done: set[str] = set(checkpoint["done"])
    state = StateManager(str(STATE_DB))

    # Records written by a run that was killed before its flush are committed first.
    stale = pending_records(int(checkpoint["committed"]))
    if stale and not args.dry_run:
        print(f"committing {len(stale)} records left by a previous run", flush=True)
        checkpoint["committed"] += flush(state, stale)
        save_checkpoint(checkpoint)

    urls = [u for u in target_urls(args.limit, args.only_missing) if u not in done]
    print(f"remaining: {len(urls)} (already done: {len(done)})", flush=True)

    crawler = TopCVCrawler()
    collected = 0
    blocked = 0

    for index, url in enumerate(urls, 1):
        try:
            html = crawler._make_request(url)
        except Exception as exc:  # noqa: BLE001
            blocked += 1
            print(f"[{index}/{len(urls)}] block #{blocked}: {type(exc).__name__}: {str(exc)[:110]}", flush=True)
            if blocked >= 2:
                print("two consecutive blocks; stopping (checkpoint kept)", flush=True)
                break
            time.sleep(600)
            continue
        blocked = 0
        if not html:
            print(f"[{index}/{len(urls)}] skip (no html): {url}", flush=True)
            continue
        try:
            item = crawler._parse_job_detail_raw(html, url)
        except Exception as exc:  # noqa: BLE001
            print(f"[{index}/{len(urls)}] parse error: {exc} | {url}", flush=True)
            continue
        if not item.get("company_name"):
            print(f"[{index}/{len(urls)}] no company parsed: {url}", flush=True)
            continue

        collected += 1
        done.add(url)
        with RECORDS_PATH.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(item, ensure_ascii=False) + "\n")
        checkpoint["done"] = sorted(done)
        save_checkpoint(checkpoint)
        print(f"[{index}/{len(urls)}] {item['company_name'][:46]} | {item['job_title'][:30]}", flush=True)

        if collected % args.flush_every == 0 and not args.dry_run:
            new = pending_records(int(checkpoint["committed"]))
            checkpoint["committed"] += flush(state, new)
            save_checkpoint(checkpoint)

        delay = args.delay_min if args.delay_min == args.delay_max else random.uniform(args.delay_min, args.delay_max)
        time.sleep(delay)
        if args.pause_every and index % args.pause_every == 0:
            pause = random.uniform(args.pause_min, args.pause_max)
            print(f"  ~ pause {pause:.0f}s", flush=True)
            time.sleep(pause)

    print(f"collected this pass: {collected} | checkpoint total: {len(done)}", flush=True)
    if args.dry_run:
        return 0
    remaining_records = pending_records(int(checkpoint["committed"]))
    checkpoint["committed"] += flush(state, remaining_records)
    save_checkpoint(checkpoint)
    return 0


if __name__ == "__main__":
    sys.exit(main())

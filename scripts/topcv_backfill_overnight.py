"""Drain the TopCV detail backfill in repeated slow passes.

TopCV blocks after roughly 25 detail requests (HTTP 429 then 403 + a 15 minute
circuit breaker). One long run therefore only ever collects a couple of dozen jobs.
This wrapper keeps starting short passes with a long pause between them, so the
backfill finishes over hours without anyone watching it:

  * each pass stops on its own after --pass-size jobs (or on the first block),
  * the wrapper waits --pause minutes, which outlives the 15 minute breaker,
  * the script exits when every remaining job has been fetched or the deadline hits.

Usage:
    lakehouse_env/bin/python scripts/topcv_backfill_overnight.py
    lakehouse_env/bin/python scripts/topcv_backfill_overnight.py --pass-size 25 --pause 18
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CHECKPOINT = PROJECT_ROOT / "data/operational/topcv_backfill_checkpoint.json"
LOG = PROJECT_ROOT / "data/operational/logs/topcv_overnight.log"
PY = str(PROJECT_ROOT / "lakehouse_env/bin/python")


def log(message: str) -> None:
    line = f"[{datetime.now().strftime('%H:%M:%S')}] {message}"
    print(line, flush=True)
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")


def done_count() -> int:
    if not CHECKPOINT.exists():
        return 0
    return len(json.loads(CHECKPOINT.read_text()).get("done", []))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pass-size", type=int, default=25,
                        help="jobs attempted per pass; TopCV blocks past roughly this")
    parser.add_argument("--pause", type=float, default=17, help="minutes between passes")
    parser.add_argument("--deadline-hours", type=float, default=6.0)
    parser.add_argument("--target", type=int, default=0,
                        help="stop once this many jobs are done (0 = all remaining)")
    args = parser.parse_args(argv)

    started = datetime.now()
    deadline = started + timedelta(hours=args.deadline_hours)
    initial = done_count()
    log(f"start: {initial} jobs already done, deadline {deadline:%H:%M}")

    for attempt in range(1, 200):
        if datetime.now() > deadline:
            log("deadline reached; stopping")
            break
        before = done_count()
        log(f"pass {attempt}: starting from {before} done")
        completed = subprocess.run(
            [PY, "scripts/topcv_detail_backfill.py", "--only-missing", "--limit", str(args.pass_size),
             "--delay-min", "6", "--delay-max", "8", "--pause-every", "12",
             "--pause-min", "25", "--pause-max", "35", "--flush-every", "25"],
            cwd=PROJECT_ROOT, capture_output=True, text=True, timeout=3600, check=False,
        )
        for line in (completed.stdout or "").strip().splitlines()[-3:]:
            log(f"  {line[:160]}")
        after = done_count()
        gained = after - before
        log(f"pass {attempt}: +{gained} jobs (total {after}, started at {initial})")

        if args.target and after >= args.target:
            log(f"target {args.target} reached; stopping")
            break
        if gained == 0:
            # Two consecutive empty passes mean the block is still active; wait longer.
            log("no progress this pass; waiting a longer pause")
            time.sleep(args.pause * 60 * 1.5)
        else:
            time.sleep(args.pause * 60)

    log(f"finished: {done_count()} jobs done (+{done_count() - initial})")
    return 0


if __name__ == "__main__":
    sys.exit(main())

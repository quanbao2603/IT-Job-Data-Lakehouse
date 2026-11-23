"""Wait for the TopCV backfill, then replay Silver, publish Gold, verify, and push.

Runs unattended so a long backfill does not need a human watching it. Steps:

  1. wait until no `topcv_detail_backfill.py` process is running,
  2. replay Silver from all Bronze objects,
  3. publish Gold (no schema, no mark_missing_day),
  4. verify the live dashboard API against the published release,
  5. commit whatever is still dirty and push to origin/main.

Each step's output is appended to data/operational/logs/final_pipeline.log and the
script exits non-zero if Gold or the verification fails, so a partial result is never
reported as success.
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
LOG = PROJECT_ROOT / "data/operational/logs/final_pipeline.log"
PY = str(PROJECT_ROOT / "lakehouse_env/bin/python")
GOLD_RELEASE = f"gold_{datetime.now().strftime('%Y%m%d_%H%M')}_final"


def log(message: str) -> None:
    line = f"[{datetime.now().strftime('%H:%M:%S')}] {message}"
    print(line, flush=True)
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")


def run(label: str, command: list[str], timeout: int = 3600) -> int:
    log(f"start {label}")
    started = time.time()
    completed = subprocess.run(
        command, cwd=PROJECT_ROOT, capture_output=True, text=True, timeout=timeout, check=False,
    )
    elapsed = int(time.time() - started)
    log(f"{label} exit={completed.returncode} ({elapsed}s)")
    for stream in (completed.stdout, completed.stderr):
        for line in (stream or "").strip().splitlines()[-4:]:
            log(f"  {line[:200]}")
    return completed.returncode


def backfill_running() -> bool:
    result = subprocess.run(["pgrep", "-f", "topcv_detail_backfill.py"], capture_output=True, text=True)
    return bool(result.stdout.strip())


def dashboard() -> dict:
    with urllib.request.urlopen("http://127.0.0.1:8010/api/dashboard", timeout=30) as response:
        return json.load(response)


def main() -> int:
    log("waiting for the TopCV backfill to finish")
    while backfill_running():
        time.sleep(60)
    log("backfill finished")

    if run("silver_replay", [PY, "scripts/replay_silver.py", "--run-id", "manual_final"]) != 0:
        log("ABORT: silver replay failed")
        return 1

    if run("gold_publish", [PY, "-m", "src.gold.run_gold_mart", "--no-schema",
                            "--no-mark-missing-day", "--gold-release-id", GOLD_RELEASE]) != 0:
        log("ABORT: gold publish failed")
        return 1

    try:
        payload = dashboard()
        meta = payload["meta"]
        summary = payload["summary"]
        log(f"live dashboard: release={meta.get('gold_release_id')} jobs={summary.get('jobs')} "
            f"companies={summary.get('companies')} median={summary.get('salary_median')}")
        if meta.get("gold_release_id") != GOLD_RELEASE:
            log(f"ABORT: dashboard still serves {meta.get('gold_release_id')}")
            return 1
    except Exception as exc:  # noqa: BLE001
        log(f"ABORT: dashboard verification failed: {exc}")
        return 1

    status = subprocess.run(["git", "status", "--porcelain"], cwd=PROJECT_ROOT,
                            capture_output=True, text=True).stdout.strip()
    if status:
        run("git_commit", ["git", "add", "-A"])
        run("git_commit", ["git", "commit", "-m",
                           f"chore: publish {GOLD_RELEASE} after the TopCV backfill and full refresh"])
    run("git_push", ["git", "push", "origin", "main"])
    log("done")
    return 0


if __name__ == "__main__":
    sys.exit(main())

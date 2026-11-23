"""Full refresh: crawl every source, replay Silver, publish Gold.

Run once after a parser change so the dashboard reflects the fixed extraction:

    lakehouse_env/bin/python scripts/full_refresh_pipeline.py [--skip-crawl]

Each crawler runs sequentially (never in parallel) so one platform cannot rate-limit
another. Per-source logs land in data/operational/logs/. A failing source is recorded
and the pipeline continues with the remaining ones; Silver/Gold only run when at least
one source produced records.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
LOG_DIR = PROJECT_ROOT / "data/operational/logs"

CRAWLERS = [
    ("careerviet", 15),
    ("itviec", 12),
    ("vietnamworks", 10),
    ("vieclam24h", 8),
    ("upzi", 10),
    ("topdev", 6),
    ("vietnamdevs", 5),
    # topcv last: slowest and most rate-limited
    ("topcv", 25),
]


def load_env() -> dict:
    env = os.environ.copy()
    for path in (Path.home() / "airflow/airflow.env", PROJECT_ROOT / ".env"):
        if not path.exists():
            continue
        for line in path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                env.setdefault(key.strip(), value.strip().strip('"').strip("'"))
    return env


def run_step(name: str, command: list[str], env: dict, timeout: int) -> dict:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_path = LOG_DIR / f"{name}.log"
    started = datetime.now()
    print(f"\n=== {name} ===", flush=True)
    with log_path.open("w", encoding="utf-8") as handle:
        try:
            completed = subprocess.run(
                command, cwd=PROJECT_ROOT, env=env, stdout=handle,
                stderr=subprocess.STDOUT, timeout=timeout, check=False,
            )
            code = completed.returncode
        except subprocess.TimeoutExpired:
            code = 124
    tail = subprocess.run(["tail", "-2", str(log_path)], capture_output=True, text=True).stdout.strip()
    elapsed = int((datetime.now() - started).total_seconds())
    print(f"{name}: exit={code} ({elapsed}s)\n  {tail[:300]}", flush=True)
    return {"step": name, "exit": code, "seconds": elapsed, "log": str(log_path)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skip-crawl", action="store_true")
    args = parser.parse_args(argv)

    env = load_env()
    results: list[dict] = []

    if not args.skip_crawl:
        for source, pages in CRAWLERS:
            results.append(run_step(
                f"crawl_{source}",
                [sys.executable, "-m", f"src.crawlers.{source}_crawler", "--mode", "full", "--max-pages", str(pages)],
                env,
                timeout=3 * 3600,
            ))

    ok_crawls = [r for r in results if r["exit"] == 0]
    if not args.skip_crawl and not ok_crawls:
        print("no source completed; skipping Silver/Gold", flush=True)
        print(json.dumps(results, indent=1))
        return 1

    results.append(run_step(
        "silver_replay",
        [sys.executable, "scripts/replay_silver.py"],
        env,
        timeout=2 * 3600,
    ))

    release_id = f"gold_{datetime.now().strftime('%Y%m%d')}_full_refresh"
    results.append(run_step(
        "gold_publish",
        [sys.executable, "-m", "src.gold.run_gold_mart", "--no-schema", "--no-mark-missing-day",
         "--gold-release-id", release_id],
        env,
        timeout=1800,
    ))

    print("\n=== summary ===")
    print(json.dumps(results, indent=1, ensure_ascii=False))
    return 0 if results[-1]["exit"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())

"""Refresh vieclam24h in small, slow slices so the WAF does not ban the host again.

One full sweep is 18 listing pages. Requesting them in a burst gets the whole domain blocked
for hours (observed: ~5 requests at ~1s, then >5h of 403 on every path and host). This script
therefore fetches only a few pages per invocation, minutes apart, and keeps a checkpoint of
which pages are already captured. When every page is done it replays Silver and publishes Gold
once, so the dashboard never shows a half-swept source.

    lakehouse_env/bin/python scripts/vieclam24h_sweep.py            # one slice
    lakehouse_env/bin/python scripts/vieclam24h_sweep.py --status   # checkpoint only

Exit codes: 0 slice done or nothing to do · 3 source blocked · 4 crawl failed · 5 rebuild failed
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

STATE_PATH = PROJECT_ROOT / "data" / "operational" / "vieclam24h_sweep.json"
PYTHON = str(PROJECT_ROOT / "lakehouse_env" / "bin" / "python")
TOTAL_PAGES = int(os.getenv("VSL_TOTAL_PAGES", "18"))
SWEEP_COOLDOWN = timedelta(days=7)


def now() -> datetime:
    return datetime.now(timezone.utc)


def load_state() -> dict:
    if STATE_PATH.exists():
        try:
            return json.loads(STATE_PATH.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    return {"sweep_id": None, "started_at": None, "completed_at": None, "done_pages": []}


def save_state(state: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")


def site_answers() -> bool:
    """One cheap request decides whether spending the page budget is safe."""
    from curl_cffi import requests as creq

    try:
        response = creq.get("https://vieclam24h.vn/", impersonate="chrome", timeout=30)
        return response.status_code == 200
    except Exception:  # noqa: BLE001
        return False


def run_crawler(page_start: int, pages: int, gap: float) -> tuple[int, dict]:
    env = dict(os.environ)
    env.setdefault("VSL_DETAIL_DELAY_MIN", str(max(8.0, gap / 10)))
    env.setdefault("VSL_DETAIL_DELAY_MAX", str(max(10.0, gap / 8)))
    command = [
        PYTHON, "-m", "src.crawlers.vieclam24h_crawler",
        "--mode", "full",
        "--page-start", str(page_start),
        "--max-pages", str(pages),
        "--page-gap", str(gap),
        "--skip-detail",
    ]
    completed = subprocess.run(
        command, cwd=PROJECT_ROOT, capture_output=True, text=True, timeout=3600, env=env,
    )
    payload: dict = {}
    for line in reversed((completed.stdout or "").strip().splitlines()):
        line = line.strip()
        if line.startswith("{") and line.endswith("}"):
            try:
                payload = json.loads(line)
                break
            except json.JSONDecodeError:
                continue
    return completed.returncode, payload


def rebuild() -> int:
    """Silver replay + Gold publish happen once per completed sweep, never per slice."""
    replay = subprocess.run(
        [PYTHON, "scripts/replay_silver.py", "--run-id", f"manual_vsl_sweep_{now():%Y%m%d}"],
        cwd=PROJECT_ROOT, capture_output=True, text=True, timeout=7200,
    )
    if replay.returncode != 0:
        print(replay.stdout[-500:], replay.stderr[-500:])
        return 5
    release_id = f"gold_{now():%Y%m%d_%H%M}_vsl"
    gold = subprocess.run(
        [PYTHON, "-m", "src.gold.run_gold_mart",
         "--no-schema", "--no-mark-missing-day", "--gold-release-id", release_id],
        cwd=PROJECT_ROOT, capture_output=True, text=True, timeout=3600,
    )
    if gold.returncode != 0:
        print(gold.stdout[-500:], gold.stderr[-500:])
        return 5
    print(gold.stdout.strip().splitlines()[-1] if gold.stdout.strip() else release_id)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Paced vieclam24h sweep")
    parser.add_argument("--pages-per-run", type=int, default=6)
    parser.add_argument("--gap", type=float, default=120.0, help="giây nghỉ giữa hai trang")
    parser.add_argument("--total-pages", type=int, default=TOTAL_PAGES)
    parser.add_argument("--status", action="store_true", help="chỉ in checkpoint")
    parser.add_argument("--force", action="store_true", help="bỏ qua thời gian chờ sau khi xong")
    args = parser.parse_args(argv)

    state = load_state()

    if args.status:
        print(json.dumps(state, ensure_ascii=False))
        return 0

    completed_at = state.get("completed_at")
    if completed_at and not args.force:
        finished = datetime.fromisoformat(completed_at)
        if now() - finished < SWEEP_COOLDOWN:
            print(json.dumps({"status": "COOLDOWN", "completed_at": completed_at,
                              "next_after": (finished + SWEEP_COOLDOWN).isoformat()}, ensure_ascii=False))
            return 0

    if not state.get("sweep_id") or completed_at:
        state = {"sweep_id": f"sweep_{now():%Y%m%d_%H%M}", "started_at": now().isoformat(),
                 "completed_at": None, "done_pages": [], "runs": state.get("runs", [])[-5:]}

    remaining = [p for p in range(1, args.total_pages + 1) if p not in state["done_pages"]]
    if not remaining:
        remaining = list(range(1, args.total_pages + 1))
        state["done_pages"] = []

    if not site_answers():
        print(json.dumps({"status": "BLOCKED", "sweep_id": state["sweep_id"],
                          "done": len(state["done_pages"]), "total": args.total_pages},
                         ensure_ascii=False))
        save_state(state)
        return 3

    page_start = remaining[0]
    pages = min(args.pages_per_run, len(remaining))
    started = now()
    exit_code, payload = run_crawler(page_start, pages, args.gap)
    entry = {"at": started.isoformat(), "from": page_start, "pages": pages,
             "exit": exit_code, "records": payload.get("records_ingested")}
    state["runs"] = (state.get("runs") or [])[-9:] + [entry]

    if exit_code != 0:
        save_state(state)
        print(json.dumps({"status": "CRAWL_FAILED", **entry}, ensure_ascii=False))
        return 4

    for page in range(page_start, page_start + pages):
        if page not in state["done_pages"]:
            state["done_pages"].append(page)
    state["done_pages"].sort()
    save_state(state)

    if len(state["done_pages"]) < args.total_pages:
        print(json.dumps({"status": "PROGRESS", "from": page_start, "pages": pages,
                          "done": len(state["done_pages"]), "total": args.total_pages,
                          "records": payload.get("records_ingested")}, ensure_ascii=False))
        return 0

    print(json.dumps({"status": "SWEEP_COMPLETE", "pages": args.total_pages}, ensure_ascii=False))
    rebuild_exit = rebuild()
    if rebuild_exit != 0:
        print(json.dumps({"status": "REBUILD_FAILED", "exit": rebuild_exit}, ensure_ascii=False))
        return 5
    state["completed_at"] = now().isoformat()
    save_state(state)
    print(json.dumps({"status": "PUBLISHED", "completed_at": state["completed_at"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

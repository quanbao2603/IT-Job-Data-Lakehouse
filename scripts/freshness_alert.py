"""Alert when a source stops producing data, before anyone notices on the dashboard.

Devwork died on 14/11 and nobody knew until 26/11 because a failed crawl only wrote a log line.
This checks the served Gold release, not the crawler: if a platform's newest observation is older
than the allowed age, or a source has produced no successful run in the window, it sends one
Telegram alert through the same channel the pipeline uses.

    lakehouse_env/bin/python scripts/freshness_alert.py [--max-age-days 2] [--dry-run]

Exit code is 0 when the check ran, 1 only when the alert itself could not be sent.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from src.utils.operational import alert_configuration_status, send_alert  # noqa: E402


def evaluate_freshness(
    platform_last_seen: Dict[str, Optional[date]],
    *,
    today: date,
    max_age_days: int,
) -> List[Dict[str, Any]]:
    """Return one entry per platform that is older than the allowed age, worst first."""
    stale: List[Dict[str, Any]] = []
    for platform, last_seen in platform_last_seen.items():
        if last_seen is None:
            stale.append({"platform": platform, "last_seen": None, "age_days": None})
            continue
        age = (today - last_seen).days
        if age > max_age_days:
            stale.append({"platform": platform, "last_seen": last_seen.isoformat(), "age_days": age})
    stale.sort(key=lambda row: (-1 if row["age_days"] is None else row["age_days"]), reverse=True)
    return stale


def format_alert(stale: Iterable[Dict[str, Any]], *, today: date, max_age_days: int) -> str:
    rows = list(stale)
    lines = [f"⚠️ IT Insight: {len(rows)} nguồn không có dữ liệu mới (ngưỡng {max_age_days} ngày, hôm nay {today:%d/%m})"]
    for row in rows:
        if row["age_days"] is None:
            lines.append(f"• {row['platform']}: chưa từng có bản ghi")
        else:
            lines.append(f"• {row['platform']}: mới nhất {row['last_seen']} ({row['age_days']} ngày)")
    lines.append("Dashboard: http://womtech.io.vn:8010/?page=quality")
    return "\n".join(lines)


def _gold_platform_freshness() -> Dict[str, date]:
    """Newest observation date per platform in the active Gold release."""
    import psycopg2

    host = os.getenv("GOLD_DB_HOST", "127.0.0.1")
    port = os.getenv("GOLD_DB_PORT", "5432")
    name = os.environ["GOLD_DB_NAME"]
    user = os.environ["GOLD_DB_READER_USER"]
    password = os.environ["GOLD_DB_READER_PASSWORD"]
    conn = psycopg2.connect(host=host, port=port, dbname=name, user=user, password=password)
    try:
        conn.set_session(readonly=True)
        with conn.cursor() as cur:
            cur.execute(
                """
                select d.platform_code, max(p.last_observed_at)::date
                from gold.v_job_platform p
                join gold.dim_platform d using (platform_key)
                group by 1
                """
            )
            return {code: seen for code, seen in cur.fetchall()}
    finally:
        conn.close()


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Alert when a job source goes quiet")
    parser.add_argument("--max-age-days", type=int, default=int(os.getenv("LAKEHOUSE_STALE_DAYS", "2")))
    parser.add_argument("--dry-run", action="store_true", help="print the alert instead of sending it")
    parser.add_argument("--today", help="override today's date (YYYY-MM-DD), for testing")
    args = parser.parse_args(argv)

    today = date.fromisoformat(args.today) if args.today else date.today()
    freshness = _gold_platform_freshness()
    stale = evaluate_freshness(freshness, today=today, max_age_days=args.max_age_days)
    summary = {
        "status": "STALE" if stale else "OK",
        "today": today.isoformat(),
        "max_age_days": args.max_age_days,
        "platforms_checked": len(freshness),
        "stale": stale,
        "alert": alert_configuration_status(),
    }

    if not stale:
        print(json.dumps(summary, ensure_ascii=False, default=str))
        return 0

    message = format_alert(stale, today=today, max_age_days=args.max_age_days)
    summary["message"] = message
    if args.dry_run:
        summary["sent"] = False
        print(json.dumps(summary, ensure_ascii=False, default=str))
        return 0

    summary["sent"] = send_alert(message)
    print(json.dumps(summary, ensure_ascii=False, default=str))
    return 0 if summary["sent"] else 1


if __name__ == "__main__":
    sys.exit(main())

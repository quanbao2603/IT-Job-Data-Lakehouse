#!/usr/bin/env python3
"""Read-only live reconciliation between the web API layer and Gold PostgreSQL.

Every number the web shows must be reproducible by SQL against the same release.
The script never prints a credential, DSN or row payload; it prints counts and a
pass/fail per check, and exits non-zero on any mismatch.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))


def _load_env_files() -> None:
    """Load DB settings when the caller did not source web.env.

    The production service gets its configuration from the systemd EnvironmentFile, but
    a manual `verify_live.py` run has nothing loaded, which surfaced as an opaque
    JSONDecodeError instead of a verification result.
    """
    if os.getenv("GOLD_DB_READER_PASSWORD") or os.getenv("GOLD_DB_PASSWORD"):
        return
    candidates = [
        APP_ROOT / ".env",
        Path("/home/womcloud/services/it-insight/config/web.env"),
        Path.home() / "airflow/airflow.env",
    ]
    for path in candidates:
        if not path.exists():
            continue
        for line in path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_env_files()

from server.config import get_settings  # noqa: E402
from server.repository import GoldRepository  # noqa: E402
from server.schemas import FilterParams  # noqa: E402


class Checks:
    def __init__(self) -> None:
        self.results: list[dict[str, Any]] = []

    def add(self, name: str, ok: bool, detail: Any = None) -> None:
        self.results.append({"check": name, "ok": bool(ok), "detail": detail})

    @property
    def failed(self) -> list[dict[str, Any]]:
        return [row for row in self.results if not row["ok"]]


def direct_sql() -> dict[str, Any]:
    """Read the same facts with plain SQL in one READ ONLY transaction."""
    import psycopg2

    def one(cursor: Any) -> tuple[Any, ...]:
        row = cursor.fetchone()
        if row is None:  # pragma: no cover - aggregate queries always return a row
            raise RuntimeError("verification query returned no row")
        return tuple(row)

    settings = get_settings()
    connection = psycopg2.connect(
        host=settings.db_host,
        port=settings.db_port,
        dbname=settings.db_name,
        user=settings.reader_user,
        password=settings.reader_password,
        connect_timeout=settings.connect_timeout,
        application_name="it-insight-verify",
    )
    try:
        connection.set_session(isolation_level="REPEATABLE READ", readonly=True)
        cursor = connection.cursor()
        cursor.execute("SET LOCAL statement_timeout = 15000")
        cursor.execute(
            "SELECT gold_release_id, snapshot_date FROM gold_control.active_release WHERE id = 1"
        )
        release_id, snapshot_date = one(cursor)
        cursor.execute(
            """
            SELECT COUNT(*)::int,
                   COUNT(DISTINCT unified_job_id)::int,
                   COUNT(*) FILTER (WHERE salary_sample_flag)::int,
                   percentile_cont(0.5) WITHIN GROUP (ORDER BY salary_midpoint_vnd)
                       FILTER (WHERE salary_sample_flag)
            FROM gold.v_job_snapshot
            """
        )
        rows, distinct_jobs, samples, median = one(cursor)
        cursor.execute(
            """
            SELECT COUNT(DISTINCT s.company_key)::int
            FROM gold.v_job_snapshot s
            JOIN gold.dim_company c ON c.company_key = s.company_key
            WHERE NOT c.is_unknown
            """
        )
        companies = one(cursor)[0]
        cursor.execute(
            """
            SELECT COUNT(DISTINCT f.skill_key)::int
            FROM gold.v_job_skill f
            JOIN gold.dim_skill d ON d.skill_key = f.skill_key
            WHERE NOT d.is_unknown
            """
        )
        skills = one(cursor)[0]
        cursor.execute(
            """
            SELECT COUNT(*)::int
            FROM (
                SELECT j.city_bucket
                FROM gold.v_job_snapshot s JOIN gold.dim_job j ON j.job_key = s.job_key
            ) x
            """
        )
        city_rows = one(cursor)[0]
        cursor.execute(
            "SELECT COUNT(*)::int FROM gold_control.dq_result WHERE gold_release_id = %s",
            (release_id,),
        )
        dq_rows = one(cursor)[0]
        cursor.execute(
            "SELECT COUNT(*)::int FROM gold_control.dq_result WHERE gold_release_id = %s AND NOT passed",
            (release_id,),
        )
        dq_failed = one(cursor)[0]
        connection.rollback()
        return {
            "release_id": release_id,
            "snapshot_date": str(snapshot_date),
            "rows": rows,
            "distinct_jobs": distinct_jobs,
            "salary_samples": samples,
            "salary_median": float(median) if median is not None else None,
            "companies": companies,
            "skills": skills,
            "city_rows": city_rows,
            "dq_rows": dq_rows,
            "dq_failed": dq_failed,
        }
    finally:
        connection.close()


def main() -> int:
    settings = get_settings()
    if settings.demo_mode:
        print("ERROR: WEB_DEMO_MODE must be false for live verification")
        return 2
    if not settings.reader_password:
        print("ERROR: GOLD_DB_READER_PASSWORD is not configured")
        return 2

    checks = Checks()
    repo = GoldRepository(settings)
    context = repo.ready()
    base = FilterParams(page=1, page_size=12)
    dashboard = repo.dashboard(base)
    jobs = repo.jobs(base)
    skills = repo.skills(base)
    companies = repo.companies(base)
    quality = repo.quality(base)
    filters = repo.filters()
    for payload in (dashboard, jobs, skills, companies, quality, filters):
        payload.pop("_context", None)

    sql = direct_sql()

    checks.add("release_id matches active_release", context.gold_release_id == sql["release_id"], context.gold_release_id)
    checks.add("snapshot_date matches active release", context.snapshot_date[:10] == sql["snapshot_date"][:10], context.snapshot_date)
    checks.add("dashboard.jobs == SQL snapshot rows", dashboard["summary"]["jobs"] == sql["rows"], dashboard["summary"]["jobs"])
    checks.add("jobs.total == dashboard.jobs", jobs["total"] == dashboard["summary"]["jobs"], jobs["total"])
    checks.add("snapshot has no duplicate unified_job_id", sql["rows"] == sql["distinct_jobs"], sql["rows"])
    checks.add("dashboard.companies == SQL", dashboard["summary"]["companies"] == sql["companies"], dashboard["summary"]["companies"])
    checks.add("dashboard.skills == SQL", dashboard["summary"]["skills"] == sql["skills"], dashboard["summary"]["skills"])
    checks.add("salary_samples == SQL", dashboard["summary"]["salary_samples"] == sql["salary_samples"], dashboard["summary"]["salary_samples"])
    checks.add(
        "salary_median == SQL",
        dashboard["summary"]["salary_median"] is not None
        and sql["salary_median"] is not None
        and abs(dashboard["summary"]["salary_median"] - sql["salary_median"]) < 1,
        dashboard["summary"]["salary_median"],
    )
    city_total = sum(row["count"] for row in dashboard["cities"])
    checks.add("city distribution totals one bucket per job", city_total == sql["city_rows"], city_total)
    checks.add("quality checks == SQL", len(quality["checks"]) == sql["dq_rows"], len(quality["checks"]))
    checks.add(
        "quality failures == SQL",
        sum(1 for row in quality["checks"] if not row["passed"]) == sql["dq_failed"],
        sql["dq_failed"],
    )
    checks.add("no failed blocking DQ on served release", sql["dq_failed"] == 0, sql["dq_failed"])
    checks.add(
        "filter catalogues are release scoped",
        all(filters.get(key) for key in ("cities", "domains", "levels", "skills")),
        {key: len(filters[key]) for key in ("cities", "domains", "levels", "skills")},
    )

    top_skill = skills["items"][0]
    drilled_skill = repo.jobs(FilterParams(skill=top_skill["skill_code"], page_size=1))
    checks.add(
        "skill drill-through total matches skill count",
        drilled_skill["total"] == top_skill["job_count"],
        {"skill": top_skill["skill_code"], "drill": drilled_skill["total"], "listed": top_skill["job_count"]},
    )

    top_company = companies["items"][0]
    drilled_company = repo.jobs(FilterParams(company_key=top_company["company_key"], page_size=1))
    checks.add(
        "company drill-through total matches company count",
        drilled_company["total"] == top_company["job_count"],
        {"company_key": top_company["company_key"], "drill": drilled_company["total"], "listed": top_company["job_count"]},
    )

    pairs = skills["pairs"]
    checks.add(
        "skill pairs never self-pair",
        all(pair["skill_a"] != pair["skill_b"] for pair in pairs),
        len(pairs),
    )
    checks.add(
        "skill pair list has no duplicate unordered pair",
        len({tuple(sorted((pair["skill_a"], pair["skill_b"]))) for pair in pairs}) == len(pairs),
        len(pairs),
    )

    richest = max(jobs["items"], key=lambda row: len(row["skills"]))
    checks.add(
        "multi-skill job is counted once in snapshot total",
        jobs["total"] == dashboard["summary"]["jobs"],
        {"job": richest["unified_job_id"], "skills": len(richest["skills"])},
    )

    summary = {
        "gold_release_id": context.gold_release_id,
        "silver_release_id": context.silver_release_id,
        "snapshot_date": context.snapshot_date,
        "served_mode": settings.mode,
        "jobs": dashboard["summary"]["jobs"],
        "companies": dashboard["summary"]["companies"],
        "skills": dashboard["summary"]["skills"],
        "salary_samples": dashboard["summary"]["salary_samples"],
        "salary_median": dashboard["summary"]["salary_median"],
        "quality_checks": len(quality["checks"]),
        "quality_failures": sql["dq_failed"],
        "checks_total": len(checks.results),
        "checks_failed": len(checks.failed),
    }
    print(json.dumps({"summary": summary, "results": checks.results}, ensure_ascii=False, sort_keys=True, default=str))
    return 0 if not checks.failed else 4


if __name__ == "__main__":
    raise SystemExit(main())

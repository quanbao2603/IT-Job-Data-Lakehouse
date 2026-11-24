"""Load job descriptions for the active Gold release.

`gold.dim_job` is deliberately lean: it carries the facets BI needs, not multi-kilobyte
job ads. The detail page still needs the ad text, so this loader copies the representative
observation's description, requirements, benefits and deadline into `gold.job_detail`,
keyed by (gold_release_id, unified_job_id).

Run after a Gold publish:

    lakehouse_env/bin/python scripts/load_job_detail.py
    lakehouse_env/bin/python scripts/load_job_detail.py --gold-release-id gold_xxx
"""
from __future__ import annotations

import argparse
import glob
import os
import sys
from datetime import date
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

import psycopg2  # noqa: E402
import pyarrow as pa  # noqa: E402
import pyarrow.parquet as pq  # noqa: E402
from psycopg2.extras import execute_values  # noqa: E402

from src.gold.connection import connect  # noqa: E402
from src.storage.atomic_writer import load_env_file  # noqa: E402

COLUMNS = (
    "description", "requirements", "benefits", "expired_date", "posted_date", "source_url",
)


def active_release(cur) -> tuple[str, str]:
    cur.execute("select gold_release_id from gold_control.active_release where id = 1")
    gold_release_id = cur.fetchone()[0]
    cur.execute(
        "select silver_release_id from gold_control.load_run where gold_release_id = %s",
        (gold_release_id,),
    )
    row = cur.fetchone()
    return gold_release_id, (row[0] if row else "")


def observations_for(silver_release_id: str) -> dict[str, dict]:
    """observation_id -> the descriptive fields, read from the Silver release on disk."""
    run_id = silver_release_id.removeprefix("silver_release_")
    root = PROJECT_ROOT / "data" / "silver" / run_id / "observations"
    files = sorted(glob.glob(str(root / "**" / "*.parquet"), recursive=True))
    if not files:
        raise SystemExit(f"no observation parquet under {root}")
    wanted = ("observation_id", "job_description_clean", "requirements_clean",
              "benefits_clean", "expired_date", "posted_date", "source_url")
    found: dict[str, dict] = {}
    for path in files:
        table = pq.read_table(path, columns=[c for c in wanted])
        for row in table.to_pylist():
            observation_id = row.get("observation_id")
            if observation_id:
                found[observation_id] = row
    return found


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Copy job ads into gold.job_detail")
    parser.add_argument("--gold-release-id", default=None)
    args = parser.parse_args(argv)

    load_env_file()
    with connect(readonly=False) as conn:
        with conn.cursor() as cur:
            gold_release_id, silver_release_id = active_release(cur)
            if args.gold_release_id:
                gold_release_id = args.gold_release_id
            if not silver_release_id:
                raise SystemExit("active Gold release has no Silver parent")

            observations = observations_for(silver_release_id)
            cur.execute(
                """
                select unified_job_id, representative_observation_id
                from gold.dim_job
                where is_current and representative_observation_id is not null
                """
            )
            pairs = cur.fetchall()

            rows = []
            missing = 0
            for unified_job_id, observation_id in pairs:
                obs = observations.get(observation_id)
                if obs is None:
                    missing += 1
                    continue
                rows.append((
                    gold_release_id,
                    unified_job_id,
                    obs.get("job_description_clean"),
                    obs.get("requirements_clean"),
                    obs.get("benefits_clean"),
                    obs.get("expired_date") or None,
                    obs.get("posted_date") or None,
                    obs.get("source_url"),
                ))

            cur.execute(
                "delete from gold.job_detail where gold_release_id = %s", (gold_release_id,)
            )
            execute_values(
                cur,
                """
                insert into gold.job_detail
                    (gold_release_id, unified_job_id, description, requirements,
                     benefits, expired_date, posted_date, source_url)
                values %s
                on conflict (gold_release_id, unified_job_id) do update set
                    description = excluded.description,
                    requirements = excluded.requirements,
                    benefits = excluded.benefits,
                    expired_date = excluded.expired_date,
                    posted_date = excluded.posted_date,
                    source_url = excluded.source_url
                """,
                rows,
                page_size=500,
            )
        conn.commit()
        print(f"gold_release_id={gold_release_id} silver={silver_release_id}")
        print(f"rows={len(rows)} missing_observation={missing}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Daily Gold mart build, scheduled after the Bronze/Silver DAG.

Kept in its own DAG on purpose: the Bronze DAG already spends its timeout budget on nine
crawlers (45 min each) plus Silver (60 min), so adding Gold there would break that budget.
Gold runs as a subprocess through LAKEHOUSE_PYTHON, exactly like the crawlers, which keeps
the Airflow worker free of project dependencies.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import timedelta
from typing import Any, Dict

import pendulum
from airflow import DAG
from airflow.operators.python import PythonOperator
from airflow.utils.trigger_rule import TriggerRule

def _flag_from_environment(name: str, default: str = "false") -> bool:
    """Read a boolean flag from the process env, falling back to the Airflow env file.

    The Bronze DAG needs the same fallback, which tells us the systemd unit does not
    always export ~/airflow/airflow.env into the worker process. Without this, Gold
    would stay silently DISABLED even after the operator set the flag.
    """
    value = os.environ.get(name)
    if value is None:
        env_path = os.environ.get("LAKEHOUSE_AIRFLOW_ENV", "/home/womcloud/airflow/airflow.env")
        try:
            with open(env_path, encoding="utf-8") as handle:
                for line in handle:
                    key, separator, raw_value = line.partition("=")
                    if separator and key.strip() == name:
                        value = raw_value.strip().strip("\"'")
                        break
        except OSError:
            value = None
    return str(value or default).lower() == "true"


PROJECT_ROOT = os.environ.get("LAKEHOUSE_PROJECT_ROOT", "/home/womcloud/IT-Job-Data-Lakehouse")
PYTHON = os.environ.get("LAKEHOUSE_PYTHON", f"{PROJECT_ROOT}/lakehouse_env/bin/python")
STATE_DB = os.environ.get("LAKEHOUSE_STATE_DB_PATH", f"{PROJECT_ROOT}/data/state_store.db")
SILVER_ROOT = os.environ.get("LAKEHOUSE_SILVER_ROOT", f"{PROJECT_ROOT}/data/silver")
GOLD_ENABLED = _flag_from_environment("LAKEHOUSE_GOLD_ENABLED")

if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

DEFAULT_ARGS = {
    "owner": "quanbao",
    "depends_on_past": False,
    "retries": 1,
    "retry_delay": timedelta(minutes=10),
    "execution_timeout": timedelta(minutes=30),
}


def _gold_preflight(**_: Any) -> Dict[str, Any]:
    """Fail fast when Gold is disabled, when PostgreSQL is unreachable, or when no release exists."""
    if not GOLD_ENABLED:
        result = {"status": "DISABLED", "reason": "LAKEHOUSE_GOLD_ENABLED=false"}
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return result
    from src.gold.silver_release_reader import latest_release

    release = latest_release(SILVER_ROOT)
    result = {
        "status": "READY" if release else "MISSING",
        "silver_release_id": release.release_id if release else None,
        "snapshot_cutoff_at": release.snapshot_cutoff_at if release else None,
        "silver_root": SILVER_ROOT,
    }
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return result


def _gold_build(**context: Any) -> Dict[str, Any]:
    preflight = context["ti"].xcom_pull(task_ids="gold_preflight") or {}
    if preflight.get("status") == "DISABLED":
        print(json.dumps(preflight, ensure_ascii=False, sort_keys=True))
        return preflight
    env = os.environ.copy()
    env.update({"PYTHONUNBUFFERED": "1", "PYTHONPATH": PROJECT_ROOT})
    command = [
        PYTHON, "-m", "src.gold.run_gold_mart",
        "--silver-root", SILVER_ROOT,
        "--state-db", STATE_DB,
    ]
    process = subprocess.Popen(
        command, cwd=PROJECT_ROOT, env=env,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1,
    )
    lines = []
    assert process.stdout is not None
    for line in process.stdout:
        print(line, end="")
        lines.append(line.rstrip("\n"))
    return_code = process.wait()

    result = None
    for line in reversed(lines):
        try:
            candidate = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(candidate, dict) and candidate.get("status"):
            result = candidate
            break
    if not isinstance(result, dict):
        raise RuntimeError(f"Gold build returned no JSON contract (exit {return_code})")
    # MISSING is an operational outcome, not a task failure: the day is recorded and BI
    # keeps serving the previous release. Anything else must fail loudly.
    if result.get("status") == "MISSING":
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return result
    if return_code != 0 or result.get("status") != "PUBLISHED":
        raise RuntimeError(
            f"Gold release not published: status={result.get('status')} "
            f"blocking={result.get('blocking_checks')}"
        )
    return result


def _gold_summary(**context: Any) -> Dict[str, Any]:
    build = context["ti"].xcom_pull(task_ids="gold_build_and_publish") or {}
    summary = {
        "status": build.get("status", "UNKNOWN"),
        "gold_release_id": build.get("gold_release_id"),
        "silver_release_id": build.get("silver_release_id"),
        "snapshot_date": build.get("snapshot_date"),
        "dq": build.get("dq_summary"),
        "row_counts": build.get("row_counts"),
    }
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True, default=str))
    return summary


try:
    from src.utils.operational import airflow_failure_callback, airflow_retry_callback
except ImportError:  # pragma: no cover - keeps Airflow import diagnostics readable
    airflow_failure_callback = None
    airflow_retry_callback = None

DEFAULT_ARGS.update({
    "on_failure_callback": airflow_failure_callback,
    "on_retry_callback": airflow_retry_callback,
})

with DAG(
    dag_id="daily_gold_mart_build",
    description="Build and publish the Gold mart from the newest sealed Silver release",
    default_args=DEFAULT_ARGS,
    start_date=pendulum.datetime(2026, 1, 1, tz="Asia/Ho_Chi_Minh"),
    schedule="0 5 * * *",
    catchup=False,
    max_active_runs=1,
    max_active_tasks=2,
    dagrun_timeout=timedelta(minutes=90),
    tags=["gold", "mart", "postgresql"],
) as dag:
    preflight = PythonOperator(task_id="gold_preflight", python_callable=_gold_preflight)
    build = PythonOperator(
        task_id="gold_build_and_publish",
        python_callable=_gold_build,
        trigger_rule=TriggerRule.ALL_SUCCESS,
    )
    summary = PythonOperator(
        task_id="gold_pipeline_summary",
        python_callable=_gold_summary,
        trigger_rule=TriggerRule.ALL_SUCCESS,
    )
    preflight >> build >> summary

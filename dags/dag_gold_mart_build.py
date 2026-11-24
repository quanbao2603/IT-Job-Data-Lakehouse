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

def _runtime_environment() -> dict[str, str]:
    """Pass the Airflow service env file to the Gold subprocess.

    The scheduler's task worker may not retain ``EnvironmentFile`` variables after
    Airflow forks the LocalExecutor task process. Loading the same file here keeps
    the subprocess on the configured PostgreSQL endpoint/password without printing
    or storing any secret in the DAG code.
    """
    env = os.environ.copy()
    env_path = env.get("LAKEHOUSE_AIRFLOW_ENV", "/home/womcloud/airflow/airflow.env")
    try:
        with open(env_path, encoding="utf-8") as handle:
            for line in handle:
                key, separator, raw_value = line.partition("=")
                if not separator or not key.strip() or key.lstrip().startswith("#"):
                    continue
                value = raw_value.strip()
                if (value.startswith('"') and value.endswith('"')) or (
                    value.startswith("'") and value.endswith("'")
                ):
                    value = value[1:-1]
                env[key.strip()] = value
    except OSError:
        pass
    return env


_PROCESS_ENV = _runtime_environment()
PROJECT_ROOT = _PROCESS_ENV.get("LAKEHOUSE_PROJECT_ROOT", "/home/womcloud/IT-Job-Data-Lakehouse")
PYTHON = _PROCESS_ENV.get("LAKEHOUSE_PYTHON", f"{PROJECT_ROOT}/lakehouse_env/bin/python")
STATE_DB = _PROCESS_ENV.get("LAKEHOUSE_STATE_DB_PATH", f"{PROJECT_ROOT}/data/state_store.db")
SILVER_ROOT = _PROCESS_ENV.get("LAKEHOUSE_SILVER_ROOT", f"{PROJECT_ROOT}/data/silver")
GOLD_ENABLED = str(_PROCESS_ENV.get("LAKEHOUSE_GOLD_ENABLED", "false")).lower() == "true"


def _gold_command() -> list[str]:
    """Build the production command without rerunning already-applied DDL."""
    return [
        PYTHON, "-m", "src.gold.run_gold_mart",
        "--silver-root", SILVER_ROOT,
        "--state-db", STATE_DB,
        "--no-schema",
        "--no-mark-missing-day",
    ]

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
    env = _runtime_environment()
    env.update({"PYTHONUNBUFFERED": "1", "PYTHONPATH": PROJECT_ROOT})
    command = _gold_command()
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


def _gold_job_detail(**_: Any) -> str:
    """Copy the representative ad text into gold.job_detail for the release just published.

    dim_job stays lean on purpose; the detail page needs the ad body, and this step keeps the
    two in sync without widening the daily schema run.
    """
    command = [PYTHON, "scripts/load_job_detail.py"]
    completed = subprocess.run(
        command, cwd=PROJECT_ROOT, capture_output=True, text=True, timeout=900,
        env=_PROCESS_ENV,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            f"gold_job_detail failed with exit {completed.returncode}: "
            f"{(completed.stderr or completed.stdout)[-500:]}"
        )
    return (completed.stdout or "").strip()[-300:]


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
    detail = PythonOperator(
        task_id="gold_job_detail",
        python_callable=_gold_job_detail,
        trigger_rule=TriggerRule.ALL_SUCCESS,
    )
    summary = PythonOperator(
        task_id="gold_pipeline_summary",
        python_callable=_gold_summary,
        trigger_rule=TriggerRule.ALL_SUCCESS,
    )
    preflight >> build >> detail >> summary

"""Scheduled retention-managed Bronze compaction.

Only partitions older than the configured safety age are considered. The task archives
and verifies source objects before removing them from the active Bronze prefix.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import timedelta
from typing import Any

import pendulum
from airflow import DAG
from airflow.operators.python import PythonOperator

try:
    from src.utils.operational import airflow_failure_callback, airflow_retry_callback
except ImportError:  # pragma: no cover
    airflow_failure_callback = None
    airflow_retry_callback = None

PROJECT_ROOT = os.environ.get("LAKEHOUSE_PROJECT_ROOT", "/home/womcloud/IT-Job-Data-Lakehouse")
PYTHON = os.environ.get("LAKEHOUSE_PYTHON", f"{PROJECT_ROOT}/lakehouse_env/bin/python")
STATE_DB = os.environ.get("LAKEHOUSE_STATE_DB_PATH", f"{PROJECT_ROOT}/data/state_store.db")
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)
SOURCES = (
    "careerviet", "devwork", "itviec", "topcv", "topdev",
    "upzi", "vieclam24h", "vietnamdevs", "vietnamworks",
)
AGE_DAYS = max(int(os.environ.get("LAKEHOUSE_COMPACTION_AGE_DAYS", "2")), 1)
MIN_FILES = max(int(os.environ.get("LAKEHOUSE_COMPACTION_MIN_FILES", "7")), 2)


def _run_compaction(**context: Any) -> dict[str, Any]:
    from src.storage.compaction_engine import CompactionEngine
    from src.storage.compaction_plan import target_partition_prefixes

    logical_date = context["logical_date"].date().isoformat()
    prefixes = target_partition_prefixes(logical_date, SOURCES, age_days=AGE_DAYS)
    engine = CompactionEngine(state_db_path=STATE_DB)
    results = []
    for prefix in prefixes:
        result = engine.compact_partition(
            prefix,
            min_files_threshold=MIN_FILES,
            purge_small_files=False,
            archive_small_files=True,
        )
        results.append({"prefix": prefix, **result})
    summary = {
        "status": "SUCCESS",
        "logical_date": logical_date,
        "age_days": AGE_DAYS,
        "min_files_threshold": MIN_FILES,
        "results": results,
        "compacted_count": sum(1 for item in results if item.get("status") == "COMPACTED"),
        "skipped_count": sum(1 for item in results if item.get("status") == "SKIPPED"),
    }
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True, default=str))
    return summary


with DAG(
    dag_id="bronze_compaction_daily",
    description="Archive-verified compaction of Bronze partitions older than the safety window",
    start_date=pendulum.datetime(2026, 1, 1, tz="Asia/Ho_Chi_Minh"),
    schedule="30 3 * * *",
    catchup=False,
    max_active_runs=1,
    max_active_tasks=1,
    dagrun_timeout=timedelta(hours=2),
    default_args={
        "owner": "quanbao",
        "retries": 1,
        "retry_delay": timedelta(minutes=10),
        "on_failure_callback": airflow_failure_callback,
        "on_retry_callback": airflow_retry_callback,
    },
    tags=["bronze", "compaction", "retention"],
) as dag:
    PythonOperator(
        task_id="compact_old_bronze_partitions",
        python_callable=_run_compaction,
        execution_timeout=timedelta(hours=2),
        retries=1,
        retry_delay=timedelta(minutes=10),
    )

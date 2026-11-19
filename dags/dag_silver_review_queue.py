"""Refresh the durable Silver human-review queue from SQLite registry."""
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
STATE_DB = os.environ.get("LAKEHOUSE_STATE_DB_PATH", f"{PROJECT_ROOT}/data/state_store.db")
QUEUE_PATH = os.environ.get("LAKEHOUSE_PENDING_REVIEW_PATH", f"{PROJECT_ROOT}/data/operational/pending_review.csv")
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)


def _sync_review_queue(**_: Any) -> dict[str, Any]:
    from src.etl.review_queue import sync_pending_review_queue

    count = sync_pending_review_queue(QUEUE_PATH, STATE_DB)
    result = {"status": "SUCCESS", "pending_review_count": count, "queue_path": QUEUE_PATH}
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return result


with DAG(
    dag_id="silver_review_queue_daily",
    description="Export unresolved Silver REVIEW_REQUIRED links into the durable operator queue",
    start_date=pendulum.datetime(2026, 1, 1, tz="Asia/Ho_Chi_Minh"),
    schedule="15 6 * * *",
    catchup=False,
    max_active_runs=1,
    dagrun_timeout=timedelta(minutes=15),
    default_args={
        "owner": "quanbao",
        "on_failure_callback": airflow_failure_callback,
        "on_retry_callback": airflow_retry_callback,
    },
    tags=["silver", "review", "operations"],
) as dag:
    PythonOperator(
        task_id="sync_silver_review_queue",
        python_callable=_sync_review_queue,
        execution_timeout=timedelta(minutes=10),
    )

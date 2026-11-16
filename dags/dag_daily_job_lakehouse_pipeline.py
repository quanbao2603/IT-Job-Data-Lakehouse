"""Daily incremental Bronze ingestion for all nine job platforms.

Airflow orchestrates scheduling, bounded retries, preflight, task failure
visibility, and the post-crawl Bronze gate. Crawlers still own watermark,
source lock, heartbeat, atomic Bronze write, manifest, and State Store commit.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from typing import Any, Dict

import pendulum
from airflow import DAG
from airflow.operators.python import PythonOperator
from airflow.utils.state import State
from airflow.utils.trigger_rule import TriggerRule
from datetime import timedelta

PROJECT_ROOT = os.environ.get("LAKEHOUSE_PROJECT_ROOT", "/home/womcloud/IT-Job-Data-Lakehouse")
PYTHON = os.environ.get("LAKEHOUSE_PYTHON", f"{PROJECT_ROOT}/lakehouse_env/bin/python")
STATE_DB = os.environ.get("LAKEHOUSE_STATE_DB_PATH", f"{PROJECT_ROOT}/data/state_store.db")
MAX_PAGES = os.environ.get("LAKEHOUSE_INCREMENTAL_MAX_PAGES", "5")
MAX_ACTIVE_TASKS = min(max(int(os.environ.get("LAKEHOUSE_MAX_ACTIVE_TASKS", "3")), 1), 3)
SILVER_ENABLED = os.environ.get("LAKEHOUSE_SILVER_ENABLED", "false").lower() == "true"
FAILURE_LEDGER = os.environ.get(
    "LAKEHOUSE_FAILURE_LEDGER",
    f"{PROJECT_ROOT}/data/operational/source_failures.jsonl",
)
os.environ.setdefault("LAKEHOUSE_FAILURE_LEDGER", FAILURE_LEDGER)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

CRAWLERS = {
    "careerviet": "src.crawlers.careerviet_crawler",
    "upzi": "src.crawlers.upzi_crawler",
    "vietnamdevs": "src.crawlers.vietnamdevs_crawler",
    "vieclam24h": "src.crawlers.vieclam24h_crawler",
    "devwork": "src.crawlers.devwork_crawler",
    "itviec": "src.crawlers.itviec_crawler",
    "topcv": "src.crawlers.topcv_crawler",
    "topdev": "src.crawlers.topdev_crawler",
    "vietnamworks": "src.crawlers.vietnamworks_crawler",
}

DEFAULT_ARGS = {
    "owner": "quanbao",
    "depends_on_past": False,
    "retries": 2,
    "retry_delay": timedelta(minutes=5),
    "execution_timeout": timedelta(hours=2),
}


def _run_crawler(platform: str, module: str, **_: object) -> Dict[str, Any]:
    """Run one crawler, stream its logs, and require its final JSON contract."""
    env = os.environ.copy()
    env.update({"PYTHONUNBUFFERED": "1", "PYTHONPATH": PROJECT_ROOT})
    command = [PYTHON, "-m", module, "--mode", "incremental", "--max-pages", MAX_PAGES]
    process = subprocess.Popen(
        command,
        cwd=PROJECT_ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    output_lines = []
    assert process.stdout is not None
    for line in process.stdout:
        print(line, end="")
        output_lines.append(line.rstrip("\n"))
    return_code = process.wait()
    result = None
    for line in reversed(output_lines):
        try:
            candidate = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(candidate, dict) and "status" in candidate:
            result = candidate
            break
    if return_code != 0:
        raise RuntimeError(f"{platform} crawler exited with code {return_code}")
    if not isinstance(result, dict) or result.get("status") != "SUCCESS":
        raise RuntimeError(f"{platform} crawler returned an invalid success contract")
    result["source"] = platform
    return result


def _run_preflight(**_: object) -> Dict[str, Any]:
    from src.utils.preflight import run_preflight

    result = run_preflight(
        project_root=PROJECT_ROOT,
        python_executable=PYTHON,
        state_db_path=STATE_DB,
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return result


def _build_task_results(dag_run: Any, task_instance: Any) -> Dict[str, Dict[str, Any]]:
    results: Dict[str, Dict[str, Any]] = {}
    for platform in CRAWLERS:
        task_id = f"crawl_{platform}_incremental"
        ti = dag_run.get_task_instance(task_id=task_id)
        if ti is None or ti.state != State.SUCCESS:
            state = (ti.state if ti else "missing") or "missing"
            results[platform] = {"status": str(state).upper(), "records_ingested": 0}
            continue
        value = task_instance.xcom_pull(task_ids=task_id)
        if isinstance(value, dict):
            results[platform] = value
        else:
            results[platform] = {"status": "INVALID_RESULT", "records_ingested": 0}
    return results


def _audit_after_crawl(**context: Any) -> Dict[str, Any]:
    from minio import Minio
    from src.storage.atomic_writer import load_env_file
    from src.utils.bronze_audit_gate import audit_bronze_run

    dag_run = context["dag_run"]
    task_instance = context["ti"]
    results = _build_task_results(dag_run, task_instance)
    load_env_file()
    client = Minio(
        endpoint=os.getenv("MINIO_ENDPOINT", "localhost:9000"),
        access_key=os.getenv("MINIO_ROOT_USER", os.getenv("MINIO_ACCESS_KEY")),
        secret_key=os.getenv("MINIO_ROOT_PASSWORD", os.getenv("MINIO_SECRET_KEY")),
        secure=os.getenv("MINIO_SECURE", "false").lower() == "true",
    )
    audit = audit_bronze_run(
        results,
        expected_sources=CRAWLERS,
        state_db_path=STATE_DB,
        minio_client=client,
        bronze_bucket=os.getenv("MINIO_BRONZE_BUCKET", "bronze"),
    )
    print(json.dumps({"source_results": results, "audit": audit}, ensure_ascii=False, sort_keys=True))
    return {"source_results": results, "audit": audit}


def _run_silver(**context: Any) -> Dict[str, Any]:
    if not SILVER_ENABLED:
        result = {"status": "DISABLED", "reason": "LAKEHOUSE_SILVER_ENABLED=false"}
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return result
    from src.etl.bronze_to_silver import BronzeToSilverPipeline
    from src.etl.silver_writer import SilverWriter
    from src.storage.entity_registry import EntityRegistry
    from src.storage.state_manager import StateManager
    from src.storage.atomic_writer import load_env_file

    load_env_file()
    run_id = str(context["dag_run"].run_id)
    registry = EntityRegistry(STATE_DB)
    state = StateManager(STATE_DB)
    pipeline = BronzeToSilverPipeline(
        registry=registry,
        writer=SilverWriter(
            os.environ.get("LAKEHOUSE_SILVER_ROOT", f"{PROJECT_ROOT}/data/silver"),
            max_records_per_batch=int(os.environ.get("LAKEHOUSE_SILVER_MAX_RECORDS_PER_BATCH", "5000")),
        ),
        run_id=run_id,
        resolution_version=os.environ.get("LAKEHOUSE_SILVER_RESOLUTION_VERSION", "v1"),
        pending_review_path=os.environ.get(
            "LAKEHOUSE_PENDING_REVIEW_PATH", f"{PROJECT_ROOT}/data/operational/pending_review.csv"
        ),
    )
    object_reader = state_reader
    report = pipeline.process_bronze_objects(state, object_reader, platforms=set(CRAWLERS))
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return report


def state_reader(object_key: str):
    """Stream one Bronze object through the existing AtomicWriter boundary."""
    from src.storage.atomic_writer import AtomicWriter
    reader = AtomicWriter(
        endpoint=os.getenv("MINIO_ENDPOINT", "localhost:9000"),
        access_key=os.getenv("MINIO_ROOT_USER", os.getenv("MINIO_ACCESS_KEY")),
        secret_key=os.getenv("MINIO_ROOT_PASSWORD", os.getenv("MINIO_SECRET_KEY")),
        secure=os.getenv("MINIO_SECURE", "false").lower() == "true",
    )
    yield from reader.stream_bronze_records(object_key)


def _report_success(**context: Any) -> Dict[str, Any]:
    audit_result = context["ti"].xcom_pull(task_ids="bronze_audit_after_crawl") or {}
    silver_result = context["ti"].xcom_pull(task_ids="silver_pipeline_guard") or {"status": "DISABLED"}
    source_results = audit_result.get("source_results", {})
    summary = {
        "status": "SUCCESS",
        "source_success_count": sum(1 for result in source_results.values() if result.get("status") == "SUCCESS"),
        "empty_success_count": sum(1 for result in source_results.values() if result.get("records_ingested", 0) == 0),
        "records_ingested": sum(int(result.get("records_ingested", 0)) for result in source_results.values()),
        "bronze_audit": audit_result.get("audit", {}).get("status"),
        "silver_trigger": silver_result.get("status", "DISABLED"),
    }
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return summary


try:
    from src.utils.operational import airflow_failure_callback, airflow_retry_callback
except ImportError:  # pragma: no cover - keeps Airflow import diagnostics readable
    airflow_failure_callback = None
    airflow_retry_callback = None

DEFAULT_ARGS.update(
    {
        "on_failure_callback": airflow_failure_callback,
        "on_retry_callback": airflow_retry_callback,
    }
)

with DAG(
    dag_id="daily_nine_platform_bronze_incremental",
    description="Daily incremental Bronze crawl for all nine IT job platforms",
    default_args=DEFAULT_ARGS,
    start_date=pendulum.datetime(2026, 1, 1, tz="Asia/Ho_Chi_Minh"),
    schedule="0 2 * * *",
    catchup=False,
    max_active_runs=1,
    max_active_tasks=MAX_ACTIVE_TASKS,
    dagrun_timeout=timedelta(hours=4),
    tags=["bronze", "incremental", "nine-platforms"],
) as dag:
    preflight = PythonOperator(
        task_id="preflight_bronze",
        python_callable=_run_preflight,
    )

    crawl_tasks = []
    for platform, module in CRAWLERS.items():
        task = PythonOperator(
            task_id=f"crawl_{platform}_incremental",
            python_callable=_run_crawler,
            op_kwargs={"platform": platform, "module": module},
        )
        preflight >> task
        crawl_tasks.append(task)

    bronze_audit = PythonOperator(
        task_id="bronze_audit_after_crawl",
        python_callable=_audit_after_crawl,
        trigger_rule=TriggerRule.ALL_DONE,
    )
    crawl_tasks >> bronze_audit

    silver_pipeline_guard = PythonOperator(
        task_id="silver_pipeline_guard",
        python_callable=_run_silver,
        trigger_rule=TriggerRule.ALL_SUCCESS,
    )
    bronze_audit >> silver_pipeline_guard

    pipeline_summary = PythonOperator(
        task_id="crawl_pipeline_summary",
        python_callable=_report_success,
        trigger_rule=TriggerRule.ALL_SUCCESS,
    )
    silver_pipeline_guard >> pipeline_summary

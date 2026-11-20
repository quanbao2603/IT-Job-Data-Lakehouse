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
from airflow.exceptions import AirflowSkipException
from airflow.operators.python import PythonOperator
from airflow.utils.state import State
from airflow.utils.trigger_rule import TriggerRule
from datetime import timedelta

def _runtime_environment() -> dict[str, str]:
    """Load the Airflow service env for DAG code and crawler subprocesses.

    LocalExecutor task processes do not always retain the systemd
    ``EnvironmentFile`` variables. Loading the same file here keeps the crawler,
    Silver subprocess and Bronze audit on the configured runtime paths without
    putting any secret in the repository.
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
MAX_PAGES = _PROCESS_ENV.get("LAKEHOUSE_INCREMENTAL_MAX_PAGES", "5")
MAX_ACTIVE_TASKS = min(max(int(_PROCESS_ENV.get("LAKEHOUSE_MAX_ACTIVE_TASKS", "3")), 1), 3)


def silver_enabled_from_environment() -> bool:
    """Read the Silver gate from process env or the Airflow service env file."""
    value = _PROCESS_ENV.get("LAKEHOUSE_SILVER_ENABLED")
    if value is None:
        env_path = os.environ.get("LAKEHOUSE_AIRFLOW_ENV", "/home/womcloud/airflow/airflow.env")
        try:
            with open(env_path, encoding="utf-8") as handle:
                for line in handle:
                    key, separator, raw_value = line.partition("=")
                    if separator and key.strip() == "LAKEHOUSE_SILVER_ENABLED":
                        value = raw_value.strip().strip("\"'")
                        break
        except OSError:
            value = None
    return str(value or "false").lower() == "true"


SILVER_ENABLED = silver_enabled_from_environment()
FAILURE_LEDGER = _PROCESS_ENV.get(
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


def is_deferred_source_block(platform: str, output_lines: list[str]) -> bool:
    """Recognize a source circuit-breaker block that should defer this DAG run."""
    if platform != "topcv":
        return False
    output = "\n".join(output_lines).lower()
    return (
        "http 403; circuit breaker active" in output
        or "cooldown active for http 403" in output
    )


def _run_crawler(platform: str, module: str, **_: object) -> Dict[str, Any]:
    """Run one crawler, stream its logs, and require its final JSON contract."""
    env = _runtime_environment()
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
        if is_deferred_source_block(platform, output_lines):
            raise AirflowSkipException(
                f"{platform} source is deferred by its circuit breaker; no request retry will be attempted"
            )
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
            if platform == "topcv" and state == State.SKIPPED:
                results[platform] = {
                    "status": "DEFERRED",
                    "records_ingested": 0,
                    "reason": "source_circuit_breaker_active",
                }
            else:
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
    deferred_sources = {
        source: result.get("reason") or "deferred"
        for source, result in results.items()
        if result.get("status") == "DEFERRED"
    }
    if deferred_sources:
        audit = {
            "status": "DEFERRED",
            "reason": "Bronze audit deferred because a source circuit breaker is active",
            "deferred_sources": deferred_sources,
            "source_count": len(CRAWLERS),
            "success_count": sum(1 for result in results.values() if result.get("status") == "SUCCESS"),
            "records_ingested": sum(int(result.get("records_ingested", 0)) for result in results.values()),
        }
    else:
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
    audit_result = context["ti"].xcom_pull(task_ids="bronze_audit_after_crawl") or {}
    audit_status = audit_result.get("audit", {}).get("status")
    if audit_status == "DEFERRED":
        result = {
            "status": "DEFERRED",
            "reason": "Bronze audit deferred; Silver waits for the blocked source cooldown",
            "deferred_sources": audit_result.get("audit", {}).get("deferred_sources", {}),
        }
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        raise AirflowSkipException(result["reason"])
    if audit_status != "PASS":
        reason = "Bronze audit did not pass; Silver was not run"
        print(json.dumps({"status": "SKIPPED", "reason": reason}, ensure_ascii=False, sort_keys=True))
        raise AirflowSkipException(reason)
    if not SILVER_ENABLED:
        result = {"status": "DISABLED", "reason": "LAKEHOUSE_SILVER_ENABLED=false"}
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return result
    from src.etl.bronze_to_silver import BronzeToSilverPipeline
    from src.etl.silver_writer import SilverWriter
    from src.storage.entity_registry import EntityRegistry
    from src.storage.state_manager import StateManager
    from minio import Minio
    from src.storage.atomic_writer import load_env_file

    load_env_file()
    silver_client = Minio(
        endpoint=os.getenv("MINIO_ENDPOINT", "localhost:9000"),
        access_key=os.getenv("MINIO_ROOT_USER", os.getenv("MINIO_ACCESS_KEY")),
        secret_key=os.getenv("MINIO_ROOT_PASSWORD", os.getenv("MINIO_SECRET_KEY")),
        secure=os.getenv("MINIO_SECURE", "false").lower() == "true",
    )
    silver_bucket = os.getenv("MINIO_SILVER_BUCKET", "silver")
    if not silver_client.bucket_exists(silver_bucket):
        silver_client.make_bucket(silver_bucket)
    run_id = str(context["dag_run"].run_id)
    registry = EntityRegistry(STATE_DB)
    state = StateManager(STATE_DB)
    pipeline = BronzeToSilverPipeline(
        registry=registry,
        writer=SilverWriter(
            _PROCESS_ENV.get("LAKEHOUSE_SILVER_ROOT", f"{PROJECT_ROOT}/data/silver"),
            max_records_per_batch=int(_PROCESS_ENV.get("LAKEHOUSE_SILVER_MAX_RECORDS_PER_BATCH", "5000")),
            minio_client=silver_client,
            bucket=silver_bucket,
            object_namespace=run_id,
        ),
        run_id=run_id,
        resolution_version=_PROCESS_ENV.get("LAKEHOUSE_SILVER_RESOLUTION_VERSION", "v1"),
        pending_review_path=_PROCESS_ENV.get(
            "LAKEHOUSE_PENDING_REVIEW_PATH", f"{PROJECT_ROOT}/data/operational/pending_review.csv"
        ),
    )
    object_reader = state_reader
    report = pipeline.process_bronze_objects(state, object_reader, platforms=set(CRAWLERS))
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    if report.get("status") != "SUCCESS":
        raise RuntimeError(f"Silver pipeline failed: {report.get('status')}")
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
    silver_result = context["ti"].xcom_pull(task_ids="silver_pipeline_guard") or {"status": "SKIPPED"}
    source_results = audit_result.get("source_results", {})
    audit_status = audit_result.get("audit", {}).get("status")
    if audit_status == "DEFERRED":
        status = "DEFERRED"
    elif audit_status == "PASS" and silver_result.get("status") == "SUCCESS":
        status = "SUCCESS"
    else:
        status = "FAILED"
    summary = {
        "status": status,
        "source_success_count": sum(1 for result in source_results.values() if result.get("status") == "SUCCESS"),
        "deferred_source_count": sum(1 for result in source_results.values() if result.get("status") == "DEFERRED"),
        "empty_success_count": sum(1 for result in source_results.values() if result.get("records_ingested", 0) == 0),
        "records_ingested": sum(int(result.get("records_ingested", 0)) for result in source_results.values()),
        "bronze_audit": audit_status,
        "silver_trigger": silver_result.get("status", "SKIPPED"),
        "deferred_sources": audit_result.get("audit", {}).get("deferred_sources", {}),
    }
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    if status == "FAILED":
        raise RuntimeError("Bronze/Silver pipeline did not complete successfully; see summary")
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
        trigger_rule=TriggerRule.ALL_DONE,
    )
    bronze_audit >> silver_pipeline_guard

    pipeline_summary = PythonOperator(
        task_id="crawl_pipeline_summary",
        python_callable=_report_success,
        trigger_rule=TriggerRule.ALL_DONE,
    )
    silver_pipeline_guard >> pipeline_summary

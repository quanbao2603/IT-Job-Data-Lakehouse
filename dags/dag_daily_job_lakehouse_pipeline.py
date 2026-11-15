"""Daily incremental Bronze ingestion for all nine job platforms.

Airflow is only the orchestrator here. Each crawler owns watermark, source lock,
atomic Bronze write, and failure state through BaseCrawler/StateManager.
"""
from __future__ import annotations

import os
from datetime import timedelta

import pendulum
from airflow import DAG
from airflow.operators.bash import BashOperator
from airflow.operators.python import PythonOperator

PROJECT_ROOT = os.environ.get("LAKEHOUSE_PROJECT_ROOT", "/home/womcloud/IT-Job-Data-Lakehouse")
PYTHON = os.environ.get("LAKEHOUSE_PYTHON", f"{PROJECT_ROOT}/lakehouse_env/bin/python")
MAX_PAGES = os.environ.get("LAKEHOUSE_INCREMENTAL_MAX_PAGES", "5")
MAX_ACTIVE_TASKS = int(os.environ.get("LAKEHOUSE_MAX_ACTIVE_TASKS", "3"))

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


def _report_success(**context: object) -> None:
    dag_run = context["dag_run"]
    print(f"Daily nine-platform crawl completed: {dag_run.run_id}")


with DAG(
    dag_id="daily_nine_platform_bronze_incremental",
    description="Daily incremental Bronze crawl for all nine IT job platforms",
    default_args=DEFAULT_ARGS,
    start_date=pendulum.datetime(2026, 1, 1, tz="Asia/Ho_Chi_Minh"),
    schedule="0 2 * * *",
    catchup=False,
    max_active_runs=1,
    # Keep the VPS stable: only three crawler processes run concurrently by default.
    max_active_tasks=MAX_ACTIVE_TASKS,
    dagrun_timeout=timedelta(hours=4),
    tags=["bronze", "incremental", "nine-platforms"],
) as dag:
    crawl_tasks = []
    for platform, module in CRAWLERS.items():
        task = BashOperator(
            task_id=f"crawl_{platform}_incremental",
            bash_command=(
                f"cd {PROJECT_ROOT} && "
                f"{PYTHON} -m {module} --mode incremental --max-pages {MAX_PAGES}"
            ),
            env={
                "PYTHONUNBUFFERED": "1",
                "PYTHONPATH": PROJECT_ROOT,
            },
            append_env=True,
        )
        crawl_tasks.append(task)

    pipeline_summary = PythonOperator(
        task_id="crawl_pipeline_summary",
        python_callable=_report_success,
        trigger_rule="all_success",
    )

    crawl_tasks >> pipeline_summary

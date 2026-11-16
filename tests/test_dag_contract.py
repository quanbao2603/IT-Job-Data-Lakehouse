import importlib.util
import os
import unittest


AIRFLOW_TEST_ENV = all(
    importlib.util.find_spec(name) is not None for name in ("airflow", "pendulum")
)


@unittest.skipUnless(AIRFLOW_TEST_ENV, "DAG contract requires the Airflow virtualenv")


class TestDagContract(unittest.TestCase):
    def test_silver_flag_reads_airflow_env_when_process_env_missing(self):
        from tempfile import TemporaryDirectory
        from unittest.mock import patch

        from dags.dag_daily_job_lakehouse_pipeline import silver_enabled_from_environment

        with TemporaryDirectory() as tmp:
            env_path = os.path.join(tmp, "airflow.env")
            with open(env_path, "w", encoding="utf-8") as handle:
                handle.write("LAKEHOUSE_SILVER_ENABLED=true\n")
            with patch.dict(os.environ, {"LAKEHOUSE_AIRFLOW_ENV": env_path}, clear=True):
                self.assertTrue(silver_enabled_from_environment())

    def test_dag_has_preflight_crawlers_audit_and_summary(self):
        os.environ.setdefault("AIRFLOW_HOME", "/home/womcloud/airflow")
        from dags.dag_daily_job_lakehouse_pipeline import CRAWLERS, dag

        task_ids = {task.task_id for task in dag.tasks}
        self.assertIn("preflight_bronze", task_ids)
        self.assertIn("bronze_audit_after_crawl", task_ids)
        self.assertIn("crawl_pipeline_summary", task_ids)
        self.assertEqual(
            {task_id for task_id in task_ids if task_id.startswith("crawl_") and task_id.endswith("_incremental")},
            {f"crawl_{source}_incremental" for source in CRAWLERS},
        )
        self.assertLessEqual(dag.max_active_tasks, 3)
        self.assertEqual(dag.max_active_runs, 1)
        self.assertEqual(dag.get_task("bronze_audit_after_crawl").trigger_rule, "all_done")
        self.assertEqual(dag.get_task("crawl_pipeline_summary").trigger_rule, "all_success")


if __name__ == "__main__":
    unittest.main()

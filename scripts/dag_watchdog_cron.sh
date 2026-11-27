#!/usr/bin/env bash
# If a repair run was killed hard, its EXIT trap never unpaused the nightly DAG and the pipeline
# would silently stop crawling. This re-arms it when nothing is holding the pause.
set -uo pipefail
cd /home/womcloud/IT-Job-Data-Lakehouse || exit 1
set -a
# shellcheck disable=SC1090
. "$HOME/airflow/airflow.env"
set +a
export AIRFLOW_HOME=/home/womcloud/airflow
AIRFLOW=/home/womcloud/airflow_env/bin/airflow

pgrep -f "repair_titles_recrawl" >/dev/null && exit 0

state=$("$AIRFLOW" dags list 2>/dev/null | awk '/daily_nine_platform_bronze_incremental/ {print $NF}')
if [ "$state" = "True" ]; then
  "$AIRFLOW" dags unpause daily_nine_platform_bronze_incremental >/dev/null 2>&1 \
    && echo "DAG đêm đang bị pause nhưng không có tiến trình sửa dữ liệu — đã bật lại."
fi
exit 0

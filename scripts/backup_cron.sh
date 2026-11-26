#!/usr/bin/env bash
# Weekly Gold + state-store backup. Silent on success, loud on failure.
set -uo pipefail
cd /home/womcloud/IT-Job-Data-Lakehouse || exit 1
set -a
# shellcheck disable=SC1090
. "$HOME/airflow/airflow.env"
set +a

out=$(lakehouse_env/bin/python scripts/backup_lakehouse.py 2>&1)
status=$?
if [ $status -ne 0 ]; then
  echo "BACKUP FAILED (exit $status)"
  echo "$out" | tail -12
  exit 1
fi
exit 0

#!/usr/bin/env bash
# Daily freshness guard: prints only when a source has gone quiet, so a healthy run stays silent.
set -uo pipefail
cd /home/womcloud/IT-Job-Data-Lakehouse || exit 1
set -a
# shellcheck disable=SC1090
. "$HOME/airflow/airflow.env"
set +a

out=$(lakehouse_env/bin/python scripts/freshness_alert.py "$@" 2>&1)
status=$?
if [ $status -ne 0 ]; then
  echo "freshness_alert FAILED (exit $status)"
  echo "$out" | tail -20
  exit 1
fi
if echo "$out" | grep -q '"status": "STALE"'; then
  echo "$out"
  exit 0
fi
exit 0

#!/usr/bin/env bash
# Weekly Silver retention: silence when nothing is prunable, one line when space is freed.
set -uo pipefail
cd /home/womcloud/IT-Job-Data-Lakehouse || exit 1
set -a
# shellcheck disable=SC1090
. "$HOME/airflow/airflow.env"
set +a

out=$(lakehouse_env/bin/python scripts/silver_retention.py --apply 2>&1) || {
  echo "silver_retention FAILED"; echo "$out" | tail -10; exit 1; }
freed=$(printf '%s' "$out" | python3 -c "
import json,sys
d=json.load(sys.stdin)
if d['pruned_count']:
    print(f\"Silver retention: xoá {d['pruned_count']} release cũ, giải phóng {d['freed_bytes']/1e6:.1f} MB (còn {d['releases_total']-d['pruned_count']} release).\")
" 2>/dev/null)
[ -n "$freed" ] && echo "$freed"
exit 0

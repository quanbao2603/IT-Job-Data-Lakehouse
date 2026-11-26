#!/usr/bin/env bash
# Weekly storage bound: Silver releases + raw response copies. Silent unless it frees space.
set -uo pipefail
cd /home/womcloud/IT-Job-Data-Lakehouse || exit 1
set -a
# shellcheck disable=SC1090
. "$HOME/airflow/airflow.env"
set +a

report=""
silver=$(lakehouse_env/bin/python scripts/silver_retention.py --apply 2>&1) || report+="silver_retention FAILED: $(printf '%s' "$silver" | tail -3)"$'\n'
raw=$(lakehouse_env/bin/python scripts/raw_retention.py --apply 2>&1) || report+="raw_retention FAILED: $(printf '%s' "$raw" | tail -3)"$'\n'
# Airflow keeps every task log forever (151 MB after three weeks) since no retention is configured.
log_before=$(du -sm "$HOME/airflow/logs" 2>/dev/null | cut -f1)
find "$HOME/airflow/logs" -type f -name "*.log" -mtime +14 -delete 2>/dev/null
find "$HOME/airflow/logs" -type d -empty -delete 2>/dev/null
log_after=$(du -sm "$HOME/airflow/logs" 2>/dev/null | cut -f1)
log_freed=$(( ${log_before:-0} - ${log_after:-0} ))

line=$(printf '%s\n%s' "$silver" "$raw" | python3 -c "
import json,sys
out=[]
for chunk in sys.stdin.read().splitlines():
    chunk=chunk.strip()
    if not chunk.startswith('{'): continue
    d=json.loads(chunk)
    if d.get('pruned_count'): out.append(f\"Silver: xoá {d['pruned_count']} release, {d['freed_bytes']/1e6:.1f} MB\")
    if d.get('expired_objects'): out.append(f\"Raw: xoá {d['expired_objects']} tệp, {d['freed_bytes']/1e6:.1f} MB\")
print('; '.join(out))
" 2>/dev/null)
[ -n "$line" ] && report+="Retention: $line"$'\n'
[ "${log_freed:-0}" -ge 50 ] && report+="Airflow logs: giải phóng ${log_freed} MB (>14 ngày)"$'\n'
printf '%s' "$report"
exit 0

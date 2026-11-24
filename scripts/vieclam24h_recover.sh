#!/usr/bin/env bash
# Wait out the vieclam24h block, then run one paced full refresh.
# The listing endpoint answers 403 after ~5 rapid pages and then blocks the IP for a while,
# so this script probes gently (one request per 5 minutes) before spending the page budget.
set -uo pipefail
cd /home/womcloud/IT-Job-Data-Lakehouse || exit 1

probe() {
  lakehouse_env/bin/python - <<'PY'
import sys
from curl_cffi import requests as creq
try:
    r = creq.get("https://vieclam24h.vn/tim-kiem-viec-lam-nhanh?occupation_ids[]=7&occupation_ids[]=8",
                 impersonate="chrome", timeout=30)
    sys.exit(0 if r.status_code == 200 and "__NEXT_DATA__" in r.text else 1)
except Exception:
    sys.exit(1)
PY
}

echo "[$(date +%H:%M)] probing vieclam24h"
for attempt in $(seq 1 10); do
  if probe; then
    echo "[$(date +%H:%M)] endpoint answered 200 after $attempt probe(s)"
    break
  fi
  echo "[$(date +%H:%M)] still blocked (attempt $attempt); sleeping 300s"
  sleep 300
done

if ! probe; then
  echo "[$(date +%H:%M)] gave up: still blocked"
  exit 3
fi

echo "[$(date +%H:%M)] starting paced full crawl"
set -a && . ~/airflow/airflow.env && set +a
VSL_PAGE_DELAY_MIN=14 VSL_PAGE_DELAY_MAX=20 \
  lakehouse_env/bin/python -m src.crawlers.vieclam24h_crawler --mode full --max-pages 18 --skip-detail \
  > /tmp/vieclam24h_full3.log 2>&1
echo "crawl_exit=$?"
tail -6 /tmp/vieclam24h_full3.log

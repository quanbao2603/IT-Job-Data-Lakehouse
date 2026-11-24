#!/usr/bin/env bash
# Wait out the vieclam24h WAF block, refresh the source, then rebuild Silver and Gold.
#
# The whole domain answers 403 (an AWS ELB error page) once the search endpoint has been hit
# ~5 times in a row, and the block outlives 45 minutes. So this script probes one request
# every 15 minutes, and only spends the page budget when the site answers again.
set -uo pipefail
cd /home/womcloud/IT-Job-Data-Lakehouse || exit 1

probe() {
  lakehouse_env/bin/python - <<'PY'
import sys
from curl_cffi import requests as creq
try:
    r = creq.get("https://vieclam24h.vn/", impersonate="chrome", timeout=30)
    sys.exit(0 if r.status_code == 200 else 1)
except Exception:
    sys.exit(1)
PY
}

echo "[$(date +%H:%M)] waiting for the vieclam24h block to lift"
for attempt in $(seq 1 16); do
  if probe; then
    echo "[$(date +%H:%M)] site answers again (probe $attempt)"
    break
  fi
  echo "[$(date +%H:%M)] still 403 (probe $attempt); sleeping 900s"
  sleep 900
done

if ! probe; then
  echo "[$(date +%H:%M)] gave up after 4 hours; cookie is valid but the IP stays blocked"
  exit 3
fi

set -a && . ~/airflow/airflow.env && set +a

echo "[$(date +%H:%M)] crawling 18 listing pages, 14-20s apart, card data only"
VSL_PAGE_DELAY_MIN=14 VSL_PAGE_DELAY_MAX=20 \
  lakehouse_env/bin/python -m src.crawlers.vieclam24h_crawler --mode full --max-pages 18 --skip-detail \
  > /tmp/vieclam24h_auto.log 2>&1
crawl_exit=$?
echo "[$(date +%H:%M)] crawl_exit=$crawl_exit"
tail -4 /tmp/vieclam24h_auto.log

if [ "$crawl_exit" -ne 0 ]; then
  echo "[$(date +%H:%M)] crawl failed; leaving Silver/Gold untouched"
  exit 4
fi

echo "[$(date +%H:%M)] replaying Silver"
lakehouse_env/bin/python scripts/replay_silver.py --run-id manual_vieclam24h_refresh \
  > /tmp/vieclam24h_replay.log 2>&1
replay_exit=$?
echo "[$(date +%H:%M)] replay_exit=$replay_exit"
tail -3 /tmp/vieclam24h_replay.log
if [ "$replay_exit" -ne 0 ]; then
  exit 5
fi

echo "[$(date +%H:%M)] publishing Gold"
lakehouse_env/bin/python -m src.gold.run_gold_mart --no-schema --no-mark-missing-day \
  --gold-release-id "gold_$(date +%Y%m%d_%H%M)_vieclam" > /tmp/vieclam24h_gold.log 2>&1
echo "[$(date +%H:%M)] gold_exit=$?"
tail -2 /tmp/vieclam24h_gold.log

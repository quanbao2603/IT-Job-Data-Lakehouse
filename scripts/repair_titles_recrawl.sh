#!/usr/bin/env bash
# Repair glued titles by re-crawling the sources that produced them.
#
# get_text(strip=True) concatenated sibling nodes, so "trợ lý dự án" + "công nghệ thông tin" was
# stored as "dự áncông nghệ" for every posting crawled before the parser fix. The stored bytes are
# the only place to recover the boundary from, so those postings must be fetched again. TopCV
# dominates: it needs 15-19s per detail page to stay under the rate limit, which is why this runs
# overnight rather than in a request.
#
#   scripts/repair_titles_recrawl.sh            # full chain, logs to data/operational/
#
# Order: cheap platforms first so partial progress is usable, TopCV last because it is hours.
set -uo pipefail
cd /home/womcloud/IT-Job-Data-Lakehouse || exit 1
set -a
# shellcheck disable=SC1090
. "$HOME/airflow/airflow.env"
set +a

STAMP="${1:-$(date -u +%Y%m%d)}"
LOG="data/operational/title_repair_${STAMP}.log"
PY=lakehouse_env/bin/python
mkdir -p data/operational
: > "$LOG"

log() { printf '[%s] %s\n' "$(date '+%H:%M:%S')" "$*" | tee -a "$LOG"; }

# The nightly DAG crawls the same sources at 02:00; two crawlers racing on one source would trip
# the per-source lock and leave the night half-written. Pause it for the duration.
AIRFLOW=/home/womcloud/airflow_env/bin/airflow
export AIRFLOW_HOME=/home/womcloud/airflow
"$AIRFLOW" dags pause daily_nine_platform_bronze_incremental >>"$LOG" 2>&1 && log "đã tạm dừng DAG đêm"
trap '"$AIRFLOW" dags unpause daily_nine_platform_bronze_incremental >>"$LOG" 2>&1 && log "đã bật lại DAG đêm"' EXIT

crawl() {
  local platform="$1" module="$2" pages="$3"
  log "bắt đầu ${platform} (${pages} trang)"
  if timeout 36000 "$PY" -m "$module" --mode full --max-pages "$pages" >>"$LOG" 2>&1; then
    log "${platform}: xong"
  else
    log "${platform}: LỖI (exit $?) — vẫn đi tiếp"
  fi
}

crawl vietnamworks src.crawlers.vietnamworks_crawler 30
crawl topdev        src.crawlers.topdev_crawler       10
crawl vietnamdevs   src.crawlers.vietnamdevs_crawler  10
crawl devwork       src.crawlers.devwork_crawler      10
crawl upzi          src.crawlers.upzi_crawler         20
crawl itviec        src.crawlers.itviec_crawler       20
crawl careerviet    src.crawlers.careerviet_crawler   30
crawl vieclam24h    src.crawlers.vieclam24h_crawler   20
# TopCV last: ~1.100 postings at 15-19s each.
crawl topcv         src.crawlers.topcv_crawler        30

log "replay Silver"
if ! "$PY" scripts/replay_silver.py --run-id "manual_title_repair_${STAMP}" >>"$LOG" 2>&1; then
  log "REPLAY LỖI — dừng, không publish Gold"
  exit 1
fi

log "publish Gold"
if ! "$PY" -m src.gold.run_gold_mart --gold-release-id "gold_${STAMP}_title_repair" \
      --no-schema --no-mark-missing-day >>"$LOG" 2>&1; then
  log "GOLD LỖI — dữ liệu cũ vẫn đang phục vụ"
  exit 1
fi

log "nạp nội dung chi tiết tin"
"$PY" scripts/load_job_detail.py >>"$LOG" 2>&1 || log "job_detail lỗi (không chặn)"

log "HOÀN TẤT — xem dashboard"
exit 0

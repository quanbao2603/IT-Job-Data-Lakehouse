#!/usr/bin/env bash
# Cron wrapper for the vieclam24h sweep.
#
# Prints NOTHING on a normal tick: the scheduler delivers stdout verbatim and an empty stdout
# sends no message, so silence means "still blocked / still collecting / nothing new". It speaks
# only when the sweep finishes and Gold is republished, or when a run fails in a NEW way (a
# repeated identical failure would otherwise nag every tick).
set -uo pipefail
cd /home/womcloud/IT-Job-Data-Lakehouse || exit 0

STATE=data/operational/vieclam24h_cron_last.txt
out=$(lakehouse_env/bin/python scripts/vieclam24h_sweep.py --pages-per-run 3 --gap 120 2>&1)
code=$?

status=UNKNOWN
case "$out" in
  *PUBLISHED*)        status=PUBLISHED ;;
  *SWEEP_COMPLETE*)   status=COMPLETED ;;
  *REBUILD_FAILED*)   status=REBUILD_FAILED ;;
  *CRAWL_FAILED*)     status=CRAWL_FAILED ;;
  *BLOCKED*)          status=BLOCKED ;;
  *PROGRESS*)         status=PROGRESS ;;
  *COOLDOWN*)         status=COOLDOWN ;;
esac

previous=$(cat "$STATE" 2>/dev/null || echo "")
printf '%s' "$status" > "$STATE"

case "$status" in
  PUBLISHED)
    echo "✅ vieclam24h đã cào xong toàn bộ danh sách và publish Gold mới."
    echo "$out" | tail -3
    ;;
  CRAWL_FAILED|REBUILD_FAILED)
    if [ "$status" != "$previous" ]; then
      echo "⚠️ vieclam24h: $status (mã thoát $code)"
      echo "$out" | tail -5
    fi
    ;;
  BLOCKED|PROGRESS|COOLDOWN|COMPLETED)
    : # im lặng: vẫn bị chặn, đang thu thập dần, hoặc vừa xong nên đang chờ
    ;;
  *)
    # Never swallow an unparseable run: a missing interpreter or a crashed script looks exactly
    # like silence otherwise, and the sweep would stop without anyone noticing.
    echo "⚠️ vieclam24h sweep: không đọc được kết quả (mã thoát $code)"
    echo "$out" | tail -5
    ;;
esac
exit 0

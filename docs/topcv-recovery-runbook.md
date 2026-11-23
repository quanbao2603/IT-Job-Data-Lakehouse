# Runbook: lấy lại tên công ty / địa điểm / cấp bậc cho tin TopCV

Parser đã sửa (`1a190d6`): đọc `.box-company-info-detail .company-name-label a.name` và
`.job-detail__info--section-content`. Việc còn lại chỉ là crawl lại khi TopCV hết chặn
403, vì Bronze cũ chỉ lưu field đã bóc tách, không lưu HTML gốc.

## 0. Điều kiện

- Cookie TopCV còn sống, đặt tại `src/crawlers/cookies/topcv_cookie.txt` (mode 600).
  Cách lấy: đăng nhập topcv.vn bằng trình duyệt → DevTools → Network → chọn request
  `www.topcv.vn/viec-lam/...` → Copy as cURL, hoặc dùng extension export cookie dạng
  `name=value; name2=value2`.
- Không có tiến trình TopCV nào đang chạy và `ingestion_runs` không có dòng `RUNNING`.

## 1. Kiểm tra quyền truy cập trước khi crawl

```bash
cd /home/womcloud/IT-Job-Data-Lakehouse
lakehouse_env/bin/python - <<'PY'
import pathlib, requests
raw = pathlib.Path("src/crawlers/cookies/topcv_cookie.txt").read_text().strip()
cookies = {k.strip(): v.strip() for k, v in (kv.split("=", 1) for kv in raw.split(";") if "=" in kv)}
r = requests.get("https://www.topcv.vn/viec-lam/software-support-specialist/2335659.html",
                 headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/131.0.0.0 Safari/537.36",
                          "Accept-Language": "vi-VN,vi;q=0.9"}, cookies=cookies, timeout=25)
print("status:", r.status_code)
PY
```

`403` → dừng, chờ hoặc đổi IP/proxy. `200` → chạy bước 2.

## 2. Crawl lại TopCV

```bash
set -a && . ~/airflow/airflow.env && set +a
lakehouse_env/bin/python -m src.crawlers.topcv_crawler --mode full --max-pages 30
```

Kỳ vọng: log mỗi tin in `[✓] <title> | Lương: ... | Kinh nghiệm: ...` với kinh nghiệm
không còn `N/A`; record mới có `company_name`, `company_size`, `company_address`,
`company_industry`, `job_location`, `job_level`.

## 3. Replay Silver từ Bronze (không crawl nguồn khác)

```bash
lakehouse_env/bin/python scripts/replay_silver.py   # run_id manual_facet_fix_<stamp>
```

## 4. Build + publish Gold

```bash
lakehouse_env/bin/python -m src.gold.run_gold_mart \
  --no-schema --no-mark-missing-day \
  --gold-release-id gold_$(date +%Y%m%d)_topcv_fix
```

## 5. Nghiệm thu

```bash
lakehouse_env/bin/python - <<'PY'
import psycopg2, pathlib
env = {}
for line in (pathlib.Path.home() / "airflow/airflow.env").read_text().splitlines():
    s = line.strip()
    if s and not s.startswith("#") and "=" in s:
        k, v = s.split("=", 1); env[k.strip()] = v.strip().strip('"').strip("'")
c = psycopg2.connect(host="127.0.0.1", dbname=env["GOLD_DB_NAME"], user=env["GOLD_DB_USER"], password=env["GOLD_DB_PASSWORD"])
c.set_session(readonly=True, autocommit=True); cur = c.cursor()
cur.execute("select gold_release_id from gold_control.active_release"); print("active:", cur.fetchone()[0])
cur.execute("""select count(*) filter (where c.is_unknown), count(*)
               from gold.dim_job j join gold.dim_company c using(company_key) where j.is_current""")
print("unknown / total:", cur.fetchone())
PY
```

Mục tiêu: số tin `is_unknown` giảm từ 1.310 xuống còn ~30 (chỉ Devwork ẩn nhà tuyển dụng);
`UNKNOWN` ở địa điểm giảm tương ứng; `job_level` của TopCV không còn NULL hàng loạt.

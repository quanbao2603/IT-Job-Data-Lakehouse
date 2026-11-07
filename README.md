# IT-Job-Data-Lakehouse

Hệ thống Data Lakehouse thu thập dữ liệu thị trường việc làm IT từ ITviec, TopCV, TopDev và VietnamWorks, sau đó ghi dữ liệu vào tầng Bronze trên MinIO/S3.

> Phạm vi repository hiện tại tập trung vào crawler, storage và Bronze ingestion. Silver/Gold chưa nằm trong source snapshot này.

## Kiến trúc hiện tại

```text
Crawler nguồn
  → MetadataEnvelope + SHA-256
  → disk-streaming writer
  → landing/<source>/ trên MinIO
  → verified copy
  → bronze/<source>/ trên MinIO
  → SQLite State Store / manifest / audit
```

Các thành phần chính:

- `src/crawlers/`: crawler cho bốn nguồn dữ liệu.
- `src/storage/`: writer, envelope, state manager và compaction.
- `src/utils/`: audit, backfill manifest, backup/restore verification và dọn test data.
- `tests/`: test suite của tầng Bronze.
- `infrastructure/docker-compose.yml`: MinIO chạy nội bộ trên VPS.
- `docs/bronze-architecture-review.md`: review kiến trúc và bằng chứng kiểm tra Bronze.

Payload Bronze hiện là **extracted observation payload** theo từng crawl run. Cookie/session, `.env`, SQLite runtime, MinIO objects, log và virtualenv không thuộc Git repository.

## Yêu cầu môi trường

- Linux/macOS/WSL hoặc môi trường Unix tương đương.
- Python 3.10+; VPS hiện dùng Python 3.10.12 cho virtualenv.
- Docker Engine và Docker Compose.
- Kết nối tới nguồn dữ liệu cần crawl.
- MinIO/S3 endpoint và credential được cung cấp qua `.env` hoặc environment variables.

## Cài đặt local

```bash
git clone https://github.com/quanbao2603/IT-Job-Data-Lakehouse.git
cd IT-Job-Data-Lakehouse

python3 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
cp .env.example .env
```

Sửa `.env` bằng credential MinIO thật trên máy local. Không commit `.env`.

Nếu chỉ cần chạy test core và đã có môi trường Python tương thích, có thể dùng virtualenv hiện có:

```bash
. .venv/bin/activate
```

Không clone hoặc commit `lakehouse_env/`; đây là virtualenv của VPS, hiện đã được ignore.

## Khởi động MinIO local

```bash
docker compose --env-file .env -f infrastructure/docker-compose.yml up -d
```

Kiểm tra service:

```bash
curl -f http://127.0.0.1:9000/minio/health/live
curl -I http://127.0.0.1:9001/
```

Compose mặc định bind MinIO vào `127.0.0.1`, không mở trực tiếp S3 API hoặc Console ra Internet. `MINIO_BROWSER_REDIRECT_URL` trong `.env.example` chỉ là ví dụ; khi chạy local nên đổi thành URL phù hợp hoặc bỏ biến này.

## Chạy test

Chạy từ thư mục gốc để Python resolve package `src`:

```bash
python -m compileall -q src tests
python -m unittest discover -s tests -v
```

Trên VPS, lệnh đã dùng là:

```bash
lakehouse_env/bin/python -m compileall -q src tests
lakehouse_env/bin/python -m unittest discover -s tests -v
```

Các test bao phủ metadata/checksum, schema drift, state lifecycle, streaming generator, fail-closed manifest commit, heartbeat và concurrency probe.

## Chạy crawler

Luôn chạy module từ thư mục gốc. Mặc định crawler chạy `incremental` với số trang nhỏ để kiểm tra:

```bash
python -m src.crawlers.itviec_crawler --mode incremental --max-pages 1
python -m src.crawlers.topcv_crawler --mode incremental --max-pages 1
python -m src.crawlers.topdev_crawler --mode incremental --max-pages 1
python -m src.crawlers.vietnamworks_crawler --mode incremental --max-pages 1
```

Full crawl:

```bash
python -m src.crawlers.itviec_crawler --mode full --max-pages 5
python -m src.crawlers.topcv_crawler --mode full --max-pages 5
python -m src.crawlers.topdev_crawler --mode full --max-pages 5
python -m src.crawlers.vietnamworks_crawler --mode full --max-pages 5
```

Crawl thật có thể cần cookie/session hợp lệ trong thư mục local riêng. Không đặt cookie thật vào GitHub.

## Công cụ Bronze

Các lệnh chạy từ project root:

```bash
# Báo cáo Bronze
python -m src.utils.bronze_audit_reporter
python -m src.utils.bronze_audit_reporter --deep

# Backfill manifest cho object đã tồn tại
python -m src.utils.backfill_manifest

# Tạo backup metadata; thêm --include-objects để sao lưu payload Bronze
python -m src.utils.backup_bronze
python -m src.utils.backup_bronze --include-objects

# Kiểm tra snapshot backup gần nhất
python -m src.utils.backup_bronze --verify-drill backups/<snapshot-directory>

# Restore payload của snapshot vào bucket kiểm thử
python -m src.utils.backup_bronze --restore-to-bucket bronze-restore-test

# Dọn object test theo utility hiện có; đọc source trước khi chạy trên dữ liệu thật
python -m src.utils.clean_test_bronze
```

Các lệnh backup/restore cần `.env`, State Store và MinIO runtime hợp lệ. Không chạy cleanup hoặc restore vào bucket production nếu chưa kiểm tra tham số.

## Cấu hình môi trường

Bắt đầu từ `.env.example`. Các biến chính:

```text
MINIO_ENDPOINT
MINIO_ROOT_USER
MINIO_ROOT_PASSWORD
MINIO_SECURE
MINIO_LANDING_BUCKET
MINIO_BRONZE_BUCKET
MINIO_ACCESS_KEY       # alias tương thích cũ, nếu cần
MINIO_SECRET_KEY       # alias tương thích cũ, nếu cần
```

Không đưa password, API key, cookie, token, database runtime hoặc object payload vào commit.

## Trạng thái VPS

Snapshot runtime đã sanitize được ghi tại:

- `docs/deployment-vps.md`
- `docs/runtime-state.md`

URL Console MinIO hiện tại trên VPS là `https://womtech.io.vn/minio/ui/`, đi qua Nginx HTTPS reverse proxy. Port MinIO `9000/9001` vẫn chỉ bind localhost.

## Quy tắc đóng góp

1. Chạy compile và test trước khi commit.
2. Không commit `.env`, cookie/session, `data/`, `backups/`, log hoặc virtualenv.
3. Không đưa secret vào README, issue, commit message hay tài liệu.
4. Khi thay đổi deployment VPS, cập nhật `docs/deployment-vps.md` và `docs/runtime-state.md` bằng thông tin đã sanitize.

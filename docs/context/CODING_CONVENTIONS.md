# CODING_CONVENTIONS.md — IT Job Data Lakehouse

---

## 1. Ngôn ngữ & Framework

| Thành phần | Công nghệ | Phiên bản |
|---|---|---|
| Ngôn ngữ chính | Python | 3.10+ |
| Object Storage | MinIO (S3-compatible) | Docker container |
| State Store | SQLite 3 (WAL mode) | Built-in |
| Crawling | requests + BeautifulSoup / JSON API | — |
| ETL | PySpark / PyArrow | — |
| Orchestration | Apache Airflow | — |
| Schema | PyArrow Schema | — |
| Containerization | Docker + Docker Compose | — |
| VPS | Ubuntu + NVMe SSD | 75GB ổ |

---

## 2. Cấu trúc thư mục `[CONFIRMED]`

```
IT-Job-Data-Lakehouse/
├── src/
│   ├── crawlers/
│   │   ├── base_crawler.py              ← Abstract base tất cả crawler
│   │   ├── itviec_crawler.py
│   │   ├── topcv_crawler.py
│   │   ├── topdev_crawler.py
│   │   ├── vietnamworks_crawler.py
│   │   └── {platform}/                 ← daily_scrape_*.py, weekly_audit_*.py
│   ├── storage/
│   │   ├── state_manager.py            ← SQLite State Store
│   │   ├── atomic_writer.py            ← MinIO staged write
│   │   ├── metadata_envelope.py        ← Wrapper chuẩn
│   │   └── compaction_engine.py        ← File compaction
│   ├── etl/
│   │   ├── bronze_to_silver.py
│   │   ├── quality_gate.py
│   │   ├── transformers.py
│   │   └── staging_validator.py
│   ├── schemas/
│   │   └── silver_schema.py            ← 41-field PyArrow schema
│   └── utils/
│       ├── minio_client.py
│       ├── bronze_audit_reporter.py
│       ├── backup_bronze.py
│       └── export_silver_for_ssis.py
├── tests/
│   └── test_bronze_layer.py            ← 19 unit & stress tests
├── dags/
│   └── dag_daily_job_lakehouse_pipeline.py
├── data/
│   └── state_store.db                  ← SQLite DB (git-ignored)
├── .agent/
│   ├── AGENTS.md                       ← Agent persona & workflow rules
│   ├── STATE.md                        ← Tiến độ dự án
│   └── BUSINESS_LOGIC.md              ← Từ điển nghiệp vụ
└── .env                                ← Credentials (git-ignored)
```

---

## 3. Quy tắc đặt tên `[CONFIRMED]`

| Đối tượng | Quy tắc | Ví dụ |
|---|---|---|
| File Python | `snake_case.py` | `state_manager.py` |
| Class | `PascalCase` | `StateManager`, `BaseCrawler` |
| Function / Method | `snake_case` | `acquire_run_lock()`, `fetch_jobs()` |
| Biến | `snake_case` | `last_watermark_ts`, `run_id` |
| Hằng số | `UPPER_SNAKE_CASE` | `STALE_LEASE_SECONDS`, `SILVER_CANONICAL_FIELDS` |
| Crawler class | `{Platform}Crawler` | `ITViecCrawler`, `TopCVCrawler` |
| Source name (string) | `lowercase` | `"itviec"`, `"topcv"` |
| MinIO bucket | `lowercase-hyphen` | `bronze`, `landing`, `silver` |
| Partition key | `snake_case=value` | `ingest_year=2026` |

---

## 4. Quy tắc Type Hint `[CONFIRMED]`

**BẮT BUỘC** dùng type hint cho tất cả function signature:

```python
# ✅ ĐÚNG
def acquire_run_lock(self, source_name: str, run_id: str, mode: str) -> Optional[str]:
    ...

def fetch_jobs(
    self,
    mode: str,
    max_pages: int,
    watermark_ts: Optional[str] = None,
    last_record_id: Optional[str] = None
) -> Iterable[Dict[str, Any]]:
    ...

# ❌ SAI
def acquire_run_lock(self, source_name, run_id, mode):
    ...
```

---

## 5. Quy tắc xử lý lỗi & Logging `[CONFIRMED]`

**Early Return pattern** — kiểm tra lỗi đầu hàm, không lồng if:

```python
# ✅ ĐÚNG
def run(self, mode: str = "incremental") -> Dict[str, Any]:
    acquired = self.state_manager.acquire_run_lock(...)
    if not acquired:
        logging.warning("BLOCKED: source đang chạy")
        return {"status": "BLOCKED"}
    
    # Main logic không lồng nhau
    ...

# ❌ SAI
def run(self, mode):
    acquired = self.state_manager.acquire_run_lock(...)
    if acquired:
        # toàn bộ logic 100 dòng lồng trong if
        ...
```

**Logging bắt buộc** dùng module `logging`, không dùng `print` ở production code (crawler hiện dùng `print` ở một số chỗ — cần refactor):

```python
import logging
logger = logging.getLogger(__name__)
logger.error(f"[{self.source_name}] Lỗi fetch page {page}: {e}", exc_info=True)
```

---

## 6. Quy tắc cấu hình & biến môi trường `[CONFIRMED]`

- **TUYỆT ĐỐI CẤM** hardcode credential trong code.
- Mọi secret đọc từ `.env` qua `os.environ.get()` hoặc `python-dotenv`.
- Tên biến môi trường: `UPPER_SNAKE_CASE`.

```python
# ✅ ĐÚNG
endpoint = os.environ.get("MINIO_ENDPOINT", "localhost:9000")
access_key = os.environ.get("MINIO_ACCESS_KEY")

# ❌ SAI
access_key = "minioadmin"
```

**Biến môi trường cần thiết** (xem `.env.example`):
```
MINIO_ENDPOINT=
MINIO_ACCESS_KEY=
MINIO_SECRET_KEY=
MINIO_SECURE=false
MINIO_BRONZE_BUCKET=bronze
MINIO_LANDING_BUCKET=landing
```

---

## 7. Quy tắc Airflow `[CONFIRMED]`

- Task/DAG **BẮT BUỘC idempotent**: chạy lại cùng ngày kết quả không thay đổi.
- **CẤM** lưu state cục bộ trong Operator (dùng StateManager qua SQLite).
- Dùng `PythonOperator` cho crawlers, không dùng `BashOperator` cho logic phức tạp.

---

## 8. Quy tắc Data Engineering `[CONFIRMED]`

- **PySpark / PyArrow > Pandas** cho tập dữ liệu > vài chục nghìn records.
- Khi schema thay đổi → **phải** cập nhật `SILVER_CANONICAL_FIELDS` trong `src/schemas/silver_schema.py`.
- Bronze ghi **JSONL + gzip** (`*.jsonl.gz`) — không ép schema.
- Silver ghi **Parquet** với Hive-style partition.
- Mọi field nằm ngoài 41 trường canonical → gom vào `platform_specific_attributes` (JSON string).

---

## 9. Những cách làm bị cấm

| ❌ Bị cấm | ✅ Thay thế |
|---|---|
| Hardcode credential | `os.environ.get()` |
| `print()` thay vì `logging` | `logging.error/warning/info()` |
| Pandas cho large dataset | PySpark / PyArrow |
| Lưu state trong Operator | `StateManager` |
| Thêm cột vào Silver mà không update schema | Cập nhật `silver_schema.py` trước |
| `SELECT * then INSERT` (TOCTOU race) | Insert-First pattern trong `BEGIN IMMEDIATE` |
| Ghi trực tiếp vào `bronze/` | Luôn qua AtomicWriter (staging → commit) |
| Xóa file trong `bronze/` | Chỉ CompactionEngine mới được move sang `archive/` |

---

## Open Questions

- `[UNCONFIRMED]` Crawler hiện có một số `print()` — đã refactor sang `logging` chưa?
- `[TODO]` Thêm `pytest` fixtures thay vì `unittest` cho test suite?


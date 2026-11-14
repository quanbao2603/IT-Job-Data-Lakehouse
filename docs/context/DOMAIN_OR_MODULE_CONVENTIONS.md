# DOMAIN_OR_MODULE_CONVENTIONS.md — Crawler & ETL Pipeline

---

## 1. Cấu trúc một Crawler chuẩn `[CONFIRMED]`

Mọi crawler kế thừa `BaseCrawler` (`src/crawlers/base_crawler.py`):

```python
from src.crawlers.base_crawler import BaseCrawler

class ITViecCrawler(BaseCrawler):
    def __init__(self, endpoint=None, access_key=None, secret_key=None,
                 secure=None, bronze_bucket=None, landing_bucket=None):
        super().__init__(
            source_name="itviec",
            endpoint=endpoint, access_key=access_key,
            secret_key=secret_key, secure=secure,
            bronze_bucket=bronze_bucket, landing_bucket=landing_bucket
        )

    def fetch_jobs(
        self,
        mode: str,
        max_pages: int,
        watermark_ts: Optional[str] = None,
        last_record_id: Optional[str] = None
    ) -> Iterable[Dict[str, Any]]:
        for page in range(1, max_pages + 1):
            self.heartbeat()          # ← BẮT BUỘC ở đầu mỗi trang (fetch phase)
            jobs = self._fetch_page(page)
            if not jobs:
                break
            for job in jobs:
                # Early stopping — incremental mode
                if mode == "incremental" and self._is_before_watermark(job, watermark_ts):
                    return
                yield job
```

**Luồng hoạt động bắt buộc của `BaseCrawler.run()`:**

```
acquire_run_lock() → get_watermark() → fetch_jobs() [yield] → 
MetadataEnvelope.wrap() → AtomicWriter [write + heartbeat/10 records] → 
commit_run() → release_lock()
```

---

## 2. Quy tắc đặt tên Crawler & Source `[CONFIRMED]`

| Đối tượng | Quy tắc | Ví dụ |
|---|---|---|
| Class Crawler | `{Platform}Crawler` (PascalCase) | `ITViecCrawler` |
| `source_name` string | lowercase, không dấu, không space | `"itviec"`, `"topcv"`, `"topdev"`, `"vietnamworks"` |
| File crawler | `{platform}_crawler.py` | `itviec_crawler.py` |
| File daily script | `daily_scrape_{platform}.py` | `daily_scrape_itviec.py` |
| File audit script | `weekly_audit_{platform}.py` | `weekly_audit_itviec.py` |

---

## 3. URL / Endpoint `[CONFIRMED]`

| Platform | Phương thức | Ghi chú |
|---|---|---|
| ITViec | HTML scraping (BeautifulSoup) | Parse DOM |
| TopCV | API JSON + HTML fallback | REST API |
| TopDev | API JSON | REST API |
| VietnamWorks | API JSON | REST API |

- URL endpoint đọc từ `.env` hoặc hardcode trong class (không phải config file riêng) — `[INFERRED]`.
- Không được thay đổi endpoint mà không kiểm tra lại HTML/API schema.

---

## 4. HTTP Request, Timeout, Retry, Rate Limit `[INFERRED]`

```python
import requests
from time import sleep

# Pattern chuẩn (inferred từ codebase)
response = requests.get(url, headers=HEADERS, timeout=30)
response.raise_for_status()

# Delay giữa các trang để tránh bị block
sleep(1)  # Minimum 1 giây giữa requests
```

- **Chưa có cơ chế retry tự động** `[UNCONFIRMED]` — nếu page fail, exception sẽ được log và run kết thúc với `FAILED`.
- **Rate limiting:** Implicit qua `sleep()` — chưa có exponential backoff `[TODO]`.

---

## 5. Pagination `[CONFIRMED]`

```python
# Pattern: iterate qua trang từ 1 đến max_pages
for page in range(1, max_pages + 1):
    self.heartbeat()
    jobs = self._fetch_page(page)
    if not jobs:
        break  # Early stop khi hết data
```

- `max_pages` là tham số bắt buộc khi gọi `crawler.run(mode, max_pages)`.
- Full mode: `max_pages` lớn (ví dụ 999) để quét tất cả.
- Incremental mode: `max_pages` nhỏ hơn + early stopping khi gặp watermark.

---

## 6. Early Stopping (Incremental Mode) `[CONFIRMED]`

```python
# Dừng sớm khi gặp bản ghi đã có trong watermark
consecutive_old = 0
EARLY_STOP_THRESHOLD = 5  # [INFERRED] dừng sau N bản ghi liên tiếp đã biết

for job in page_jobs:
    if job['id'] == last_record_id or job['posted_at'] <= watermark_ts:
        consecutive_old += 1
        if consecutive_old >= EARLY_STOP_THRESHOLD:
            return  # Generator kết thúc
    else:
        consecutive_old = 0
        yield job
```

---

## 7. Parse, Normalize, Validate `[CONFIRMED]`

- **Bronze:** KHÔNG normalize — yield nguyên bản từ nguồn.
- **Silver (`transformers.py`):** Normalize lương, company name, skills, temporal fields.
- **Validation:** Chỉ ở Silver Quality Gate — Bronze nhận tất cả.

---

## 8. Chống Duplicate `[CONFIRMED]`

**Hai lớp bảo vệ:**

1. **Bronze:** `acquire_run_lock()` với `idx_active_running_per_source` — không cho 2 tiến trình cùng source chạy song song.
2. **Silver:** `unified_job_id` = `{platform}_{original_job_id}` làm primary key; MERGE INTO / upsert xử lý duplicate khi re-ingest.

---

## 9. Lưu raw data `[CONFIRMED]`

```
Bronze: s3://bronze/{source}/ingest_year=.../ingest_month=.../ingest_day=.../
          part_{source}_{mode}_{ts}_{uuid}.jsonl.gz
          
Mỗi dòng trong file là một JSON object đã được bọc bởi MetadataEnvelope.
Không ép schema — mọi field gốc giữ nguyên.
```

---

## 10. Logging & Theo dõi lỗi `[CONFIRMED]`

```python
# Crawler in ra stdout (hiện dùng print, TODO: chuyển sang logging)
print(f"🚀 BẮT ĐẦU PHIÊN CÀO [{source_name.upper()}] - CHẾ ĐỘ: {mode.upper()}")
print(f"   - Mã phiên chạy: {run_id}")

# Mọi exception phải được:
# 1. Log ra stdout/stderr
# 2. Ghi vào ingestion_runs.error_message
# 3. Cập nhật status = 'FAILED'
# 4. KHÔNG được nuốt exception im lặng
```

---

## 11. Test & Fixture `[CONFIRMED]`

**File test:** `tests/test_bronze_layer.py` — 17 unit tests (bản project cũ có 20; 3 test heartbeat/index/stress chưa port lại — xem CURRENT_STATUS mục 2)

**Tests quan trọng:**
- `test_multiprocess_concurrency_race`: 2 process OS thật, `mp.Barrier`, chỉ 1 được acquire lock.
- `test_heartbeat_lifecycle`: verify heartbeat cập nhật `heartbeat_at` trong DB.
- `test_manifest_failure_aborts_commit`: verify SHA-256 mismatch abort commit.
- Stress test: 10 vòng liên tiếp, kết quả idempotent.

**Pattern test:**
```python
import unittest
import tempfile
from src.storage.state_manager import StateManager

class TestBronzeLayer(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
        self.sm = StateManager(db_path=self.tmp.name)
```

---

## 12. Implementation chuẩn để tham khảo `[CONFIRMED]`

| Mục đích | File |
|---|---|
| Crawler pattern chuẩn | `src/crawlers/base_crawler.py` |
| Crawler hoàn chỉnh nhất | `src/crawlers/itviec_crawler.py` |
| State Store đầy đủ nhất | `src/storage/state_manager.py` |
| Atomic write pattern | `src/storage/atomic_writer.py` |

---

## 13. Hành vi KHÔNG được tự ý thay đổi `[CONFIRMED]`

- Thứ tự: `acquire_run_lock` → `fetch` → `envelope` → `write atomic` → `commit` — không được đảo thứ tự.
- `source_name` lowercase — nếu thay đổi sẽ break watermark lookup trong SQLite.
- SHA-256 checksum trong `metadata_envelope.py` — không được tắt.
- Partial unique index `idx_active_running_per_source` — không được drop.
- Staging `.tmp` → commit rename pattern — không được ghi thẳng vào `bronze/`.

---

## Open Questions

- `[TODO]` Thêm exponential backoff + retry cho HTTP requests.
- `[UNCONFIRMED]` Threshold `EARLY_STOP_THRESHOLD` chính xác là bao nhiêu consecutive old records?
- `[TODO]` Chuyển `print()` sang `logging` module trong toàn bộ crawler.


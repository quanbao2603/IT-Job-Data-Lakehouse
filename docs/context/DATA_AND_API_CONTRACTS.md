# DATA_AND_API_CONTRACTS.md — IT Job Data Lakehouse

---

## 1. Bronze — Metadata Envelope Schema `[CONFIRMED]`

Mọi bản ghi lưu vào Bronze phải được bọc trong cấu trúc 3 tầng:

```json
{
  "_metadata": {
    "ingested_at": "2026-11-08T10:30:00Z",
    "source": "itviec",
    "payload_checksum": "<SHA-256 hex của raw_payload string>",
    "pipeline_run_id": "itviec_incremental_20261108_103000_a1b2"
  },
  "raw_payload": {
    "job_id": "...",
    "title": "...",
    "...": "toàn bộ field gốc không thay đổi"
  },
  "_rescued_data": {
    "unexpected_field_1": "...",
    "...": "..."
  }
}
```

**Quy tắc:**
- `raw_payload`: giữ nguyên 100% — không ép kiểu, không xóa field, không sửa lỗi chính tả.
- `_rescued_data`: field không dự tính từ nguồn → tự động nhét vào đây (không được drop).
- `payload_checksum`: SHA-256 tính từ chuỗi JSON của `raw_payload` (canonical serialization).

---

## 2. SQLite State Store Tables `[CONFIRMED]`

### `ingestion_state` — Watermark gần nhất

```sql
CREATE TABLE ingestion_state (
    source_name       TEXT PRIMARY KEY,  -- 'itviec', 'topcv', 'topdev', 'vietnamworks'
    last_watermark_ts TEXT,              -- ISO 8601 UTC timestamp
    last_record_id    TEXT,              -- ID bản ghi cuối cùng đã nạp
    last_run_status   TEXT,              -- 'SUCCESS', 'FAILED', 'RUNNING'
    records_ingested  INTEGER DEFAULT 0, -- Tổng bản ghi đã nạp đợt gần nhất
    updated_at        TEXT               -- ISO 8601 UTC
);
```

### `ingestion_runs` — Audit trail phiên chạy

```sql
CREATE TABLE ingestion_runs (
    run_id           TEXT PRIMARY KEY,   -- "{source}_{mode}_{timestamp}_{uuid4[:4]}"
    source_name      TEXT NOT NULL,
    mode             TEXT NOT NULL,      -- 'full' | 'incremental'
    started_at       TEXT NOT NULL,
    finished_at      TEXT,
    status           TEXT NOT NULL,      -- 'RUNNING' | 'SUCCESS' | 'FAILED'
    records_ingested INTEGER DEFAULT 0,
    bronze_s3_uri    TEXT,               -- "s3://bronze/itviec/.../part_*.jsonl.gz"
    duration_sec     REAL DEFAULT 0.0,
    error_message    TEXT,
    process_id       INTEGER,            -- PID tiến trình OS
    heartbeat_at     TEXT                -- Cập nhật định kỳ khi đang RUNNING
);

-- PARTIAL UNIQUE INDEX — khóa chống tiến trình trùng lặp:
CREATE UNIQUE INDEX idx_active_running_per_source
ON ingestion_runs(source_name) WHERE status = 'RUNNING';
```

**Stale Lease:** `STALE_LEASE_SECONDS = 7200` (2 giờ). Phiên RUNNING với `heartbeat_at` cũ hơn 2 giờ được tự động thu hồi khi tiến trình mới acquire lock.

### `compaction_audit_logs` — Lịch sử compaction `[INFERRED]`

Ghi lại mỗi lần `CompactionEngine` chạy: source, số files gộp, tổng records, s3_uri kết quả.

---

## 3. Silver Canonical Schema — 41 trường `[CONFIRMED]`

Định nghĩa bằng PyArrow tại `src/schemas/silver_schema.py`.

| Nhóm | Trường | Kiểu | Ghi chú |
|---|---|---|---|
| **Định danh & Kiểm toán** | `unified_job_id` | string | PK = `LOWER(platform) + '_' + TRIM(original_job_id)` |
| | `platform` | string | 'itviec' \| 'topcv' \| 'topdev' \| 'vietnamworks' |
| | `original_job_id` | string | ID gốc từ nguồn |
| | `job_url` | string | URL đầy đủ |
| | `content_hash` | string | SHA-256 nội dung để detect UPDATED |
| | `crawled_at` | timestamp | UTC |
| | `silver_updated_at` | timestamp | UTC |
| | `is_active` | bool | false khi job đóng (CLOSED) |
| **Vị trí & Mô hình** | `job_title` | string | **MANDATORY** — không được null |
| | `job_level` | string | Fresher/Junior/Mid/Senior/Lead/Manager |
| | `job_expertise` | string | — |
| | `job_domain` | string | — |
| | `work_mode` | string | Remote/Hybrid/Onsite |
| | `working_time` | string | — |
| | `overtime_policy` | string | — |
| **Doanh nghiệp** | `company_name` | string | **MANDATORY** — đã lược bỏ tiền tố pháp nhân |
| | `company_name_raw` | string | Tên gốc 100% |
| | `company_size` | string | — |
| | `company_industry` | string | — |
| | `company_type` | string | — |
| | `company_country` | string | — |
| **Địa bàn** | `city` | string | — |
| | `full_address` | string | — |
| **Lương** | `salary_raw` | string | Chuỗi gốc từ nguồn |
| | `salary_min_vnd` | int64 | BIGINT — quy VND (USD × 25,400) |
| | `salary_max_vnd` | int64 | BIGINT |
| | `is_negotiable` | bool | true nếu "Thoả thuận / Attractive" |
| | `salary_currency` | string | 'VND' \| 'USD' |
| **Kinh nghiệm & Tương tác** | `exp_years_min` | float | — |
| | `exp_years_max` | float | — |
| | `vacancies_count` | int32 | — |
| | `views_count` | int32 | — |
| | `applications_count` | int32 | — |
| **Thời gian** | `posted_date` | date | ISO YYYY-MM-DD |
| | `expired_date` | date | Quy đổi từ relative time |
| **Kỹ năng & Nội dung** | `skills_raw` | string | Chuỗi gốc |
| | `skills_standardized` | list<string> | Output LLM + taxonomy >120 kỹ năng |
| | `job_description_clean` | string | — |
| | `requirements_clean` | string | — |
| | `benefits_clean` | string | — |
| **Bảo toàn** | `platform_specific_attributes` | string | JSON string — mọi field ngoài 41 trường |

---

## 4. Quality Gate Rules `[CONFIRMED]`

| Rule | Điều kiện vi phạm | Hành động |
|---|---|---|
| Mandatory Fields | `job_title IS NULL` hoặc `company_name IS NULL` | → Quarantine |
| Salary Outlier (Low) | `salary_min_vnd < 3,000,000` VND/tháng | → Quarantine |
| Salary Outlier (High) | `salary_max_vnd > 300,000,000` VND/tháng | → Quarantine |
| Schema Drift Alert | `quarantine_rate_pct > 5.0%` trong một batch | → Báo động + chặn SSIS handoff |
| Zero Data Loss | Mọi field không thuộc 41 trường | → `platform_specific_attributes` |

---

## 5. CDC — Change Data Capture `[CONFIRMED]`

| Event | Điều kiện | Hành động Silver |
|---|---|---|
| `NEW` | `unified_job_id` chưa tồn tại | INSERT |
| `UPDATED` | `content_hash` thay đổi | UPSERT (MERGE INTO) |
| `CLOSED` | Job không còn xuất hiện trong listing | UPDATE `is_active = false` |
| `REOPEN` | Job xuất hiện lại sau CLOSED | UPDATE `is_active = true` |

---

## 6. Transformation Rules bắt buộc `[CONFIRMED]`

```python
# 1. Unified Job ID
unified_job_id = f"{platform.lower()}_{original_job_id.strip()}"

# 2. Lương USD → VND
FX_USD_TO_VND = 25_400
salary_min_vnd = salary_min_usd * FX_USD_TO_VND  # BẮT BUỘC, không dùng tỷ giá live

# 3. Skills standardized
# Dùng LLM API + word-boundary regex trên taxonomy >120 kỹ năng
# Xuất CSV cho SSIS: dùng dấu ";" làm separator
skills_csv = "; ".join(skills_standardized)

# 4. Company name cleaning
# Lược bỏ: "Công ty TNHH", "CTCP", "Co. Ltd", "JSC", "Corp", "Inc", ...

# 5. Temporal
# "đăng 3 ngày trước" → posted_date = today - 3 days → ISO YYYY-MM-DD
# "15 ngày nữa" → expired_date = today + 15 days → ISO YYYY-MM-DD
```

---

## 7. MinIO Object Naming Convention `[CONFIRMED]`

```
s3://bronze/{source_name}/ingest_year={YYYY}/ingest_month={MM}/ingest_day={DD}/
  part_{source}_{mode}_{timestamp}_{short_uuid}.jsonl.gz

Ví dụ:
  part_itviec_incremental_20261108_103000_a1b2.jsonl.gz
```

- `{short_uuid}`: 4 ký tự hex từ `uuid4().hex[:4]` — chống xung đột tên khi ghi song song.

---

## Open Questions

- `[NEEDS_CONFIRMATION]` Tỷ giá USD/VND: hardcode `25,400` hay cho phép cập nhật?
- `[UNCONFIRMED]` Silver MERGE INTO dùng Delta Lake hay append + deduplicate?
- `[TODO]` Schema version: khi thêm trường Silver mới → migrate strategy?


# DECISIONS.md — IT Job Data Lakehouse

> Bảng các quyết định kiến trúc và kỹ thuật quan trọng.
> Mỗi quyết định đánh dấu trạng thái: **CONFIRMED** / **TEMPORARY** / **REJECTED** / **OPEN**

---

## Bảng quyết định

| ID | Quyết định | Trạng thái | File bằng chứng |
|---|---|---|---|
| D-01 | SQLite WAL làm State Store thay vì file JSON | CONFIRMED | `src/storage/state_manager.py` |
| D-02 | Insert-First concurrency pattern (không Select-then-Insert) | CONFIRMED | `state_manager.py:acquire_run_lock()` |
| D-03 | Staging `.tmp` → verify SHA-256 → commit rename (Atomic Write) | CONFIRMED | `src/storage/atomic_writer.py` |
| D-04 | Hive-style Ingestion Time partitioning (không Event Time) | CONFIRMED | `docs/bronze-architecture-review.md` |
| D-05 | JSONL + gzip ở Bronze (không Parquet) | CONFIRMED | Bronze files trên MinIO |
| D-06 | Dual-Phase Heartbeat (fetch phase + write phase) | CONFIRMED | `src/crawlers/base_crawler.py` |
| D-07 | MetadataEnvelope với SHA-256 checksum | CONFIRMED | `src/storage/metadata_envelope.py` |
| D-08 | 41 trường canonical Silver (PyArrow schema) | CONFIRMED | `src/schemas/silver_schema.py` |
| D-09 | FX USD→VND hardcode 25,400 | CONFIRMED | `BUSINESS_LOGIC.md` |
| D-10 | MinIO port bind 127.0.0.1 (không 0.0.0.0) | CONFIRMED | `docs/bronze-architecture-review.md` |
| D-11 | CompactionEngine move file cũ → `bronze/archive/` (không xóa) | CONFIRMED | `src/storage/compaction_engine.py` |
| D-12 | Stale Lease = 2 giờ (7200 giây) | CONFIRMED | `state_manager.py:STALE_LEASE_SECONDS` |
| D-13 | Silver dùng MERGE INTO / Upsert với `unified_job_id` PK | CONFIRMED | `BUSINESS_LOGIC.md` |
| D-14 | skills_standardized dùng LLM API + taxonomy >120 kỹ năng | CONFIRMED | `BUSINESS_LOGIC.md` |
| D-15 | Silver phân vùng `platform` + `posted_date` | CONFIRMED | `BUSINESS_LOGIC.md` |
| D-16 | Silver dùng Delta Lake hay plain Parquet? | OPEN | Chưa chốt |
| D-17 | Airflow executor: Local hay Celery? | OPEN | Chưa chốt |

---

## Chi tiết quyết định

### D-01: SQLite WAL làm State Store

**Bối cảnh:** Cần lưu watermark và audit trail cho crawler, chạy trên VPS đơn.

**Các phương án đã cân nhắc:**
- File JSON state (`state_manifest.json`)
- PostgreSQL (database riêng)
- SQLite WAL

**Phương án chọn:** SQLite WAL với `PRAGMA journal_mode=WAL; PRAGMA busy_timeout=5000;`

**Lý do:**
- ACID đầy đủ, không cần server riêng.
- WAL mode: đọc đồng thời non-blocking.
- Partial Unique Index hỗ trợ concurrency lock nguyên tử.
- File JSON không có ACID — race condition khi 2 process ghi cùng lúc.

**Trade-off:** Không scale ra nhiều machine (single-node only). Chấp nhận được vì MVP chạy trên 1 VPS.

---

### D-02: Insert-First Concurrency Pattern

**Bối cảnh:** Cần đảm bảo chỉ 1 tiến trình được crawl mỗi source tại một thời điểm.

**Phương án bị loại:** `SELECT status WHERE source=X → if not RUNNING: INSERT` — TOCTOU race condition, 2 process có thể cùng thấy "not RUNNING".

**Phương án chọn:** `BEGIN IMMEDIATE; INSERT INTO ingestion_runs (..., status='RUNNING'); COMMIT` — SQLite tự enforce unique constraint qua partial index, raise `IntegrityError` nếu có conflict.

**Bằng chứng:** `test_multiprocess_concurrency_race` với `mp.Barrier` — 2 process OS thật, chỉ 1 thắng. `[CONFIRMED]`

---

### D-03: Atomic Write Protocol

**Bối cảnh:** Tránh file corrupt nửa chừng trong MinIO.

**Phương án chọn:** 3 bước:
1. Ghi vào `landing/staging/{run_id}.tmp`
2. Tính SHA-256 manifest của file hoàn chỉnh
3. PUT vào `bronze/` → verify manifest sau PUT → nếu mismatch: abort + rollback

**Trade-off:** Tốn thêm 1 PUT call. Chấp nhận được vì fail-closed là ưu tiên hơn throughput.

---

### D-04: Ingestion Time Partitioning

**Bối cảnh:** Cần partition strategy cho MinIO Bronze.

**Phương án bị loại:** Event Time (posted_date) — thiếu tin cậy vì nguồn báo sai ngày, format khác nhau giữa 4 platform.

**Phương án chọn:** Ingestion Time UTC (`ingest_year`, `ingest_month`, `ingest_day`) — luôn chính xác, do hệ thống kiểm soát.

---

### D-09: FX USD/VND Hardcode 25,400

**Bối cảnh:** Một số job posting dùng USD.

**Quyết định:** Hardcode tỷ giá 25,400 — không call API live exchange rate.

**Lý do:** MVP/tiểu luận; tránh dependency vào external API; tỷ giá ổn định trong ngắn hạn.

**Hệ quả:** Nếu tỷ giá biến động lớn → `salary_min_vnd`/`salary_max_vnd` không còn chính xác. Cần update thủ công.

**Trạng thái:** CONFIRMED nhưng có thể thay đổi nếu dự án mở rộng.

---

### D-16: Silver Layer Storage Format — OPEN

**Bối cảnh:** Cần lựa chọn format lưu Silver.

**Phương án đang cân nhắc:**
- **Delta Lake** (`delta-spark`): MERGE INTO native, time travel, ACID đầy đủ. Nhưng cần Delta tương thích version Spark.
- **Plain Parquet + Hive partition**: Đơn giản hơn, không cần Delta runtime. MERGE INTO = overwrite partition.

**Trạng thái:** `[OPEN]` — cần xác nhận trước khi implement Silver layer.

---

## Open Questions

- D-16: Silver storage format → cần xác nhận.
- D-17: Airflow executor → cần xác nhận.
- Có cần version schema (schema evolution) cho Silver không?


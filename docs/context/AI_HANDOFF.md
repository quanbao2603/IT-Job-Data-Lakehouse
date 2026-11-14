# AI_HANDOFF.md — IT Job Data Lakehouse

> **Dành cho AI tiếp theo.** Đọc file này TRƯỚC, sau đó đọc các file còn lại trong `docs/context/`.

---

## Tóm tắt dự án (10 dòng)

Hệ thống **Data Lakehouse** thu thập và phân tích dữ liệu việc làm IT từ 4 sàn Việt Nam (ITViec, TopCV, TopDev, VietnamWorks). Stack: Python 3.10+, MinIO (S3-compatible), SQLite WAL, PySpark, Apache Airflow, Docker trên VPS Ubuntu NVMe 75GB. Kiến trúc Medallion: **Bronze** (raw JSONL.gz) → **Silver** (Parquet 41 trường chuẩn hóa) → **Gold** (Star Schema). Bronze V2.0 đã hoàn chỉnh với 5,444 records, 19 unit tests PASS, concurrency lock nguyên tử, dual-phase heartbeat. Silver đang xây dựng. Gold và Airflow chưa implement. Team: QuanBao (Lakehouse) + Thưởng (SSIS/SQL Server/Power BI).

---

## Files BẮT BUỘC đọc trước khi sửa code

| Thứ tự | File | Lý do |
|---|---|---|
| 1 | `docs/context/AI_HANDOFF.md` | File này — đọc trước |
| 2 | `docs/context/ARCHITECTURE.md` | Toàn bộ sơ đồ kiến trúc, storage layout, luồng dữ liệu |
| 3 | `docs/context/DATA_AND_API_CONTRACTS.md` | Schema Bronze/Silver, Quality Gate, CDC rules |
| 4 | `docs/context/CODING_CONVENTIONS.md` | Type hints, early return, logging, cấm gì |
| 5 | `docs/context/DOMAIN_OR_MODULE_CONVENTIONS.md` | Crawler pattern, pagination, heartbeat, test pattern |
| 6 | `docs/context/DECISIONS.md` | Quyết định không được tự ý thay đổi |
| 7 | `docs/context/CURRENT_STATUS.md` | Trạng thái hiện tại, việc đang dở |
| 8 | `.agent/BUSINESS_LOGIC.md` (old project) | Từ điển nghiệp vụ, 41 trường Silver, Quality Gate rules |

**Tham khảo code mẫu:**
- `src/crawlers/base_crawler.py` — Pattern crawler chuẩn
- `src/storage/state_manager.py` — SQLite State Store đầy đủ
- `src/storage/atomic_writer.py` — Atomic write pattern
- `src/crawlers/itviec_crawler.py` — Crawler implementation mẫu

---

## Quy chuẩn BẮT BUỘC tuân thủ

1. **Type Hints** trên mọi function signature — không exception.
2. **Early Return** pattern — không lồng if sâu.
3. **logging** thay vì `print()` — ở production code.
4. **StateManager** cho mọi state — không lưu state cục bộ trong Operator.
5. **SILVER_CANONICAL_FIELDS** (41 trường) — thay đổi schema phải update `src/schemas/silver_schema.py` trước.
6. **Airflow tasks phải idempotent** — chạy lại cùng ngày không đổi kết quả.
7. **PySpark/PyArrow > Pandas** cho large dataset.
8. **Medallion boundaries:**
   - Bronze: nhận tất cả, không normalize, không ép schema.
   - Silver: normalize + quality gate + 41 trường chuẩn.
   - Gold: aggregate, star schema.

---

## Điều KHÔNG được tự ý thay đổi `[CONFIRMED]`

| ❌ Không được | Lý do |
|---|---|
| Drop `idx_active_running_per_source` | Phá vỡ concurrency lock |
| Ghi thẳng vào `s3://bronze/` không qua AtomicWriter | File corrupt nửa chừng |
| Thay đổi `source_name` case/spelling | Break watermark lookup |
| Xóa file trong `s3://bronze/` | Chỉ CompactionEngine được move sang `archive/` |
| Tắt SHA-256 checksum trong MetadataEnvelope | Mất khả năng verify integrity |
| Đảo thứ tự: acquire_lock → fetch → envelope → write → commit | Race condition + data loss |
| Hardcode credential | Security violation |

---

## Cách xác minh sau khi triển khai

```bash
# 1. Unit tests
python -m unittest discover -s tests -v
# Phải: 17 passed, 0 failed trên Linux/VPS (Windows local có 4 lỗi encoding print — xem CURRENT_STATUS mục 2)

# 2. Compile check
python -m compileall -q src tests

# 3. Bronze audit
python -m src.utils.bronze_audit_reporter --deep
# Phải: 100% SHA-256 match

# 4. State Store
sqlite3 data/state_store.db "SELECT * FROM ingestion_state;"
# Phải: 4 records với status SUCCESS
```

---

## Công việc tiếp theo (ưu tiên cao → thấp)

1. **[BLOCKER]** Chốt Silver storage format: Delta Lake hay plain Parquet?
2. **[HIGH]** Hoàn thiện `src/etl/bronze_to_silver.py` — PySpark ETL
3. **[HIGH]** Hoàn thiện `src/etl/quality_gate.py` — 3 rules (mandatory, salary outlier, schema drift)
4. **[HIGH]** `src/etl/transformers.py` — salary norm, company name, skills LLM + taxonomy
5. **[MEDIUM]** Kết nối `dags/dag_daily_job_lakehouse_pipeline.py` → 4 crawler `--mode incremental`
6. **[MEDIUM]** Gold Layer: `Fact_Job_Postings` + 5 Dim + `Bridge_Job_Skills`
7. **[LOW]** Refactor `print()` → `logging` trong crawlers

---

## Câu hỏi cần hỏi người dùng trước khi tiếp tục

1. **Silver storage format:** Delta Lake (cần delta-spark) hay plain Parquet + overwrite partition?
2. **Airflow executor:** Local executor (1 VPS, đơn giản) hay Celery (cần Redis)?
3. **LLM API cho skill extraction:** Gemini Flash hay OpenAI? API key đã có chưa?
4. **Full Crawl mới nhất:** Đã xác nhận xong chưa? Chạy `bronze_audit_reporter --deep` để có số chính xác.
5. **MinIO port:** Đã hardened sang `127.0.0.1:9000` chưa hay vẫn `0.0.0.0`?

---

## Vị trí project

| Project | Đường dẫn |
|---|---|
| **NEW** (đang làm việc) | `d:\HCMUTE\TLCN\IT-Job-Data-Lakehouse-VPS\` |
| **OLD** (backup, source of truth cho code) | `d:\hcmute\TLCN\IT-Job-Data-Lakehouse\` |
| Old project backup trong new | `d:\HCMUTE\TLCN\IT-Job-Data-Lakehouse-VPS\backup\` (git-ignored) |

---

> **Trước khi sửa code, hãy đọc `PROJECT_CONTEXT.md`, `ARCHITECTURE.md`, `CODING_CONVENTIONS.md`, `DATA_AND_API_CONTRACTS.md`, `DOMAIN_OR_MODULE_CONVENTIONS.md`, `DECISIONS.md` và `CURRENT_STATUS.md`. Không tự ý phá vỡ quy chuẩn hoặc thay đổi quyết định kiến trúc đã được đánh dấu `CONFIRMED`. Nếu thông tin mâu thuẫn hoặc chưa rõ, hãy nêu vấn đề và hỏi trước khi triển khai.**


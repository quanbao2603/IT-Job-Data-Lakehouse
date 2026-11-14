# PROJECT_CONTEXT.md — IT Job Data Lakehouse

> **Phân loại thông tin:** CONFIRMED = đã xác nhận trong code/tài liệu | INFERRED = suy luận từ bằng chứng | TODO = việc cần làm

---

## 1. Tên & Mục tiêu dự án

**Tên:** Hệ thống Data Lakehouse Tổng hợp và Phân tích Thị trường Việc làm IT (TLCN — Tiểu luận chuyên ngành)  
**Tác giả:** Nguyễn Quân Bảo (Ingestion / Lakehouse / Airflow) & Nguyễn Hữu Thưởng (SSIS / SQL Server / Power BI)

**Vấn đề giải quyết** `[CONFIRMED]`:  
Dữ liệu tuyển dụng IT bị phân mảnh trên 4 nền tảng lớn (ITViec, TopCV, TopDev, VietnamWorks). Hệ thống thu thập, chuẩn hóa và phân tích dữ liệu, cung cấp:
- Tổng hợp dữ liệu vĩ mô thị trường (lương, kỹ năng, xu hướng).
- Theo dõi biến động thị trường theo thời gian.
- Tích hợp AI Agent (Text-to-SQL) để truy vấn bằng ngôn ngữ tự nhiên.

---

## 2. Phạm vi hiện tại

| Tầng | Trạng thái | Mô tả |
|---|---|---|
| **Bronze** (Raw Ingestion) | ✅ HOÀN THÀNH — V2.0 Hardened | 4 crawler, State Store SQLite, MinIO, 19 unit tests |
| **Silver** (Cleaned & Conformed) | 🔄 ĐANG XÂY DỰNG | Khung ETL (`bronze_to_silver.py`), silver schema 41 trường, quality gate |
| **Gold** (Data Marts) | 📋 THIẾT KẾ XONG | Star schema (Fact + 5 Dim + Bridge), chưa code |
| **Airflow DAGs** | 📋 KẾ HOẠCH | DAG `dag_daily_job_lakehouse_pipeline.py` — chưa kết nối crawler Bronze V2 |
| **AI Agent (Text-to-SQL)** | 📋 TƯƠNG LAI | Ngoài phạm vi MVP hiện tại |

**Nằm ngoài phạm vi MVP:** WORM immutable hoàn chỉnh, raw HTML byte-level storage, production-grade HA.

---

## 3. Người dùng & luồng sử dụng chính

- **QuanBao:** Vận hành pipeline ingestion, debug crawler, monitor MinIO/SQLite trên VPS.
- **Thưởng:** Nhận file CSV export từ Silver (`export_silver_for_ssis.py`) → nạp SSIS → SQL Server → Power BI dashboard.

---

## 4. Trạng thái dự án hiện tại `[CONFIRMED — 08/11/2026]`

### ✅ Tính năng đã hoàn thành (Bronze V2.0)

1. **4 Crawler hoạt động:** `itviec_crawler.py`, `topcv_crawler.py`, `topdev_crawler.py`, `vietnamworks_crawler.py`
2. **State Store SQLite WAL** tại `data/state_store.db` — 3 bảng: `ingestion_state`, `ingestion_runs`, `compaction_audit_logs`
3. **Concurrency Lock:** SQLite Partial Unique Index `idx_active_running_per_source` + `BEGIN IMMEDIATE` Insert-First pattern
4. **Dual-Phase Heartbeat:** Phát tín hiệu sống ở cả pha Fetch và pha Ghi Storage (mỗi 10 bản ghi)
5. **AtomicWriter:** Ghi staging `.tmp` → verify SHA-256 manifest → commit rename → archive
6. **MetadataEnvelope:** Bọc mỗi bản ghi với `_metadata` + `raw_payload` + `_rescued_data`
7. **CompactionEngine:** Gộp file nhỏ, lưu `s3://bronze/archive/`
8. **Backup & Restore:** `backup_bronze.py` với `verify_backup_drill()`, khớp 100% (17/17 objects)
9. **19 Unit & Stress Tests** — tất cả PASS (bao gồm `mp.Barrier` race condition, stress 10 vòng)

### 📊 Dữ liệu Bronze hiện tại

| Sàn | Files | Records | SHA-256 |
|---|---|---|---|
| ITViec | 4 | 690 | 100% |
| TopCV | 5 | 2,618 | 100% |
| TopDev | 4 | 1,245 | 100% |
| VietnamWorks | 4 | 891 | 100% |
| **Tổng** | **17** | **5,444** | **100%** |

### 🔄 Đang làm

- Tầng Silver: `src/etl/bronze_to_silver.py`, `src/etl/quality_gate.py`, `src/etl/transformers.py`
- Silver schema 41 trường chuẩn: `src/schemas/silver_schema.py`

### 📋 Việc tiếp theo (theo ưu tiên)

1. **[TODO]** Nghiệm thu Full Crawl mới: `python -m src.utils.bronze_audit_reporter --deep`
2. **[TODO]** Hoàn thiện Tầng Silver: chuẩn hóa 41 trường, Quality Gate, Quarantine Zone, ghi Parquet
3. **[TODO]** Kết nối Airflow DAG → 4 crawler Bronze V2 (`--mode incremental`)
4. **[TODO]** Incremental crawl tự động theo lịch Airflow
5. **[TODO]** Tầng Gold: Fact_Job_Postings + 5 Dim tables + Bridge_Job_Skills

---

## 5. Điểm cần biết trước khi sửa code

- Mọi thay đổi schema **phải** tuân thủ `SILVER_CANONICAL_FIELDS` (41 trường) — xem `src/schemas/silver_schema.py`.
- Crawler **không được** lưu state cục bộ — mọi state qua `StateManager`.
- Incremental mode dùng `last_watermark_ts` và `last_record_id` từ SQLite — không hardcode watermark.
- VPS mirror: thay đổi `src/` phải đồng bộ với `vps/` (scp hoặc rsync).

---

## Open Questions

- `[UNCONFIRMED]` Phiên Full Crawl mới nhất trên VPS có hoàn tất chưa? Cần chạy `bronze_audit_reporter --deep` để xác nhận.
- `[NEEDS_CONFIRMATION]` Silver layer: dùng Delta Lake hay plain Parquet + Hive-style partition?
- `[TODO]` Airflow: local executor hay Celery executor trên VPS?


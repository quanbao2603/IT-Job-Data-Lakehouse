# CURRENT_STATUS.md — IT Job Data Lakehouse

> **Cập nhật lần cuối:** 13/11/2026  
> **Người cập nhật:** Antigravity (context migration)

---

## 1. Công việc đã hoàn thành gần đây `[CONFIRMED]`

### Bronze Layer V2.0 — HOÀN CHỈNH (08/11/2026)

**Files đã thêm/sửa:**

| File | Thay đổi |
|---|---|
| `src/storage/state_manager.py` | Insert-First concurrency lock + Stale Lease Recovery + Heartbeat |
| `src/crawlers/base_crawler.py` | `_current_run_id`, `self.heartbeat()` helper |
| `src/crawlers/itviec_crawler.py` | Dual-phase heartbeat (fetch + write) |
| `src/crawlers/topcv_crawler.py` | Dual-phase heartbeat |
| `src/crawlers/topdev_crawler.py` | Dual-phase heartbeat |
| `src/crawlers/vietnamworks_crawler.py` | Dual-phase heartbeat |
| `src/storage/atomic_writer.py` | Staging directory, fail-closed, atomic rename |
| `src/utils/backup_bronze.py` | `verify_backup_drill()` — 17/17 objects matched |
| `tests/test_bronze_layer.py` | Nâng lên 19 tests (từ 14) |
| `docs/bronze-design-review-stages-1-5.md` | Tài liệu thiết kế chính thức |
| `docs/bronze-architecture-review.md` | Báo cáo thẩm định đánh giá 9.0/10 |

### Context Migration — 13/11/2026

| File | Thay đổi |
|---|---|
| `d:\HCMUTE\TLCN\IT-Job-Data-Lakehouse-VPS\backup\` | Copy toàn bộ old project (281 files, 166MB) |
| `.gitignore` | Thêm `backup/` |
| `docs/context/` | Tạo mới — 8 file context handoff |

---

## 2. Kết quả test `[CONFIRMED — 08/11/2026]`

```
Ran 19 tests in ~12.3s
OK — 19 passed, 0 failed

Tests quan trọng:
  ✅ test_multiprocess_concurrency_race     (mp.Barrier, 2 process OS)
  ✅ test_heartbeat_lifecycle               (heartbeat_at update)
  ✅ test_manifest_failure_aborts_commit    (SHA-256 mismatch abort)
  ✅ stress_test_10_rounds                  (idempotency 10 vòng)
  ✅ crash_after_copy_idempotent_retry
  ✅ test_consuming_writer_heartbeat        (generator heartbeat)
  ✅ fetch_phase_heartbeat_test
```

> **`[UPDATE 14/11/2026 — đối chiếu repo mới]`**: `tests/test_bronze_layer.py` hiện có **17 test methods** (chạy trên Windows local: 13 pass + 4 lỗi encoding `print` emoji khi stdout bị pipe; chưa kiểm chứng lại trên VPS/Linux). Bản project cũ trong `backup/tests/` có **20 test**; 3 test chưa được port sang repo mới: `test_fetch_phase_heartbeat_progress`, `test_partial_unique_index_schema_exists`, `test_stress_concurrency_race`. Cần port lại trước khi nghiệm thu Bronze.

## 3. Dữ liệu Bronze hiện tại `[CONFIRMED — 08/11/2026]`

| Sàn | Files | Size | Records | SHA-256 | Watermark (last_record_id) |
|---|---|---|---|---|---|
| ITViec | 4 | 0.42 MB | 690 | 100% | 1612 |
| TopCV | 5 | 2.96 MB | 2,618 | 100% | 2345651 |
| TopDev | 4 | 0.14 MB | 1,245 | 100% | 2140638 |
| VietnamWorks | 4 | 1.42 MB | 891 | 100% | 2128851 |
| **Tổng** | **17** | **4.94 MB** | **5,444** | **100%** | — |

> **Lưu ý:** Dữ liệu này từ Full Crawl trước 08/11. Có thể đã cào lại từ đó — cần chạy `bronze_audit_reporter --deep` để xác nhận số mới nhất.

---

## 4. Lỗi hiện tại & Blocker

| Lỗi | Trạng thái | Mức độ |
|---|---|---|
| Silver layer chưa hoàn chỉnh | 🔄 Đang xây dựng | BLOCKER cho Gold & Dashboard |
| Airflow DAG chưa kết nối Bronze V2 crawlers | 📋 TODO | Medium |
| Crawler dùng `print()` thay vì `logging` | ⚠️ Technical debt | Low |
| Silver storage format chưa chốt (Delta vs Parquet) | ❓ OPEN | BLOCKER cho Silver |

---

## 5. Việc đang làm dở

- `src/etl/bronze_to_silver.py` — PySpark read Bronze → transform → write Silver
- `src/etl/quality_gate.py` — Mandatory fields check, salary outlier, quarantine
- `src/etl/transformers.py` — Salary normalization, company name, skills LLM
- `src/schemas/silver_schema.py` — 41-field PyArrow schema

---

## 6. Việc tiếp theo (theo ưu tiên)

| # | Việc | Lệnh / File |
|---|---|---|
| 1 | **Nghiệm thu Full Crawl mới** | `python -m src.utils.bronze_audit_reporter --deep` |
| 2 | **Chốt Silver storage format** (Delta vs Parquet) | Quyết định D-16 |
| 3 | **Hoàn thiện Silver ETL** | `src/etl/bronze_to_silver.py` |
| 4 | **Quality Gate** | `src/etl/quality_gate.py` |
| 5 | **Kết nối Airflow DAG** | `dags/dag_daily_job_lakehouse_pipeline.py` |
| 6 | **Incremental Crawl tự động** | `--mode incremental` via Airflow |
| 7 | **Gold Layer** | Star schema implementation |

---

## 7. Thay đổi chưa được kiểm chứng

- `[UNCONFIRMED]` Phiên Full Crawl mới nhất trên VPS (sau 08/11) — số bản ghi thực tế?
- `[UNCONFIRMED]` MinIO port binding: đã hardened sang `127.0.0.1` chưa hay vẫn `0.0.0.0`?
- `[UNCONFIRMED]` Airflow scheduler đang chạy hay đã dừng?

---

## 8. Lệnh kiểm tra nhanh VPS

```bash
# Audit Bronze
python -m src.utils.bronze_audit_reporter --deep

# Kiểm tra State Store
sqlite3 data/state_store.db "SELECT source_name, last_run_status, records_ingested, updated_at FROM ingestion_state;"

# Kiểm tra MinIO buckets
# (lưu ý 14/11/2026: src/utils/minio_client.py CHƯA tồn tại trong repo — lệnh này chờ port hoặc thay bằng mc/py trực tiếp)
python -m src.utils.minio_client list-buckets

# Chạy incremental crawl
python -m src.crawlers.itviec_crawler --mode incremental --max-pages 10
```


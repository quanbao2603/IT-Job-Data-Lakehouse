# Thiết Kế Chi Tiết Kiến Trúc Tầng Đồng (Bronze Layer) — IT Job Data Lakehouse

> **Dự án:** IT Job Data Lakehouse
> **Phiên bản tài liệu:** 1.0 — 19/11/2026
> **Phạm vi:** 9 nền tảng tuyển dụng IT (`careerviet`, `upzi`, `vietnamdevs`, `vieclam24h`, `devwork`, `itviec`, `topcv`, `topdev`, `vietnamworks`).
> **Vai trò tầng:** Thu thập và cam kết bất biến (immutable commit) các extracted observation thô, có kiểm chứng toàn vẹn và sổ cái đối soát.
> **Đầu ra cho tầng kế tiếp:** `s3://bronze/{source}/ingest_year=.../part_{run_id}.jsonl.gz` + manifest `ingestion_objects` + audit gate PASS.
> **Nguyên tắc cốt lõi:** *"Bronze không suy diễn. Bronze chỉ cam kết những gì đã nhìn thấy, đã băm, đã kiểm chứng kích thước và đã ghi sổ. Không có tệp rỗng, không có watermark đi trước manifest, không có pipeline chết âm thầm."*
> **Tài liệu anh em:** `docs/silver-design-stages.md` (v3.0), `docs/gold-design-stages.md` (v5.0), `docs/hermes-gold-review-brief.md`.
> **Cơ sở bằng chứng:** mọi số liệu trong file này đọc từ code tại HEAD `d279d1a`, không từ tài liệu thiết kế gốc.

---

## KẾT LUẬN TỔNG DUYỆT

### Đã triển khai và đã kiểm chứng bằng test

```text
9 crawler có parser riêng, mỗi cái có suite test riêng.
MetadataEnvelope: 9 SCHEMA_CONTRACTS, 6 khóa _metadata, verify_integrity,
    schema drift rescue.
StateManager: 4 bảng SQLite, INSERT-first lock, partial unique index,
    stale/dead-PID recovery, heartbeat, watermark kép.
AtomicWriter: giao thức staging-to-bronze 6 bước, idempotent retry,
    bounded memory streaming, stream_bronze_records.
CompactionEngine: verified stream merge, archive lifecycle, fail-closed
    khi record count lệch.
Pre-flight: disk, RAM, HTTPS egress, MinIO bucket, SQLite integrity,
    chặn khi còn source RUNNING.
Bronze audit gate: manifest count, object size, file sha256, envelope
    checksum từng bản ghi, record count, dangling landing.
Operational: redaction, failure ledger JSONL append-only, phân loại lỗi,
    Airflow retry/failure callback.
DAG Airflow: đã chạy trên VPS, scheduler + webserver active dưới systemd.
```

### Ba phát hiện quan trọng nhất khi đối chiếu code

```text
1. CompactionEngine KHÔNG được bất kỳ DAG hay lệnh nào gọi.
   Nó chỉ xuất hiện trong tests/test_bronze_layer.py.
   → Bài toán small-file mà Giai đoạn 5 đặt ra VẪN CHƯA ĐƯỢC GIẢI QUYẾT
     trong vận hành. Mỗi ngày 9 tệp mới, không bao giờ gom.

2. Timeout thật khác thiết kế đã chốt ở Silver/Gold.
   Code: execution_timeout = 2 giờ/task, dagrun_timeout = 4 giờ.
   Thiết kế Silver/Gold ghi: crawler 45 phút, toàn DAG 120 phút.
   → Một crawler treo sẽ giữ khóa State Store tới 2 giờ thay vì 45 phút,
     và Silver pipeline có thể không còn cửa sổ chạy trong ngày.

3. Alerting chưa bao giờ bắn.
   send_alert() tồn tại và đã được test phần redaction, nhưng
   ALERT_ENABLED mặc định "false" và KHÔNG được set ở bất kỳ đâu:
   không trong install_airflow_vps.sh, không trong airflow.env mẫu,
   không trong service file.
   → Đúng điều Giai đoạn 6 cấm: pipeline chết âm thầm.
```

### Việc còn lại của tầng Bronze (xếp theo độ nghiêm trọng)

```text
B-GAP-1  Alerting chưa bật                → nghiêm trọng, dễ sửa
B-GAP-2  Compaction không được gọi        → nghiêm trọng, cần thiết kế lịch chạy
B-GAP-3  Timeout code != thiết kế          → trung bình, cần chốt một con số
B-GAP-4  MinIO Lifecycle expiration chưa cấu hình (dù tài liệu nói có)
B-GAP-5  latest_source_results() là code chết, DAG tin XCom
B-GAP-6  Cookie session hết hạn chỉ fail-closed, chưa có quy trình làm mới
```

---

## GIAI ĐOẠN 0: MA TRẬN "TÀI LIỆU GỐC vs CODE THẬT"

**Mục tiêu:** tài liệu thiết kế Bronze ban đầu viết khi hệ thống mới có 4 sàn. Code đã đi tới 9 sàn. Ghi lại đúng chỗ nào lệch để người review không sửa theo bản giấy.

| # | Tài liệu gốc nói | Code thật hiện nay | Xử lý |
|---|---|---|---|
| 1 | `SCHEMA_CONTRACTS` định nghĩa cho **4 sàn** (`itviec`, `topcv`, `topdev`, `vietnamworks`) | **9 sàn**, số trường: topdev 22; careerviet/devwork/upzi/vieclam24h/vietnamdevs 25; itviec/vietnamworks 29; topcv 30 | Bản giấy đã lỗi thời. Số trường 22–30 vẫn đúng như mô tả |
| 2 | Heartbeat pha thu thập ở "cả 4 crawler" | **9 crawler** đều có `_make_request` + heartbeat | Cần cập nhật |
| 3 | `acquire_run_lock` dùng `timeout=5.0` giây | Code dùng **`timeout=3.0`** | Chênh 2 giây, không đổi hành vi khóa nhưng tài liệu phải khớp |
| 4 | Giai đoạn 6 mô tả DAG 6 task: Pre-flight → Lookup State → Resilient Ingest → Checksum & Atomic Commit → Update State → Trigger Downstream | DAG thật: `preflight_bronze` → 9 task `crawl_*_incremental` → `bronze_audit_after_crawl` → `silver_pipeline_guard` → `crawl_pipeline_summary`. Lookup State / Commit / Update State **nằm bên trong tiến trình crawler**, không phải task Airflow | Đây là khác biệt kiến trúc có chủ đích (crawler tự sở hữu watermark + lock + commit). Tài liệu gốc cần viết lại |
| 5 | "lỗi mạng tự thử lại 3 lần, mỗi lần cách nhau 2 phút" | Hai tầng retry chồng nhau: crawler `_make_request(max_retries=4, base_backoff=3.0)` kèm jitter `random.uniform(1.0,3.0)`; Airflow `retries=2, retry_delay=5 phút` | Cần ghi rõ hai tầng, vì tổng số lần thử là tích của chúng |
| 6 | Module dự kiến: `fetcher.py`, `envelope.py`, `writer.py`, `compactor.py` | Tên thật: `src/crawlers/base_crawler.py` + 9 crawler, `src/storage/metadata_envelope.py`, `src/storage/atomic_writer.py`, `src/storage/compaction_engine.py` | Ánh xạ tên |
| 7 | State lưu "SQLite / Postgres" | **SQLite** `data/state_store.db`. PostgreSQL chỉ dùng cho Airflow metadata và Gold | Chốt SQLite |
| 8 | `_metadata` có 5 khóa (`ingested_at`, `source`, `payload_checksum`, `pipeline_run_id`, `format_version`) | **6 khóa**: thêm `observed_at` (commit Hermes `08b9eb8`) | QUAN TRỌNG: `observed_at` là nền tảng idempotency của `observation_id` bên Silver và cutoff bên Gold |
| 9 | dọn dẹp orphan "kết hợp hàm ứng dụng + chính sách Lifecycle Expiration của MinIO" | Chỉ có `clean_stale_landing_uploads(max_age_hours=6)`. **Không có cấu hình Lifecycle nào** trong `infrastructure/docker-compose.yml` hay script nào | B-GAP-4 |

---

## GIAI ĐOẠN 1: HẠ TẦNG LƯU TRỮ (OBJECT STORAGE & PHÂN VÙNG)

### 1. MinIO và ranh giới mạng

```text
Chạy trong container, bind mount vào ../data/minio_storage.
Cổng S3 API     : 127.0.0.1:9000   (chỉ loopback)
Cổng Console    : 127.0.0.1:9001   (chỉ loopback)
MINIO_ROOT_USER / MINIO_ROOT_PASSWORD nạp từ .env (file git-ignore).
MINIO_BROWSER_REDIRECT_URL cấu hình qua env khi truy cập console qua domain.
```

**Bất biến mạng:** không có cổng MinIO nào bind ra giao diện công cộng. Toàn bộ truy cập S3 từ tiến trình dự án đi qua loopback. Console đi qua nginx + domain, không mở port.

### 2. Hai bucket, một vùng cam kết

| Bucket | Vai trò | Đường dẫn | Tuổi thọ |
|---|---|---|---|
| `landing` | Vùng đệm, chưa cam kết | `{source}/temp_{run_id}.uploading` | Ngắn hạn, bị dọn |
| `bronze` | Đã cam kết, bất biến | `{source}/ingest_year=YYYY/ingest_month=MM/ingest_day=DD/part_{run_id}.jsonl.gz` | Lâu dài |
| `bronze/archive/...` | Tệp nguồn sau compaction | `archive/{đường dẫn cũ}` | Theo retention |

**Vì sao tách hai bucket:** `landing` cho phép tiến trình chết giữa chừng mà không để lại dữ liệu dở trong vùng cam kết. `bronze` chỉ nhận object sau khi đã verify kích thước.

### 3. Phân vùng theo ingestion time, không theo nghiệp vụ

```text
s3://bronze/{source}/ingest_year={YYYY}/ingest_month={MM}/ingest_day={DD}/part_{run_id}.jsonl.gz
```

| Yếu tố | Quy ước |
|---|---|
| `{source}` | Tên sàn, đã `.lower().strip()` |
| `{run_id}` | `{source}_{mode}_{YYYYMMDD_HHMMSS}_{uuid4[:4]}` |
| Trục phân vùng | **Ingestion time UTC**, không phải `posted_date_iso` |

**Lý do kỹ thuật:** `posted_date` thuộc về nghiệp vụ nguồn, có thể null, có thể bị sửa giữa hai lần cào. Phân vùng vật lý theo nó sẽ làm partition nhảy qua lại khi chạy lại. `run_id` là đơn vị cam kết tự nhiên của Bronze: một phiên cào một tệp.

**Phân biệt ba mốc thời gian (hay bị nhầm):**

```text
ingest_time (phân vùng)  = thời điểm hệ thống nạp
ingested_at (_metadata)  = thời điểm bọc envelope, UTC
observed_at (_metadata)  = thời điểm quan sát, ĐÓNG BĂNG lúc bọc Bronze
posted_date (payload)    = thời điểm nhà tuyển dụng đăng tin, thuộc nghiệp vụ
```

`observed_at` do Hermes thêm vào `_metadata` (commit `08b9eb8`) là mốc Silver dùng để sinh `observation_id`. Vì nó nằm trong Bronze metadata, chạy lại Silver trên cùng input cho cùng ID — đó là điều kiện để Bronze→Silver idempotent.

---

## GIAI ĐOẠN 2: SỔ CÁI TRẠNG THÁI, KHÓA ĐỒNG THỜI VÀ WATERMARK

### 1. Công nghệ

SQLite ACID tại `data/state_store.db`, qua `src/storage/state_manager.py`. Tranh chấp khóa dùng `sqlite3.connect(timeout=3.0)` + `BEGIN IMMEDIATE`.

### 2. Bốn bảng cốt lõi

**`ingestion_state` — watermark và trạng thái hiện hành**

| Cột | Ý nghĩa |
|---|---|
| `source_name` | PK |
| `last_watermark_ts` | Ingestion watermark UTC |
| `source_watermark` | Mốc gốc từ nguồn (`posted_date_iso` / `approvedOn` / `scraped_at`) |
| `watermark_strategy` | `id_cursor` \| `datetime_cursor` |
| `last_record_id` | Con trỏ ID bản ghi mới nhất phiên thành công |
| `last_run_status` | `RUNNING` \| `SUCCESS` \| `FAILED` |
| `records_ingested` | Số bản ghi phiên gần nhất |
| `updated_at` | Bắt buộc NOT NULL |

**`ingestion_runs` — lịch sử phiên và khóa độc quyền**

| Cột | Ý nghĩa |
|---|---|
| `run_id` | PK |
| `source_name`, `mode` | `full` \| `incremental` |
| `started_at`, `finished_at`, `duration_sec` | Đo thời gian thật |
| `status` | `RUNNING` \| `SUCCESS` \| `FAILED` \| `ABANDONED` |
| `records_ingested`, `bronze_s3_uri`, `error_message` | Bằng chứng phiên |
| `process_id` | OS PID của tiến trình |
| `heartbeat_at` | Nhịp tim gần nhất UTC |

Ràng buộc bất biến cấp database:

```sql
CREATE UNIQUE INDEX idx_active_running_per_source
ON ingestion_runs(source_name) WHERE status = 'RUNNING';
```

**`ingestion_objects` — sổ cái tệp vật lý**

| Cột | Ý nghĩa |
|---|---|
| `object_key` | PK, đường dẫn trong bucket `bronze` |
| `run_id`, `source_name` | Truy vết về phiên |
| `file_size_bytes`, `records_count` | Đối soát |
| `checksum_sha256` | Băm toàn bộ tệp `.jsonl.gz` |
| `committed_at` | Thời điểm đăng ký |
| `is_compacted`, `compacted_into_key` | Vòng đời sau compaction |

**`compaction_audit_logs`** — `compaction_id` PK, `partition_prefix`, `files_merged_count`, `total_records`, `old_total_bytes`, `new_compacted_bytes`, `bytes_saved`, `compacted_key`, `executed_at`.

### 3. Khóa "Insert-First" và thu hồi phiên treo

**Vì sao không dùng SELECT-then-INSERT:** hai tiến trình có thể cùng đọc thấy "không có RUNNING" rồi cùng insert. Chỉ mục partial unique mới là trọng tài thật.

```text
1. BEGIN IMMEDIATE
2. INSERT INTO ingestion_runs(..., status='RUNNING', process_id, heartbeat_at)
     - Thành công  → nguồn này trống, chiếm khóa, upsert ingestion_state, COMMIT
     - IntegrityError → có tiến trình khác đang giữ khóa, ROLLBACK, trả None
3. Trước đó, _recover_stale_runs() đã dọn các dòng RUNNING ma:
     os.kill(process_id, 0) thất bại  → ABANDONED (PID đã chết)
     heartbeat quá STALE_LEASE_SECONDS=7200 → ABANDONED (treo)
```

**Vì sao kiểm tra cả PID lẫn heartbeat:** chỉ dựa PID sẽ sai khi hệ điều hành tái sử dụng PID của tiến trình đã chết. Kết hợp hai tín hiệu mới đủ.

### 4. Watermark kép và early stopping

```text
last_watermark_ts  : mốc hệ thống (khi nào ta nạp)
source_watermark   : mốc nguồn (khi nào tin được đăng)
last_record_id     : con trỏ ID

Incremental: duyệt từ mới tới cũ, gặp lại last_record_id hoặc
             approvedOn <= watermark thì dừng (watermark_hit).
Giả định:    danh sách nguồn sắp giảm dần theo thời gian đăng.
Full mode:   bỏ qua watermark, vẫn bị trần max_pages giới hạn.
Không có tin mới: commit SUCCESS với records_ingested=0,
             KHÔNG sinh tệp rỗng trên MinIO.
```

**Rủi ro của giả định sắp xếp:** nếu một sàn đổi thứ tự listing (không giảm dần theo ngày đăng), early stopping sẽ bỏ sót tin cũ chưa từng nạp. Đây là điểm Silver không phát hiện được — Bronze cần đối soát định kỳ bằng `mode=full` hoặc so `source_count` với số tin sàn công bố.

### 5. Manifest-Before-Watermark

Thứ tự cam kết bắt buộc trong `BaseCrawler.run()`:

```text
1. Ghi và kiểm chứng tệp trên MinIO Bronze.
2. Đăng ký manifest vào ingestion_objects (kích thước, records, checksum).
   Nếu bước này fail → raise RuntimeError, phiên FAILED, watermark GIỮ NGUYÊN.
3. commit_success(): cập nhật watermark + chuyển ingestion_runs sang SUCCESS.
```

**Ý nghĩa:** watermark chỉ tiến khi manifest đã ghi. Nếu manifest fail mà watermark vẫn tiến, dữ liệu đó mất vĩnh viễn vì không phiên nào đọc lại. Test `test_manifest_failure_aborts_commit` khóa hành vi này.

Với tệp đã copy lên MinIO nhưng chưa ghi được manifest (tiến trình chết giữa bước 1 và 2), hệ thống **không xóa mù quáng**. Có hai công cụ đối soát: `src/utils/backfill_manifest.py` và `src/utils/bronze_audit_reporter.py`.

---

## GIAI ĐOẠN 3: LỚP ĐÓNG GÓI SIÊU DỮ LIỆU VÀ KIỂM SOÁT TRÔI DẠT SCHEMA

### 1. Cấu trúc envelope

```json
{
  "_metadata": {
    "ingested_at": "2026-11-06T15:25:52.143125Z",
    "observed_at": "2026-11-06T15:25:52Z",
    "source": "topcv",
    "payload_checksum": "<sha256>",
    "pipeline_run_id": "topcv_incremental_20261106_152552_bc30",
    "format_version": "1.0"
  },
  "raw_payload": { "platform": "topcv", "job_id": "123456", "...": "..." },
  "_rescued_data": { "new_unseen_api_field": "giá trị ngoài hợp đồng" }
}
```

`raw_payload` giữ nguyên tên để tương thích ngược với Silver, dù bản chất là **extracted payload** đã qua parser, không phải HTML thô.

### 2. Ba cấp mã băm — không được dùng lẫn

| Cấp | Trường | Ai tính | Trên dữ liệu gì | Dùng để |
|---|---|---|---|---|
| Nghiệp vụ | `content_hash` | Crawler | Tổ hợp trường cốt lõi của tin | Silver phát hiện tin đổi nội dung → `UPDATED` |
| Bản ghi | `payload_checksum` | `MetadataEnvelope._compute_sha256` | `json.dumps(sort_keys=True, ensure_ascii=False)` trên toàn dict **trước khi** tách field lạ | `verify_integrity()` phát hiện tệp hỏng/ghi dở |
| Tệp | `file_checksum_sha256` | `AtomicWriter` | Toàn bộ byte của `.jsonl.gz` | Audit gate đối soát object trên MinIO |

`verify_integrity()` tính lại trên `{**raw_payload, **_rescued_data}` — vì checksum gốc tính trước khi tách, nên bản chất dữ liệu không mất khi rescue.

**Vì sao phải tách ba cấp:** nếu chỉ có một checksum, không phân biệt được "tin đổi nghiệp vụ" (bình thường) với "tệp hỏng khi lưu" (sự cố). Silver cần cấp 1; audit cần cấp 3; toàn vẹn bản ghi cần cấp 2.

### 3. Schema drift rescue và ràng buộc quan trọng hay bị bỏ quên

```python
SCHEMA_CONTRACTS: Dict[str, Set[str]]   # 9 sàn
# Trường trong contract  → giữ lại raw_payload
# Trường ngoài contract  → chuyển _rescued_data, pipeline KHÔNG dừng
```

**Ràng buộc phải nhớ khi thêm trường mới cho crawler:**

```text
canonicalize_bronze_record() chỉ đọc raw_payload, KHÔNG đọc _rescued_data.
→ Một trường crawler phát ra nhưng không khai trong SCHEMA_CONTRACTS
  sẽ rơi vào rescued và VÔ HÌNH với Silver.
→ Đây chính là nguyên nhân GAP-8: 5 sàn có trường `industries` trong
  contract nhưng transformers không đọc; nếu ngược lại (crawler phát ra
  `industries` mà contract không liệt kê) thì còn nặng hơn: mất dữ liệu
  mà không ai thấy.
→ Quy trình bắt buộc: THÊM TRƯỜNG Ở CRAWLER = CẬP NHẬT SCHEMA_CONTRACTS
  CÙNG COMMIT.
```

Giới hạn hiện tại: rescue chỉ bắt được field thừa ở mức dictionary **sau khi** parser đã trích xuất. Nếu DOM/API đổi cấu trúc khiến parser không bóc được field cũ, kết quả là null hàng loạt chứ không phải drift. Lớp đó do Silver Quality Gate (tỷ lệ null) phát hiện, không phải Bronze.

---

## GIAI ĐOẠN 4: ĐỘNG CƠ GHI KIỂM CHỨNG VÀ STREAMING

### 1. Pipeline bộ nhớ đệm cố định

```text
Crawler yield từng bản ghi
  → enveloped_generator() bọc envelope + heartbeat mỗi 10 bản ghi
  → gzip.open(temp, mode="wt", encoding="utf-8") ghi stream ra đĩa tạm
  → băm SHA-256 theo chunk 65536 byte
  → fput_object() lên landing
  → copy_object() landing → bronze (server-side)
```

Không bước nào nạp toàn bộ tập bản ghi vào RAM. Mức tiêu thụ cực đại phụ thuộc `max_pages`, không phụ thuộc tổng số tin của sàn.

### 2. Giao thức staging-to-bronze 6 bước

| Bước | Hành động | Nếu fail |
|---|---|---|
| 1 | Stream gzip ra đĩa tạm; từ chối khi `records_count == 0` | `ValueError`, không có tệp rỗng |
| 2 | Upload `landing/{source}/temp_{run_id}.uploading` | Dọn tệp tạm cục bộ trong `finally` |
| 3 | `stat_object` landing, so `size == compressed_size_bytes` | Xóa object landing, `IOError` |
| 4 | Kiểm tra object đích trên bronze; chưa có thì `copy_object` | — |
| 5 | `stat_object` bronze, xác nhận kích thước khớp tuyệt đối | `IOError` |
| 6 | Xóa landing + xóa tệp tạm cục bộ | Nuốt `OSError`, không làm fail phiên đã commit |

### 3. Idempotency khi chết sau bước 5

```text
Tiến trình chết sau khi tệp đã nằm trên bronze nhưng trước khi ghi SQLite.
Chạy lại cùng run_id / target_bronze_key:
  stat bronze → size khớp → trả status "ALREADY_COMMITTED", không ghi đè.
Chạy lại với run_id mới:
  tạo tệp mới; audit gate phát hiện trùng nội dung qua file_checksum_sha256.
```

**Vì sao so bằng kích thước chứ không chỉ sự tồn tại:** object cùng tên nhưng dung lượng khác nghĩa là nội dung khác, phải ghi đè hoặc báo lỗi, không được coi là đã commit.

### 4. Heartbeat hai pha

```text
Pha thu thập : đầu mỗi trang listing và trước mỗi lượt tải trang chi tiết
               (cả 9 crawler).
Pha lưu trữ  : ngay sau khi chiếm khóa, sau bản ghi đầu, mỗi 10 bản ghi
               đi qua enveloped_generator, và ngay trước commit_success().
```

Heartbeat là đầu vào của `STALE_LEASE_SECONDS`. Không có nó, một phiên treo sẽ giữ khóa mãi; có nó, phiên treo quá 2 giờ bị chuyển `ABANDONED` và phiên sau chiếm được khóa.

---

## GIAI ĐOẠN 5: GOM TỆP NHỎ VÀ VÒNG ĐỜI LƯU TRỮ

### 1. Verified stream compaction 6 bước

```text
1. Quét phân vùng, lọc .jsonl.gz chưa phải part_compacted_*
2. Nếu số tệp < min_files_threshold (mặc định 2) → SKIPPED
3. Stream merge qua generator → write_atomic(target_bronze_key=.../part_compacted_...)
4. ĐỌC LẠI object đã commit trên bronze, đếm verified_count
     lệch → XÓA tệp gom, raise IOError, GIỮ nguyên tệp nguồn
5. Ghi compaction_audit_logs + đăng ký ingestion_objects +
   đánh dấu is_compacted=1, compacted_into_key
6. Vòng đời tệp nguồn:
     archive_small_files=True (mặc định): copy sang archive/...,
        verify kích thước bản sao, chỉ khi khớp mới xóa bản gốc
     purge_small_files=True: xóa trực tiếp sau khi sổ cái đã có ánh xạ
```

**Thứ tự bước 4 trước bước 5 là then chốt:** chỉ ghi manifest và audit SAU khi đối soát đạt. Nếu ngược lại, một tệp gom hỏng sẽ được sổ cái công nhận là hợp lệ. Test `test_compaction_record_count_mismatch_fail_closed` xác nhận: khi lệch, không có audit log, không có manifest mới, và **tệp nguồn không bị xóa**.

### 2. B-GAP-2: engine này chưa được ai gọi

```text
grep toàn repo: CompactionEngine chỉ xuất hiện trong tests/test_bronze_layer.py.
Không DAG, không CLI, không lệnh cron nào gọi compact_partition().
```

Hệ quả vận hành: mỗi ngày 9 tệp mới, không bao giờ gom. Sau 30 ngày một partition ngày có ~30 tệp cho một sàn. Silver đọcBronze qua `list_active_bronze_objects()` nên vẫn đúng, nhưng:

```text
- Số object phải stat + băm mỗi lần audit gate tăng tuyến tính.
- Đọc nhiều tệp nhỏ chậm hơn một tệp lớn trên cùng dung lượng.
- Dung lượng landing/bronze phình, đĩa VPS còn 45GB.
```

**Đề xuất (chưa triển khai, cần chốt):** thêm task `bronze_compaction` vào DAG, chạy SAU `bronze_audit_after_crawl`, trên partition của **H-2 ngày** trở về trước (để chắc không còn record mới ghi vào), `min_files_threshold` nên đặt theo ngày (ví dụ 7) chứ không phải 2, và mặc định `archive_small_files=True`. Không nên gom cùng ngày với phiên cào vì đang ghi vào chính partition đó.

---

## GIAI ĐOẠN 6: CHÍN CRAWLER VÀ CHÍNH SÁCH FAIL-CLOSED

### 1. Hợp đồng một crawler

```python
class BaseCrawler(abc.ABC):
    def fetch_jobs(mode, max_pages, watermark_ts, last_record_id=None) -> Iterable[dict]
    def run(mode="incremental", max_pages=5) -> dict   # lock → fetch → wrap → write → manifest → watermark
```

| Cơ chế | Hành vi |
|---|---|
| Khóa | `acquire_run_lock` trả None → in cảnh báo, trả `status=LOCKED`, **exit code 1** |
| Fail-closed rỗng | `first_job is None` mà nguồn không xác nhận total = 0 → `CrawlerSourceError` |
| `require_listing_data` | Listing parser trả 0 bản ghi trong khi source total > 0 hoặc không rõ → raise |
| Chi tiết lỗi | Rơi về payload thẻ (card fallback), không mất cả tin |
| Hợp đồng CLI | `emit_cli_result()` in MỘT dòng JSON cuối; `exit_code_for_result()` chỉ trả 0 khi `status == "SUCCESS"` |
| Khi thất bại | `mark_failure()` + `append_failure_event()` vào ledger đã redact, rồi `raise` |

**Vì sao exit code chỉ 0 khi SUCCESS:** Airflow cần phân biệt "không có tin mới" (vẫn SUCCESS, `records_ingested=0`) với "bị chặn/nguồn lỗi" (phải FAILED để retry và cảnh báo). Nếu crawler exit 0 khi LOCKED, DAG sẽ báo xanh một phiên không nạp gì.

### 2. Chính sách HTTP theo mã lỗi

```text
200            → parse
429            → backoff mũ có jitter: base_backoff * 2^attempt + random(1.0, 3.0)
500/502/503/504→ thử lại, sleep 1.5 + attempt
404/410        → tin không tồn tại hoặc đã đóng, BỎ QUA tin này (không phải lỗi nguồn)
403            → raise CrawlerSourceError  (bị chặn IP / anti-bot, phải dừng)
```

`max_retries=4`, `base_backoff=3.0` trong `_make_request` của các crawler.

### 3. Hai tầng retry chồng nhau (điểm hay bị hiểu sai)

```text
Tầng 1  Trong crawler : 4 lần thử mỗi URL, backoff giây
Tầng 2  Airflow       : retries=2, retry_delay=5 phút cho cả task

→ Một URL lỗi dai dẳng có thể được thử 4 lần, rồi cả task thử lại 3 lần tổng cộng.
→ Điều này giải thích vì sao execution_timeout 2 giờ là quá rộng so với nhu cầu,
  và vì sao một sàn bị chặn có thể kéo dài toàn bộ DAG.
```

### 4. Nhịp điệu request

Các crawler card-style dùng `REQUEST_DELAY_MIN/MAX` ngẫu nhiên giữa các trang và `random.uniform(0.8, 1.2)` giữa các tin chi tiết. `careerviet` có thêm cooldown (commit `fa31f59`) để giảm bị giới hạn tốc độ.

---

## GIAI ĐOẠN 7: PRE-FLIGHT, AUDIT GATE, DLQ VÀ CẢNH BÁO

### 1. Pre-flight chỉ đọc, không sửa

| Kiểm tra | Ngưỡng | Nguồn cấu hình |
|---|---|---|
| Disk trống | ≥ 15% | `LAKEHOUSE_MIN_FREE_DISK_PERCENT` |
| MemAvailable | ≥ 1024 MB | `LAKEHOUSE_MIN_AVAILABLE_MEMORY_MB` |
| HTTPS egress | status < 400 | `LAKEHOUSE_PREFLIGHT_HTTPS_URL` (mặc định google generate_204) |
| MinIO | bucket tồn tại + có credential | `MINIO_*` |
| State Store | file tồn tại, thư mục ghi được, `PRAGMA integrity_check = ok`, bảng `ingestion_state` đọc được | `LAKEHOUSE_STATE_DB_PATH` |
| Khóa đang chạy | **TỪ CHỐI chạy** nếu còn dòng `status='RUNNING'` | — |

Hai kiểm tra cuối là giá trị thật: preflight không chỉ "môi trường đủ khỏe" mà còn bảo đảm không có phiên ma đang giữ khóa trước khi DAG bắt đầu.

### 2. Bronze audit gate — bằng chứng trước khi cho Silver chạy

`audit_bronze_run()` chuỗi kiểm tra fail-closed, dừng ở lỗi đầu tiên:

```text
1. Mọi source mong đợi phải có kết quả và status SUCCESS
2. run_id phải tồn tại trong ingestion_runs và status SUCCESS
3. records_ingested > 0 thì PHẢI có manifest
4. Tổng records_count trong manifest == records_ingested của run
5. checksum_sha256 phải có trong manifest
6. stat_object(bronze).size == độ dài thực đọc được
7. sha256 nội dung đọc từ MinIO == checksum manifest
8. verify_integrity() ĐẠT cho TỪNG bản ghi trong tệp
9. Số bản ghi đọc được == records_count manifest
10. Không còn object .uploading nào trong landing
```

**Ý nghĩa bước 8:** audit gate không chỉ kiểm tra tệp, mà giải nén và xác minh **từng bản ghi**. Đây là điểm mạnh nhất của Bronze: một tệp có thể đúng kích thước và đúng checksum nhưng vẫn chứa bản ghi hỏng; bước 8 bắt được.

**Chi phí:** bước 6–9 đọc toàn bộ nội dung mọi object của mọi run. Với 9 sàn mỗi ngày, chi phí này tăng theo lượng tệp — liên quan trực tiếp tới B-GAP-2 (không compaction).

### 3. B-GAP-5: hai đường lấy kết quả song song

```text
bronze_audit_gate.latest_source_results(state_db, expected_sources)
    → đọc run cuối mỗi source từ SQLite.
DAG thật dùng _build_task_results(dag_run, ti) → đọc XCom theo task_id.
```

`latest_source_results()` **không được DAG gọi**. Nó hữu ích khi XCom mất hoặc khi chạy lại độc lập, nhưng hiện là code chết. Nên hoặc nối vào DAG làm đường đối chiếu (XCom vs State Store phải khớp), hoặc xóa để khỏi gây nhầm.

### 4. Redaction và DLQ

`redact_message()` loại: HTML tag, `password|passwd|token|api_key|secret|cookie|authorization` dạng `k=v`, `Bearer <token>`, cắt tối đa 500 ký tự.

`classify_failure()` taxonomy: `BLOCKED` (403/anti-bot), `RETRYABLE` (429/timeout/connection), `PARSER_FAILURE`, `STORAGE_FAILURE`, `STATE_FAILURE`, `INGESTION_FAILURE`.

Ledger: `data/operational/source_failures.jsonl`, append-only, mỗi event có `event_id`, `dag_id`, `run_id`, `task_id`, `source`, `failure_class`, `http_status`, `attempt`, `message_redacted`, `created_at`, `resolved_at`.

### 5. B-GAP-1: cảnh báo chưa bật

```python
def send_alert(...):
    if os.getenv("ALERT_ENABLED", "false").lower() != "true":
        return False
```

`ALERT_ENABLED` **không được set** ở `install_airflow_vps.sh`, ở service file, hay ở airflow.env mẫu. Chỉ `docs/airflow-section-6-implementation-spec.md` nhắc tới. Kết quả: `airflow_failure_callback` vẫn ghi ledger nhưng **không gửi Telegram/Discord**.

Điều này vi phạm chính mục tiêu Giai đoạn 6 bản gốc: *"không để pipeline chết âm thầm"*. Đã ghi nhận trong `docs/airflow-section-6-review.md`: alert delivery thật chưa chạy vì thiếu credential.

---

## GIAI ĐOẠN 8: ĐIỀU PHỐI AIRFLOW

### 1. Cấu hình hiện hành trong code

```python
dag_id            = "daily_nine_platform_bronze_incremental"
schedule          = "0 2 * * *"  (Asia/Ho_Chi_Minh)
start_date        = 2026-01-01
catchup           = False
max_active_runs   = 1
max_active_tasks  = clamp(int(LAKEHOUSE_MAX_ACTIVE_TASKS, default 3), 1, 3)
dagrun_timeout    = 4 giờ
retries           = 2
retry_delay       = 5 phút
execution_timeout = 2 giờ  (mỗi task)
```

### 2. Đồ thị task thật

```text
preflight_bronze
  ├── crawl_careerviet_incremental ─┐
  ├── crawl_upzi_incremental ───────┤
  ├── crawl_vietnamdevs_incremental ┤
  ├── crawl_vieclam24h_incremental ─┤  (max 3 chạy đồng thời)
  ├── crawl_devwork_incremental ────┤
  ├── crawl_itviec_incremental ─────┤
  ├── crawl_topcv_incremental ──────┤
  ├── crawl_topdev_incremental ─────┤
  └── crawl_vietnamworks_incremental┘
        ↓ [ALL_DONE]
  bronze_audit_after_crawl
        ↓ [ALL_SUCCESS]
  silver_pipeline_guard   (gate LAKEHOUSE_SILVER_ENABLED, mặc định false)
        ↓ [ALL_SUCCESS]
  crawl_pipeline_summary
```

Hai chi tiết đáng chú ý:

```text
- bronze_audit dùng ALL_DONE, không ALL_SUCCESS: audit cần chạy cả khi có crawl
  fail, vì chính nó phải phát hiện và báo source nào fail. Nếu dùng ALL_SUCCESS,
  một crawler fail sẽ khiến audit bị skip → mất bằng chứng.
- silver_pipeline_guard dùng ALL_SUCCESS: Silver chỉ chạy khi CẢ 9 sàn thành công.
  Đây là chính sách MVP (toàn vẹn cao nhất), đổi lấy việc một sàn bị chặn sẽ
  chặn cả Silver ngày hôm đó.
```

### 3. B-GAP-3: ngân sách timeout không khớp

```text
Code    : 9 crawler × tối đa 2 giờ, DAG tối đa 4 giờ.
Thiết kế Silver/Gold: crawler 45 phút, Silver ETL 60 phút, toàn DAG 120 phút.
```

Với `max_active_tasks=3` và 9 task, có 3 đợt crawler. Nếu mỗi đợt mất gần trần, Bronze một mình đã vượt 120 phút trước khi Silver bắt đầu. Cần chốt MỘT bộ số và cập nhật cả ba tài liệu.

Đề xuất: crawler `execution_timeout=45 phút`, `dagrun_timeout=3 giờ`, Silver `execution_timeout=60 phút`, và DAG Gold riêng (đã có) không nằm trong ngân sách này.

### 4. Restart service sau khi đổi env

```text
Cả airflow-scheduler.service và airflow-webserver.service dùng
EnvironmentFile=/home/womcloud/airflow/airflow.env,
và DAG gọi crawler bằng env = os.environ.copy().

→ Sửa airflow.env BẮT BUỘC phải restart hai service.
→ Đây là nguyên nhân thật của lỗi "You need to initialize the database"
  khi chạy airflow CLI thủ công mà không . airflow.env trước:
  Airflow rơi về SQLite mặc định thay vì metadata PostgreSQL.
→ KHÔNG chạy `airflow db init` để chữa lỗi đó.
```

---

## GIAI ĐOẠN 9: HỢP ĐỒNG BÀN GIAO BRONZE → SILVER

Silver chỉ được phép đọc Bronze qua hai cửa đã kiểm chứng:

```text
Cửa 1  StateManager.list_active_bronze_objects(source)
       → danh sách object_key có is_compacted = 0, kèm records_count
         và checksum_sha256 từ manifest.
Cửa 2  AtomicWriter.stream_bronze_records(object_key)
       → stream từng bản ghi JSON đã giải nén, bộ nhớ đệm cố định.

Điều kiện Silver được chạy: bronze_audit_after_crawl trả PASS.
```

**Bản ghi Bronze phải có đủ những gì Silver cần để sinh `observation_id`:**

| Nhu cầu Silver | Trường Bronze | Nơi lấy | Nếu thiếu |
|---|---|---|---|
| `platform` | `raw_payload.platform` hoặc `_metadata.source` | Envelope | Quarantine `MISSING_MANDATORY_FIELD` |
| `source_job_id` | `raw_payload.job_id` / `source_job_id` | Payload | Quarantine `SCHEMA_DRIFT_MISSING_JOB_ID` |
| `observed_at` | `_metadata.observed_at` → `payload.observed_at` → `payload.scraped_at` → `_metadata.ingested_at` | Ưu tiên metadata | Raise, không tự sinh giờ hiện tại |
| `content_hash` | `raw_payload.content_hash` | Crawler tính | Raise → quarantine |
| `job_title`, `company_name` | payload | Parser | Raise → quarantine |

**Bất biến quan trọng nhất của toàn bộ pipeline:**

```text
observed_at PHẢI đến từ Bronze metadata, không từ đồng hồ lúc Silver chạy.
Nhờ đó cùng một input Bronze cho cùng observation_id ở mọi lần chạy lại,
và Gold có cutoff ổn định. Đây là lý do commit 08b9eb8 thêm
_metadata.observed_at là thay đổi có ý nghĩa kiến trúc, không phải cosmetic.
```

---

## GIAI ĐOẠN 10: BẢO MẬT VÀ BÍ MẬT VẬN HÀNH

```text
Git-ignore đã cấu hình:
  .env, .env.*          (!.env.example được phép commit)
  data/                 (SQLite state store + MinIO volume + silver output)
  lakehouse_env/        (virtualenv)
  src/crawlers/cookies/ (SESSION COOKIE THẬT của 9 sàn)
  logs/, backups/, *.log

Bất biến:
1. Cookie phiên KHÔNG bao giờ được commit. Đã có 9 file cookie trên máy local
   và đang được ignore — giữ nguyên như vậy.
2. Credential MinIO/PostgreSQL/Telegram/Discord chỉ nằm trong .env hoặc
   ~/airflow/airflow.env (chmod 600).
3. Log và alert phải qua redact_message() trước khi ra ngoài.
4. Bronze audit gate và failure ledger không ghi raw payload chứa cookie/header.
5. Lệnh dán vào terminal nên viết MỘT DÒNG: xuống dòng giữa chừng làm
   `set +a` nuốt `export`, biến không được xuất mà không báo lỗi.
```

---

## GIAI ĐOẠN 11: BỘ KIỂM THỬ NGHIỆM THU BRONZE

| Mã | Kịch bản | Kết quả mong đợi | Test hiện có |
|---|---|---|---|
| `B-01` | Envelope tính checksum và verify đạt | Đủ 6 khóa `_metadata`, verify True | `test_checksum_sha256_generation` |
| `B-02` | Field ngoài contract bị rescue, không làm dừng | Có trong `_rescued_data`, absent ở `raw_payload` | `test_schema_drift_rescues_unknown_fields` |
| `B-03` | Contract từng sàn khớp schema | `company_industry` nằm trong payload | `test_topcv_schema_contract_consistency` |
| `B-04` | Mỗi source chỉ một RUNNING | Lock thứ hai trả None | `test_concurrency_exclusive_lock` |
| `B-05` | Hai tiến trình thật đua khóa | Đúng 1 winner, 1 dòng RUNNING | `test_multiprocess_concurrency_race` |
| `B-06` | 10 vòng đua liên tiếp | Mỗi vòng 1 winner | `test_stress_concurrency_race` |
| `B-07` | Partial unique index tồn tại trong `sqlite_master` | Có `WHERE status='RUNNING'` | `test_partial_unique_index_schema_exists` |
| `B-08` | PID chết → phục hồi tức thì | Khóa mới chiếm được | `test_dead_pid_instant_recovery` |
| `B-09` | Heartbeat cập nhật `heartbeat_at` | Row RUNNING đổi mốc | `test_heartbeat_lifecycle` |
| `B-10` | Không có tin mới → SUCCESS 0 bản ghi, không ghi tệp | `write_atomic` không được gọi | `test_crawler_empty_stream_handled_gracefully` |
| `B-11` | Stream generator tới writer đúng số bản ghi | 2 records, manifest 1 object | `test_crawler_generator_yields_stream_to_writer` |
| `B-12` | Manifest fail → phiên FAILED, watermark đứng | `error_message` chứa "Manifest thất bại" | `test_manifest_failure_aborts_commit` |
| `B-13` | Heartbeat chạy định kỳ khi ghi | ≥ 3 lần cho 25 bản ghi | `test_periodic_heartbeat_during_crawl` |
| `B-14` | Heartbeat ở pha fetch | ≥ 4 lần | `test_fetch_phase_heartbeat_progress` |
| `B-15` | Thiếu credential MinIO → từ chối | `ValueError` | `test_missing_credentials_rejection` |
| `B-16` | Không ghi tệp rỗng | `ValueError` | `test_empty_records_rejection` |
| `B-17` | Chết sau copy, chạy lại | `ALREADY_COMMITTED`, không copy lại | `test_idempotent_retry_after_copy_crash` |
| `B-18` | Đọc stream `.jsonl.gz` | Đủ bản ghi, đúng nội dung | `test_stream_bronze_records_reads_jsonl_gz` |
| `B-19` | Dưới ngưỡng gom | `SKIPPED`, giữ tệp | `test_compaction_skips_below_threshold` |
| `B-20` | Gom thành công, archive | Đủ audit + manifest, bản sao archive, xóa bản gốc | `test_compaction_happy_path_archive_mode` |
| `B-21` | Record count lệch sau gom | Xóa tệp gom, KHÔNG ghi audit, GIỮ tệp nguồn | `test_compaction_record_count_mismatch_fail_closed` |
| `B-22` | 403 / listing rỗng không xác nhận | Fail-closed, watermark không tiến | `test_fail_closed_crawlers` (10 test) |
| `B-23` | Redaction secret + HTML trong ledger | `[REDACTED]`, append-only | `test_redaction_removes_credentials_and_html`, `test_failure_event_is_append_only_jsonl_and_redacted` |
| `B-24` | Pre-flight disk/RAM/network/State | Trả runtime contract đã sanitize | `test_preflight_*` |
| `B-25` | Audit gate: manifest/count/checksum/envelope | PASS khi đủ bằng chứng, fail khi lệch | `test_audit_*` (2 test) |
| `B-26` | Hợp đồng DAG | Đủ 9 task crawl + audit + silver gate | `test_dag_contract` (2 test) |

**Độ phủ hiện tại:** 24 test Bronze layer + 10 fail-closed + 8 operational gates + 2 DAG contract + 5 suite parser theo sàn.

**Khoang trống kiểm thử còn lại:**

```text
1. Chưa có test cho việc compaction được lên lịch (vì nó chưa được lên lịch).
2. Chưa có test end-to-end Bronze → Silver trên cùng một state store thật.
3. Chưa có test cho đường latest_source_results() (code chết).
4. Chưa có test cho alert delivery thật (thiếu credential).
5. Test trên Windows có 40 lỗi môi trường (CP1252 khi in tiếng Việt,
   PermissionError khi dọn SQLite temp). Không phản ánh lỗi logic,
   nhưng làm CI đa nền tảng không đáng tin.
```

---

## TỔNG KẾT — CHECKLIST TRẠNG THÁI BRONZE

| STT | Hạng mục | Module | Trạng thái |
|---|---|---|---|
| 1 | MinIO 2 bucket, bind loopback | `infrastructure/docker-compose.yml` | ☑ Đang chạy trên VPS |
| 2 | State Store 4 bảng + INSERT-first lock | `src/storage/state_manager.py` | ☑ Xong, có test đa tiến trình |
| 3 | Metadata Envelope 9 contract + rescue | `src/storage/metadata_envelope.py` | ☑ Xong, có `observed_at` |
| 4 | AtomicWriter 6 bước + idempotent retry | `src/storage/atomic_writer.py` | ☑ Xong |
| 5 | 9 crawler fail-closed + heartbeat 2 pha | `src/crawlers/*.py` | ☑ Xong |
| 6 | Pre-flight có thể cấu hình ngưỡng | `src/utils/preflight.py` | ☑ Xong |
| 7 | Audit gate per-record verify | `src/utils/bronze_audit_gate.py` | ☑ Xong |
| 8 | DLQ + redaction + phân loại lỗi | `src/utils/operational.py` | ☑ Xong |
| 9 | DAG 9 sàn, max 3 task, audit ALL_DONE | `dags/dag_daily_job_lakehouse_pipeline.py` | ☑ Đang chạy trên VPS |
| 10 | Compaction engine | `src/storage/compaction_engine.py` | ☑ Code xong, ☐ **chưa được lịch gọi** |
| 11 | Alert Telegram/Discord thật | `src/utils/operational.py` | ☐ B-GAP-1, `ALERT_ENABLED` chưa bật |
| 12 | MinIO Lifecycle expiration | `infrastructure/` | ☐ B-GAP-4 |
| 13 | Timeout khớp thiết kế Silver/Gold | `dags/` | ☐ B-GAP-3 |
| 14 | Backfill manifest đối soát | `src/utils/backfill_manifest.py` | ☑ Có công cụ, ☐ chưa có quy trình chạy định kỳ |

---

## CÁC QUYẾT ĐỊNH ĐÃ CHỐT CỦA BRONZE

| Mã | Quyết định | Cam kết |
|---|---|---|
| **D-11** | Compaction mặc định **archive**, không purge | Copy sang `archive/{old}` + verify kích thước, chỉ xóa bản gốc khi bản sao khớp |
| **D-16** | Bronze lưu **JSONL.gz**, phân vùng ingestion time | Không dùng định dạng nghiệp vụ ở Bronze; nghiệp vụ thuộc Silver |
| **D-14** | Taxonomy kỹ năng chuẩn hóa ở **Silver**, không ở Bronze | Bronze giữ `skills` thô của sàn; `skills_standardized` sinh ở Silver |
| **D-B01** | Dedup đa nguồn **deferred sang Silver** | Bronze không xóa, không gộp, chỉ cam kết observation |
| **D-B02** | `observed_at` đóng băng trong `_metadata` lúc bọc Bronze | Nền tảng idempotency cho `observation_id` và cutoff của Gold |
| **D-B03** | Crawler sở hữu lock + watermark + commit; Airflow chỉ điều phối và gate | Giải thích vì sao DAG không có task "Update State" riêng |
| **D-B04** | `canonicalize_bronze_record` không đọc `_rescued_data` | Giữ cơ chế phát hiện drift; buộc cập nhật `SCHEMA_CONTRACTS` khi thêm field |
| **D-B05** | Silver gate = `ALL_SUCCESS` cho MVP | Một sàn lỗi chặn cả Silver ngày đó, đổi lấy toàn vẹn cao nhất |

---

## GIỚI HẠN CỦA TẦNG BRONZE

```text
1. Bronze không biết tin nào trùng, tin nào còn hiệu lực, tin nào của công ty nào
   sau khi chuẩn hóa. Đó là việc của Silver.
2. Schema rescue chỉ bắt field THỪA. Field MẤT vì DOM đổi cấu trúc biểu hiện
   bằng null hàng loạt, phát hiện ở Silver Quality Gate, không ở Bronze.
3. Early stopping giả định listing sắp giảm dần theo ngày đăng. Vi phạm giả định
   này sẽ sót tin cũ mà không có tín hiệu.
4. content_hash do crawler tự định nghĩa. Hai sàn có thể định nghĩa khác nhau;
   so sánh hash giữa hai nền tảng là vô nghĩa.
5. Cookie phiên là điểm yếu vận hành: hết hạn → 403 → BLOCKED → fail-closed.
   Chưa có quy trình làm mới tự động, cũng chưa có cảnh báo (B-GAP-1).
6. Audit gate đọc lại toàn bộ object mỗi ngày: chi phí tăng theo số tệp,
   liên quan trực tiếp B-GAP-2.
7. Chưa đo được N_d và tỷ lệ phủ field trên dữ liệu thật vì
   LAKEHOUSE_SILVER_ENABLED vẫn false và chưa có release Silver nào.
8. Test Bronze trên Windows có lỗi môi trường (encoding, temp SQLite lock),
   không phản ánh lỗi logic nhưng làm CI đa nền tảng chưa đáng tin.
```

---

## BẰNG CHỨNG PHẢI TRẢ SAU MỖI BƯỚC BRONZE

```text
1. run_id từng source + status + records_ingested.
2. bronze_s3_uri và file_checksum_sha256 của object đã cam kết.
3. Kết quả bronze_audit_after_crawl: checked_objects, source_count,
   empty_success_count, và danh sách source fail nếu có.
4. Dung lượng landing còn object .uploading (phải = 0).
5. Số dòng ingestion_objects có is_compacted = 0 theo từng source.
6. Với compaction: compaction_id, files_merged_count, total_records,
   bytes_saved, compacted_key, và xác nhận tệp nguồn vẫn còn trong archive.
7. Với alert: event_id trong ledger + timestamp gửi + phản hồi channel.
```

Không ghi cookie, token, mật khẩu hay raw payload chứa dữ liệu cá nhân vào bằng chứng. Không đánh dấu bước hoàn thành chỉ vì code chạy hết mà chưa đối soát manifest với object thật.

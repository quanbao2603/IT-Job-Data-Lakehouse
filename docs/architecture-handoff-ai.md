# IT-Job-Data-Lakehouse — Kiến trúc handoff cho AI

> Tài liệu này được xuất từ lần quét repository và runtime read-only ngày 2026-11-14.
> Mục đích: giúp một AI khác hiểu nhanh phạm vi, cấu trúc, luồng dữ liệu, trạng thái kiểm chứng và các giới hạn hiện tại.
> Không chứa credential, API key, cookie/session value, database runtime, log hay object payload.

---

## 1. Tóm tắt hệ thống

`IT-Job-Data-Lakehouse` là một hệ thống thu thập dữ liệu tuyển dụng IT từ bốn nền tảng:

- ITviec
- TopCV
- TopDev
- VietnamWorks

Repository hiện tập trung vào:

1. crawler và parser theo từng nguồn;
2. ingestion vào tầng Bronze trên MinIO/S3;
3. metadata envelope, checksum và schema-drift rescue;
4. SQLite State Store, watermark, manifest, run lock và heartbeat;
5. audit, backup/restore verification, compaction và test.

**Chưa có trong source snapshot:** Silver, Gold, data warehouse, dashboard, scheduler/orchestrator production và pipeline phân tích nghiệp vụ.

Tên chính xác của Bronze hiện tại nên là:

> Immutable extracted/parsed observation Bronze

Payload hiện là dữ liệu đã được parser/API trích xuất; hệ thống chưa lưu nguyên byte HTML/HTTP response để gọi là raw byte-level lake.

---

## 2. Sơ đồ kiến trúc tổng thể

```text
                   ┌──────────────────────────────────────┐
                   │  External job platforms              │
                   │  ITviec / TopCV / TopDev /           │
                   │  VietnamWorks                          │
                   └──────────────────┬───────────────────┘
                                      │ HTTP HTML/API/RSC
                                      ▼
                   ┌──────────────────────────────────────┐
                   │  Source crawlers                      │
                   │  source-specific session/parser       │
                   │  URL/job-id filtering + watermark     │
                   └──────────────────┬───────────────────┘
                                      │ iterable job payloads
                                      ▼
                   ┌──────────────────────────────────────┐
                   │  BaseCrawler.run()                    │
                   │  acquire run lock                     │
                   │  read watermark                       │
                   │  envelope each record                  │
                   │  heartbeat                            │
                   └───────────────┬───────────────┬────────┘
                                   │               │
                         payload   │               │ state/run
                                   ▼               ▼
                   ┌──────────────────────┐  ┌──────────────────────┐
                   │ MetadataEnvelope     │  │ SQLite State Store    │
                   │ checksum             │  │ runs/state/manifest   │
                   │ schema contract      │  │ lock/heartbeat/audit   │
                   │ _rescued_data       │  └──────────┬───────────┘
                   └──────────────┬───────┘             │
                                  │ enveloped JSONL      │ manifest + watermark
                                  ▼                      │
                   ┌───────────────────────────────────┴────┐
                   │ AtomicWriter                            │
                   │ local gzip temp → Landing upload        │
                   │ size verify → MinIO server-side copy     │
                   │ → Bronze size verify → landing cleanup   │
                   └──────────────────┬──────────────────────┘
                                      │
             ┌────────────────────────┴────────────────────────┐
             │                                                  │
             ▼                                                  ▼
   ┌──────────────────────┐                         ┌──────────────────────┐
   │ MinIO landing bucket │                         │ MinIO bronze bucket  │
   │ temporary .uploading │                         │ partitioned JSONL.GZ  │
   │ normally empty       │                         │ active data objects   │
   └──────────────────────┘                         └──────────┬───────────┘
                                                               │
                         ┌─────────────────────────────────────┼──────────────┐
                         │                                     │              │
                         ▼                                     ▼              ▼
                Bronze audit reporter                 Backup/restore    Compaction
                checksum/schema/count                 verification      engine
```

### Luồng commit chính

```text
fetch source
  → filter trong crawler
  → BaseCrawler acquire lock
  → read watermark
  → parse job
  → MetadataEnvelope.wrap()
  → gzip JSONL vào temporary local file
  → upload landing/<source>/temp_<run>.uploading
  → verify landing size
  → copy server-side sang bronze/<source>/ingest_year=.../part_<run>.jsonl.gz
  → verify Bronze size
  → xóa landing temporary object
  → register ingestion_objects manifest
  → commit SUCCESS + watermark trong SQLite
```

Đây là **verified staging-to-Bronze copy**, không phải transaction nguyên tử xuyên MinIO và SQLite.

---

## 3. Cấu trúc repository đã quét

### 3.1. Cây source được theo dõi bởi Git

```text
IT-Job-Data-Lakehouse/
├── .env.example
├── .gitignore
├── README.md
├── requirements.txt
├── infrastructure/
│   └── docker-compose.yml
├── docs/
│   ├── bronze-architecture-review.md
│   ├── bronze-design-review-stages-1-5.md
│   ├── deployment-vps.md
│   ├── project-structure.md
│   ├── runtime-state.md
│   └── architecture-handoff-ai.md       # tài liệu này
├── src/
│   ├── crawlers/
│   │   ├── base_crawler.py
│   │   ├── itviec_crawler.py
│   │   ├── topcv_crawler.py
│   │   ├── topdev_crawler.py
│   │   └── vietnamworks_crawler.py
│   ├── storage/
│   │   ├── atomic_writer.py
│   │   ├── compaction_engine.py
│   │   ├── metadata_envelope.py
│   │   └── state_manager.py
│   └── utils/
│       ├── backfill_manifest.py
│       ├── backup_bronze.py
│       ├── bronze_audit_reporter.py
│       └── clean_test_bronze.py
└── tests/
    └── test_bronze_layer.py
```

Lần quét repository trước khi tạo tài liệu này xác nhận:

- **24 file được Git theo dõi**;
- **13 module Python trong `src/`**;
- **1 test module**;
- **5 tài liệu Markdown trước khi thêm tài liệu này**;
- source Python: khoảng **3.413 dòng**;
- test Python: khoảng **382 dòng**.

Sau lần quét, tài liệu handoff này được tạo mới và README được thêm một liên kết tới nó; hai thay đổi đó đang là thay đổi local chưa commit.

### 3.2. Runtime tồn tại trên VPS nhưng không thuộc source commit

```text
.env                         # secret cấu hình local
src/crawlers/cookies/        # cookie/session runtime theo nguồn
data/state_store.db         # SQLite State Store
 data/minio_storage/         # bind mount dữ liệu MinIO
backups/                     # snapshot/backup runtime
logs/ và *.log               # log crawl/vận hành
lakehouse_env/               # Python virtualenv
__pycache__/                 # bytecode tự sinh
```

Các khu vực trên bị loại khỏi Git bằng `.gitignore` hoặc được giữ ngoài tài liệu chi tiết để không làm lộ dữ liệu nhạy cảm/runtime.

---

## 4. Trách nhiệm từng module

### 4.1. `src/crawlers/base_crawler.py`

`BaseCrawler` là orchestration layer dùng chung cho bốn crawler:

- tạo `run_id`;
- acquire exclusive run lock theo source;
- đọc watermark và `last_record_id`;
- gọi `fetch_jobs()` của source;
- bọc từng job bằng `MetadataEnvelope`;
- truyền generator vào `AtomicWriter`;
- gọi heartbeat đầu run và định kỳ theo batch;
- đăng ký object vào manifest;
- commit SUCCESS và watermark;
- chuyển run sang FAILED khi exception.

Nếu không acquire được lock, run trả trạng thái `LOCKED` và không ghi dữ liệu.

### 4.2. Crawler theo nguồn

#### `itviec_crawler.py`

- HTTP HTML parser bằng BeautifulSoup.
- Đọc cookie runtime nếu có.
- Tìm link `/it-jobs/...-<id>`.
- Parse JSON-LD và HTML detail.
- Loại URL lặp trong candidate list.
- Có retry HTTP 429/5xx và delay.
- Tạo các trường job/company/salary/description/requirements/benefits.

#### `topcv_crawler.py`

- Search URL hiện tại: `/tim-viec-lam-it?page=<page>`.
- Parser HTML bằng BeautifulSoup.
- Loại URL lặp bằng `seen`.
- Có watermark hit theo ID, retry 429/5xx và delay.
- Parse thông tin tuyển dụng, công ty, skills, mô tả, yêu cầu và phúc lợi.

#### `topdev_crawler.py`

- Lọc 13 nhóm ngành IT.
- Ưu tiên giải mã Next.js RSC Flight payload.
- Có fallback DOM parser.
- Loại trùng theo `seen_job_ids` trong cùng run.
- Dừng khi không có `new_in_page` để tránh vòng lặp trang trùng/rỗng.
- Hỗ trợ watermark và retry HTTP.

#### `vietnamworks_crawler.py`

- Search API theo nhóm ngành IT.
- Parse detail Next.js/RSC; fallback về Search API payload.
- Loại trùng theo `seen_ids` trong cùng run.
- Hỗ trợ watermark, retry và delay.
- Chuẩn hóa thông tin job, company, location, skills, function và industry.

Các crawler đều phát ra **parsed observation payload**, không phải response byte-level nguyên bản.

### 4.3. `src/storage/metadata_envelope.py`

`MetadataEnvelope` chuẩn hóa mỗi record thành:

```json
{
  "_metadata": {
    "ingested_at": "UTC timestamp",
    "source": "source name",
    "payload_checksum": "SHA-256 canonical payload",
    "pipeline_run_id": "run id",
    "format_version": "1.0"
  },
  "raw_payload": {},
  "_rescued_data": {}
}
```

Chức năng:

- checksum SHA-256 trên payload canonical JSON;
- schema contract riêng cho từng source;
- field không thuộc contract được đưa vào `_rescued_data`;
- verify checksum khi audit.

Lưu ý thuật ngữ: `raw_payload` là tên field tương thích; nội dung bên trong đã được parser từ nguồn.

### 4.4. `src/storage/atomic_writer.py`

`AtomicWriter`:

- đọc cấu hình MinIO từ environment/`.env`;
- bảo đảm bucket landing và bronze tồn tại;
- ghi iterable record vào file `.jsonl.gz` tạm trên disk;
- tính kích thước và SHA-256 của file gzip;
- upload file vào landing với hậu tố `.uploading`;
- verify kích thước landing;
- server-side copy sang Bronze;
- verify kích thước Bronze;
- xóa object landing tạm sau khi copy;
- trả `bronze_key`, counts, sizes và file checksum.

Object key hiện có dạng:

```text
<source>/ingest_year=YYYY/ingest_month=MM/ingest_day=DD/part_<run_id>.jsonl.gz
```

Giới hạn cần nhớ:

- check retry hiện kiểm tra object đích theo key và kích thước;
- kích thước giống nhau chưa chứng minh bytes giống nhau;
- writer hỗ trợ streaming iterable, nhưng crawler TopCV/TopDev/VietnamWorks vẫn có các list trung gian.

### 4.5. `src/storage/state_manager.py`

SQLite State Store tạo và quản lý:

- `ingestion_state`: watermark, strategy, last record ID, trạng thái source;
- `ingestion_runs`: lịch sử run, status, PID, heartbeat, duration, lỗi;
- `ingestion_objects`: object manifest, size, record count, checksum, compaction state;
- `compaction_audit_logs`: lịch sử compaction.

Cơ chế concurrency:

- `BEGIN IMMEDIATE`;
- partial unique index trên `source_name` khi `status='RUNNING'`;
- stale/dead PID recovery;
- heartbeat lease;
- một active run tối đa cho mỗi source.

Đây là lock **theo run/source**, không phải uniqueness constraint cho từng job record.

### 4.6. `src/storage/compaction_engine.py`

- Chọn các `.jsonl.gz` nhỏ theo prefix partition.
- Stream record từ các object cũ vào output compacted object.
- Ghi `compaction_audit_logs`.
- Đăng ký output manifest.
- Đánh dấu object cũ `is_compacted=1`.
- Có policy archive hoặc purge.

Mặc định code hiện tại có `purge_small_files=True`; vì vậy cần gọi đây là retention-managed compaction, không mặc định gọi là WORM immutable.

### 4.7. `src/utils/`

- `bronze_audit_reporter.py`: đếm object/record/bytes, trạng thái run và verify envelope checksum/schema drift.
- `backfill_manifest.py`: quét object Bronze rồi bổ sung `ingestion_objects` cho object chưa có manifest.
- `backup_bronze.py`: backup SQLite, tùy chọn copy object Bronze, manifest SHA-256, verify snapshot và restore sang bucket test.
- `clean_test_bronze.py`: xóa object/state test theo prefix định trước; cần đọc và kiểm tra scope trước khi chạy trên dữ liệu thật.

---

## 5. Mô hình lưu trữ và trạng thái

### 5.1. MinIO

Compose định nghĩa một service:

```text
container: datalake-minio
S3 API:    127.0.0.1:9000
Console:   127.0.0.1:9001
mount:     ../data/minio_storage:/data
```

Bucket logic:

```text
landing/   temporary staging objects, expected empty after successful run
bronze/    committed extracted observations
```

MinIO chỉ bind localhost trong Compose. Reverse proxy/Nginx và domain là deployment state ngoài repository; cấu hình host không được coi là source code của project.

### 5.2. SQLite State Store

Logical lifecycle của một run:

```text
(no row)
   → RUNNING + PID + heartbeat
   → SUCCESS + finished_at + manifest + watermark
                         hoặc
   → FAILED + error_message
   → ABANDONED nếu PID chết hoặc lease stale
```

Manifest object lifecycle:

```text
active object
   → is_compacted=1 + compacted_into_key
   → archive/purge tùy policy compaction
```

### 5.3. Partition và record format

- Partition theo source và ingestion date.
- File format: gzip-compressed JSON Lines.
- Một dòng là một envelope record.
- Record có payload source-specific; không có một schema business đồng nhất hoàn toàn giữa bốn nguồn.

---

## 6. Chống trùng lặp và tính nhất quán

### Đã có

1. `seen`/`seen_ids`/`seen_job_ids` trong từng crawler để loại trùng trong cùng run.
2. TopDev dừng khi trang không tạo record mới.
3. Watermark và last record ID cho incremental crawl.
4. State Store unique partial index ngăn hai run đồng thời của cùng source.
5. Object key theo run và manifest theo object.
6. Writer có best-effort retry recognition khi cùng key đã tồn tại.

### Chưa có

1. Bảng record-level với unique key toàn cục.
2. Unique constraint xuyên các lần crawl trên `source + canonical_job_id`.
3. Global dedup theo canonical URL hoặc identity registry.
4. Quy tắc chuẩn hóa job ID đồng nhất giữa các nguồn.
5. Cross-source entity resolution cho cùng một tin đăng ở nhiều nền tảng.

Vì vậy, kết luận đúng là:

> Hệ thống có layered duplicate prevention ở crawler, watermark, run lock và object lifecycle; chưa có global record-level deduplication/idempotency tuyệt đối ở Bronze.

Các lần kiểm tra dữ liệu trước đó cho thấy 2.733 record hiện tại có URL tuyển dụng duy nhất, nhưng đó là kết quả audit dataset hiện tại, không thay thế cho một unique constraint trong kiến trúc.

---

## 7. Tình trạng kiểm chứng runtime gần nhất

Lệnh đã chạy trực tiếp từ project root:

```bash
lakehouse_env/bin/python -m src.utils.bronze_audit_reporter --deep
lakehouse_env/bin/python -m unittest discover -s tests -v
python -m compileall -q src tests
```

Kết quả audit ngày 2026-11-14:

```text
Bronze sources SUCCESS:       4/4
Bronze objects:               4
Bronze records:               2,733
Bronze compressed size:       2.87 MB
SHA-256 valid:                2,733/2,733 (100%)
Schema drift:                 0 records
Landing temporary objects:    0 theo audit hiện tại
State Store history:          7 runs
  SUCCESS:                    5
  FAILED:                     1
  ABANDONED:                  1
  RUNNING hiện tại:           0
```

Kết quả test:

```text
Ran 17 tests
OK
```

Test bao phủ checksum, schema rescue, watermark, manifest, compaction tracking, run lock tuần tự, multiprocess lock race, dead-PID recovery, heartbeat, streaming generator, empty stream và manifest failure path.

### Git state tại thời điểm xuất tài liệu

```text
branch:       main
working tree: clean
HEAD:         6ad7ba9
origin/main:  6ad7ba9
commit:       fix: harden Bronze ingestion and document design review
```

---

## 8. Các giới hạn cần nói rõ trong tiểu luận

Không nên dùng các tuyên bố tuyệt đối sau nếu chưa bổ sung bằng chứng:

```text
raw byte-level 100%
zero data loss
WORM immutable tuyệt đối
atomic transaction hoàn chỉnh giữa MinIO và SQLite
idempotency production-grade tuyệt đối
O(1) RAM cho toàn bộ crawler
```

Cách diễn đạt được khuyến nghị:

- “verified extracted Bronze ingestion”;
- “SHA-256 integrity verification ở envelope/file level”;
- “exclusive per-source run lock”;
- “best-effort same-key retry protection”;
- “crawler-level duplicate filtering và watermark-based incremental ingestion”;
- “writer hỗ trợ iterable/gzip streaming, còn memory end-to-end phụ thuộc crawler”.

---

## 9. Khoảng trống và thứ tự ưu tiên phát triển

### P0 — nếu muốn nâng chất lượng dedup

1. Tạo canonical identity: `source_name + canonical_job_url` hoặc source-native job ID đáng tin cậy.
2. Tạo record identity/index trong State Store hoặc một Bronze index riêng.
3. Enforce unique key trước khi commit record vào active Bronze.
4. Sửa các parser tạo ID từ hậu tố slug dễ va chạm.
5. Thêm audit cross-run, không chỉ audit trong một object/run.

### P1 — nếu muốn productionize ingestion

1. Retry idempotency bằng checksum thực tế, không chỉ size.
2. Reconciliation định kỳ giữa MinIO objects, manifest và run state.
3. Trạng thái orphan/recovery cho object đã copy nhưng chưa commit manifest.
4. Lock riêng cho compaction cùng partition.
5. Quy định rõ archive/purge và retention.
6. Test failure path với MinIO thật hoặc test container.

### P2 — nếu muốn hoàn thiện lakehouse

1. Thêm scheduler/orchestrator và observability.
2. Xây Silver schema chuẩn hóa giữa bốn nguồn.
3. Xây Gold marts/metrics cho phân tích thị trường việc làm.
4. Thêm data quality checks ở Silver/Gold.
5. Thêm dashboard hoặc query layer.

---

## 10. Hướng dẫn AI khác bắt đầu đọc repository

Đọc theo thứ tự sau để không bỏ sót dependency:

```text
1. README.md
2. docs/architecture-handoff-ai.md              # tài liệu này
3. src/crawlers/base_crawler.py
4. src/storage/metadata_envelope.py
5. src/storage/atomic_writer.py
6. src/storage/state_manager.py
7. một crawler nguồn cụ thể
8. src/utils/bronze_audit_reporter.py
9. tests/test_bronze_layer.py
10. docs/bronze-design-review-stages-1-5.md
11. docs/deployment-vps.md và docs/runtime-state.md
```

AI khác phải coi các path sau là **runtime/secret boundary** và không đọc hoặc xuất nội dung nếu không có yêu cầu an toàn rõ ràng:

```text
.env
src/crawlers/cookies/
data/
backups/
logs/
lakehouse_env/
```

Khi trả lời về trạng thái hiện tại, AI phải phân biệt:

- fact từ source code;
- fact từ audit runtime tại một timestamp;
- design intention trong tài liệu;
- limitation chưa được chứng minh.

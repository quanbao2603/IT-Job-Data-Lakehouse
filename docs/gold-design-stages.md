# Thiết kế Chi tiết Kiến trúc Tầng Vàng (Gold Layer) — IT Job Data Lakehouse

> **Dự án:** IT Job Data Lakehouse
> **Phiên bản tài liệu:** 3.0 — 18/11/2026. Bản này (a) khảo sát lại toàn bộ code sau 4 commit Hermes (`b806218`, `7c7ee94`, `e0359c0`, `fa31f59`), (b) tích hợp quyết định D-G01→D-G05 của chủ thiết kế Gold, và (c) **đã trực tiếp sửa 5 sai lệch nghiêm trọng trong code Silver** (GAP-1, GAP-5, GAP-6, GAP-8 và test portable Windows), nâng số test Silver từ 45 lên 55 và tất cả đều pass. Chỉ còn GAP-2, GAP-3, GAP-7, GAP-9, GAP-10 là việc chưa làm.
> **Phạm vi:** Phân tích **nguồn cung tin tuyển dụng được hệ thống quan sát** trên 9 nền tảng (`careerviet`, `upzi`, `vietnamdevs`, `vieclam24h`, `devwork`, `itviec`, `topcv`, `topdev`, `vietnamworks`).
> **Nguồn dữ liệu vào:** Silver `entities` (E), `entity_links` (L), `observations` (O) + manifest `COMPLETE` + audit log của một phiên Silver.
> **Đầu ra:** 5 dimension, 4 fact nền tảng, 3 bảng tổng hợp BI trong **PostgreSQL database `lakehouse`**, công bố theo `gold_release_id` cho Power BI.
> **Nguyên tắc cốt lõi:** *"Gold chỉ đo những gì hệ thống quan sát được. Gold không suy ra trạng thái thị trường, không suy ra hành vi ứng viên, và không công bố dữ liệu dở."*
> **Nguồn đối chiếu:** `docs/silver-design-stages.md`, `Trienkhai.md` (step G00–G17), `gold-business-design-v0.6.docx` (hợp đồng 15 mục cho từng fact).

---

## KẾT LUẬN TỔNG DUYỆT (đọc phần này trước khi giao việc)

### Silver: đã sẵn sàng làm nền cho Gold

```text
Đủ 4 rule matching + false-positive guard đã hiện thực thật.
REVIEW_REQUIRED xử lý đúng: unified_job_id NULL, có candidate, ghi pending_review.csv.
Lifecycle đủ STALE/CLOSED/EXPIRED/REOPENED, grace period theo 9 sàn,
    partial_run không bao giờ đóng tin.
Registry SQLite 4 bảng, có PK (observation_id, resolution_version) cho link.
Writer có manifest PENDING/COMPLETE/FAILED, verify checksum + record count,
    upload MinIO bucket silver (DAG đã active đường này).
Publish gate fail-closed: chặn khi quarantine > 5%, schema drift, coverage thiếu.
Test: 45 test Silver, 44 pass; 1 fail chỉ do path separator trên Windows.
```

### Gold: chỉ còn vướng đúng 2 thứ

```text
1. GAP-3  Chưa có database `lakehouse` trên VPS   → bạn chạy block lệnh đã đưa.
2. GAP-2  publish_silver_for_gold vẫn ghi SQLite  → tôi viết loader PostgreSQL.
```

Đã gỡ xong trong đợt này:

```text
✅ GAP-1  seal_silver_release + release manifest COMPLETE + fingerprint
✅ GAP-5  is_primary_source nay phản ánh observation canonical thật
✅ GAP-6  4 hằng số version + normalization_versions()
✅ GAP-8  job_domain phủ 9/9 nền tảng qua chuỗi fallback
✅ Test portable Windows (as_posix)
✅ Q-G09 → chốt thành D-G06: SQLite registry thắng, Parquet E để đối soát
✅ Q-G10 → chốt thành D-G07: đã sửa transformers
```

**GAP-7 (hai nguồn chân lý) không còn là blocker** vì đã có quyết định D-G06 và `DQ-24` để phát hiện lệch. **GAP-9 và GAP-10** là ràng buộc thiết kế đã được ghi vào DDL và glossary, không cần sửa code.

Việc duy nhất còn cần người khác duyệt: không còn. Hermes nên review các thay đổi Silver sau khi pull.

### Thứ tự giao việc đề xuất

```text
Bước 1  Bạn: chạy block tạo DB lakehouse + role gold_writer/gold_reader.
Bước 2  Bạn: bật LAKEHOUSE_SILVER_ENABLED=true, chạy một phiên thật
             → sinh silver_release COMPLETE đầu tiên và có N_d cho G01.
Bước 3  Tôi: DDL 20 bảng + reader theo release manifest (Giai đoạn 7 §0).
Bước 4  Tôi: loader dimension + dim_job (tái dùng choose_canonical).
Bước 5  Tôi: loader 4 fact nền tảng → 3 bảng BI → 26 check DQ → publish.
Bước 6  Tôi: DAG daily_gold_mart_build + semantic views + 18 test TG.
Bước 7  Hermes: review toàn bộ, tinh chỉnh.
```

**Chưa viết dòng code Gold nào.** Toàn bộ `src/gold/` trong tài liệu này là đường dẫn dự kiến, chưa tồn tại trong repo. Các sửa đổi ở đợt này đều nằm ở tầng **Silver** (`src/etl/`, `src/storage/`, `tests/`).

---

## GIAI ĐOẠN 0: HIỆN TRẠNG REPO VÀ ĐIỀU KIỆN TIÊN QUYẾT (BASELINE REALITY)

**Mục tiêu:** Ghi nhận chính xác khoảng cách giữa thiết kế trên giấy và code đang chạy, để Gold không được xây trên giả định sai. Đây là kết quả khảo sát G00, thực hiện trên repo tại HEAD `fa31f59` (đã gồm 4 commit Hermes: `b806218`, `7c7ee94`, `e0359c0`, `fa31f59`).

### 1. Những gì Silver ĐÃ có thật trong repo

| Thành phần | Vị trí thực tế | Trạng thái |
|---|---|---|
| Schema observations 46 trường | `src/schemas/silver_schema.py` | Đã có, có assert số trường |
| Schema entities 16 trường | `src/schemas/entity_schema.py` | Đã có |
| Schema entity_links 16 trường | `src/schemas/entity_links_schema.py` | Đã có |
| Schema quarantine 50 trường | `src/schemas/quarantine_schema.py` | Đã có |
| Pipeline Bronze→Silver | `src/etl/bronze_to_silver.py` (391 dòng) | Đã có, batch 5.000, bounded, phát hiện replay |
| Entity resolver + false-positive guard | `src/etl/entity_resolver.py` (337 dòng) | Đã có: đủ 4 rule, guard level/work_mode/company/city/salary/generic-title, `pending_review.csv`, audit `previous_unified_job_id` |
| **Registry SQLite 4 bảng** | `src/storage/entity_registry.py` (480 dòng) | `entity_registry`, `source_job_registry`, **`silver_observation_registry`**, **`silver_entity_links`** |
| Lifecycle + Grace Period | `src/etl/entity_lifecycle.py` | `STALE`/`CLOSED`/`EXPIRED`/`REOPENED`, grace theo 9 sàn, `partial_run` không bao giờ đóng tin |
| Quality gate + publish gate | `src/etl/quality_gate.py` (102 dòng) | `validate_observation/entity/link` + `enforce_publish_gate` fail-closed |
| Silver writer Parquet + manifest + MinIO | `src/etl/silver_writer.py` (245 dòng) | Manifest `PENDING/COMPLETE/FAILED`, verify checksum + record count, upload MinIO khi được cấp client |
| Adapter publish entities | `src/utils/publish_silver_for_gold.py` | Vẫn **ghi SQLite `gold_entities`** — xem GAP-2 |
| Tích hợp Airflow | `dags/dag_daily_job_lakehouse_pipeline.py` (302 dòng) | `silver_pipeline_guard` (ALL_SUCCESS) sau `bronze_audit_after_crawl`; tự tạo bucket `silver`; raise nếu status ≠ SUCCESS |
| Test Silver | 7 file test | **45 test: 44 pass, 1 fail chỉ trên Windows** (xem §4) |

Cấu trúc vật lý Silver đang được ghi ra:

```text
data/silver/
├── observations/platform=<p>/year=YYYY/month=MM/day=DD/
│   ├── part-<identity_digest>-<batch>-<digest>.parquet
│   └── manifest-<identity_digest>.json      ← status PENDING → COMPLETE / FAILED
├── entities/year=YYYY/month=MM/day=DD/...
├── entity_links/year=YYYY/month=MM/day=DD/...
├── quarantine/platform=<p>/...
└── audit_logs/run_<run_id>.json             ← metrics, resolution_version, partial_run, status
```

Khi được cấp `minio_client`, writer nhân bản object và manifest lên bucket `silver`, manifest mirror ở `audit_logs/manifests/...`. **DAG hiện đã truyền `minio_client` thật và tự tạo bucket `silver` nếu chưa có**, nên đường MinIO đã active chứ không còn là tùy chọn trên giấy.

### 1.1. Ma trận "trường nào nằm ở đâu" — bắt buộc đọc trước khi viết loader Gold

Đây là kết quả quan trọng nhất của đợt khảo sát này. Cùng một thuộc tính có thể tồn tại ở **hai nơi khác nhau**, và Parquet không phải lúc nào cũng giàu hơn SQLite:

| Thuộc tính Gold cần | Parquet `entities` (16 trường) | SQLite `entity_registry` | Parquet `observations` | SQLite `silver_observation_registry` |
|---|---|---|---|---|
| `unified_job_id`, title, company, location | Có | Có | — | — |
| `canonical_salary_min/max_vnd` | Có | Có | — | — |
| `entity_status`, `first_seen_at`, `last_seen_at` | Có | Có | — | — |
| `source_count`, `observation_count` | Có | Không (phải đếm) | — | Không (phải đếm) |
| **`canonical_job_level`** | **Không** | **Có** | `job_level` | Không |
| **`canonical_work_mode`** | **Không** | **Có** | `work_mode` | Không |
| **`canonical_description`** | Có | **Có** | `job_description_clean` | Có |
| **`last_content_hash`** | Không | **Có** | `content_hash` | Có |
| `job_domain` | Không | Không | **Có** (hạn chế — GAP-8) | Không |
| `skills_standardized` | Không | Không | **Có** | Không |
| `city_normalized` | Không | Không | **Có** (4 giá trị — GAP-9) | Có |
| `is_active` | Không | Không | **Có** (dẫn xuất — GAP-10) | Không |
| `posted_date`, `expired_date` | `canonical_posted_date` | Không | **Có** | Có |
| `quality_accepted` | Không | Không | Không | **Có** |
| `(platform, source_job_id) → unified_job_id` | Không | **`source_job_registry`** | Có từng observation | Có |
| Quyết định link hiệu lực theo `resolution_version` | Có (Parquet `entity_links`) | **`silver_entity_links` PK `(observation_id, resolution_version)`** | — | — |

**Hệ quả:** `fact_job_skill_daily` và `fact_job_market_daily` (trục `job_domain`) **bắt buộc** phải đọc Parquet `observations`, không thể chỉ dùng E. Còn `canonical_job_level`/`canonical_work_mode` cấp entity **chỉ** có trong SQLite registry. Gold vì thế cần **reader hai nguồn**, và phải chốt nguồn nào thắng khi mâu thuẫn (Q-G09).

### 2. Sáu Gap chặn Gold — phải xử lý trước khi code Gold

| Mã Gap | Vấn đề | Hệ quả nếu bỏ qua | Hướng xử lý bắt buộc |
|---|---|---|---|
| **GAP-1** | ✅ **ĐÃ SỬA.** Trước đây chưa có `silver_release_id`; manifest chỉ ở cấp partition. | Gold không thể "đọc đúng một release", sẽ quét thư mục ngày và suy ra trạng thái hiện hành — đúng điều `Trienkhai.md` §2.1 cấm. | Đã thêm `BronzeToSilverPipeline.seal_silver_release()` + `SilverWriter.write_release_manifest()`. Mỗi phiên ghi `data/silver/releases/<silver_release_id>.json` (và mirror lên MinIO), chứa `status`, `manifest_fingerprint`, `snapshot_cutoff_at`, `taxonomy_versions`, `metrics` và danh sách object kèm `sha256`/`records`. Report Silver nay trả về `silver_release_id`, `silver_release_status`, `manifest_fingerprint`. |
| **GAP-2** | **`publish_entities` ghi SQLite `gold_entities`**, trong khi kiến trúc đã chốt là Gold PostgreSQL. | Gold thật không tồn tại; Power BI không kết nối được; SQLite không chịu được concurrent BI read. | Giữ hàm này làm bridge kiểm thử local; viết loader PostgreSQL thật ở `src/gold/`. Không để BI đọc SQLite. |
| **GAP-3** | **Chưa có database `lakehouse` trên VPS**, PostgreSQL hiện chỉ có DB `airflow`. | Không có nơi chứa 12 bảng Gold. | Tạo DB + role Gold read-only, không dùng role `airflow`. **Đã xác minh 18/11/2026:** VPS chỉ có `postgres` và `airflow`; `listen_addresses = localhost`; disk còn 45GB; RAM 9.7GB (6.4GB available). |
| **GAP-4** | **Đã giảm mức độ.** Parquet `entities` 16 trường vẫn không có `job_level`/`work_mode`, nhưng SQLite `entity_registry` **đã có** `canonical_job_level` và `canonical_work_mode`. Riêng `job_domain` và `skills_standardized` vẫn **chỉ** có ở Parquet `observations`. | Nếu Gold chỉ đọc Parquet E thì `dim_job` thiếu level/work_mode. Nếu chỉ đọc SQLite thì thiếu domain/skill. | Gold bắt buộc dùng **reader hai nguồn** (xem §1.1) và chốt Q-G09. |
| **GAP-5** | ✅ **ĐÃ SỬA.** `is_primary_source` trước đây gán `= (match_status == 'DISTINCT_ENTITY')`, tức "observation đã tạo entity", không phải "observation tốt nhất". | Chính sách "ưu tiên `is_primary_source` khi duy nhất" của G07 sẽ chọn sai observation đại diện. | `bronze_to_silver.process()` nay tính observation canonical bằng chính `choose_canonical()` rồi mới set `is_primary_source`. **Lưu ý ngữ nghĩa còn lại:** link là bản ghi audit bất biến nên cờ này **time-scoped** — link cũ vẫn giữ `True` nếu nó từng là canonical. Gold phải lấy link được chấp nhận **mới nhất** theo `matched_at` cho mỗi entity. |
| **GAP-6** | ✅ **ĐÃ SỬA.** `SKILL_TAXONOMY` ~130 kỹ năng đã có nhưng không có version; quy tắc tên công ty và city cũng vậy. | `dim_skill`/`dim_company` đổi mapping sẽ âm thầm viết lại lịch sử báo cáo. | Đã thêm `SKILL_TAXONOMY_VERSION`, `COMPANY_NORMALIZATION_VERSION`, `CITY_NORMALIZATION_VERSION`, `TRANSFORM_VERSION` và hàm `normalization_versions()` trong `transformers.py`. Các version này được ghi vào report Silver và vào release manifest, nên Gold chỉ việc chép vào `gold_control.load_run`. |
| **GAP-7** | **Hai nguồn chân lý song song cho cùng thuộc tính entity.** `canonical_description`, salary, level, work_mode tồn tại ở cả Parquet E và SQLite `entity_registry`, không có cơ chế đối soát. | Gold có thể nạp hai giá trị khác nhau tùy reader; báo cáo không tái lập được. | Chốt Q-G09: đề xuất **SQLite registry là nguồn cấp entity hiện hành**, Parquet E là bản công bố/đối soát; thêm DQ so khớp hai nguồn. |
| **GAP-8** | ✅ **ĐÃ SỬA.** `job_domain` trước đây chỉ sinh cho 2/9 nền tảng. | Trục ngành của `fact_job_market_daily` sẽ là `UNKNOWN` cho ~7/9 nền tảng → biểu đồ "ngành nào nhiều tin nhất" vô nghĩa. | `canonicalize_bronze_record` nay dùng chuỗi fallback `job_domain → job_industry → industries → company_industry → company_field`, phủ cả 9 nền tảng. Có 4 test kiểm chứng từng nhánh. **Việc còn lại:** đo tỷ lệ phủ thật ở G01 (DQ-25). |
| **GAP-9** | **`city_normalized` chỉ có 4 giá trị.** `normalize_city()` map về `Hà Nội`, `Hồ Chí Minh`, `Đà Nẵng`, hoặc `Khác` (mọi tỉnh khác); `NULL` khi nguồn thiếu city. | DDL Gold đang dùng `DEFAULT 'UNKNOWN'` sẽ không khớp giá trị thật `Khác`; dễ nhầm `Khác` (đã xác định là tỉnh khác) với `UNKNOWN` (thiếu dữ liệu). | Chốt trục city Gold = 5 nhóm tường minh: `Hà Nội`, `Hồ Chí Minh`, `Đà Nẵng`, `Khác`, `UNKNOWN`. Không gộp `Khác` vào `UNKNOWN`. |
| **GAP-10** | **`observations.is_active` là trường DẪN XUẤT, không phải cờ từ sàn.** Code thật: `is_active = not expired_date or expired_date >= observed_at[:10]`. | Định nghĩa Q-G04 nói `is_active` là "cờ quan sát tức thời từ API/Web của sàn" — **không đúng với code**. Kết luận vẫn giữ (không dùng làm KPI) nhưng lý do khác: nó chỉ là "chưa quá `expired_date` tại thời điểm quan sát". | Sửa lại glossary Gold cho đúng bản chất; không dùng `is_active` làm tử số `open_job_count` (đã ràng buộc bằng `DQ-23`). |

### 3. Điều kiện tiên quyết (phải xong trước G04)

```text
P-01  Silver chạy thật ít nhất một phiên với LAKEHOUSE_SILVER_ENABLED=true.
P-02  ✅ ĐÃ XONG (GAP-1): seal_silver_release sinh silver_release_id,
      manifest COMPLETE + manifest_fingerprint + object list có sha256.
P-03  GAP-3: PostgreSQL database `lakehouse` và role gold_writer/gold_reader.
P-04  Chốt hợp đồng metric (Giai đoạn 6): D-G05 đã chốt open_job_count;
      các metric còn lại vẫn cần đủ cohort, tử số, mẫu số và ví dụ tính tay.
P-05  ✅ ĐÃ XONG (D-G02): match_status được Gold chấp nhận.
P-06  ✅ ĐÃ XONG: timezone Asia/Ho_Chi_Minh, cutoff theo release.
P-07  ✅ ĐÃ XONG (GAP-6): 4 hằng số version + normalization_versions().
P-08  Đo được N_d (số unified_job_id hợp lệ/ngày) từ dữ liệu thật ở G01.
P-09  ✅ ĐÃ XONG (GAP-8): job_domain phủ 9/9 nền tảng; còn phải đo tỷ lệ phủ thật.
P-10  GAP-9: chốt trục city 5 nhóm, phân biệt `Khác` với `UNKNOWN`
      (đã ràng buộc bằng chk_market_city và DQ-26).
P-11  ✅ ĐÃ XONG (D-G06): SQLite registry thắng, Parquet E đối soát, DQ-24 chặn lệch.
```

**Chỉ còn P-03 chặn việc viết DDL.** P-04, P-08, P-10 cần dữ liệu thật của một phiên Silver để hoàn tất. Mọi con số kích thước trong tài liệu này là ước lượng cho tới khi G01 đo được `N_d` thật.

### 4. Trạng thái kiểm thử đã chạy thật (18/11/2026)

```text
Bộ test Silver đã chạy: 8 file, 55 test
    test_silver_contracts.py
    test_silver_registry_resolver.py
    test_silver_quality_lifecycle.py
    test_silver_writer.py
    test_bronze_to_silver.py
    test_silver_gold_publish.py
    test_silver_lifecycle_acceptance.py
    test_silver_release_and_domain.py   ← mới, 10 test cho các fix đợt này

Kết quả: 55 PASS / 0 FAIL
```

Test duy nhất từng fail (`test_entities_partition_by_canonical_posted_date`) là **lỗi portable trên Windows, không phải lỗi logic Silver**: test assert chuỗi `year=2026/month=11/day=10` trong khi path Windows dùng `\`. Đã sửa bằng `Path.as_posix()` nên chạy đúng trên cả Windows và Ubuntu.

10 test mới khóa các hành vi Gold phụ thuộc:

```text
TestJobDomainCoverage          4 test: job_domain / industries / company_industry / None
TestNormalizationVersions      1 test: 4 hằng số version
TestSilverReleaseSealing       3 test: manifest COMPLETE + object list + sha256,
                                       fingerprint deterministic khi replay,
                                       is_primary_source đánh đúng observation canonical
TestObservedAtProvenance       2 test: observed_at đóng băng từ Bronze metadata
```

### 5. Những gì đã xác minh là ĐÚNG, Gold có thể dựa vào

```text
Resolver đủ 4 rule theo đúng thứ tự thiết kế:
    SOURCE_JOB_ID → CANONICAL_URL → EXACT_NORMALIZED_IDENTITY → FUZZY
False-positive guard đã hiện thực thật (không chỉ trên giấy):
    xung đột level (intern↔senior/lead/director, fresher↔senior/lead/director)
    xung đột work_mode (full-time↔part-time)
    company similarity < 55
    city khác nhau và không bên nào remote
    khoảng lương không giao nhau
    title generic (developer/engineer/manager/software developer/software engineer)
REVIEW_REQUIRED: unified_job_id = NULL, có candidate_entity_id, ghi pending_review.csv,
    không tạo entity — đúng D-03 và đúng test T-12.
silver_entity_links có PK (observation_id, resolution_version)
    → đúng khóa "quyết định hiệu lực mỗi observation theo version" mà Gold cần ở G05.
silver_observation_registry có quality_accepted, observed_at, salary, city_normalized,
    job_description_clean → đủ để chọn observation đại diện cho dim_job.
EXPIRED chỉ được gán từ CLOSED/STALE và chỉ khi có bằng chứng hết hạn tường minh;
    code ghi rõ "never infer expiry from absence" → xác nhận D-G05 loại EXPIRED là đúng.
partial_run không bao giờ đánh dấu STALE/CLOSED → khớp Partial-Run Semantics.
enforce_publish_gate chặn khi quarantine > 5%, schema drift, hoặc coverage thiếu.
```

### 6. Một trạng thái thiết kế có nhưng code chưa từng sinh ra

```text
MERGED và SPLIT có trong VALID_ENTITY_STATUSES của quality_gate,
nhưng KHÔNG có bất kỳ đường code nào gán hai giá trị này.
Trạng thái thực tế code sinh ra: NEW, ACTIVE, UPDATED, STALE, CLOSED, REOPENED, EXPIRED.
```

Hệ quả: **Q-G05 hiện chưa có dữ liệu để kiểm chứng**. Gold vẫn phải xử lý phòng vệ (không đếm vào `open_job_count`, giữ lineage), nhưng không thể viết acceptance test thật cho MERGED/SPLIT cho tới khi Silver có đường merge/split.

---

## GIAI ĐOẠN 1: PHẠM VI, ĐƠN VỊ ĐO VÀ QUY TẮC ĐỌC SILVER

### 1. Ba đơn vị đo không được hoán đổi

```text
Tin hợp nhất (unified job)  = một unified_job_id.
Tin nguồn (source job)       = một cặp (platform, source_job_id).
Observation                  = một lần hệ thống ghi nhận một tin nguồn.
```

Ba đơn vị này **không được cộng, không được đổi tên cho nhau** trên dashboard. Ví dụ: một tin hợp nhất có thể có 3 tin nguồn và 45 observation; báo cáo "số tin tuyển dụng" chỉ được dùng `unified_job_id` distinct.

### 2. Hợp đồng đọc Silver

| Tập Silver | Nội dung Gold dùng | Khóa | Ràng buộc |
|---|---|---|---|
| `entities` (E) | canonical title/company/location/salary, `first_seen_at`, `last_seen_at`, `entity_status`, `source_count`, `observation_count` | `unified_job_id` | Bảng hiện hành, **không tự cung cấp lịch sử trạng thái** |
| `entity_links` (L) | bằng chứng nối observation↔tin hợp nhất, quyết định match, `matched_at`, `resolution_version` | `observation_id` → `unified_job_id` | Chỉ dùng quyết định **hiệu lực tại cutoff**; gỡ phiên link cũ |
| `observations` (O) | platform, `source_job_id`, lương, `skills_standardized`, `job_domain`, `job_level`, `work_mode`, city, thời điểm quan sát | `observation_id` | `source_job_id` chỉ duy nhất trong từng platform |
| `quarantine` | **Không vào Gold nghiệp vụ** | — | Chỉ dùng đối soát vận hành |

**Quy tắc join bắt buộc:**

```text
E ← L ← O sau khi đã gỡ các phiên liên kết cũ.
Không join toàn bộ lịch sử L vào E.
L chỉ đóng góp khi match_status được chấp nhận VÀ unified_job_id NOT NULL.
```

### 3. Trạng thái link được Gold chấp nhận — ĐÃ CHỐT (D-G02 / Q-G02)

| `match_status` | `unified_job_id` | Gold có dùng không | Lý do |
|---|---|---|---|
| `AUTO_MATCH` | NOT NULL | **Có** | Liên kết đã được chấp nhận |
| `MANUAL_MATCH` | NOT NULL | **Có** | Kỹ sư đã xác nhận |
| `DISTINCT_ENTITY` | NOT NULL | **Có** | Entity mới được tạo, có ID canonical |
| `REVIEW_REQUIRED` | NULL | **Không** | Chưa gắn job; chỉ vào hàng chờ review |
| `UNMATCHED` | NULL | **Không** | Không đủ dữ liệu định danh |

Ràng buộc này khớp `quality_gate.validate_link`: link `REVIEW_REQUIRED`/`UNMATCHED` **không được** có `unified_job_id`. Nếu Gold thấy vi phạm, đó là lỗi dữ liệu phải chặn publish.

### 4. Thời gian và cutoff

```text
Mọi timestamp nguồn parse về UTC.
snapshot_date xác định theo Asia/Ho_Chi_Minh (Asia/Bangkok).
Mọi phép so sánh trong một release dùng cùng một snapshot_cutoff_at.
Không dùng quyết định link sinh sau cutoff để sửa lịch sử ngày cũ.
```

**Đã xác minh — `observed_at` an toàn để làm khóa cutoff.** Hermes đã thêm `_metadata.observed_at` vào `MetadataEnvelope.wrap()`, lấy từ `raw_payload.observed_at` hoặc `raw_payload.scraped_at` **tại thời điểm bọc Bronze**. `canonicalize_bronze_record` ưu tiên trường này:

```python
observed_at = metadata.observed_at or payload.observed_at or payload.scraped_at or metadata.ingested_at
```

Hệ quả tốt cho Gold: `observed_at` **bất biến theo Bronze**, không phụ thuộc thời điểm chạy Silver, nên `observation_id` và cutoff đều idempotent khi replay. Gold có thể tin `observed_at` làm trục thời gian mà không lo lệch giữa các lần chạy lại. Có 2 test khóa hành vi này (`TestObservedAtProvenance`).

### 5. Ngày thiếu dữ liệu

```text
Có Silver release COMPLETE  → snapshot_date = LOADED
Không có release hợp lệ     → snapshot_date = MISSING
```

**Ngày `MISSING` không được ghi KPI bằng 0.** BI phải hiển thị khoảng trống, không hiển thị "0 tin".

### 6. Ngoài phạm vi Gold

Silver hiện chưa có view, search, save, apply, ATS stage, candidate profile, invoice/payment, recommendation impression. Vì vậy **không tạo**:

```text
fact_job_view, fact_search, fact_application,
fact_revenue, fact_match_score,
KPI View→Apply, Time-To-Hire, MRR/ARR, retention ứng viên.
```

`entity_links.match_score` là **điểm ghép trùng tin nguồn**, tuyệt đối không dùng làm điểm phù hợp ứng viên–việc làm.
`observations.vacancies_count` có default = 1 trong thiết kế nguồn, nên **chưa dùng làm KPI số vị trí tuyển chính thức**.

### 7. Ngữ nghĩa `entity_status` và `observations.is_active` — ĐÃ CHỐT (D-G05 / Q-G04)

Quyết định đã được chủ thiết kế Gold chốt ngày 18/11/2026:

> Crawler ghi nhận `is_active = True` là **cờ quan sát tức thời** (point-in-time observation flag từ API/Web của sàn). Metric "Số tin đang tuyển" chỉ được tính toán hợp lệ **tại tầng Canonical Entities (Gold)** dựa trên `entity_status`, nơi đã loại bỏ trùng lặp đa nguồn và áp dụng cơ chế suy diễn đóng tin theo Grace Period.

**Điều chỉnh theo code thật (GAP-10):** kiểm tra `canonicalize_bronze_record` cho thấy `is_active` **không được đọc từ API/Web của sàn**, mà được Silver dẫn xuất:

```python
is_active = not expired_date or expired_date >= observed_at[:10]
```

Nghĩa là nó chỉ diễn đạt "tin chưa vượt `expired_date` tại thời điểm quan sát". Kết luận của quyết định **không thay đổi** (vẫn không dùng làm KPI "đang tuyển"), nhưng lý do mạnh hơn: `is_active` vừa là trường dẫn xuất, vừa ở cấp observation đơn lẻ, nên càng không thể đại diện cho trạng thái của một tin hợp nhất đa nguồn. Glossary Power BI phải ghi đúng bản chất này.

Hệ quả triển khai bắt buộc:

```text
observations.is_active:
    Là trường DẪN XUẤT: not expired_date OR expired_date >= observed_at[:10].
    KHÔNG phải cờ do sàn trả về.
    KHÔNG được dùng làm tử số cho metric "số tin đang tuyển".
    KHÔNG được OR/AND gộp qua nhiều observation để suy ra trạng thái tin.
    Chỉ dùng làm ngữ cảnh bằng chứng nguồn và đối soát.

entity_status (tầng E, đã dedup + đã áp dụng Grace Period):
    Là nguồn DUY NHẤT hợp lệ để suy ra "tin đang tuyển".
    Metric open_job_count được tính tại Gold từ entity_status.
```

**Tập trạng thái được đếm là tin đang tuyển:** `quality_gate.VALID_ENTITY_STATUSES` trong repo có **9** giá trị, không chỉ `CLOSED`. Vì vậy quy tắc "`entity_status != 'CLOSED'`" cần được cụ thể hóa như sau:

| `entity_status` | Nghĩa vận hành | Literal `!= CLOSED` | Tập khuyến nghị (D-G05) | Lý do |
|---|---|---|---|---|
| `NEW` | Lần đầu hệ thống thấy | đếm | **đếm** | Tin mới, đang tuyển |
| `ACTIVE` | Vẫn đang được quan sát | đếm | **đếm** | Đang tuyển |
| `UPDATED` | Nội dung thay đổi | đếm | **đếm** | Đang tuyển, có sửa |
| `REOPENED` | Đã đóng rồi xuất hiện lại | đếm | **đếm** | Đang tuyển trở lại |
| `STALE` | Chưa thấy gần đây, **chưa** quá Grace Period | đếm | **đếm** | Grace Period tồn tại đúng để tránh đóng nhầm |
| `CLOSED` | Đã quá Grace Period | không đếm | **không đếm** | Đã suy diễn là đóng |
| `EXPIRED` | Hết hạn đăng theo nguồn | **đếm** | **không đếm** | Tin hết hạn không phải "đang tuyển" |
| `MERGED` | Đã gộp vào entity khác | **đếm** | **không đếm** | Đếm sẽ **trùng** với entity đích, làm tăng ảo |
| `SPLIT` | Đã tách entity | **đếm** | **không đếm** | Trạng thái kỹ thuật, không phải trạng thái tuyển |

**Quy tắc được ghi vào tài liệu này (chờ bạn xác nhận một lần):**

```sql
open_job_count = 1
    WHEN entity_status IN ('NEW', 'ACTIVE', 'UPDATED', 'REOPENED', 'STALE')
ELSE 0
```

Nếu giữ đúng literal `entity_status != 'CLOSED'`, ba trạng thái `EXPIRED`, `MERGED`, `SPLIT` sẽ bị đếm là "đang tuyển". `MERGED` nguy hiểm nhất vì làm **đếm trùng** cùng một tin ở cả entity nguồn và entity đích, phá vỡ kiểm tra `DQ-03` (row count khớp E hiệu lực) và làm sai mọi biểu đồ tổng quan. Đây là điểm duy nhất trong bốn quyết định cần bạn xác nhận lại.

---

## GIAI ĐOẠN 2: QUYẾT ĐỊNH CÔNG NGHỆ VÀ KIẾN TRÚC LƯU TRỮ GOLD

### 1. Quyết định D-G01 — Gold lưu trên PostgreSQL (ĐÃ CHỐT theo Q-G01)

`gold-business-design-v0.6.docx` đề xuất Apache Iceberg trên MinIO. `Trienkhai.md` §2.2 cho phép rõ: *"Trường hợp hạ tầng chưa hỗ trợ Iceberg, dùng Parquet/SQL mart tạm thời nhưng giữ nguyên khóa, hợp đồng dữ liệu và cơ chế release; không tuyên bố đã có giao dịch Iceberg."*

Đối chiếu với hiện trạng repo và VPS:

| Tiêu chí | PostgreSQL (ĐÃ CHỐT cho MVP) | Iceberg trên MinIO |
|---|---|---|
| Đã có trong kiến trúc chốt | Có (`Silver Parquet → Gold PostgreSQL → Power BI`) | Chưa |
| Hạ tầng VPS hiện có | PostgreSQL đã cài cho Airflow | Cần catalog + engine MERGE (Spark/Trino) chưa có |
| RAM VPS (đo thực tế 18/11/2026: 9.7GB total, 6.4GB available) | Đủ cho mart quy mô N_d/ngày | Spark/Trino + JVM sẽ cạnh tranh RAM với Airflow, MinIO, 9router |
| Publish nguyên tử xuyên 12 bảng | **Có thật** — một transaction + đổi con trỏ | Không; docx thừa nhận Iceberg không bảo đảm |
| Power BI | Connector PostgreSQL chuẩn, DirectQuery được | Cần Trino/Spark SQL, phải kiểm chứng |
| MERGE/upsert theo grain | `INSERT ... ON CONFLICT` | Cần engine MERGE |
| Chi phí kiểm chứng G03 | Thấp | Cao |

**ĐÃ CHỐT (Q-G01, 18/11/2026):** MVP dùng PostgreSQL database `lakehouse`, schema `gold` (12 bảng) + schema `gold_control` (điều khiển release). Giữ nguyên toàn bộ khóa, grain, hợp đồng metric và cơ chế release của docx để khi scale-up sang Iceberg chỉ đổi tầng lưu trữ, không đổi ngữ nghĩa.

**Điều kiện chuyển sang Iceberg:** khi tổng dòng fact vượt khả năng PostgreSQL đo được thực tế, hoặc khi cần time-travel đa engine. Lúc đó phải chạy lại G03 và giữ `gold_release_id` làm khóa phiên.

### 2. Quyết định D-G02 — Publish bằng con trỏ `active_release` trong một transaction

```text
1. Nạp toàn bộ staging + dim + fact với gold_release_id mới.
2. Chạy DQ; nếu fail → giữ active_release cũ, đánh dấu release FAILED.
3. Nếu pass → trong MỘT transaction:
     UPDATE gold_control.active_release SET gold_release_id = <new>, switched_at = now();
4. Refresh BI sau khi con trỏ đã đổi.
```

BI **luôn** đọc qua view lọc theo `active_release`, không đọc bảng fact trực tiếp. Nhờ vậy BI không bao giờ trộn hai release.

### 3. Quyết định D-G03 — DAG Gold riêng, không nhồi vào DAG Bronze/Silver

DAG hiện tại `daily_nine_platform_bronze_incremental` đã dùng ngân sách timeout: crawler 45 phút, Silver 60 phút, `dagrun_timeout` 4 giờ, `max_active_runs=1`, `max_active_tasks=3`. Thêm Gold vào sẽ phá vỡ ngân sách này.

```text
DAG mới: daily_gold_mart_build
Schedule: 0 5 * * * (Asia/Ho_Chi_Minh), sau DAG Bronze/Silver 02:00
Cảm biến: chờ silver release COMPLETE của cùng logical date
max_active_runs = 1
max_active_tasks = 2
dagrun_timeout = 90 phút
```

### 4. Quyết định D-G04 — Power BI truy cập qua SSH tunnel

```text
Power BI Desktop → SSH tunnel → 127.0.0.1:5433 → PostgreSQL VPS:5432 → DB lakehouse
Role: gold_reader (SELECT-only trên schema gold + gold_control)
Không mở 5432 ra Internet. Không dùng role airflow.
```

SSIS chỉ là adapter tùy chọn, không nằm trong pipeline cốt lõi.

---

## GIAI ĐOẠN 3: SCHEMA GOLD — 5 DIMENSION

### 1. Tổng quan dimension

| Dimension | Khóa | Grain | Nguồn | Chính sách |
|---|---|---|---|---|
| `dim_date` | `date_key` | Một ngày lịch | Sinh từ lịch | Không phụ thuộc Silver; lịch `Asia/Ho_Chi_Minh` |
| `dim_job` | `job_key` | Một **phiên** thuộc tính của `unified_job_id` | E + O đại diện qua L | SCD2 từ ngày Gold go-live; đúng một phiên current/job |
| `dim_company` | `company_key` | Một định danh tên công ty chuẩn hóa | O.`company_name_normalized` | Không fuzzy merge; không khẳng định pháp nhân |
| `dim_platform` | `platform_key` | Một nền tảng nguồn | Danh mục 9 nguồn + O/L | Giữ `unknown` để rà soát mã mới |
| `dim_skill` | `skill_key` | Một mã skill trong một `taxonomy_version` | O.`skills_standardized` | Không tự sinh mã từ text tự do |

### 2. DDL PostgreSQL

```sql
-- dim_date: sinh một lần cho toàn bộ khoảng vận hành
CREATE TABLE gold.dim_date (
    date_key      INTEGER PRIMARY KEY,          -- YYYYMMDD
    full_date     DATE    NOT NULL UNIQUE,
    year          SMALLINT NOT NULL,
    quarter       SMALLINT NOT NULL,
    month         SMALLINT NOT NULL,
    week_of_year  SMALLINT NOT NULL,
    day_of_week   SMALLINT NOT NULL,
    is_weekend    BOOLEAN  NOT NULL,
    timezone_name TEXT     NOT NULL DEFAULT 'Asia/Ho_Chi_Minh'
);

-- dim_company: khóa theo tên chuẩn hóa + version thuật toán
CREATE TABLE gold.dim_company (
    company_key                 TEXT PRIMARY KEY,   -- sha256(company_name_normalized|version)
    company_name_normalized     TEXT NOT NULL,
    company_display_name        TEXT NOT NULL,
    company_alias_raw           TEXT,
    company_industry            TEXT,
    company_size                TEXT,
    normalization_version       TEXT NOT NULL,
    is_unknown                  BOOLEAN NOT NULL DEFAULT FALSE,
    created_at                  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at                  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_dim_company_name ON gold.dim_company(company_name_normalized);

-- dim_platform: danh mục 9 nguồn đã quan sát
CREATE TABLE gold.dim_platform (
    platform_key    SMALLINT PRIMARY KEY,
    platform_code   TEXT NOT NULL UNIQUE,       -- itviec, topcv, ...
    platform_name   TEXT NOT NULL,
    is_active       BOOLEAN NOT NULL DEFAULT TRUE,
    is_unknown      BOOLEAN NOT NULL DEFAULT FALSE
);

-- dim_skill: một mã trong một taxonomy version
CREATE TABLE gold.dim_skill (
    skill_key         TEXT PRIMARY KEY,         -- sha256(skill_code|taxonomy_version)
    taxonomy_version  TEXT NOT NULL,
    skill_code        TEXT NOT NULL,
    skill_name        TEXT NOT NULL,
    skill_group       TEXT,
    is_unknown        BOOLEAN NOT NULL DEFAULT FALSE,
    UNIQUE (taxonomy_version, skill_code)
);

-- dim_job: SCD2, đúng một phiên current cho mỗi unified_job_id
CREATE TABLE gold.dim_job (
    job_key                     TEXT PRIMARY KEY,   -- sha256(unified_job_id|version_seq)
    unified_job_id              TEXT NOT NULL,
    version_seq                 INTEGER NOT NULL,
    canonical_title             TEXT NOT NULL,
    company_key                 TEXT NOT NULL REFERENCES gold.dim_company(company_key),
    canonical_location          TEXT,
    city_normalized             TEXT,
    job_domain                  TEXT,
    job_level                   TEXT,
    work_mode                   TEXT,
    salary_band                 TEXT,               --derived, xem Giai đoạn 5
    canonical_salary_min_vnd    BIGINT,
    canonical_salary_max_vnd    BIGINT,
    canonical_currency          TEXT,
    canonical_posted_date       DATE,
    entity_status               TEXT NOT NULL,
    representative_observation_id TEXT,
    representative_fallback     BOOLEAN NOT NULL DEFAULT FALSE,
    valid_from_release          TEXT NOT NULL,      -- gold_release_id mở phiên
    valid_to_release            TEXT,               -- NULL = đang current
    valid_from_at               TIMESTAMPTZ NOT NULL,
    valid_to_at                 TIMESTAMPTZ,
    is_current                  BOOLEAN NOT NULL DEFAULT TRUE,
    UNIQUE (unified_job_id, version_seq)
);
CREATE UNIQUE INDEX uq_dim_job_current ON gold.dim_job(unified_job_id) WHERE is_current;
CREATE INDEX idx_dim_job_company ON gold.dim_job(company_key);
CREATE INDEX idx_dim_job_status ON gold.dim_job(entity_status);
```

### 3. Quy tắc nạp dimension

**`dim_job` — chọn observation đại diện (G07):**

**Nguyên tắc mới sau khảo sát:** Gold **không được tự chế** tie-break khác Silver. Silver đã có `choose_canonical()` trong `src/etl/entity_lifecycle.py`; Gold phải tái sử dụng đúng thứ tự đó, nếu không `dim_job` và `entities.canonical_*` sẽ chọn hai observation khác nhau cho cùng một tin.

```text
1. Lọc quality_accepted = TRUE (đúng như Silver làm).
2. Sắp theo max() với đúng thứ tự khóa của choose_canonical():
      observed_at
      → length(job_description_clean)
      → salary_min_vnd IS NOT NULL
      → expired_date IS NOT NULL
      → city_normalized IS NOT NULL
      → SOURCE_PRIORITY: itviec(1) → vietnamworks(2) → topcv(3) → khác(99)
      → observation_id            (chốt cuối, bảo đảm deterministic)
3. is_primary_source CHỈ được dùng làm tín hiệu phụ, không phải nguồn chân lý (GAP-5):
   code hiện gán is_primary_source = (match_status == 'DISTINCT_ENTITY'),
   tức là "observation đã tạo ra entity", không phải "observation tốt nhất".
   Nếu observation được chọn KHÔNG phải is_primary_source → representative_fallback = TRUE.
4. E cấp canonical title/company/location/salary/status/description.
5. canonical_job_level và canonical_work_mode lấy từ SQLite entity_registry
   (Parquet E 16 trường không có — GAP-4).
6. job_domain, city_normalized, skills_standardized lấy từ Parquet observations
   (không có ở bất kỳ bảng entity nào).
7. Khi một thuộc tính được theo dõi thay đổi: đóng phiên cũ
   (valid_to_release, valid_to_at, is_current = FALSE) và mở phiên mới.
```

**Điểm thuận lợi đã xác minh:** `silver_observation_registry` chứa đúng và đủ các trường cần cho tie-break ở bước 2 (`observed_at`, `job_description_clean`, `salary_min_vnd`, `expired_date`, `city_normalized`, `platform`, `observation_id`, `quality_accepted`). Nghĩa là **Gold có thể tái lập lựa chọn canonical của Silver chỉ từ SQLite**, không cần đọc Parquet cho việc này — giảm đáng kể chi phí G07.

**Nghiệm thu:** đúng một phiên current mỗi job; phiên không chồng lấn; snapshot ngày cũ vẫn nối được phiên đúng của ngày đó.

**`dim_company`:** sinh key từ tên chuẩn hóa + `normalization_version`, giữ alias gốc để đối soát. Tên giống nhau **chưa chứng minh** cùng pháp nhân. Thay đổi quy tắc gộp phải tăng version và có migration. Không fuzzy merge tự động.

**`dim_skill`:** mapping mới **không được âm thầm viết lại báo cáo lịch sử**; đổi taxonomy là đổi `taxonomy_version` và có chính sách restatement riêng.

**Thành viên `UNKNOWN`:** mọi dimension nghiệp vụ phải có thành viên `UNKNOWN` tường minh khi nguồn thiếu. Không dùng NULL làm nhóm phân tích.

---

## GIAI ĐOẠN 4: SCHEMA GOLD — 4 FACT NỀN TẢNG

Mỗi fact dưới đây được mô tả theo đúng hợp đồng 15 mục của `gold-business-design-v0.6.docx`, rút gọn thành các phần triển khai được: grain, DDL, metric, nguồn Silver, join, partition, refresh, DQ, glossary — kèm **câu hỏi nghiệp vụ tương ứng** mà fact đó phải trả lời.

---

### Fact 1 — `fact_job_daily_snapshot`

**Là gì:** mỗi tin tuyển dụng hợp nhất có một dòng theo ngày snapshot.

**Câu hỏi nghiệp vụ phải trả lời:**

```text
1. Mỗi ngày hệ thống quan sát được bao nhiêu tin tuyển dụng?
2. Có bao nhiêu tin lần đầu xuất hiện trong dữ liệu?
3. Số tin thay đổi thế nào theo ngành, địa điểm và cấp bậc?
```

| Mục | Thiết kế |
|---|---|
| Grain | `snapshot_date × unified_job_id`. Khóa **không** chứa platform hay observation. |
| Metric | `job_count = 1`; `open_job_count` (0/1, suy từ `entity_status` theo D-G05 — **không** dùng `observations.is_active`); `newly_seen_job_count` (NULL tại baseline); salary min/max VND; `source_count`, `observation_count` từ E; `last_seen_at`. |
| Silver vào | E là nguồn chính; L/O chỉ bổ sung thuộc tính đại diện qua `dim_job`. |
| Join | Chọn **một** E hiệu lực mỗi `unified_job_id` tại cutoff; gắn `job_key` SCD2 as-of. Tin mới quan sát = ID chưa có trong snapshot đầy đủ liền trước, **không phải** ngày `posted_date`. |
| Refresh | Hằng ngày sau Silver release COMPLETE; một ngày một cutoff/release đã chốt. |
| SLA | Gold ≤ 2 giờ sau Silver COMPLETE; ngày thiếu release là `MISSING`, không phải 0. |
| Kích thước | `N_d` dòng/ngày, `N_d` = số `unified_job_id` hợp lệ tại cutoff; đo thật ở G01. |
| Partition | Theo tháng `snapshot_date`; index `(snapshot_date, unified_job_id)`. |
| DQ | Unique grain; row count = số E hiệu lực; `salary_min ≤ salary_max`; `job_key` đúng phiên; không có observation tương lai. |
| Glossary | "Tin hợp nhất được quan sát". `newly_seen` = lần đầu **Gold** thấy ID. `source_count` không chứng minh số nền tảng còn đăng. |

```sql
CREATE TABLE gold.fact_job_daily_snapshot (
    gold_release_id        TEXT   NOT NULL,
    snapshot_date          DATE   NOT NULL,
    date_key               INTEGER NOT NULL REFERENCES gold.dim_date(date_key),
    unified_job_id         TEXT   NOT NULL,
    job_key                TEXT   NOT NULL REFERENCES gold.dim_job(job_key),
    company_key            TEXT   NOT NULL REFERENCES gold.dim_company(company_key),
    platform_count_key     SMALLINT,
    posted_date_key        INTEGER REFERENCES gold.dim_date(date_key),
    job_count              SMALLINT NOT NULL DEFAULT 1,
    open_job_count         SMALLINT NOT NULL DEFAULT 0,  -- D-G05: suy từ entity_status
    newly_seen_job_count   SMALLINT,               -- NULL tại ngày baseline
    entity_status          TEXT   NOT NULL,
    canonical_salary_min_vnd BIGINT,
    canonical_salary_max_vnd BIGINT,
    salary_midpoint_vnd    NUMERIC(14,2),
    salary_sample_flag     BOOLEAN NOT NULL DEFAULT FALSE,
    source_count           INTEGER NOT NULL,
    observation_count      INTEGER NOT NULL,
    last_seen_at           TIMESTAMPTZ NOT NULL,
    snapshot_cutoff_at     TIMESTAMPTZ NOT NULL,
    silver_release_id      TEXT   NOT NULL,
    loaded_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (gold_release_id, snapshot_date, unified_job_id),
    CONSTRAINT chk_snapshot_salary_order
        CHECK (canonical_salary_min_vnd IS NULL
               OR canonical_salary_max_vnd IS NULL
               OR canonical_salary_min_vnd <= canonical_salary_max_vnd),
    CONSTRAINT chk_snapshot_job_count CHECK (job_count = 1),
    CONSTRAINT chk_snapshot_open_count CHECK (open_job_count IN (0, 1)),
    CONSTRAINT chk_snapshot_open_status CHECK (
        open_job_count = 0
        OR entity_status IN ('NEW', 'ACTIVE', 'UPDATED', 'REOPENED', 'STALE')
    ),
    CONSTRAINT chk_snapshot_closed_not_open CHECK (
        entity_status NOT IN ('CLOSED', 'EXPIRED', 'MERGED', 'SPLIT')
        OR open_job_count = 0
    )
);
CREATE INDEX idx_snapshot_date ON gold.fact_job_daily_snapshot(snapshot_date);
CREATE INDEX idx_snapshot_release ON gold.fact_job_daily_snapshot(gold_release_id);
```

---

### Fact 2 — `fact_job_platform_daily`

**Là gì:** bằng chứng một tin hợp nhất được quan sát trên một nền tảng trong cửa sổ thời gian đã chọn.

**Câu hỏi nghiệp vụ phải trả lời:**

```text
1. Nền tảng nào có nhiều tin được quan sát nhất?
2. Tin nào xuất hiện trên nhiều nền tảng?
3. Mức độ trùng tin giữa các nền tảng ra sao?
```

| Mục | Thiết kế |
|---|---|
| Grain | `snapshot_date × unified_job_id × platform_key`. |
| Metric | `job_platform_count = 1`; `source_listing_count` distinct; `observation_count_in_window`; `last_observed_at`; `freshness_days`. |
| Silver vào | O + L đã chấp nhận + E hiệu lực. |
| Join | Chọn O mới nhất mỗi `(platform, source_job_id)` trong `evidence_window_days` **= 14 ngày (ĐÃ CHỐT Q-G03)** tại cutoff; nối L hiệu lực theo `observation_id` rồi E theo `unified_job_id`; khử trùng tại grain fact. Giá trị này có version và phải đo lại sau G01. |
| Kích thước | Tối đa ≈ `N_d × P` dòng/ngày, P = số platform có bằng chứng/job; đo ở G01. |
| DQ | Unique grain; FK đến snapshot; platform hợp lệ; `last_observed_at ≤ cutoff`; L không `REVIEW_REQUIRED`/`UNMATCHED`. |
| Glossary | "Nền tảng có bằng chứng quan sát gần đây". Không có observation trong ngày **không chứng minh** tin đã bị gỡ. |

```sql
CREATE TABLE gold.fact_job_platform_daily (
    gold_release_id            TEXT   NOT NULL,
    snapshot_date              DATE   NOT NULL,
    date_key                   INTEGER NOT NULL REFERENCES gold.dim_date(date_key),
    unified_job_id             TEXT   NOT NULL,
    job_key                    TEXT   NOT NULL REFERENCES gold.dim_job(job_key),
    platform_key               SMALLINT NOT NULL REFERENCES gold.dim_platform(platform_key),
    company_key                TEXT   NOT NULL REFERENCES gold.dim_company(company_key),
    job_platform_count         SMALLINT NOT NULL DEFAULT 1,
    source_listing_count       INTEGER NOT NULL,
    observation_count_in_window INTEGER NOT NULL,
    last_observed_at           TIMESTAMPTZ NOT NULL,
    freshness_days             SMALLINT NOT NULL,
    evidence_window_days       SMALLINT NOT NULL,
    snapshot_cutoff_at         TIMESTAMPTZ NOT NULL,
    silver_release_id          TEXT   NOT NULL,
    loaded_at                  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (gold_release_id, snapshot_date, unified_job_id, platform_key),
    CONSTRAINT chk_platform_listing_count CHECK (source_listing_count >= 1),
    CONSTRAINT chk_platform_freshness CHECK (freshness_days >= 0),
    FOREIGN KEY (gold_release_id, snapshot_date, unified_job_id)
        REFERENCES gold.fact_job_daily_snapshot(gold_release_id, snapshot_date, unified_job_id)
);
CREATE INDEX idx_platform_daily_platform ON gold.fact_job_platform_daily(platform_key, snapshot_date);
```

**Lưu ý đối soát:** tổng theo platform **có thể lớn hơn** tổng số job (một job có nhiều nền tảng); nhưng số job distinct có bằng chứng nguồn **không được vượt** số job trong snapshot cùng cohort. `source_count` từ E chỉ dùng đối soát, không mặc nhiên là số nền tảng còn trực tuyến.

---

### Fact 3 — `fact_job_skill_daily`

**Là gì:** các kỹ năng chuẩn hóa được nhắc đến trong từng tin hợp nhất theo ngày.

**Câu hỏi nghiệp vụ phải trả lời:**

```text
1. Kỹ năng nào xuất hiện trong nhiều tin nhất?
2. Mỗi ngành hoặc cấp bậc thường nhắc đến kỹ năng nào?
3. Xu hướng xuất hiện của một kỹ năng tăng hay giảm theo thời gian?
```

| Mục | Thiết kế |
|---|---|
| Grain | `snapshot_date × unified_job_id × skill_key`. |
| Metric | `job_skill_count = 1`; `supporting_platform_count`; `latest_observed_at`. Tỷ lệ job có skill chuẩn hóa tính ở semantic layer, không lưu ở fact. |
| Silver vào | O.`skills_standardized` + L đã chấp nhận + E hiệu lực + taxonomy đã duyệt. |
| Join | Dùng **cùng tập** O/L hợp lệ với `fact_job_platform_daily`; explode skill; map taxonomy; bỏ rỗng; khử trùng `(snapshot_date, unified_job_id, skill_key)`. |
| DQ | Unique grain; `skill_key` hợp lệ; không skill rỗng; FK đến snapshot; **crawl lặp không tăng job count**. |
| Glossary | "Tin có nhắc đến kỹ năng". Không phải kỹ năng ứng viên, không phải lượt tìm kiếm, không phải yêu cầu bắt buộc đã xác minh. |

```sql
CREATE TABLE gold.fact_job_skill_daily (
    gold_release_id          TEXT   NOT NULL,
    snapshot_date            DATE   NOT NULL,
    date_key                 INTEGER NOT NULL REFERENCES gold.dim_date(date_key),
    unified_job_id           TEXT   NOT NULL,
    job_key                  TEXT   NOT NULL REFERENCES gold.dim_job(job_key),
    skill_key                TEXT   NOT NULL REFERENCES gold.dim_skill(skill_key),
    taxonomy_version         TEXT   NOT NULL,
    job_skill_count          SMALLINT NOT NULL DEFAULT 1,
    supporting_platform_count SMALLINT NOT NULL,
    latest_observed_at       TIMESTAMPTZ NOT NULL,
    snapshot_cutoff_at       TIMESTAMPTZ NOT NULL,
    silver_release_id        TEXT   NOT NULL,
    loaded_at                TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (gold_release_id, snapshot_date, unified_job_id, skill_key),
    CONSTRAINT chk_skill_platform_count CHECK (supporting_platform_count >= 1),
    FOREIGN KEY (gold_release_id, snapshot_date, unified_job_id)
        REFERENCES gold.fact_job_daily_snapshot(gold_release_id, snapshot_date, unified_job_id)
);
CREATE INDEX idx_skill_daily_skill ON gold.fact_job_skill_daily(skill_key, snapshot_date);
```

Skill chưa map được đưa vào **báo cáo coverage**, không coi text tự do là skill chuẩn.

**Bản chất thật của `skills_standardized` (đã xác minh trong `standardize_skills()`):**

```text
Nguồn quét: description + requirements + benefits (đã strip HTML).
KHÔNG phải: danh sách skill tag do sàn cung cấp (cái đó nằm ở skills_raw).
Taxonomy: ~130 kỹ năng cố định trong SKILL_TAXONOMY, khớp bằng word-boundary regex.
Trường hợp đặc biệt: "java" dùng negative lookahead để không khớp "javascript".
```

Hệ quả cho glossary và dashboard: `skill_mentioned_job_count` đo **kỹ năng được nhắc đến trong nội dung tin**, kể cả khi nó xuất hiện ở phần quyền lợi (ví dụ "we use Jira"). Nó **không** đo "kỹ năng bắt buộc phải có". Nhãn BI phải nói rõ điều này, và không được dùng để suy ra yêu cầu tuyển dụng cứng.

Vì `SKILL_TAXONOMY` hiện **không có version** (GAP-6), mọi thay đổi danh sách kỹ năng sẽ âm thầm làm lệch so sánh xu hướng theo thời gian — câu hỏi nghiệp vụ số 3 của Fact 3 ("xu hướng tăng hay giảm") chỉ trả lời được đáng tin sau khi P-07 xong.

---

### Fact 4 — `fact_job_change_event`

**Là gì:** một lần phát hiện tin nguồn thay đổi thông tin nghiệp vụ giữa hai lần quan sát.

**Câu hỏi nghiệp vụ phải trả lời:**

```text
1. Tin nào đã điều chỉnh lương?
2. Tin nào thay đổi kỹ năng, địa điểm hoặc hình thức làm việc?
3. Loại thông tin nào được nhà tuyển dụng cập nhật thường xuyên nhất?
```

| Mục | Thiết kế |
|---|---|
| Grain | Một observation gây thay đổi nghiệp vụ của một `(platform, source_job_id)`. Khóa `(platform, source_job_id, observation_id)`. |
| Metric | `change_event_count = 1`; cờ `title_changed`, `salary_changed`, `location_changed`, `work_mode_changed`, `skills_changed`; `salary_delta_vnd`; `skill_added_count`, `skill_removed_count` khi đủ dữ liệu. |
| Silver vào | Toàn bộ lịch sử O; L để gắn `unified_job_id` tại thời điểm quan sát nếu có quyết định hợp lệ. |
| Join | Sắp O theo `observed_at, observation_id` trong từng tin nguồn; so sánh bộ trường nghiệp vụ chuẩn hóa với observation liền trước. `content_hash` chỉ là **tín hiệu hỗ trợ**: nếu nội dung đổi vì phần mô tả không liên quan, không tạo sự kiện nghiệp vụ. Observation đầu tiên là baseline, **không phải** change. |
| Late arrival | Kích hoạt tính lại timeline của nguồn bị ảnh hưởng; replay idempotent; đánh dấu `revised`. |
| DQ | Unique event; `event_at` đúng `observed_at`; không sinh event từ crawl lặp; không âm thầm bỏ observation đến muộn. |
| Glossary | "Thay đổi phát hiện giữa hai lần quan sát nguồn"; **không** khẳng định thời điểm nhà tuyển dụng thực sự sửa tin. |

```sql
CREATE TABLE gold.fact_job_change_event (
    gold_release_id      TEXT   NOT NULL,
    event_date           DATE   NOT NULL,
    event_date_key       INTEGER NOT NULL REFERENCES gold.dim_date(date_key),
    platform_key         SMALLINT NOT NULL REFERENCES gold.dim_platform(platform_key),
    source_job_id        TEXT   NOT NULL,           -- degenerate dimension
    observation_id       TEXT   NOT NULL,
    previous_observation_id TEXT,
    unified_job_id       TEXT,                      -- NULL nếu chưa có link hiệu lực
    job_key              TEXT REFERENCES gold.dim_job(job_key),
    company_key          TEXT REFERENCES gold.dim_company(company_key),
    change_event_count   SMALLINT NOT NULL DEFAULT 1,
    title_changed        BOOLEAN NOT NULL DEFAULT FALSE,
    salary_changed       BOOLEAN NOT NULL DEFAULT FALSE,
    location_changed     BOOLEAN NOT NULL DEFAULT FALSE,
    work_mode_changed    BOOLEAN NOT NULL DEFAULT FALSE,
    skills_changed       BOOLEAN NOT NULL DEFAULT FALSE,
    salary_delta_vnd     BIGINT,
    salary_old_vnd       BIGINT,
    salary_new_vnd       BIGINT,
    skill_added_count    SMALLINT,
    skill_removed_count  SMALLINT,
    link_resolved        BOOLEAN NOT NULL DEFAULT TRUE,
    is_revised           BOOLEAN NOT NULL DEFAULT FALSE,
    observed_at          TIMESTAMPTZ NOT NULL,
    snapshot_cutoff_at   TIMESTAMPTZ NOT NULL,
    silver_release_id    TEXT   NOT NULL,
    loaded_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (gold_release_id, platform_key, source_job_id, observation_id),
    CONSTRAINT chk_change_event_has_flag CHECK (
        title_changed OR salary_changed OR location_changed
        OR work_mode_changed OR skills_changed
    )
);
CREATE INDEX idx_change_event_date ON gold.fact_job_change_event(event_date);
CREATE INDEX idx_change_event_job ON gold.fact_job_change_event(unified_job_id);
```

Một observation có nhiều trường thay đổi **vẫn chỉ tạo một event** với nhiều cờ và old/new được phép lưu. Thiếu link hiệu lực thì giữ source key, đặt `unified_job_id = NULL` và `link_resolved = FALSE`.

---

## GIAI ĐOẠN 5: BA BẢNG TỔNG HỢP PHỤC VỤ BI

Ba bảng này được tính từ fact nền tảng **cùng `gold_release_id`**. Chúng tăng tốc truy vấn nhưng **không tạo sự kiện nghiệp vụ mới**.

**Quy tắc chống cộng chéo:** BI không được cộng số job qua các nhóm có thể chồng lặp như skill hay platform để ra tổng job. KPI toàn thị trường chỉ lấy từ `fact_job_daily_snapshot`.

---

### Fact 5 — `fact_job_market_daily`

**Là gì:** tổng hợp từ snapshot theo ngày × ngành × địa điểm × cấp bậc.

**Câu hỏi nghiệp vụ:**

```text
1. Ngành và địa điểm nào có nhiều tin tuyển dụng nhất?
2. Phân khúc nào có nhiều tin mới được quan sát?
3. Phân bố lương đăng tuyển theo từng phân khúc, trên bao nhiêu tin có dữ liệu lương?
```

| Mục | Thiết kế |
|---|---|
| Grain | `snapshot_date × job_domain × city/location × job_level`; `UNKNOWN` là nhóm tường minh. |
| Metric | `observed_job_count`; `newly_seen_job_count`; `salary_sample_count`; P25/P50/P75 của `salary_midpoint_vnd` trên mẫu đủ điều kiện. |
| Join | Một job trong snapshot thuộc **đúng một** tổ hợp phân nhóm as-of. Chỉ tính midpoint khi cả min/max VND hợp lệ; loại mẫu thương lượng/thiếu theo metric contract. |
| DQ | Tổng `observed_job_count` qua các nhóm loại trừ nhau = snapshot cohort; `salary_sample_count ≤ observed_job_count`; percentile `NULL` khi không có mẫu. |
| Glossary | "Lương đăng tuyển" = trung điểm khoảng lương, không phải lương thực nhận; **luôn hiển thị cỡ mẫu**. |

```sql
CREATE TABLE gold.fact_job_market_daily (
    gold_release_id      TEXT   NOT NULL,
    snapshot_date        DATE   NOT NULL,
    date_key             INTEGER NOT NULL REFERENCES gold.dim_date(date_key),
    job_domain           TEXT   NOT NULL DEFAULT 'UNKNOWN',
    city_normalized      TEXT   NOT NULL DEFAULT 'UNKNOWN',
    job_level            TEXT   NOT NULL DEFAULT 'UNKNOWN',
    observed_job_count   INTEGER NOT NULL,
    newly_seen_job_count INTEGER,
    salary_sample_count  INTEGER NOT NULL DEFAULT 0,
    salary_p25_vnd       NUMERIC(14,2),
    salary_p50_vnd       NUMERIC(14,2),
    salary_p75_vnd       NUMERIC(14,2),
    salary_avg_vnd       NUMERIC(14,2),
    snapshot_cutoff_at   TIMESTAMPTZ NOT NULL,
    silver_release_id    TEXT   NOT NULL,
    loaded_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (gold_release_id, snapshot_date, job_domain, city_normalized, job_level),
    CONSTRAINT chk_market_city CHECK (
        city_normalized IN ('Hà Nội', 'Hồ Chí Minh', 'Đà Nẵng', 'Khác', 'UNKNOWN')
    ),
    CONSTRAINT chk_market_sample CHECK (salary_sample_count <= observed_job_count),
    CONSTRAINT chk_market_no_negative CHECK (observed_job_count >= 0)
);
CREATE INDEX idx_market_daily_date ON gold.fact_job_market_daily(snapshot_date);
```

Percentile tính bằng `percentile_cont(...) WITHIN GROUP (ORDER BY salary_midpoint_vnd)` trên tập `salary_sample_flag = TRUE`. Nhóm có mẫu nhỏ phải được dán nhãn trong BI, không được suy diễn.

**Cảnh báo thực tế về hai trục phân nhóm (GAP-8 và GAP-9) — bắt buộc đọc trước khi xây dashboard:**

```text
Trục job_domain:
    Code thật chỉ sinh job_domain cho 2/9 nền tảng
    (itviec qua payload.job_domain, vietnamworks qua payload.job_industry).
    Năm nền tảng careerviet/upzi/vietnamdevs/vieclam24h/devwork CÓ trường
    `industries` trong schema contract nhưng transformers KHÔNG đọc.
    → Nếu không sửa P-09, biểu đồ "ngành nào nhiều tin nhất" sẽ dominated
      bởi nhóm UNKNOWN và KHÔNG có giá trị nghiệp vụ.

Trục city:
    normalize_city() chỉ trả về 4 giá trị: Hà Nội, Hồ Chí Minh, Đà Nẵng, Khác.
    Mọi tỉnh thành khác bị gộp vào "Khác"; NULL khi nguồn thiếu city.
    → Trục city Gold là 5 nhóm tường minh:
      Hà Nội / Hồ Chí Minh / Đà Nẵng / Khác / UNKNOWN.
    → KHÔNG được gộp "Khác" vào "UNKNOWN": "Khác" nghĩa là đã xác định
      được địa điểm nhưng không thuộc 3 thành phố lớn; "UNKNOWN" nghĩa là
      nguồn không cung cấp địa điểm. Gộp lại sẽ mất khả năng phân biệt
      "tin ở tỉnh" với "tin thiếu dữ liệu".
```

Đây là lý do `fact_job_market_daily` **chưa nên** được coi là bảng trả lời trọn vẹn câu hỏi "ngành và địa điểm nào có nhiều tin nhất" cho tới khi P-09 xong.

---

### Fact 6 — `fact_company_hiring_daily`

**Là gì:** tổng hợp số tin theo ngày và tên công ty chuẩn hóa.

**Câu hỏi nghiệp vụ:**

```text
1. Công ty nào có nhiều tin tuyển dụng được quan sát nhất?
2. Công ty nào đang tăng số tin mới được quan sát?
3. Một công ty tập trung tuyển ở những địa điểm và ngành nào?
```

| Mục | Thiết kế |
|---|---|
| Grain | `snapshot_date × company_key`. |
| Metric | `observed_job_count`; `newly_seen_job_count`; `distinct_city_count`; `distinct_domain_count`; `salary_sample_count`. |
| Join | Mỗi snapshot job gắn **đúng một** `company_key` as-of; group by; giữ `UNKNOWN` khi không xác định được. |
| DQ | Tổng `observed_job_count` gồm cả `UNKNOWN` khớp snapshot; một job/ngày thuộc đúng một company key. |
| Glossary | "Theo tên công ty chuẩn hóa" — không phải doanh thu, không phải số pháp nhân đã xác minh. Không dùng để xếp hạng khách hàng trả phí. |

```sql
CREATE TABLE gold.fact_company_hiring_daily (
    gold_release_id      TEXT   NOT NULL,
    snapshot_date        DATE   NOT NULL,
    date_key             INTEGER NOT NULL REFERENCES gold.dim_date(date_key),
    company_key          TEXT   NOT NULL REFERENCES gold.dim_company(company_key),
    observed_job_count   INTEGER NOT NULL,
    newly_seen_job_count INTEGER,
    distinct_city_count  SMALLINT NOT NULL DEFAULT 0,
    distinct_domain_count SMALLINT NOT NULL DEFAULT 0,
    salary_sample_count  INTEGER NOT NULL DEFAULT 0,
    snapshot_cutoff_at   TIMESTAMPTZ NOT NULL,
    silver_release_id    TEXT   NOT NULL,
    loaded_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (gold_release_id, snapshot_date, company_key),
    CONSTRAINT chk_company_sample CHECK (salary_sample_count <= observed_job_count)
);
CREATE INDEX idx_company_daily_date ON gold.fact_company_hiring_daily(snapshot_date);
```

**Không dùng số observation làm số tin tuyển dụng.**

---

### Fact 7 — `fact_skill_pair_daily`

**Là gì:** tổng hợp số tin cùng nhắc đến một cặp kỹ năng.

**Câu hỏi nghiệp vụ:**

```text
1. Những cặp kỹ năng nào thường xuất hiện cùng nhau?
2. Cặp kỹ năng nào đang xuất hiện nhiều hơn theo thời gian?
3. Với một kỹ năng cụ thể, kỹ năng nào thường đi kèm nhất?
```

| Mục | Thiết kế |
|---|---|
| Grain | `snapshot_date × cặp (skill_a_key, skill_b_key)` không thứ tự. |
| Metric | `co_mentioned_job_count` distinct. `share_of_skill_a_jobs` tính ở semantic layer với mẫu số rõ. |
| Join | Từ `fact_job_skill_daily` cùng release: lấy skill distinct mỗi job/ngày; tạo tổ hợp với `skill_a_key < skill_b_key`; nhóm và đếm distinct `unified_job_id`. |
| Kích thước | Mỗi job có k skill tạo `k(k−1)/2` cặp trước tổng hợp — **báo cáo job có k bất thường trước** khi tạo tổ hợp lớn. |
| DQ | Không có cặp tự ghép hay đảo chiều; `co_mentioned_job_count ≤` số job có từng skill; không đếm lặp một job. |
| Glossary | "Cặp kỹ năng cùng được nhắc đến trong tin"; không khẳng định cả hai là yêu cầu bắt buộc; không dùng làm điểm match ứng viên. |

```sql
CREATE TABLE gold.fact_skill_pair_daily (
    gold_release_id       TEXT   NOT NULL,
    snapshot_date         DATE   NOT NULL,
    date_key              INTEGER NOT NULL REFERENCES gold.dim_date(date_key),
    skill_a_key           TEXT   NOT NULL REFERENCES gold.dim_skill(skill_key),
    skill_b_key           TEXT   NOT NULL REFERENCES gold.dim_skill(skill_key),
    taxonomy_version      TEXT   NOT NULL,
    co_mentioned_job_count INTEGER NOT NULL,
    snapshot_cutoff_at    TIMESTAMPTZ NOT NULL,
    silver_release_id     TEXT   NOT NULL,
    loaded_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (gold_release_id, snapshot_date, skill_a_key, skill_b_key),
    CONSTRAINT chk_pair_ordered CHECK (skill_a_key < skill_b_key),
    CONSTRAINT chk_pair_positive CHECK (co_mentioned_job_count >= 1)
);
CREATE INDEX idx_skill_pair_a ON gold.fact_skill_pair_daily(skill_a_key, snapshot_date);
```

Ràng buộc `skill_a_key < skill_b_key` loại trừ cả cặp tự ghép (`a = a`) lẫn cặp đảo chiều ngay ở tầng DB.

---

## GIAI ĐOẠN 6: HỢP ĐỒNG METRIC VÀ BẢNG ĐIỀU KHIỂN RELEASE

### 1. Hợp đồng metric phải chốt trước khi code (G02)

| Metric | Cohort | Cách tính | Điều KHÔNG được diễn giải |
|---|---|---|---|
| `observed_job_count` | E hiệu lực tại cutoff | Đếm distinct `unified_job_id` trong snapshot hợp lệ | Không phải toàn bộ tin trên thị trường |
| `open_job_count` | Snapshot row có `entity_status` thuộc tập D-G05 | Tổng `open_job_count` (0/1); suy **duy nhất** từ `entity_status` tầng E | Không dùng `observations.is_active`; không khẳng định tin còn hiển thị trên sàn |
| `newly_seen_job_count` | ID chưa có ở snapshot đầy đủ liền trước | 1 nếu lần đầu xuất hiện sau mốc đó; ngày baseline để `NULL` | Không phải tin vừa được nhà tuyển dụng đăng |
| `platform_covered_job_count` | L chấp nhận + O trong `evidence_window_days` | Distinct `unified_job_id` có bằng chứng nguồn | Không khẳng định tin còn trực tuyến |
| `skill_mentioned_job_count` | O có `skills_standardized` map được taxonomy | Distinct `unified_job_id` có skill chuẩn hóa | Không phải lượt tìm kiếm hay số ứng viên |
| `changed_source_job_count` | O của cùng `(platform, source_job_id)` | Distinct cặp có thay đổi trường nghiệp vụ trong kỳ | Không phải số việc làm mới |
| `salary_midpoint_vnd` | Cả min và max VND hợp lệ, không thương lượng | `(min + max) / 2` | Không phải lương thực nhận |
| `salary_sample_count` | Mỗi nhóm phân tích | Số tin hợp nhất đủ điều kiện tính lương | Không được bỏ khỏi biểu đồ lương |

Mỗi metric khi chốt phải có đủ: **cohort, tử số, mẫu số, nguồn bảng, điều kiện loại trừ, ví dụ tính tay**. Thiếu một trong sáu yếu tố thì chưa được code.

### 2. Bảng điều khiển `gold_control`

```sql
CREATE TABLE gold_control.load_run (
    gold_release_id           TEXT PRIMARY KEY,
    silver_release_id         TEXT NOT NULL,
    silver_manifest_fingerprint TEXT NOT NULL,
    snapshot_date             DATE NOT NULL,
    snapshot_cutoff_at        TIMESTAMPTZ NOT NULL,
    transform_version         TEXT NOT NULL,
    skill_taxonomy_version    TEXT NOT NULL,
    company_normalization_version TEXT NOT NULL,
    evidence_window_days      SMALLINT NOT NULL DEFAULT 14,   -- ĐÃ CHỐT Q-G03
    started_at                TIMESTAMPTZ NOT NULL,
    finished_at               TIMESTAMPTZ,
    status                    TEXT NOT NULL,   -- RUNNING/SUCCESS/FAILED/MISSING
    row_counts                JSONB,
    error_message             TEXT
);

CREATE TABLE gold_control.daily_snapshot (
    snapshot_date        DATE PRIMARY KEY,
    gold_release_id      TEXT,
    silver_release_id    TEXT,
    status               TEXT NOT NULL,        -- LOADED / MISSING / FAILED
    silver_ready_at      TIMESTAMPTZ,
    gold_published_at    TIMESTAMPTZ,
    bi_refreshed_at      TIMESTAMPTZ,
    sla_breached         BOOLEAN NOT NULL DEFAULT FALSE,
    notes                TEXT
);

CREATE TABLE gold_control.active_release (
    id                SMALLINT PRIMARY KEY DEFAULT 1 CHECK (id = 1),
    gold_release_id   TEXT NOT NULL,
    snapshot_date     DATE NOT NULL,
    switched_at       TIMESTAMPTZ NOT NULL,
    previous_release  TEXT
);

CREATE TABLE gold_control.dq_result (
    gold_release_id  TEXT NOT NULL,
    check_code       TEXT NOT NULL,
    check_name       TEXT NOT NULL,
    expected         TEXT,
    actual           TEXT,
    diff             TEXT,
    severity         TEXT NOT NULL,            -- BLOCK / WARN
    passed           BOOLEAN NOT NULL,
    checked_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (gold_release_id, check_code)
);
```

**Yêu cầu truy vết:** mọi row Gold phải mang `gold_release_id` và `silver_release_id`, để có thể truy ngược bất kỳ con số nào trên dashboard về đúng phiên Silver nguồn.

---

## GIAI ĐOẠN 7: STAGING VÀ READER SILVER THEO RELEASE (G05)

### 0. Hợp đồng Silver release — đã có thật trong code, Gold đọc đúng shape này

`seal_silver_release()` ghi `data/silver/releases/<silver_release_id>.json` (mirror lên MinIO bucket `silver` tại key `releases/<silver_release_id>.json`). Đây là hợp đồng Gold phải đọc:

```json
{
  "silver_release_id": "silver_release_<airflow_run_id>",
  "run_id": "<airflow_run_id>",
  "pipeline": "bronze_to_silver_v1",
  "resolution_version": "v1",
  "taxonomy_versions": {
    "skill_taxonomy_version": "skill-taxonomy-v1",
    "company_normalization_version": "company-normalization-v1",
    "city_normalization_version": "city-normalization-v1",
    "transform_version": "bronze-to-silver-transform-v1"
  },
  "snapshot_cutoff_at": "<ISO-8601 UTC>",
  "sealed_at": "<ISO-8601 UTC>",
  "status": "COMPLETE | FAILED",
  "object_count": 12,
  "record_count": 4310,
  "manifest_fingerprint": "<sha256 của danh sách object>",
  "incomplete_object_count": 0,
  "metrics": { "total_observations_processed": 0, "quarantine_rate_pct": 0.0 },
  "failed_platforms": [],
  "partial_run": false,
  "coverage_warning": false,
  "schema_drift_warnings": [],
  "objects": [
    {"dataset": "entities", "partition": "year=2026/month=11/day=10",
     "object_key": "entities/year=2026/month=11/day=10/part-....parquet",
     "path": "...", "records": 120, "sha256": "...", "status": "COMPLETE",
     "manifest_path": "..."}
  ],
  "release_manifest_path": "...",
  "release_object_key": "releases/<silver_release_id>.json",
  "release_manifest_sha256": "..."
}
```

**Quy tắc Gold bắt buộc tuân theo:**

```text
1. CHỈ đọc release có status = "COMPLETE".
2. CHỈ đọc các object nằm trong mảng "objects" của release đó.
   Tuyệt đối không list thư mục ngày để suy ra tập hiệu lực.
3. Verify sha256 và records của từng object trước khi nạp.
4. Lưu manifest_fingerprint vào gold_control.load_run làm lineage.
5. snapshot_cutoff_at của release chính là snapshot_cutoff_at của Gold phiên đó.
6. taxonomy_versions chép thẳng vào gold_control.load_run.
7. incomplete_object_count > 0 hoặc partial_run = true → không publish.
```

`silver_release_id` cũng được trả về trong report Silver (`silver_release_id`, `silver_release_status`, `manifest_fingerprint`, `release_object_count`, `release_record_count`), nên DAG Gold có thể nhận qua XCom thay vì phải đoán đường dẫn.

### 1. staging tables

```sql
CREATE SCHEMA IF NOT EXISTS gold_staging;

CREATE TABLE gold_staging.effective_entity (
    silver_release_id  TEXT NOT NULL,
    unified_job_id     TEXT NOT NULL,
    -- toàn bộ 16 trường E
    source_object_key  TEXT NOT NULL,          -- lineage về object Silver
    PRIMARY KEY (silver_release_id, unified_job_id)
);

CREATE TABLE gold_staging.effective_link (
    silver_release_id  TEXT NOT NULL,
    observation_id     TEXT NOT NULL,
    unified_job_id     TEXT,
    platform           TEXT NOT NULL,
    source_job_id      TEXT NOT NULL,
    match_method       TEXT NOT NULL,
    match_status       TEXT NOT NULL,
    match_score        DOUBLE PRECISION,
    matched_at         TIMESTAMPTZ NOT NULL,
    resolution_version TEXT NOT NULL,
    is_primary_source  BOOLEAN NOT NULL,
    accepted_for_gold  BOOLEAN NOT NULL,
    source_object_key  TEXT NOT NULL,
    PRIMARY KEY (silver_release_id, observation_id)
);

CREATE TABLE gold_staging.source_observation (
    silver_release_id  TEXT NOT NULL,
    observation_id     TEXT NOT NULL,
    platform           TEXT NOT NULL,
    source_job_id      TEXT NOT NULL,
    observed_at        TIMESTAMPTZ NOT NULL,
    -- các trường nghiệp vụ Gold cần
    source_object_key  TEXT NOT NULL,
    PRIMARY KEY (silver_release_id, observation_id)
);

CREATE TABLE gold_staging.conflict_report (
    gold_release_id  TEXT NOT NULL,
    conflict_type    TEXT NOT NULL,   -- DUP_ENTITY / MULTI_PRIMARY / AMBIGUOUS_LINK / FUTURE_OBSERVATION / SOURCE_DIVERGENCE
    entity_or_observation_id TEXT NOT NULL,
    detail           JSONB,
    detected_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

### 2. Quy tắc dựng tập hiệu lực

```text
effective_entity:
    đúng MỘT E hiện hành mỗi unified_job_id tại snapshot_cutoff_at.
    Trùng → ghi conflict DUP_ENTITY và CHẶN publish.

effective_link:
    với mỗi observation_id, lấy quyết định hiệu lực mới nhất
    theo (resolution_version, matched_at) và rule đã duyệt ở G02.
    accepted_for_gold = match_status ∈ {AUTO_MATCH, MANUAL_MATCH, DISTINCT_ENTITY}
                        AND unified_job_id IS NOT NULL.
    Link đa nghĩa → conflict AMBIGUOUS_LINK, chặn publish.

source_observation:
    lấy O đến cutoff; loại observation có observed_at > cutoff
    (ghi conflict FUTURE_OBSERVATION).
    Không dùng quyết định link sinh sau cutoff để sửa lịch sử ngày cũ.
```

### 3. Nguồn đọc cụ thể cho từng staging table (đã xác minh trên code)

| Staging | Nguồn chính | Lý do | Nguồn đối soát |
|---|---|---|---|
| `effective_entity` | SQLite `entity_registry` | Có `canonical_job_level`, `canonical_work_mode`, `last_content_hash` mà Parquet E không có | Parquet `entities` (bản công bố 16 trường) |
| `effective_link` | SQLite `silver_entity_links` | PK `(observation_id, resolution_version)` bảo đảm đúng một quyết định mỗi observation/version — đúng semantics Gold cần | Parquet `entity_links` (có `is_primary_source`, `matcher_version`, `source_url`) |
| `source_observation` (khung) | SQLite `silver_observation_registry` | Có `quality_accepted`, `observed_at`, salary, `city_normalized`, `content_hash` | Parquet `observations` |
| `source_observation` (nghiệp vụ) | **Parquet `observations`** — bắt buộc | `job_domain`, `skills_standardized`, `skills_raw`, `job_title`, `job_level`, `work_mode`, `city`, `is_active`, `exp_years_*`, `vacancies_count` **chỉ tồn tại ở đây** | — |
| Source job ↔ entity | SQLite `source_job_registry` | Nơi **duy nhất** có mapping `(platform, source_job_id) → unified_job_id` cấp tin nguồn; cần cho `fact_job_change_event` | Parquet `entity_links` (platform + source_job_id từng observation) |

**Kết luận triển khai:** Gold cần **cả hai** reader.

```text
Reader SQLite (state_store.db, read-only):
    entity_registry, source_job_registry,
    silver_observation_registry, silver_entity_links
    → dựng effective_entity, effective_link, khung source_observation,
      chọn observation đại diện cho dim_job, và mapping tin nguồn cho change_event.

Reader Parquet (MinIO bucket silver / data/silver):
    observations
    → bổ sung job_domain, skills_standardized, job_level, work_mode, city,
      is_active cho fact_job_skill_daily, fact_job_market_daily, dim_job.
```

Hai reader phải khớp nhau ở `observation_id` và `unified_job_id`. Nếu một `observation_id` có trong Parquet nhưng không có trong SQLite (hoặc ngược lại) → ghi conflict `SOURCE_DIVERGENCE` và **chặn publish**. Đây là kiểm tra mới, chưa có trong thiết kế ban đầu.

`is_primary_source` không có trong SQLite; nếu Gold cần thì suy ra từ `match_status = 'DISTINCT_ENTITY'` (đúng công thức Silver đang dùng) hoặc đọc từ Parquet `entity_links` — nhưng theo GAP-5 thì **không nên coi nó là nguồn chân lý** ở cả hai đường.

### 4. Nghiệm thu G05 không có entity trùng, link đa nghĩa hay observation tương lai đi vào fact; bản ghi `REVIEW_REQUIRED`/`UNMATCHED` **không tự được gắn một job**.

---

## GIAI ĐOẠN 8: THỨ TỰ NẠP, PUBLISH, SLA VÀ LỊCH SỬ (G16)

### 1. Thứ tự nạp bắt buộc

```text
 1. Xác nhận Silver release COMPLETE (manifest + audit + fingerprint)
 2. Staging E/L/O tại snapshot_cutoff_at
 3. dim_date → dim_platform → dim_company → dim_skill
 4. dim_job (SCD2, chọn observation đại diện)
 5. fact_job_daily_snapshot            ← phải có trước 3 fact còn lại (FK)
 6. fact_job_platform_daily
 7. fact_job_skill_daily
 8. fact_job_change_event
 9. fact_job_market_daily
10. fact_company_hiring_daily
11. fact_skill_pair_daily              ← sau fact_job_skill_daily
12. Data quality → publish gold_release_id → đổi active_release → refresh BI
```

Một release lỗi **không được** đổi con trỏ BI sang dữ liệu dở.

### 2. Publish và idempotency

```text
Mọi bảng nạp theo gold_release_id.
Retry cùng gold_release_id phải idempotent:
    DELETE FROM gold.<table> WHERE gold_release_id = ? trước khi nạp lại,
    hoặc INSERT ... ON CONFLICT DO UPDATE theo đúng PK đã khai báo.
Backfill/revision tạo gold_release_id MỚI, giữ lineage và bản cũ theo retention.
Không sửa tại chỗ một release đã công bố.
```

### 3. SLA và retention

```text
Gold công bố ≤ 2 giờ sau Silver release COMPLETE.
BI refresh ≤ 3 giờ sau Silver release COMPLETE.
Ghi silver_ready_at, gold_published_at, bi_refreshed_at vào daily_snapshot.
Vượt SLA → sla_breached = TRUE và bắn cảnh báo.
Retention dữ liệu nóng 24 tháng là đề xuất ban đầu; phải chốt với bên quản trị dữ liệu.
```

**Đây là SLA đề xuất, chưa phải cam kết** — phải đo thời gian thực tế trước khi công bố chính thức.

---

## GIAI ĐOẠN 9: DATA QUALITY VÀ ĐỐI SOÁT (G15)

### 1. Bộ kiểm tra và ngưỡng chặn

| Mã | Kiểm tra | Mức |
|---|---|---|
| `DQ-01` | PK unique trên cả 12 bảng | **BLOCK** |
| `DQ-02` | FK logic: fact platform/skill → snapshot; dim_job → dim_company | **BLOCK** |
| `DQ-03` | `row_count(fact_job_daily_snapshot)` = số E hiệu lực trong release | **BLOCK** |
| `DQ-04` | `salary_min ≤ salary_max` trên mọi bảng có lương | **BLOCK** |
| `DQ-05` | `job_key` đúng phiên tại `snapshot_date` | **BLOCK** |
| `DQ-06` | Không có `observed_at`/`matched_at` > `snapshot_cutoff_at` | **BLOCK** |
| `DQ-07` | Không có link `REVIEW_REQUIRED`/`UNMATCHED` trong fact | **BLOCK** |
| `DQ-08` | Đúng một phiên `dim_job.is_current` mỗi `unified_job_id` | **BLOCK** |
| `DQ-09` | Tổng `observed_job_count` nhóm loại trừ nhau = snapshot cohort | **BLOCK** |
| `DQ-10` | `salary_sample_count ≤ observed_job_count` | **BLOCK** |
| `DQ-11` | Tổng job theo `company_key` (gồm UNKNOWN) = snapshot | **BLOCK** |
| `DQ-12` | Không cặp skill tự ghép/đảo chiều | **BLOCK** |
| `DQ-13` | `co_mentioned_job_count ≤` số job có từng skill | **BLOCK** |
| `DQ-14` | `fact_job_change_event` PK `(platform, source_job_id, observation_id)` unique | **BLOCK** |
| `DQ-15` | Observation đầu tiên không bị gọi là change | **BLOCK** |
| `DQ-16` | Lineage: mọi row có `silver_release_id` truy được về manifest | **BLOCK** |
| `DQ-17` | Null coverage title/company/city/domain/skill/salary trong ngưỡng | WARN |
| `DQ-18` | Tỷ lệ skill chưa map taxonomy | WARN |
| `DQ-19` | Tỷ lệ `representative_fallback = TRUE` trong `dim_job` | WARN |
| `DQ-20` | Crawl lặp không làm tăng job count theo skill | **BLOCK** |
| `DQ-21` | `SUM(open_job_count) ≤ SUM(job_count)` trên mọi nhóm | **BLOCK** |
| `DQ-22` | Không row nào có `entity_status ∈ {CLOSED, EXPIRED, MERGED, SPLIT}` mà `open_job_count = 1` | **BLOCK** |
| `DQ-23` | `open_job_count` không được suy từ `observations.is_active` (kiểm tra code + lineage) | **BLOCK** |
| `DQ-24` | Mọi `observation_id`/`unified_job_id` phải khớp giữa reader SQLite và reader Parquet (`SOURCE_DIVERGENCE`) | **BLOCK** |
| `DQ-25` | Trục `job_domain`: tỷ lệ `UNKNOWN` phải được ghi nhận và báo cáo; vượt ngưỡng đã duyệt thì cảnh báo dashboard | WARN |
| `DQ-26` | Trục city chỉ nhận 5 giá trị tường minh; không có giá trị lạ ngoài `{Hà Nội, Hồ Chí Minh, Đà Nẵng, Khác, UNKNOWN}` | **BLOCK** |

**Chính sách chặn:** release FAIL thì `active_release` giữ nguyên, BI vẫn đọc bản cũ, ngày đó ghi `FAILED`. Không bao giờ đổi `MISSING` thành 0. Mọi sai lệch ngoài ngưỡng đã duyệt phải có nguyên nhân hoặc chặn phát hành.

### 2. Các kịch bản khó bắt buộc phải test

```text
Ngày thiếu Silver release                 → MISSING, không fact row mới
Silver partial (thiếu platform)            → không publish, không suy coverage
Link đổi phiên giữa hai release            → dùng quyết định hiệu lực tại cutoff
entity_status MERGED / SPLIT / EXPIRED     → open_job_count = 0, không đếm vào tin đang tuyển (D-G05)
Crawl lặp cùng nội dung                    → không tăng job/skill count
Observation đến muộn (late arrival)        → replay timeline, đánh dấu revised
Một tin nguồn đổi nội dung nhiều lần       → mỗi observation một event, không trùng
Công ty đổi tên                            → phiên dim_company mới, không viết lại lịch sử
Salary thiếu / thương lượng                → loại khỏi mẫu lương, vẫn đếm vào observed
Skill chưa map                             → vào báo cáo coverage, không sinh skill_key
```

---

## GIAI ĐOẠN 10: SEMANTIC MODEL VÀ POWER BI (G17)

### 1. View công bố cho BI

BI **chỉ** đọc các view này, không đọc bảng fact trực tiếp:

```sql
CREATE VIEW gold.v_active_release AS
    SELECT gold_release_id, snapshot_date FROM gold_control.active_release WHERE id = 1;

CREATE VIEW gold.v_job_snapshot AS
    SELECT s.* FROM gold.fact_job_daily_snapshot s
    JOIN gold_control.active_release a ON a.id = 1
     AND a.gold_release_id = s.gold_release_id;
-- tương tự cho v_job_platform, v_job_skill, v_change_event,
-- v_market_daily, v_company_hiring, v_skill_pair
```

### 2. Nhãn thuật ngữ bắt buộc trên dashboard

| Thuật ngữ hiển thị | Định nghĩa |
|---|---|
| Tin hợp nhất được quan sát | Một `unified_job_id` trong snapshot hợp lệ; phản ánh tập nguồn hệ thống thu thập, **không phải toàn thị trường** |
| Tin mới được quan sát | ID lần đầu xuất hiện sau snapshot Gold đầy đủ trước đó; **không phải** ngày nhà tuyển dụng đăng |
| Tin đang tuyển | Tin hợp nhất có `entity_status` thuộc tập đang tuyển (D-G05); suy từ Grace Period ở tầng canonical. **Không** dùng `observations.is_active` vì đó là trường dẫn xuất từ `expired_date` ở cấp một lần crawl |
| Nguồn có bằng chứng gần đây | Có observation/link hợp lệ trong cửa sổ đã duyệt; **không xác nhận** listing còn trực tuyến |
| Kỹ năng được nhắc đến | Có skill chuẩn hóa trong tin nguồn hợp lệ; **không phải** năng lực ứng viên |
| Lương đăng tuyển | Trung điểm khoảng lương chuẩn VND; percentile trên mẫu đủ điều kiện, luôn kèm `salary_sample_count` |
| Theo tên công ty chuẩn hóa | Nhóm theo tên đã chuẩn hóa; **không phải** pháp nhân đã xác minh hay doanh thu |

### 3. Dashboard tối thiểu

```text
1. Tổng quan thị trường      ← fact_job_market_daily + snapshot
2. Độ phủ nền tảng            ← fact_job_platform_daily
3. Kỹ năng và cặp kỹ năng     ← fact_job_skill_daily + fact_skill_pair_daily
4. Công ty tuyển dụng         ← fact_company_hiring_daily
5. Biến động tin nguồn        ← fact_job_change_event
```

**Quy tắc KPI:** KPI toàn thị trường lấy từ snapshot. Không cộng `job_platform_count` hay `job_skill_count` qua các nhóm để ra tổng job.

### 4. Ví dụ truy vấn trả lời đúng câu hỏi nghiệp vụ

```sql
-- Fact 1 / Q1: Mỗi ngày hệ thống quan sát được bao nhiêu tin?
SELECT snapshot_date, SUM(job_count) AS observed_jobs
FROM gold.v_job_snapshot GROUP BY snapshot_date ORDER BY snapshot_date;

-- Fact 1 / Q2: Bao nhiêu tin lần đầu xuất hiện?
SELECT snapshot_date, SUM(newly_seen_job_count) AS newly_seen
FROM gold.v_job_snapshot
WHERE newly_seen_job_count IS NOT NULL
GROUP BY snapshot_date;

-- D-G05: Số tin đang tuyển, suy từ entity_status, KHÔNG dùng is_active
SELECT snapshot_date, SUM(open_job_count) AS open_jobs
FROM gold.v_job_snapshot GROUP BY snapshot_date ORDER BY snapshot_date;

-- Fact 2 / Q2: Tin nào xuất hiện trên nhiều nền tảng?
SELECT unified_job_id, COUNT(DISTINCT platform_key) AS platforms
FROM gold.v_job_platform GROUP BY unified_job_id HAVING COUNT(DISTINCT platform_key) > 1;

-- Fact 3 / Q1: Kỹ năng nào xuất hiện trong nhiều tin nhất?
SELECT d.skill_name, COUNT(DISTINCT f.unified_job_id) AS jobs
FROM gold.v_job_skill f JOIN gold.dim_skill d USING (skill_key)
GROUP BY d.skill_name ORDER BY jobs DESC LIMIT 20;

-- Fact 4 / Q1: Tin nào đã điều chỉnh lương?
SELECT event_date, platform_key, source_job_id, salary_old_vnd, salary_new_vnd, salary_delta_vnd
FROM gold.v_change_event WHERE salary_changed ORDER BY event_date DESC;

-- Fact 5 / Q3: Phân bố lương theo phân khúc, kèm cỡ mẫu
SELECT job_domain, city_normalized, job_level,
       observed_job_count, salary_sample_count,
       salary_p25_vnd, salary_p50_vnd, salary_p75_vnd
FROM gold.v_market_daily WHERE snapshot_date = CURRENT_DATE;

-- Fact 7 / Q3: Với một kỹ năng, kỹ năng nào thường đi kèm nhất?
SELECT d.skill_name, p.co_mentioned_job_count
FROM gold.fact_skill_pair_daily p JOIN gold.dim_skill d ON d.skill_key = p.skill_b_key
WHERE p.skill_a_key = :skill_key AND p.snapshot_date = CURRENT_DATE
ORDER BY p.co_mentioned_job_count DESC;
```

**Nghiệm thu G17:** mỗi KPI đối soát được bằng SQL ở cùng ngày/release; người dùng phân biệt được **số tin**, **số tin nguồn**, **số lần crawl** và **số kỹ năng**.

---

## GIAI ĐOẠN 11: TÍCH HỢP AIRFLOW

```text
DAG: daily_gold_mart_build          schedule 0 5 * * *  Asia/Ho_Chi_Minh
     max_active_runs = 1            max_active_tasks = 2
     dagrun_timeout = 90 phút       catchup = False

 gold_preflight          Kiểm tra PostgreSQL, quyền ghi, disk, kết nối MinIO
        ↓
 wait_silver_release     Cảm biến chờ silver_release COMPLETE của cùng logical date
        ↓
 stage_silver_release    Dựng effective_entity / effective_link / source_observation
        ↓
 load_dimensions         dim_date, dim_platform, dim_company, dim_skill
        ↓
 load_dim_job            SCD2 + chọn observation đại diện
        ↓
 load_fact_snapshot      fact_job_daily_snapshot
        ↓
 load_fact_platform      fact_job_platform_daily
 load_fact_skill         fact_job_skill_daily
 load_fact_change        fact_job_change_event
        ↓ [ALL_SUCCESS]
 load_bi_aggregates      market_daily, company_hiring_daily, skill_pair_daily
        ↓
 run_data_quality        20 check DQ, ghi gold_control.dq_result
        ↓
 publish_gold_release    Một transaction đổi active_release (chỉ khi DQ pass)
        ↓
 gold_pipeline_summary   Ghi daily_snapshot, đo SLA, cảnh báo
```

**Ràng buộc vận hành:**

```text
Timeout từng task nạp fact: 20 phút.
Timeout toàn DAG: 90 phút.
Không dùng role PostgreSQL của Airflow cho Gold.
Không ghi secret vào code; đọc từ ~/airflow/airflow.env hoặc .env đã git-ignore.
Task publish chỉ chạy khi run_data_quality trả PASS toàn bộ check BLOCK.
```

**Chỉ kích hoạt DAG Gold sau khi toàn bộ acceptance test Giai đoạn 12 PASS 100%.**

---

## GIAI ĐOẠN 12: ĐIỀU KIỆN NGHIỆM THU — 18 ACCEPTANCE TESTS GOLD

| STT | Mã | Kịch bản | Kết quả mong đợi |
|---|---|---|---|
| 1 | `TG-01` | Nạp một release với `N_d` E hiệu lực | `fact_job_daily_snapshot` có đúng `N_d` dòng; PK unique; `SUM(open_job_count)` khớp số E có status thuộc tập D-G05 |
| 2 | `TG-02` | Chạy lại cùng `gold_release_id` | Số dòng không đổi — idempotent |
| 3 | `TG-03` | Ngày không có Silver release | `daily_snapshot.status = MISSING`; không có fact row mới; KPI không phải 0 |
| 4 | `TG-04` | Silver partial, thiếu 2 platform | Không publish; `active_release` giữ bản cũ |
| 5 | `TG-05` | Link `REVIEW_REQUIRED` với `unified_job_id` NULL | Không xuất hiện trong bất kỳ fact nào |
| 6 | `TG-06` | Observation có `observed_at > cutoff` | Bị loại, ghi `FUTURE_OBSERVATION` vào conflict_report |
| 7 | `TG-07` | Hai E cùng `unified_job_id` trong một release | Chặn publish với conflict `DUP_ENTITY` |
| 8 | `TG-08` | `dim_job` sau khi title thay đổi | Đúng một phiên `is_current`; phiên cũ có `valid_to_release` |
| 9 | `TG-09` | Snapshot ngày cũ truy vấn lại | Nối đúng phiên `job_key` của ngày đó, không phải phiên hiện tại |
| 10 | `TG-10` | Crawl lặp cùng nội dung | `fact_job_skill_daily` không tăng job count |
| 11 | `TG-11` | Một job có 3 nền tảng | 3 dòng `fact_job_platform_daily`; snapshot vẫn 1 dòng |
| 12 | `TG-12` | `salary_min > salary_max` | Chặn bởi `chk_snapshot_salary_order` |
| 13 | `TG-13` | Nhóm không có mẫu lương | Percentile NULL; `salary_sample_count = 0`; không suy ra 0 đồng |
| 14 | `TG-14` | Observation đầu tiên của một tin nguồn | Không tạo `fact_job_change_event` |
| 15 | `TG-15` | Tin nguồn đổi lương giữa hai observation | Đúng 1 event, `salary_changed = TRUE`, `salary_delta_vnd` đúng |
| 16 | `TG-16` | Observation đến muộn | Replay timeline, không tạo event trùng, đánh dấu `is_revised` |
| 17 | `TG-17` | Skill pair cho job có 4 skill | Đúng 6 cặp, không cặp đảo chiều hay tự ghép |
| 18 | `TG-18` | Publish thành công rồi BI đọc | BI chỉ thấy một `gold_release_id`; không trộn hai release |

---

## GIAI ĐOẠN 13: LỘ TRÌNH TRIỂN KHAI THEO STEP G00–G17

| Step | Nội dung | File dự kiến | Nghiệm thu |
|---|---|---|---|
| G00 | Khảo sát repo/môi trường | Tài liệu này, Giai đoạn 0 | Xác định được cách đọc một release Silver và cách publish mà không hardcode credential |
| G01 | Profiling Silver bằng dữ liệu thật | `docs/silver-profile.md` | Dựng được tập E/L/O hiệu lực cho một release; đối soát row count với manifest; đo được `N_d` |
| G02 | Chốt hợp đồng nghiệp vụ | `gold-metric-contract.yml` | Mỗi metric có cohort, tử số, mẫu số, nguồn, loại trừ, ví dụ tính tay |
| G03 | Kiểm chứng công nghệ Gold | `docs/gold-tech-validation.md` | Chạy lại cùng input không nhân dòng; lỗi trước publish không lộ dữ liệu dở |
| G04 | Control + schema Gold | `src/gold/ddl.sql`, `src/gold/schema.py` | Test unique grain; truy được mọi row về release nguồn |
| G05 | Reader **kép** (SQLite registry + Parquet observations), dựng tập hiệu lực, phát hiện `SOURCE_DIVERGENCE` | `src/gold/silver_release_reader.py` | Không entity trùng/link đa nghĩa/observation tương lai; hai reader khớp nhau |
| G06 | Dimension đơn giản | `src/gold/load_dimensions.py` | Rerun không đổi key; mã chưa map được báo cáo |
| G07 | `dim_job` SCD2 | `src/gold/load_dim_job.py` | Đúng một phiên current; phiên không chồng lấn |
| G08 | `fact_job_daily_snapshot` | `src/gold/load_snapshot_fact.py` | Row count = E hiệu lực; PK unique; không số âm |
| G09 | `fact_job_platform_daily` | `src/gold/load_platform_fact.py` | Unique grain; mọi row có snapshot job |
| G10 | `fact_job_skill_daily` | `src/gold/load_skill_fact.py` | Crawl lặp không tăng job count |
| G11 | `fact_job_change_event` | `src/gold/load_change_event.py` | PK unique; observation đầu không là change |
| G12 | `fact_job_market_daily` | `src/gold/load_market_aggregate.py` | Tổng nhóm loại trừ nhau = cohort |
| G13 | `fact_company_hiring_daily` | `src/gold/load_company_aggregate.py` | Tổng gồm UNKNOWN khớp snapshot |
| G14 | `fact_skill_pair_daily` | `src/gold/load_skill_pair.py` | Không cặp đảo chiều/tự ghép |
| G15 | Data quality + đối soát | `src/gold/data_quality.py` | Release fail giữ BI ở bản cũ |
| G16 | Publish, lịch sử, SLA | `src/gold/release_controller.py`, `dags/dag_gold_mart_build.py` | Retry idempotent; BI không trộn release |
| G17 | Semantic model + nghiệm thu nghiệp vụ | `src/gold/semantic_views.sql`, `docs/gold-user-guide.md` | Mỗi KPI đối soát SQL cùng ngày/release |

### Checklist triển khai

| STT | Hạng mục | Module | Trạng thái |
|---|---|---|---|
| 1 | ✅ Sửa GAP-1: `seal_silver_release` + `write_release_manifest` | `src/etl/bronze_to_silver.py`, `src/etl/silver_writer.py` | ☑ Xong, có test |
| 2 | Tạo database `lakehouse` + role `gold_writer`, `gold_reader` | `infrastructure/postgres/create_gold_db.sh` | ☐ |
| 3 | DDL 5 dim + 7 fact + 4 control + staging | `src/gold/ddl.sql` | ☐ |
| 4 | **Reader kép**: SQLite registry + Parquet observations, dựng tập hiệu lực, phát hiện `SOURCE_DIVERGENCE` | `src/gold/silver_release_reader.py` | ☐ |
| 5 | Loader dimension | `src/gold/load_dimensions.py` | ☐ |
| 6 | Loader `dim_job` SCD2, tái dùng đúng tie-break `choose_canonical()` | `src/gold/load_dim_job.py` | ☐ |
| 7 | Loader 4 fact nền tảng | `src/gold/load_*.py` | ☐ |
| 8 | Loader 3 bảng tổng hợp BI | `src/gold/load_*_aggregate.py` | ☐ |
| 9 | 26 check DQ + ghi `dq_result` | `src/gold/data_quality.py` | ☐ |
| 10 | Release controller + publish transaction | `src/gold/release_controller.py` | ☐ |
| 11 | Semantic views + nhãn thuật ngữ | `src/gold/semantic_views.sql` | ☐ |
| 12 | DAG `daily_gold_mart_build` | `dags/dag_gold_mart_build.py` | ☐ |
| 13 | 18 acceptance tests Gold | `tests/test_gold_mart.py` | ☐ |
| 14 | Cấu hình Power BI qua SSH tunnel | `docs/powerbi-gold-connection.md` | ☐ |
| 15 | ✅ GAP-6: 4 hằng số version + `normalization_versions()` | `src/etl/transformers.py` | ☑ Xong, có test |
| 16 | ✅ GAP-8: `job_domain` fallback `industries`/`company_industry`/`company_field` | `src/etl/transformers.py` | ☑ Xong, 4 test |
| 17 | GAP-2: chuyển publish sang PostgreSQL, ngưng dùng SQLite `gold_entities` | `src/utils/publish_silver_for_gold.py` | ☐ |
| 18 | ✅ Sửa test portable Windows bằng `Path.as_posix()` | `tests/test_bronze_to_silver.py` | ☑ Xong |
| 19 | ✅ GAP-5: `is_primary_source` tính từ `choose_canonical()` thật | `src/etl/bronze_to_silver.py` | ☑ Xong, có test |

---

## CÁC QUYẾT ĐỊNH ĐÃ CHỐT (CONFIRMED DECISIONS)

Chủ thiết kế Gold chốt ngày 18/11/2026. Các quyết định này là ràng buộc khi triển khai G04 trở đi.

| Mã | Quyết định | Nội dung đã chốt |
|---|---|---|
| **D-G01** (Q-G01) | **Nơi lưu trữ Gold** | **PostgreSQL** database `lakehouse`, schema `gold` + `gold_control`. Iceberg chỉ là lộ trình scale-up, giữ nguyên khóa, grain và hợp đồng release. |
| **D-G02** (Q-G02) | **`match_status` được Gold chấp nhận** | `AUTO_MATCH`, `MANUAL_MATCH`, `DISTINCT_ENTITY` (điều kiện `unified_job_id NOT NULL`). Loại `REVIEW_REQUIRED` và `UNMATCHED`. |
| **D-G03** (Q-G03) | **`evidence_window_days`** | **14 ngày**, có version, đo và hiệu chỉnh lại sau G01 khi có dữ liệu thật. |
| **D-G05** (Q-G04) | **Ngữ nghĩa `is_active` và `entity_status`** | `observations.is_active` = cờ quan sát tức thời của một lần crawl, **không** dùng làm metric. "Số tin đang tuyển" (`open_job_count`) chỉ được suy tại tầng Gold từ `entity_status` của canonical entity, nơi đã dedup đa nguồn và đã áp dụng Grace Period. |
| **D-G06** (Q-G09) | **Nguồn nào thắng khi Parquet E và SQLite registry lệch** | **SQLite `entity_registry` thắng** cho thuộc tính cấp entity (vì giàu hơn: có `canonical_job_level`, `canonical_work_mode`, `last_content_hash`, và là nơi resolver ghi trực tiếp trong cùng transaction). Parquet `entities` là **bản công bố** dùng để đối soát và lineage. `DQ-24` so khớp hai nguồn và **chặn publish** khi lệch. |
| **D-G07** (Q-G10) | **`job_domain` phải phủ 9 nền tảng** | Đã sửa `canonicalize_bronze_record` dùng chuỗi fallback `job_domain → job_industry → industries → company_industry → company_field`. Trục ngành của `fact_job_market_daily` vì thế mới có nghĩa. |

### Điểm còn phải xác nhận trong D-G05

Quy tắc gốc là `entity_status != 'CLOSED'`. Repo thực tế có 9 giá trị trạng thái, nên tài liệu này dùng tập tường minh:

```text
open_job_count = 1 khi entity_status ∈ {NEW, ACTIVE, UPDATED, REOPENED, STALE}
open_job_count = 0 khi entity_status ∈ {CLOSED, EXPIRED, MERGED, SPLIT}
```

Lý do phải loại thêm `MERGED`: nếu đếm, cùng một tin bị đếm ở cả entity nguồn và entity đích, làm tăng ảo số tin đang tuyển và phá `DQ-03`. Lý do loại `EXPIRED`: tin đã hết hạn đăng không phải tin đang tuyển. Xem bảng đầy đủ ở Giai đoạn 1 §7.

## CÁC QUYẾT ĐỊNH CÒN LẠI — CHỐT SAU (KHÔNG CHẶN BẮT ĐẦU)

| Mã | Câu hỏi | Khuyến nghị hiện tại |
|---|---|---|
| **Q-G05** | Xử lý `MERGED`/`SPLIT` chi tiết trong báo cáo lịch sử? | **Hiện chưa có dữ liệu để chốt.** Đã xác minh: hai trạng thái này có trong `VALID_ENTITY_STATUSES` nhưng **không đường code nào sinh ra chúng**. MVP: `open_job_count = 0`, giữ lineage, viết xử lý phòng vệ nhưng không thể test thật. |
| **Q-G06** | `is_primary_source` có phải nguồn chân lý cho observation đại diện? | **Không** (GAP-5). Dùng lại đúng tie-break `choose_canonical()` của Silver + cờ `representative_fallback`. |
| **Q-G07** | Retention dữ liệu nóng? | Đề xuất 24 tháng, chốt với bên quản trị dữ liệu. |
| **Q-G08** | ✅ Đã xử lý: `is_primary_source` đã được sửa để phản ánh observation canonical thật. | Còn lại một ràng buộc cho Gold: cờ này **time-scoped** trên link bất biến, nên phải lấy link mới nhất theo `matched_at`. |
| **Q-G11** | Trục city có nên mở rộng ngoài 4 nhóm hiện tại? | Chưa. MVP giữ 5 nhóm tường minh (`Hà Nội`, `Hồ Chí Minh`, `Đà Nẵng`, `Khác`, `UNKNOWN`). Mở rộng `CITY_ALIASES` là thay đổi Silver, làm sau khi có dữ liệu thật. |

---

## GIỚI HẠN CỦA THIẾT KẾ NÀY

```text
1. Mọi con số kích thước là ước lượng cho tới khi G01 đo được N_d thật.
2. Gold không có lịch sử trước ngày go-live: không dựng lại trạng thái cũ
   từ một bản E hiện hành.
3. Gold chỉ đo nguồn cung hệ thống quan sát được, không phải toàn thị trường.
4. Không có fact ứng viên, lượt xem, ứng tuyển, doanh thu, recommendation.
5. match_score không dùng cho đề xuất việc làm.
6. vacancies_count (default 1) chưa dùng làm KPI số vị trí tuyển.
7. SLA 2 giờ / 3 giờ là đề xuất, chưa phải cam kết đo được.
8. Tài liệu này phụ thuộc GAP-1 và GAP-3 được xử lý; nếu chưa, Gold không chạy được.
9. Trục job_domain đã phủ 9/9 nền tảng nhờ chuỗi fallback, nhưng tỷ lệ phủ
   THẬT vẫn chưa đo; phải đo ở G01 và báo cáo qua DQ-25.
10. Trục city chỉ có 5 nhóm; mọi tỉnh ngoài 3 thành phố lớn bị gộp vào "Khác".
11. MERGED/SPLIT chưa có đường code nào sinh ra, nên không thể viết
    acceptance test thật cho hai trạng thái này.
12. Thuộc tính entity tồn tại ở hai nguồn (Parquet E và SQLite registry).
    Đã chốt D-G06 (SQLite thắng) và có DQ-24 chặn khi lệch, nhưng rủi ro
    lệch vẫn phải được giám sát liên tục.
13. skills_standardized là kết quả text-mining trên mô tả/yêu cầu/quyền lợi,
    không phải skill tag do sàn cung cấp, nên không suy ra được
    "kỹ năng bắt buộc".
14. is_primary_source là cờ time-scoped trên link bất biến; Gold phải lấy
    link mới nhất theo matched_at, không được lấy bất kỳ link nào có cờ True.
```

---

## BẰNG CHỨNG PHẢI TRẢ SAU MỖI STEP

```text
File đã sửa.
Lệnh đã chạy và kết quả thật.
silver_release_id và gold_release_id.
Manifest fingerprint.
Số dòng đầu vào/đầu ra theo đúng grain.
DQ pass/fail từng check.
Bước tiếp theo.
```

Không ghi secret hoặc raw payload nhạy cảm vào bằng chứng. **Không đánh dấu step hoàn thành chỉ vì code chạy hết mà chưa đối soát dữ liệu.**

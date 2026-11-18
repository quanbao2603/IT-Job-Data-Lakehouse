# Thiết kế Chi tiết Kiến trúc Tầng Bạc (Silver Layer) — IT Job Data Lakehouse

> **Dự án:** IT Job Data Lakehouse  
> **Phạm vi:** 9 nền tảng tuyển dụng IT (`careerviet`, `upzi`, `vietnamdevs`, `vieclam24h`, `devwork`, `itviec`, `topcv`, `topdev`, `vietnamworks`).  
> **Nguồn dữ liệu vào:** `s3://bronze/` — Dữ liệu trích xuất thô định dạng `.jsonl.gz` bọc Metadata Envelope.  
> **Đầu ra:** `s3://silver/` — Bộ ba bảng Parquet phân vùng Hive-style (`observations/`, `entities/`, `entity_links/`).  
> **Nguyên tắc cốt lõi:** *"Làm sạch một lần, dùng được mãi. Silver không xóa observation lịch sử. Silver tạo canonical entity và lưu liên kết giữa entity với các observation nguồn."*

---

## GIAI ĐOẠN 1: THIẾT KẾ HẠ TẦNG LƯU TRỮ TẦNG BẠC (SILVER STORAGE ARCHITECTURE)

**Mục tiêu:** Quy hoạch namespace lưu trữ độc lập, phân tách rạch ròi 3 lớp dữ liệu: Lịch sử quan sát nguồn → Thực thể việc làm hợp nhất → Bằng chứng liên kết kiểm toán.

### 1. Kiến trúc 5-Prefix (Observation–Entity–Link Model)

Toàn bộ dữ liệu Silver được lưu trữ trong bucket `s3://silver/` trên MinIO, quy hoạch thành 5 không gian tên (prefix) độc lập:

```text
s3://silver/
├── observations/                          ← Lớp 1: Toàn bộ quan sát chuẩn hóa từ từng nguồn (46 trường canonical)
│   └── platform=itviec/
│       └── year=2026/month=11/day=15/
│           └── part-0.parquet
│
├── entities/                              ← Lớp 2: Danh mục tin tuyển dụng hợp nhất (Canonical Entities - 16 trường)
│   └── year=2026/month=11/day=15/
│       └── part-0.parquet
│
├── entity_links/                          ← Lớp 3: Sổ cái liên kết chứng minh Entity ↔ Observation (Audit Trail - 16 trường)
│   └── year=2026/month=11/day=15/
│       └── part-0.parquet
│
├── quarantine/                            ← Vùng kiểm dịch bản ghi vi phạm Quality Gate (50 trường)
│   └── platform={platform}/year=.../month=.../day=.../
│       └── part-0.parquet
│
└── audit_logs/                            ← Nhật ký kiểm toán và báo cáo chất lượng phiên ETL
    └── run_{YYYYMMDD_HHMMSS}.json
```

**Ý nghĩa kiến trúc:**
- `observations/`: Lưu trữ toàn bộ lịch sử quan sát đã chuẩn hóa từ từng nguồn. **Tuyệt đối không xóa observation vì lý do trùng lặp.**
- `entities/`: Chứa bản ghi chính tắc (Canonical Record) đại diện cho vị trí việc làm hợp nhất trên toàn hệ thống. Đây là nguồn dữ liệu chính cho Tầng Vàng (Gold Layer).
- `entity_links/`: Lưu quan hệ giữa canonical entity và các observation nguồn. Lưu vết chi tiết rule matching, điểm số tương đồng, candidate_entity_id và trạng thái rà soát (16 trường) — đóng vai trò bằng chứng giải trình (Audit Trail).

### 2. Chiến lược Phân vùng Thời gian

| Không gian lưu trữ | Cột phân vùng thời gian | Bản chất phân vùng | Lý do kỹ thuật & nghiệp vụ |
|---|---|---|---|
| `observations/` | `observed_at` (ngày cào) | Phân vùng Kỹ thuật (Technical) | Luôn có sẵn, không bao giờ null, bảo toàn tính bất biến của dữ liệu theo ngày chạy ETL. |
| `entities/` | `canonical_posted_date` (ngày đăng đại diện) | Phân vùng Nghiệp vụ (Business) | Phục vụ trực tiếp Tầng Gold phân tích xu hướng thị trường lao động theo đúng thời điểm phát hành tin. |
| `entity_links/` | `matched_at` (thời điểm matching) | Phân vùng Kiểm toán (Audit) | Theo dõi lịch sử đưa ra quyết định gộp tin của thuật toán matching qua các phiên. |

> **Lý do KHÔNG dùng `canonical_posted_date` làm partition duy nhất cho `observations/`:**
> 1. Tin cũ có thể được cập nhật lương hôm nay → `posted_date` sẽ gán vào partition tháng trước.
> 2. Nhiều nguồn cào không có trường `posted_date` → partition bị null.
> 3. `posted_date` từ nguồn có thể sai hoặc bị thay đổi giữa các lần cào.
> 4. Cùng entity xuất hiện ở nhiều ngày quan sát → phân vùng theo `posted_date` gây trùng lặp partition.

### 3. Định dạng Lưu trữ Cột hóa

- **Định dạng:** Apache Parquet — cột hóa (columnar), hỗ trợ nén từ điển (dictionary encoding), predicate pushdown và tương thích tối đa với DuckDB, PySpark, Polars.
- **Thuật toán nén:** Snappy — cân bằng hoàn hảo giữa tốc độ giải nén I/O và tỷ lệ nén trên ổ NVMe VPS.
- **Ràng buộc cấu trúc:** Áp dụng định kiểu tĩnh (Strict PyArrow Schema) khi ghi dữ liệu.

---

## GIAI ĐOẠN 2: HỢP ĐỒNG DỮ LIỆU — SCHEMA 3 LỚP (SILVER DATA CONTRACTS)

**Mục tiêu:** Định nghĩa hợp đồng dữ liệu chuẩn xác cho ba lớp dữ liệu chính và vùng kiểm dịch: 46 trường canonical cho `observations/`, 16 trường cho `entities/`, 16 trường cho `entity_links/` và 50 trường cho `quarantine/`.

### 1. Schema Lớp `observations/` (46 Trường Canonical Nghiệp Vụ)

Định nghĩa chặt chẽ trong `src/schemas/silver_schema.py` bằng PyArrow. **Tổng số trường nghiệp vụ là đúng 46 trường**:

| Nhóm | # | Tên Trường | Kiểu dữ liệu | Nullable | Mô tả nghiệp vụ |
|---|---|---|---|---|---|
| **Nhóm 1: Định danh & Nguồn** | 1 | `source_job_id` | string | ❌ | ID gốc từ sàn (MANDATORY) |
| | 2 | `observation_id` | string | ❌ | SHA-256 có delimiter UTF-8 định danh lần quan sát |
| | 3 | `platform` | string | ❌ | Tên sàn (`careerviet`, `upzi`, `itviec`, `topcv`...) — **Đồng thời là Partition Key cấp 1** |
| | 4 | `source_url` | string | ✅ | URL bài đăng gốc chưa xử lý |
| | 5 | `canonical_url` | string | ✅ | URL đã bóc tách sạch tracking parameters |
| | 6 | `content_hash` | string | ✅ | SHA-256 băm nội dung để nhận diện thay đổi |
| | 7 | `crawled_at` | string | ❌ | Thời điểm cào (ISO 8601 UTC) |
| | 8 | `observed_at` | string | ❌ | Thời điểm quan sát ổn định lấy từ Bronze crawl metadata, dùng dẫn xuất `year`, `month`, `day`; không dùng thời điểm hiện tại của Silver ETL |
| | 9 | `ingested_at` | string | ❌ | Thời điểm commit vào Bronze |
| | 10 | `silver_updated_at` | string | ❌ | Thời điểm cập nhật lần cuối vào Silver |
| | 11 | `is_active` | bool | ❌ | `true` = Đang tuyển; `false` = Hết hạn/Đóng |
| **Nhóm 2: Vị trí & Mô hình** | 12 | `job_title` | string | ❌ | Tiêu đề tin tuyển dụng gốc — MANDATORY |
| | 13 | `job_title_normalized` | string | ✅ | Tiêu đề viết thường, lọc stop words để matching |
| | 14 | `job_level` | string | ✅ | Intern / Fresher / Junior / Mid / Senior / Lead / Director |
| | 15 | `job_expertise` | string | ✅ | Chuyên môn kỹ thuật (Backend, Frontend, DevOps...) |
| | 16 | `job_domain` | string | ✅ | Lĩnh vực ngành (Fintech, E-commerce, Edtech...) |
| | 17 | `work_mode` | string | ✅ | `On-site` / `Hybrid` / `Remote` |
| | 18 | `working_time` | string | ✅ | Giờ làm việc (Full-time, Part-time...) |
| | 19 | `overtime_policy` | string | ✅ | Chính sách OT |
| **Nhóm 3: Doanh nghiệp** | 20 | `company_name` | string | ❌ | Tên công ty đã làm sạch pháp nhân — MANDATORY |
| | 21 | `company_name_raw` | string | ✅ | Tên công ty 100% nguyên gốc từ sàn |
| | 22 | `company_name_normalized` | string | ✅ | Tên công ty viết thường, bỏ pháp nhân để matching |
| | 23 | `company_size` | string | ✅ | Quy mô nhân sự (50-100, 1000+...) |
| | 24 | `company_industry` | string | ✅ | Lĩnh vực kinh doanh |
| | 25 | `company_type` | string | ✅ | Startup / Enterprise / Outsourcing / Product |
| | 26 | `company_country` | string | ✅ | Quốc gia xuất xứ của doanh nghiệp |
| **Nhóm 4: Địa bàn** | 27 | `city` | string | ✅ | Tỉnh/thành phố chuẩn hóa |
| | 28 | `city_normalized` | string | ✅ | Tên thành phố viết thường phục vụ matching |
| | 29 | `full_address` | string | ✅ | Địa chỉ đầy đủ gốc nơi làm việc |
| **Nhóm 5: Lương & Đãi ngộ** | 30 | `salary_raw` | string | ✅ | Chuỗi lương gốc từ nguồn |
| | 31 | `salary_min_vnd` | int64 | ✅ | Mức lương sàn (VNĐ/tháng) |
| | 32 | `salary_max_vnd` | int64 | ✅ | Mức lương trần (VNĐ/tháng) |
| | 33 | `is_negotiable` | bool | ❌ | `true` nếu "Thỏa thuận / Thương lượng" |
| | 34 | `salary_currency` | string | ✅ | `VND` / `USD` |
| **Nhóm 6: Kinh nghiệm & Định lượng** | 35 | `exp_years_min` | float64 | ✅ | Năm kinh nghiệm tối thiểu |
| | 36 | `exp_years_max` | float64 | ✅ | Năm kinh nghiệm tối đa |
| | 37 | `vacancies_count` | int64 | ❌ | Số vị trí tuyển (default = 1) |
| **Nhóm 7: Chu kỳ thời gian** | 38 | `posted_date` | string | ✅ | ISO `YYYY-MM-DD` ngày đăng từ nguồn |
| | 39 | `expired_date` | string | ✅ | ISO `YYYY-MM-DD` ngày hết hạn |
| **Nhóm 8: Kỹ năng & Nội dung** | 40 | `skills_raw` | list\<string\> | ✅ | Tags kỹ năng gốc từ sàn |
| | 41 | `skills_standardized` | list\<string\> | ✅ | Kỹ năng chuẩn hóa theo taxonomy >120 IT skills |
| | 42 | `job_description_clean` | string | ✅ | Mô tả công việc — đã strip HTML |
| | 43 | `description_normalized` | string | ✅ | Văn bản mô tả viết thường phục vụ fuzzy match |
| | 44 | `requirements_clean` | string | ✅ | Yêu cầu ứng viên đã làm sạch |
| | 45 | `benefits_clean` | string | ✅ | Quyền lợi, đãi ngộ đã làm sạch |
| **Nhóm 9: Bảo toàn gốc** | 46 | `platform_specific_attributes` | string | ✅ | JSON string — toàn bộ trường dị thể còn lại |

> **Quy chuẩn Phân vùng Hive của Observations:**
> `platform` là trường canonical #3, đồng thời được PyArrow dùng làm partition key cấp 1.
> Khi lưu trữ ra cấu trúc thư mục, hệ thống bổ sung **3 cột partition dẫn xuất từ `observed_at`**: `year`, `month`, `day`.
> Tổng số cột vật lý trên tệp Parquet là: **$46 + 3 = 49$ cột**. Tuyệt đối không tạo trùng lặp một cột `platform` thứ hai.

---

### 2. Schema Lớp `entities/` (Canonical Entity Hợp Nhất — 16 Trường)

Định nghĩa trong `src/schemas/entity_schema.py`. Trường `unified_job_id` sử dụng toàn bộ chuỗi băm 64 ký tự SHA-256 (`uj_{sha256_full}`) để giảm nguy cơ xung đột (Hash Collision) xuống mức không đáng kể trong phạm vi dự án:

| Nhóm | # | Tên Trường | Kiểu dữ liệu | Nullable | Mô tả |
|---|---|---|---|---|---|
| **Định danh** | 1 | `unified_job_id` | string | ❌ | ID canonical ổn định toàn hệ thống (`uj_{sha256_full}`: 67 ký tự) |
| **Thuộc tính đại diện** | 2 | `canonical_title` | string | ❌ | Tiêu đề đại diện |
| | 3 | `canonical_company` | string | ❌ | Công ty đại diện |
| | 4 | `canonical_location` | string | ✅ | Địa điểm đại diện |
| | 5 | `canonical_salary_min_vnd` | int64 | ✅ | Lương tối thiểu hợp nhất (VNĐ) |
| | 6 | `canonical_salary_max_vnd` | int64 | ✅ | Lương tối đa hợp nhất (VNĐ) |
| | 7 | `canonical_currency` | string | ✅ | Đơn vị tiền tệ (`VND` / `USD`) |
| | 8 | `canonical_description` | string | ✅ | Mô tả công việc đại diện |
| | 9 | `canonical_posted_date` | string | ✅ | **Ngày đăng đại diện được chọn từ canonical observation (Dùng phân vùng)** |
| **Thống kê nguồn** | 10 | `source_count` | int64 | ❌ | Số nền tảng chứa tin |
| | 11 | `observation_count` | int64 | ❌ | Số observation lịch sử |
| **Vòng đời & Thời gian** | 12 | `first_seen_at` | string | ❌ | Thời điểm nhìn thấy entity lần đầu |
| | 13 | `last_seen_at` | string | ❌ | Thời điểm nhìn thấy entity gần nhất |
| | 14 | `entity_status` | string | ❌ | Trạng thái thực thể (`NEW`, `ACTIVE`, `UPDATED`, `CLOSED`, `REOPENED`, `MERGED`, `SPLIT`) |
| | 15 | `canonical_updated_at` | string | ❌ | Thời điểm canonical record cập nhật |
| | 16 | `resolution_version` | string | ❌ | Phiên bản thuật toán matching |

**Phân vùng Hive của Entities:** Phân vùng 3 cấp `year`, `month`, `day` được dẫn xuất từ `canonical_posted_date` (nếu null thì dùng ngày cào `first_seen_at` làm fallback).

---

### 3. Schema Lớp `entity_links/` (Bảng Liên Kết Entity ↔ Observation — 16 Trường)

Định nghĩa trong `src/schemas/entity_links_schema.py`:

| # | Tên Trường | Kiểu dữ liệu | Nullable | Mô tả |
|---|---|---|---|---|
| 1 | `unified_job_id` | string | ✅ | ID entity canonical (FK trỏ về `entities`). **Giá trị `NULL` khi `match_status IN ('REVIEW_REQUIRED', 'UNMATCHED')`** |
| 2 | `candidate_entity_id` | string | ✅ | ID của entity canonical ứng viên tiềm năng có điểm tương đồng cao nhất đang chờ thẩm định (`NULL` khi `match_status` là `DISTINCT_ENTITY`, `UNMATCHED`, hoặc `AUTO_MATCH`) |
| 3 | `observation_id` | string | ❌ | ID observation nguồn (FK trỏ về `observations`) |
| 4 | `platform` | string | ❌ | Nền tảng tuyển dụng |
| 5 | `source_job_id` | string | ❌ | ID gốc trên nền tảng |
| 6 | `source_url` | string | ✅ | URL gốc |
| 7 | `canonical_url` | string | ✅ | URL đã normalize |
| 8 | `match_method` | string | ❌ | Rule đã dùng (`SOURCE_JOB_ID`, `CANONICAL_URL`, `EXACT_NORMALIZED_IDENTITY`, `FUZZY_TITLE_COMPANY_LOCATION`, `MANUAL_REVIEW`) |
| 9 | `match_score` | float64 | ✅ | Điểm tương đồng từ 0.0 đến 1.0 |
| 10 | `match_status` | string | ❌ | Trạng thái quyết định (`AUTO_MATCH`, `REVIEW_REQUIRED`, `DISTINCT_ENTITY`, `UNMATCHED`, `MANUAL_MATCH`) |
| 11 | `matched_at` | string | ❌ | Thời điểm matching (Cột phân vùng) |
| 12 | `matcher_version` | string | ❌ | Phiên bản matcher |
| 13 | `review_reason` | string | ✅ | Lý do cần review (nếu `REVIEW_REQUIRED`) |
| 14 | `previous_unified_job_id` | string | ✅ | ID cũ nếu entity được tách/gộp lại |
| 15 | `resolution_version` | string | ❌ | Phiên bản resolution |
| 16 | `is_primary_source` | bool | ❌ | `true` nếu observation này cung cấp canonical record |

**+ 3 cột phân vùng Hive:** `year`, `month`, `day` (dẫn xuất theo `matched_at`).

---

### 4. Schema Lớp `quarantine/` (50 Trường = 46 Canonical + 4 Audit)

Định nghĩa trong `src/schemas/quarantine_schema.py`:

```python
SILVER_QUARANTINE_SCHEMA = pa.schema([
    *SILVER_OBSERVATIONS_SCHEMA,                                  # Đúng 46 trường canonical
    pa.field("rejection_reason",    pa.string(), nullable=False), # Mã lỗi Quality Gate
    pa.field("quarantined_at",      pa.string(), nullable=False), # Thời điểm cách ly ISO 8601
    pa.field("audit_run_id",        pa.string(), nullable=False), # Pipeline run_id gây lỗi
    pa.field("raw_payload_snippet", pa.string(), nullable=True),  # 300 ký tự preview dữ liệu thô
])
# Tổng cộng: 46 + 4 = 50 trường dữ liệu
```

---

## GIAI ĐOẠN 3: HỆ THỐNG ĐỊNH DANH & CHUẨN HÓA IDENTITY NGUỒN (SOURCE IDENTITY & STANDARDIZATION)

**Mục tiêu:** Định nghĩa chuẩn xác công thức băm định danh có delimiter UTF-8, hợp đồng giao dịch cho `entity_registry`, làm sạch dữ liệu và tạo sẵn các trường normalized theo batch bộ nhớ có kiểm soát.

### 1. Hệ Thống Định Danh & Công Thức Băm Chuẩn

#### 1.1. `source_job_id`
`source_job_id` là ID nguyên bản của tin trên từng nền tảng (Ví dụ: `vieclam24h = 200975007`, `devwork = 2759`). `source_job_id` chỉ có ý nghĩa duy nhất trong phạm vi một platform. **Không được dùng riêng `source_job_id` để dedup xuyên các nguồn.**

#### 1.2. `observation_id` (Bắt Buộc Delimiter UTF-8)
Để triệt tiêu nguy cơ xung đột hash (Hash Collision) do ghép chuỗi không có phân cách:
```python
import hashlib

def generate_observation_id(platform: str, source_job_id: str, observed_at: str, content_hash: str) -> str:
    """Tạo observation_id duy nhất với delimiter cố định và encode UTF-8."""
    raw_key = f"{platform}|{source_job_id}|{observed_at}|{content_hash}"
    return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()
```
*Mục đích:* Phân biệt cùng tin ở các ngày quan sát khác nhau, phân biệt hai phiên bản nội dung và bảo đảm tính idempotency khi chạy lại ETL. `observed_at` phải được lấy nguyên vẹn từ Bronze metadata của crawl run hoặc một giá trị processing ổn định đã lưu trong Bronze; tuyệt đối không gọi `datetime.now()` khi tái xử lý cùng input. Nếu Bronze không có thời điểm quan sát ổn định, pipeline phải dừng với lỗi dữ liệu thay vì tự sinh thời điểm mới.

#### 1.3. `unified_job_id` (Định dạng Đầy Đủ 64 Ký Tự Băm)
Để giảm nguy cơ xung đột (hash collision) xuống mức không đáng kể trong phạm vi dự án (với không gian $2^{256}$ trạng thái của thuật toán SHA-256), hệ thống sử dụng toàn bộ 64 ký tự băm:
```python
def generate_unified_job_id(identity_seed: str) -> str:
    """Sinh unified_job_id (độ dài 67 ký tự: uj_ + 64 hex) giảm nguy cơ collision xuống mức không đáng kể."""
    return "uj_" + hashlib.sha256(identity_seed.encode("utf-8")).hexdigest()
```

**Quy cách định nghĩa `identity_seed` khi tạo entity mới:**
Khi một observation được xác định là tin tuyển dụng mới (`DISTINCT_ENTITY` hoặc lần đầu tiên nhìn thấy trên hệ thống), hệ thống **tuyệt đối không được** dùng trực tiếp `company_name_normalized + title_normalized + city_normalized` làm seed vì hai tin tuyển dụng độc lập của cùng một công ty có thể vô tình trùng fingerprint này.

Thay vào đó, `identity_seed` được hình thành từ identity ổn định của observation đầu tiên phát hiện ra entity đó:
```python
# Tạo identity_seed bất biến từ observation gốc đầu tiên:
identity_seed = f"{platform}|{source_job_id}|{observation_id}"
```
Trong MVP chỉ sử dụng seed chuẩn duy nhất `f"{platform}|{source_job_id}|{observation_id}"`; không dùng biến thể seed theo thời điểm hiện tại hoặc seed không có `observation_id`.

**Bảo toàn tính bất biến của `unified_job_id`:**
- Ngay sau khi sinh ra `unified_job_id`, giá trị này được lưu cố định vào `entity_registry` (SQLite) và bảng liên kết `entity_links`.
- Trong các chu kỳ cào tiếp theo, dù nhà tuyển dụng có chỉnh sửa tiêu đề (`job_title`), lương (`salary`), mô tả (`job_description`), hay hạn nộp hồ sơ (`expired_date`), hệ thống chỉ cập nhật bản ghi mà **không bao giờ sinh lại ID mới** cho cặp `(platform, source_job_id)` đã tồn tại.

---

### 2. Schema và Transaction Contract của `entity_registry`

`entity_registry` được lưu trữ tại bảng SQLite trong `data/state_store.db`:

```sql
CREATE TABLE IF NOT EXISTS entity_registry (
    unified_job_id                 TEXT PRIMARY KEY,
    candidate_identity_fingerprint TEXT NOT NULL,
    canonical_title                TEXT NOT NULL,
    canonical_company              TEXT NOT NULL,
    canonical_city                 TEXT,
    entity_status                  TEXT NOT NULL DEFAULT 'NEW',
    first_seen_at                  TEXT NOT NULL,
    last_seen_at                   TEXT NOT NULL,
    resolution_version             TEXT NOT NULL DEFAULT 'v1',
    created_at                     TEXT NOT NULL,
    updated_at                     TEXT NOT NULL
);

-- Index thông thường phục vụ tra cứu candidate nhanh, KHÔNG ĐẶT UNIQUE:
CREATE INDEX IF NOT EXISTS idx_entity_registry_fingerprint ON entity_registry(candidate_identity_fingerprint);
CREATE INDEX IF NOT EXISTS idx_entity_registry_status ON entity_registry(entity_status);
```

#### 2.1. Bảng ánh xạ source job bất biến

Để bảo đảm quy tắc `(platform, source_job_id)` luôn trỏ về cùng một entity qua mọi phiên crawl, hệ thống duy trì thêm bảng ánh xạ trong cùng `data/state_store.db`:

```sql
CREATE TABLE IF NOT EXISTS source_job_registry (
    platform          TEXT NOT NULL,
    source_job_id     TEXT NOT NULL,
    unified_job_id    TEXT NOT NULL,
    first_observed_at TEXT NOT NULL,
    last_observed_at  TEXT NOT NULL,
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL,
    PRIMARY KEY (platform, source_job_id),
    FOREIGN KEY (unified_job_id) REFERENCES entity_registry(unified_job_id)
);

CREATE INDEX IF NOT EXISTS idx_source_job_registry_entity
ON source_job_registry(unified_job_id);
```

Đây là bảng tra cứu source-native identity. Khi `(platform, source_job_id)` đã tồn tại, mọi observation tiếp theo phải sử dụng `unified_job_id` đã lưu; không được tạo entity mới chỉ vì title, salary, description hoặc deadline thay đổi. Việc tạo hoặc cập nhật `entity_registry` và `source_job_registry` phải nằm trong cùng giao dịch `BEGIN IMMEDIATE`.

**Bản Chất Của `candidate_identity_fingerprint`:**
1. **Không đặt `UNIQUE`:** Fingerprint được ghép từ:
   $$\text{candidate\_identity\_fingerprint} = \text{company\_name\_normalized} + "|" + \text{job\_title\_normalized} + "|" + \text{city\_normalized}$$
   Hai vị trí tuyển dụng khác nhau của cùng một công ty tại cùng một thành phố (ví dụ: 2 nhóm Backend riêng biệt tuyển 2 đợt khác nhau) vẫn có thể trùng chuỗi này. Do đó, trường này **chỉ dùng để thu hẹp phạm vi tìm kiếm ứng viên (Candidate Lookup)**, không phải là khóa định danh cứng.
2. **Tính ổn định của `unified_job_id`:** Sau khi entity đã được tạo và gán `unified_job_id`, ID này được giữ ổn định vĩnh viễn qua `entity_registry` và liên kết trong `entity_links`.
3. **Thay đổi nội dung không sinh entity mới:** Khi tiêu đề (`job_title`), lương (`salary`), hoặc mô tả (`job_description`) thay đổi qua các ngày cào, hệ thống **không được tạo entity mới** nếu `(platform, source_job_id)` đã tồn tại liên kết tới `unified_job_id`.

**Hợp Đồng Giao Dịch SQLite:**
- Thao tác đọc/ghi vào `entity_registry` và `source_job_registry` bắt buộc chạy trong cùng giao dịch SQLite `BEGIN IMMEDIATE` nhằm ngăn chặn xung đột (race-condition) giữa các tiến trình ETL chạy đồng thời.
- Bật `PRAGMA foreign_keys = ON` khi mở kết nối để kiểm tra FK của `source_job_registry`.

---

### 3. Transformers Chuẩn Hóa & Giới Hạn Batch Bộ Nhớ

#### 3.1. Giới hạn Bộ nhớ (Bounded-Batch Memory Execution)
- **Trong giai đoạn MVP:** Dữ liệu được xử lý bằng Pandas DataFrame nhưng **bắt buộc chia theo batch giới hạn** (`max_records_per_batch = 5,000` dòng/lần) để đảm bảo an toàn bộ nhớ trên VPS 8GB RAM, không full-scan toàn bộ bộ nhớ.
- **Lộ trình mở rộng (Scale-up):** Khi tập dữ liệu vượt quá 500,000 bản ghi, module sẽ chuyển thẳng sang streaming engine bằng PyArrow Table hoặc DuckDB.

#### 3.2. Chi tiết Các Transformer Nghiệp Vụ
- **Làm sạch Tên Doanh nghiệp (`clean_company_name`):** Lột bỏ 2 vòng regex tiền tố (`Công ty TNHH`, `CTCP`) và hậu tố (`Co., Ltd`, `JSC`, `Inc.`), giữ nguyên `company_name_raw` 100%.
- **Chuẩn hóa Lương (`parse_salary`):** Nhận diện thỏa thuận (`is_negotiable = True`). Loại bỏ dấu chấm, phẩy phân cách số (`15.000.000` → `15000000`). Quy đổi tỷ giá cố định: $\text{USD} \times 25,400 = \text{VNĐ}$ (không gọi API live để giữ Idempotency). Lương năm tự chia 12.
- **Chuẩn hóa Địa bàn, Cấp bậc:** Phân loại 7 cấp bậc, chuẩn hóa về Hà Nội / Hồ Chí Minh / Đà Nẵng / Khác.
- **Chuẩn hóa Kỹ năng IT (`standardize_skills`):** Từ điển >120 IT skills qua Word Boundary Regex (`\bpython\b`, `\bjava\b(?!\s*script)`).
- **Chuẩn hóa URL (`make_canonical_url`):** Loại bỏ hoàn toàn query tracking parameters (`utm_*`, `ref`, `source`, `fbclid`, `search_id`).

---

## GIAI ĐOẠN 4: ENTITY RESOLUTION & MATCHING ĐA NGUỒN (CROSS-SOURCE RESOLUTION)

**Mục tiêu:** Nhận diện các tin tuyển dụng xuất hiện đồng thời trên nhiều sàn và liên kết chúng về một `unified_job_id` duy nhất mà không xóa observation gốc.

### 1. Phân Biệt Ba Loại Dữ Liệu Trùng Lặp

```text
Duplicate observation:
    Cùng một tin được crawl nhiều lần từ một nguồn.

Cross-source duplicate:
    Cùng một vị trí tuyển dụng xuất hiện trên nhiều nền tảng.

Updated observation:
    Cùng một tin nhưng nội dung, lương hoặc deadline thay đổi.
```
**Các khái niệm trên không được xử lý như nhau.**

### 2. Thứ Tự Matching (Từ Chắc Chắn Đến Không Chắc Chắn)

```text
[Input Observation]
        ↓
[Rule 1: Source-Native Identity] ───────── Khớp? ──(Yes)──> AUTO_MATCH (1.0)
        ↓ (No)
[Rule 2: Canonical URL] ────────────────── Khớp? ──(Yes)──> AUTO_MATCH (1.0)
        ↓ (No)
[Rule 3: Exact Normalized Identity] ────── Khớp? ──(Yes)──> AUTO_MATCH (0.98)
        ↓ (No)
[Rule 4: Fuzzy Candidate Matching] ─────── Điểm số? ───────> Phân ngưỡng (>=0.90 / 0.75-0.90 / <0.75)
```

#### Rule 1 — Source-Native Identity (`SOURCE_JOB_ID`)
Trong cùng một platform:
```text
platform + source_job_id
```
Nếu đã tồn tại:
- Coi là cùng source job;
- Không tạo entity mới;
- Nếu content hash thay đổi thì đánh dấu `UPDATED`;
- Tạo observation mới nếu đây là lần quan sát mới.
```text
match_method = SOURCE_JOB_ID
match_score = 1.0
match_status = AUTO_MATCH
```

#### Rule 2 — Canonical URL (`CANONICAL_URL`)
Nếu hai observation có cùng `canonical_url`:
```text
match_method = CANONICAL_URL
match_score = 1.0
match_status = AUTO_MATCH
```
*Lưu ý:* Chỉ sử dụng rule này khi URL thực sự xác định cùng một tin. Không gộp chỉ vì hai URL có cùng hostname hoặc cùng category path.

#### Rule 3 — Exact Normalized Identity (`EXACT_NORMALIZED_IDENTITY`)
Tạo identity key:
```text
normalized_identity_key =
    company_name_normalized
    + "|"
    + job_title_normalized
    + "|"
    + city_normalized
```
Chỉ auto-match khi:
- `company_name_normalized` phù hợp;
- `job_title_normalized` phù hợp;
- `city_normalized` phù hợp;
- Không có mâu thuẫn rõ ràng về cấp bậc hoặc loại hình công việc.
```text
match_method = EXACT_NORMALIZED_IDENTITY
match_score = 0.98
match_status = AUTO_MATCH
```

#### Rule 4 — Fuzzy Candidate Matching (`FUZZY_TITLE_COMPANY_LOCATION`)
Chỉ chạy fuzzy matching bằng thư viện `rapidfuzz` khi ba rule phía trên không tìm được match.

**Công thức tính điểm tương đồng:**
```text
match_score =
    0.40 * title_similarity
  + 0.25 * company_similarity
  + 0.15 * location_similarity
  + 0.10 * salary_overlap
  + 0.10 * description_similarity
```

**Các thành phần:**
- `title_similarity`: Độ tương đồng token sau normalize (`rapidfuzz.fuzz.token_set_ratio`).
- `company_similarity`: Độ tương đồng tên công ty đã normalize (`rapidfuzz.fuzz.token_sort_ratio`).
- `location_similarity`: Độ tương đồng tỉnh/thành phố và địa điểm.
- `salary_overlap`:
  - `1.0`: Hai khoảng lương giao nhau.
  - `0.5`: Một bên không có dữ liệu lương (Thỏa thuận).
  - `0.0`: Khoảng lương mâu thuẫn rõ ràng.
- `description_similarity`: Độ tương đồng token trên mô tả đã làm sạch.

**Ngưỡng phân loại quyết định:**
```text
match_score >= 0.90:
    AUTO_MATCH

0.75 <= match_score < 0.90:
    REVIEW_REQUIRED

match_score < 0.75:
    DISTINCT_ENTITY
```
**Không được auto-match chỉ dựa trên title giống nhau.**

### 3. Quy Chuẩn Xử Lý Trạng Thái Matching

Pipeline phải áp dụng các quy tắc trạng thái sau một cách deterministic:

```text
Đã có (platform, source_job_id) trong source_job_registry:
    dùng lại unified_job_id đã lưu
    match_status = AUTO_MATCH
    match_method = SOURCE_JOB_ID

Có candidate phù hợp và score >= 0.90:
    dùng unified_job_id của candidate
    match_status = AUTO_MATCH

Có candidate và 0.75 <= score < 0.90:
    không tạo entity mới
    unified_job_id = NULL
    candidate_entity_id = candidate tốt nhất
    match_status = REVIEW_REQUIRED

Không có candidate phù hợp hoặc score < 0.75:
    tạo entity mới từ identity_seed của observation đầu tiên
    unified_job_id = ID entity mới
    match_status = DISTINCT_ENTITY

Thiếu source_job_id hoặc thiếu dữ liệu tối thiểu để tạo entity:
    không tạo entity
    unified_job_id = NULL
    candidate_entity_id = NULL nếu không có candidate
    match_status = UNMATCHED
```

`DISTINCT_ENTITY` là trạng thái của một observation đã được xác định là entity mới và bắt buộc tạo entity canonical. `UNMATCHED` chỉ dùng cho observation không đủ dữ liệu định danh hoặc không thể đưa ra quyết định hợp lệ; observation này không được tạo entity cho đến khi dữ liệu được bổ sung hoặc kỹ sư xử lý.

### 3.1. Quy chuẩn xử lý `REVIEW_REQUIRED` và `UNMATCHED`

Khi một observation rơi vào vùng nghi ngờ ($0.75 \le \text{match\_score} < 0.90$):
- **Không tạo canonical entity:** Tuyệt đối không ghi vào `entities/` canonical để tránh làm bẩn và sinh rác trong danh mục entity chính thức của Tầng Vàng (Gold Layer).
- **Lưu vết đầy đủ vào `entity_links/`:** Bản ghi liên kết kiểm toán được ghi nhận với:
  + `unified_job_id = NULL` (do chưa được gắn chính thức vào bất kỳ canonical entity nào).
  + `candidate_entity_id = <uj_candidate>` (lưu ID của canonical entity tiềm năng có điểm tương đồng cao nhất đang chờ thẩm định).
  + `match_status = 'REVIEW_REQUIRED'` (hoặc `'UNMATCHED'`).
  + `review_reason = 'Fuzzy score in [0.75, 0.90) requires human review'`.
- **Xuất hàng chờ kiểm duyệt:** Tự động trích xuất các bản ghi này vào tệp `data/operational/pending_review.csv` (và ghi nhận vào `s3://silver/quarantine/` nếu cấu hình yêu cầu) để kỹ sư xem xét và thẩm định trước phiên chạy tiếp theo.

### 4. Lá Chắn Ngăn Chặn Gộp Nhầm (False-Positive Guard)

Không được tự động gộp nếu có một trong các điều kiện:
- Công ty khác nhau rõ ràng;
- Thành phố khác nhau và cả hai không phải remote;
- Cấp bậc khác nhau nghiêm trọng;
- Một tin là internship còn tin kia là senior;
- Loại hình Full-time và Part-time mâu thuẫn rõ ràng;
- Khoảng lương mâu thuẫn rõ ràng;
- Mô tả công việc khác nhau về bản chất;
- Title chỉ giống do cùng chứa từ khóa phổ biến như `Developer`, `Engineer`, `Manager`;
- Một tin tuyển nhiều vị trí khác nhau nhưng bị gom nhầm từ title chung.

Trường hợp không chắc chắn phải dùng:
```text
match_status = REVIEW_REQUIRED
```
Không được ép tự động thành `AUTO_MATCH`.

---

## GIAI ĐOẠN 5: QUẢN LÝ VÒNG ĐỜI & LỊCH SỬ ENTITY (LIFECYCLE & TEMPORAL HISTORY)

**Mục tiêu:** Quản lý vòng đời đầy đủ của tin tuyển dụng theo thời gian mà không bao giờ xóa dữ liệu quan sát lịch sử.

### 1. Quản Lý Lịch Sử Entity (Observation History)

Cùng một tin được crawl nhiều lần không phải là duplicate semantic cần xóa:
```text
2026-11-15: salary = 20,000,000
2026-11-16: salary = 25,000,000
```
Đây là hai observation của cùng `unified_job_id`, trong đó observation mới là phiên bản cập nhật.

**Quy tắc:**
```text
same source_job_id + same content_hash:
    observation lặp kỹ thuật hoặc snapshot giống nhau;
    không tạo entity mới.

same source_job_id + content_hash khác:
    giữ observation mới;
    entity_status = UPDATED.

Tin biến mất khỏi nguồn một lần:
    chưa được đánh dấu CLOSED ngay.

Tin không xuất hiện trong grace period:
    entity_status = CLOSED.

Tin CLOSED xuất hiện lại:
    entity_status = REOPENED.
```

### 2. Cấu Hình Grace Period Đủ 9 Nền Tảng

Grace period được cấu hình riêng theo đặc thù từng sàn để tránh đóng tin nhầm:

```python
SOURCE_GRACE_PERIOD_DAYS = {
    "careerviet": 14,     # CareerViet gói tin tiêu chuẩn 14 ngày
    "upzi": 14,           # Upzi nền tảng tuyển dụng trẻ
    "vietnamdevs": 14,    # VietnamDevs diễn đàn/cổng IT
    "vieclam24h": 14,     # Vieclam24h chu kỳ phổ thông
    "devwork": 14,        # Devwork mô hình kết nối headhunter
    "itviec": 7,          # ITViec cập nhật nhanh, tin biến động ngắn
    "topcv": 14,          # TopCV lưu trữ tin lâu hơn
    "topdev": 10,         # TopDev chu kỳ tuyển dụng IT trung bình
    "vietnamworks": 21,   # VietnamWorks gói tuyển dụng kéo dài
}
```
*Ghi chú:* Đây là giá trị MVP ban đầu, có thể điều chỉnh sau khi theo dõi tỷ lệ chớp tắt (flickering) của tin trên thực tế.

### 3. Canonical Record Selection

Khi nhiều observation thuộc cùng một entity, canonical record được chọn theo thứ tự ưu tiên:
1. Observation mới nhất và vượt Quality Gate;
2. Có description đầy đủ hơn (độ dài văn bản lớn hơn);
3. Có salary hợp lệ (`salary_min_vnd` / `salary_max_vnd` khác null);
4. Có deadline hợp lệ;
5. Có location chuẩn hóa;
6. Source có độ tin cậy cao hơn theo cấu hình:
   $$\text{ITViec (1)} \to \text{VietnamWorks (2)} \to \text{TopCV (3)} \to \text{Các sàn khác}$$
7. Nếu vẫn hòa, chọn observation có `observed_at` mới nhất.

Các observation không được chọn làm canonical vẫn được giữ trong `observations/` và `entity_links/`. **Không được xóa observation chỉ vì không được chọn làm canonical record.**

---

## GIAI ĐOẠN 6: CỔNG KIỂM ĐỊNH CHẤT LƯỢNG & VÙNG CÁCH LY (QUALITY GATE & QUARANTINE)

**Mục tiêu:** Phân tách rõ ràng giữa lỗi cứng (Hard Failure) và cảnh báo mềm (Soft Review), cách ly dữ liệu lỗi có lưu vết kiểm toán đầy đủ.

### 1. Phân Loại Quy Tắc Kiểm Định Chất Lượng

Hệ thống phân tách nghiêm ngặt giữa lỗi nghiêm trọng làm hỏng cấu trúc và các điểm bất thường về nội dung:

| Mã quy tắc | Tên quy tắc kiểm định | Điều kiện vi phạm | Phân loại | Hành động |
|---|---|---|---|---|
| **R-01** | `SCHEMA_DRIFT_MISSING_JOB_ID` | `source_job_id` rỗng hoặc null | **Hard Failure** | Chuyển thẳng `quarantine/` |
| **R-02** | `MISSING_MANDATORY_FIELD: job_title` | `job_title` rỗng hoặc null | **Hard Failure** | Chuyển thẳng `quarantine/` |
| **R-03** | `MISSING_MANDATORY_FIELD: company_name` | `company_name` rỗng hoặc null | **Hard Failure** | Chuyển thẳng `quarantine/` |
| **R-04** | `SALARY_MIN_GREATER_THAN_MAX` | `salary_min_vnd > salary_max_vnd` | **Hard Failure** | Chuyển thẳng `quarantine/` |
| **R-05** | `SALARY_TOO_LOW_OUTLIER` | `salary_min_vnd < 3,000,000 VND` | **Warning / Soft Review** | Nếu `job_level` là `Intern` hoặc `Fresher`, giữ bản ghi hợp lệ và ghi warning; các trường hợp còn lại chuyển `quarantine/` |
| **R-06** | `SALARY_TOO_HIGH_OUTLIER` | `salary_max_vnd > 300,000,000 VND` | **Warning / Soft Review** | Chuyển `quarantine/`, không tự động sửa giá trị |
| **R-07** | `FUTURE_POSTED_DATE` | `posted_date > ngày xử lý UTC` | **Warning / Soft Review** | Chuyển `quarantine/`, không tự động thay đổi ngày gốc |
| **R-08** | `STALE_POSTED_DATE` | `posted_date < 2020-01-01` | **Warning / Soft Review** | Chuyển `quarantine/`, không tự động xóa hoặc sửa ngày gốc |

### 2. Quality Gate Sau Entity Resolution

Một entity chỉ được ghi vào `s3://silver/entities/` khi:
- Có `unified_job_id`;
- Có canonical title;
- Có canonical company;
- Có entity status hợp lệ;
- Liên kết tới ít nhất một observation hợp lệ;
- Không vi phạm false-positive guard;
- Có source link và resolution metadata;
- Không bị đánh dấu `REVIEW_REQUIRED` (chỉ đưa vào sau khi kỹ sư rà soát xác nhận).

Các entity hoặc observation không đạt phải ghi vào `s3://silver/quarantine/` kèm:
```text
quarantine_reason
quality_rule
resolution_status
```

### 3. Cổng Bàn Giao Dữ Liệu Tầng Vàng (`publish_silver_for_gold`)

Kiến trúc chuẩn của dự án là:
$$\text{Silver Parquet} \longrightarrow \text{Gold PostgreSQL} \longrightarrow \text{Power BI / Analytics}$$

Task `publish_silver_for_gold` chịu trách nhiệm đồng bộ bảng `entities` sang PostgreSQL. Công cụ SSIS đóng vai trò là adapter phụ trợ tùy chọn, không phải thành phần bắt buộc của pipeline cốt lõi.

**Điều kiện chặn bàn giao (Block Gate):**
```python
if summary_report["metrics"]["quarantine_rate_pct"] > 5.0:
    raise ValueError("Quarantine rate > 5% — Chặn đứng quá trình load dữ liệu lên Gold PostgreSQL!")
if summary_report["schema_drift_warnings"]:
    raise ValueError("Schema drift phát hiện — Kỹ sư phải xem xét trước khi load sang Gold!")
```

---

## GIAI ĐOẠN 7: GHI PARQUET PHÂN VÙNG & SỔ CÁI KIỂM TOÁN (SILVER WRITER & AUDIT LEDGER)

**Mục tiêu:** Ghi dữ liệu Parquet phân vùng an toàn, bảo đảm tính Idempotency ở cấp độ partition/file và lưu vết lịch sử khi thuật toán thay đổi.

### 1. Quy Trình Ghi Parquet và Manifest 7 Bước

```text
Bước 1: Tập hợp records hợp lệ theo batch giới hạn (Bounded Batch) → Ép kiểu chặt chẽ:
           ├── salary_min_vnd, salary_max_vnd → Int64 (nullable)
           ├── exp_years_min, exp_years_max → Float64 (nullable)
           └── vacancies_count → int64 (default = 1, non-nullable)
Bước 2: Gắn schema PyArrow tương ứng:
           ├── observations → SILVER_OBSERVATIONS_SCHEMA (46 trường)
           ├── entities     → SILVER_ENTITY_SCHEMA (16 trường)
           └── entity_links → SILVER_ENTITY_LINKS_SCHEMA (16 trường)
Bước 3: Ghi ra thư mục tạm local với output key deterministic:
           ├── format = "parquet", compression = "snappy"
           ├── partitioning_flavor = "hive"
           ├── observations: append-only theo `observation_id`
           ├── entity_links: deterministic theo `observation_id + resolution_version`
           ├── entities: upsert theo `unified_job_id`, không append mù
           └── Không dùng `overwrite_or_ignore` như cơ chế duy nhất để cập nhật entities
Bước 4: Upload file tạm lên MinIO với manifest trạng thái `PENDING`.
Bước 5: Verify checksum SHA-256 và record count.
Bước 6: Trong `BEGIN IMMEDIATE`, cập nhật `entity_registry` và `source_job_registry`.
Bước 7: Đánh dấu manifest `COMPLETE`; nếu lỗi, giữ manifest `FAILED` để reconciliation xử lý.
```

### 2. Bản Chất Idempotency & Tính Nhất Quán Dữ Liệu

- **Idempotency của observations:** `observation_id` deterministic; khi đã tồn tại thì lần chạy lại không ghi thêm bản sao.
- **Idempotency của entity_links:** khóa logic là `observation_id + resolution_version`; lần chạy lại cùng input và version phải upsert cùng kết quả.
- **Idempotency của entities:** upsert theo `unified_job_id`; không append mù. Khi entity đổi partition do canonical date thay đổi, writer phải ghi partition mới và xóa hoặc đánh dấu bản ghi cũ theo manifest.
- **Bảo vệ cục bộ:** SQLite `BEGIN IMMEDIATE` bảo vệ việc đọc/ghi `entity_registry` và `source_job_registry`, ngăn race-condition giữa các tiến trình ETL.
- **Tính nhất quán SQLite–MinIO:** đây không phải giao dịch phân tán. Quy trình phục hồi bắt buộc là: upload manifest `PENDING` → verify checksum và record count → commit registry → đánh dấu `COMPLETE`. Nếu MinIO upload hoặc SQLite commit thất bại, manifest giữ trạng thái `FAILED`; reconciliation sẽ hoàn tất an toàn hoặc dọn artifact dở dang trước phiên kế tiếp.

### 3. Audit và Idempotency Khi Thuật Toán Thay Đổi

Mỗi lần chạy entity resolution phải ghi vào `s3://silver/audit_logs/`:
```text
run_id
resolution_version
input_observation_count
new_entity_count
updated_entity_count
closed_entity_count
reopened_entity_count
auto_match_count
review_required_count
distinct_entity_count
quarantine_count
```

Chạy lại cùng input với cùng `resolution_version` phải cho kết quả idempotent:
```text
same observation_id
same unified_job_id
same match_method
same match_score
same match_status
```
**Không tạo entity mới chỉ vì ETL chạy lại.**

Nếu resolution algorithm thay đổi, tăng phiên bản lên `resolution_version = v2` và lưu vết audit:
```text
old_unified_job_id
new_unified_job_id
old_match_score
new_match_score
change_reason
changed_at
```

### 4. Báo Cáo Kiểm Toán & Data Quality Scorecard

```json
{
  "run_id": "run_20261115_020000",
  "pipeline": "bronze_to_silver_v3",
  "execution_mode": "daily",
  "resolution_version": "v1",
  "duration_seconds": 64.5,
  "metrics": {
    "total_observations_processed": 180,
    "valid_observations": 171,
    "quarantined_records": 9,
    "new_entity_count": 42,
    "updated_entity_count": 103,
    "closed_entity_count": 5,
    "reopened_entity_count": 2,
    "auto_match_count": 26,
    "review_required_count": 4,
    "distinct_entity_count": 141,
    "quarantine_rate_pct": 5.0
  },
  "scorecard": [
    {"attribute": "Mức Lương (Fill Rate)",     "rate_pct": 72.5},
    {"attribute": "Địa bàn (Fill Rate)",        "rate_pct": 89.2},
    {"attribute": "Kỹ năng IT (Fill Rate)",     "rate_pct": 94.4},
    {"attribute": "Kinh nghiệm (Fill Rate)",    "rate_pct": 81.1},
    {"attribute": "Cấp bậc (Fill Rate)",        "rate_pct": 68.5},
    {"attribute": "Match Score trung bình",     "value": 0.94}
  ],
  "schema_drift_warnings": [],
  "status": "SUCCESS"
}
```

---

## GIAI ĐOẠN 8: ĐIỀU PHỐI AIRFLOW & BẢO ĐẢM VẬN HÀNH (ORCHESTRATION & ACCEPTANCE TESTS)

**Mục tiêu:** Tự động hóa pipeline Bronze → Silver qua Apache Airflow cho toàn bộ 9 nền tảng, thiết lập đúng vị trí Audit và cam kết vượt qua bộ 18 Acceptance Tests bắt buộc.

### 1. Ranh Giới Dữ Liệu Giữa Các Tầng Kiến Trúc

```text
Bronze:
    Giữ toàn bộ extracted observations theo crawl run.
    Không dedup semantic đa nguồn.

Silver observations/:
    Chuẩn hóa từng source record.
    Giữ toàn bộ lịch sử observation.

Silver entities/:
    Hợp nhất cùng một tin tuyển dụng.
    Tạo canonical record.

Silver entity_links/:
    Lưu liên kết và bằng chứng matching.

Gold (PostgreSQL):
    Đọc canonical entities.
    Tạo fact/dimension phục vụ SQL, Power BI và phân tích.
```

---

### 2. Cấu Trúc DAG Airflow Điều Phối Tự Động (Đủ 9 Nền Tảng)

Triển khai trong tệp `dags/dag_daily_job_lakehouse_pipeline.py`. Thứ tự thực hiện đặt **`bronze_audit_after_crawl` ngay sau khi cào**, kiểm tra toàn vẹn Bronze trước khi cho phép Silver ETL khởi động:

```text
preflight_bronze (Kiểm tra Disk NVMe, RAM, MinIO, State Store)
  ├── crawl_careerviet_incremental ────┐
  ├── crawl_upzi_incremental ──────────┤
  ├── crawl_vietnamdevs_incremental ───┤
  ├── crawl_vieclam24h_incremental ────┤
  ├── crawl_devwork_incremental ───────┼─ (Cào 9 sàn song song, max 3 active tasks)
  ├── crawl_itviec_incremental ────────┤
  ├── crawl_topcv_incremental ─────────┤
  ├── crawl_topdev_incremental ────────┤
  └── crawl_vietnamworks_incremental ──┘
        ↓ [trigger_rule: ALL_SUCCESS]
  bronze_audit_after_crawl (Đối soát checksum SHA-256 & Manifest Bronze)
        ↓
  transform_bronze_to_observations (Làm sạch & Ghi observations/)
        ↓
  entity_resolution_matching (So khớp 4 mức & Gán unified_job_id)
        ↓
  entity_lifecycle (Cập nhật trạng thái NEW/UPDATED/CLOSED/REOPENED)
        ↓
  quality_gate_and_quarantine (Kiểm tra Hard/Soft rules & Cách ly dữ liệu bẩn)
        ↓
  silver_writer (Ghi Parquet phân vùng observations/entities/links)
        ↓
  publish_silver_for_gold (Đồng bộ entities sang Gold PostgreSQL)
        ↓
  crawl_pipeline_summary (Báo cáo tổng kết toàn pipeline)
```

**Chính Sách Chạy Một Phần (Partial-Run Semantics):**
- Trong giai đoạn MVP: Sử dụng `trigger_rule = ALL_SUCCESS` cho bước Bronze Audit và Silver Resolution toàn hệ thống để đảm bảo dữ liệu toàn vẹn cao nhất.
- Nếu bật chế độ Partial Run (`partial_run = true`):
  - Ghi nhận rõ ràng danh sách `failed_sources` và `blocked_sources` vào audit log.
  - **Tuyệt đối không đánh dấu `CLOSED`** đối với bất kỳ tin nào thuộc về sàn cào bị lỗi/bị chặn trong phiên đó.
  - Bắn cảnh báo `coverage_warning` tới Telegram/Discord.

**Cấu Hình Timeout Đồng Bộ:**
- `timeout` cho từng crawler task: 45 phút.
- `timeout` cho pipeline Silver ETL + Resolution: 60 phút.
- `execution_timeout` toàn bộ DAG: 120 phút.
- `max_active_tasks`: 3 (bảo vệ bộ nhớ RAM VPS).
- `max_active_runs`: 1 (chống chạy chồng chéo khóa State Store).

---

### 3. Điều Kiện Nối Silver Vào Airflow & 18 Acceptance Tests Bắt Buộc

**Chỉ sau khi toàn bộ 18 test dưới đây PASS 100% mới được kích hoạt Silver vào DAG Airflow:**

| STT | Mã Test | Kịch bản kiểm thử (Test Scenario) | Kết quả mong đợi (Expected Outcome) |
|---|---|---|---|
| 1 | `T-01` | Cùng `platform + source_job_id` qua hai lần crawl | Không tạo entity mới; nếu content hash thay đổi thì đánh dấu `UPDATED`. |
| 2 | `T-02` | Cùng canonical URL giữa hai nguồn | `match_method = CANONICAL_URL`, `match_score = 1.0`, `match_status = AUTO_MATCH`. |
| 3 | `T-03` | Cùng company/title/location sau normalize | `match_method = EXACT_NORMALIZED_IDENTITY`, `match_score = 0.98`, `match_status = AUTO_MATCH`. |
| 4 | `T-04` | Title giống nhưng company khác | `DISTINCT_ENTITY` — sinh 2 `unified_job_id` riêng biệt. |
| 5 | `T-05` | Cùng company/title nhưng location khác | `DISTINCT_ENTITY` hoặc `REVIEW_REQUIRED` (nếu cả hai không phải remote). |
| 6 | `T-06` | Internship và senior có title tương tự | `DISTINCT_ENTITY` — chặn đứng nhờ False-Positive Guard. |
| 7 | `T-07` | Hai khoảng salary giao nhau | `salary_overlap > 0` (được cộng điểm matching). |
| 8 | `T-08` | Một record thiếu salary (Thỏa thuận) | `salary_overlap = 0.5` (điểm trung tính). |
| 9 | `T-09` | Content thay đổi nhưng vẫn là cùng entity | Observation mới được lưu, entity giữ nguyên `unified_job_id`, `entity_status = UPDATED`. |
| 10 | `T-10` | Entity chuyển từ `ACTIVE` sang `CLOSED` | Tự động chuyển `CLOSED` sau khi vượt quá Grace Period của nguồn. |
| 11 | `T-11` | Entity `CLOSED` xuất hiện lại | Tự động phục hồi sang `REOPENED`. |
| 12 | `T-12` | Fuzzy score trong vùng `[0.75, 0.90)` | `match_status = REVIEW_REQUIRED`; không đưa vào `entities/` canonical; ghi vào `entity_links/` với `unified_job_id = NULL` và `candidate_entity_id` lưu ID candidate; tự động xuất ra `data/operational/pending_review.csv`. |
| 13 | `T-13` | Fuzzy score dưới ngưỡng `< 0.75` | Coi là tin riêng biệt (`DISTINCT_ENTITY`). |
| 14 | `T-14` | Chạy lại cùng input | Không tạo entity mới — Idempotency tuyệt đối. |
| 15 | `T-15` | Thay đổi `resolution_version` | Sổ cái kiểm toán ghi nhận `old_unified_job_id`, `new_unified_job_id` và lý do. |
| 16 | `T-16` | Observation vẫn tồn tại sau khi entity được merge | Không được xóa observation lịch sử. |
| 17 | `T-17` | Một entity liên kết tới nhiều platform | `source_count` bằng đúng số platform thực tế trong `entity_links`. |
| 18 | `T-18` | Hai tin khác nhau không bị false-positive merge | Phân loại `DISTINCT_ENTITY` chính xác, không gộp nhầm. |

---

## TỔNG KẾT — CHECKLIST TRIỂN KHAI TẦNG BẠC (IMPLEMENTATION CHECKLIST)

| STT | Hạng mục công việc | Module chịu trách nhiệm | Trạng thái |
|---|---|---|---|
| 1 | Tạo bucket `silver` trên MinIO VPS (chạy `mc mb local/silver` nếu chưa có) | `infrastructure/docker-compose.yml` | ☐ Cần chạy lệnh trên VPS |
| 2 | Hiện thực 4 Schema PyArrow (`observations` 46 trường, `entities` 16 trường, `entity_links` 16 trường, `quarantine` 50 trường) | `src/schemas/silver_schema.py`, `src/schemas/entity_schema.py`, `src/schemas/entity_links_schema.py`, `src/schemas/quarantine_schema.py` | ☐ Cần viết mới |
| 3 | Triển khai các hàm làm sạch & bóc tách chuẩn hóa dữ liệu | `src/etl/transformers.py` | ☐ Cần bổ sung |
| 4 | Xây dựng bộ máy Entity Resolution & False-Positive Guard với `rapidfuzz` | `src/etl/entity_resolver.py` | ☐ Cần viết mới |
| 5 | Xây dựng bộ máy quản lý vòng đời & Canonical Selection | `src/etl/entity_lifecycle.py` | ☐ Cần viết mới |
| 6 | Quản lý `entity_registry` và `source_job_registry` bền vững trong SQLite, cùng giao dịch `BEGIN IMMEDIATE` | `src/storage/entity_registry.py` | ☐ Cần viết mới |
| 7 | Tái cấu trúc pipeline ETL nhiều bước tích hợp Entity Resolution | `src/etl/bronze_to_silver.py` | ☐ Cần refactor |
| 8 | Triển khai 8 quy tắc kiểm định chất lượng phân loại Hard/Soft Failure | `src/etl/quality_gate.py` | ☑ Đã có sẵn |
| 9 | Đồng bộ dữ liệu Canonical sang Tầng Vàng PostgreSQL | `src/utils/publish_silver_for_gold.py` | ☐ Cần viết mới |
| 10 | Tích hợp vào DAG Airflow điều phối tự động đủ 9 sàn | `dags/dag_daily_job_lakehouse_pipeline.py` | ☐ Cần cập nhật |
| 11 | Viết bộ kiểm thử tự động cho 18 bài test chấp nhận | `tests/test_silver_resolution.py` | ☐ Cần viết mới |

---

## CÁC QUYẾT ĐỊNH KIẾN TRÚC ĐÃ CHỐT (CONFIRMED ARCHITECTURAL DECISIONS)

| Mã Quyết định | Nội dung quyết định | Chi tiết kỹ thuật & Cam kết |
|---|---|---|
| **D-01 (trước là Q-01) — CONFIRMED** | **Thư viện Fuzzy String Matching** | Chốt sử dụng thư viện **`rapidfuzz`** (C++ optimized, tốc độ nhanh hơn `difflib.SequenceMatcher` gấp 10 lần, tiết kiệm tối đa CPU cho VPS). Bổ sung `rapidfuzz>=3.0.0` vào `requirements.txt`. |
| **D-02 (trước là Q-02) — CONFIRMED** | **Lưu trữ entity registry** | Chốt lưu trữ `entity_registry` và `source_job_registry` trong tệp SQLite tại `data/state_store.db` (dùng chung với Ingestion State Store), cập nhật trong cùng giao dịch `BEGIN IMMEDIATE`, bật `PRAGMA foreign_keys = ON`. |
| **D-03 (trước là Q-03) — CONFIRMED** | **Quy trình xử lý `REVIEW_REQUIRED`** | Chốt lưu vết vào `entity_links/` với `unified_job_id = NULL`, `candidate_entity_id = <uj_candidate>`, `match_status = 'REVIEW_REQUIRED'` và xuất vào `data/operational/pending_review.csv` để kỹ sư rà soát thủ công trước khi gắn canonical entity. |

---

## QUY TRÌNH KIỂM TRA HẠ TẦNG TRƯỚC KHI TEST (OPERATIONAL PREFLIGHT - Q-04)

Trước khi kích hoạt chạy test Silver ETL trên VPS, kỹ sư thực hiện lệnh kiểm tra và khởi tạo bucket `silver`:

```bash
# 1. Kiểm tra bucket silver đã tồn tại chưa:
mc ls local/silver

# 2. Nếu bucket chưa tồn tại, khởi tạo bucket silver mới (sau khi xác nhận alias local và endpoint chính xác):
mc mb local/silver
```

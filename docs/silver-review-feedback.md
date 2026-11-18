# Nhận xét rà soát thiết kế Tầng Silver

**Dự án:** IT Job Data Lakehouse  
**Phạm vi:** Rà soát thiết kế Silver theo mô hình Observation–Entity–Link và mục tiêu MVP/thesis  
**Số nền tảng:** 9  
**Tài liệu được rà soát:** `CHI TIẾT TRIỂN KHAI TẦNG SILVER` trên Notion  
**Kết luận:** Đủ tốt về thiết kế nghiệp vụ Silver MVP, nhưng cần sửa một số mâu thuẫn kỹ thuật trước khi Hermes triển khai code.

---

## 1. Đánh giá tổng quan

Thiết kế hiện tại đã bao phủ đúng những phần cốt lõi của chống trùng lặp đa nguồn:

- `observations/` giữ lịch sử từng nguồn;
- `entities/` lưu canonical record;
- `entity_links/` lưu bằng chứng matching;
- `source_job_id` và `observation_id`;
- `unified_job_id`;
- exact matching;
- canonical URL matching;
- normalized identity matching;
- fuzzy candidate matching;
- `match_method`, `match_score`, `match_status`;
- ngưỡng `AUTO_MATCH`, `REVIEW_REQUIRED`, `DISTINCT_ENTITY`;
- false-positive guard;
- entity lifecycle;
- grace period theo source;
- canonical record selection;
- quality gate và quarantine;
- audit và idempotency;
- acceptance tests;
- Airflow handoff;
- Gold handoff.

Đánh giá theo phạm vi:

```text
Silver storage architecture:          ĐẠT
Observation–Entity–Link model:        ĐẠT
Cross-source dedup design:             ĐẠT
MVP implementation readiness:          GẦN ĐẠT
Production-ready:                      CHƯA
```

Thiết kế hiện tại phù hợp để triển khai **Silver MVP/thesis** sau khi sửa các điểm kỹ thuật dưới đây. Không cần triển khai toàn bộ các tính năng production nâng cao như manual review UI, embedding hoặc LLM matching trong giai đoạn đầu.

---

## 2. Các phần thiết kế đã đúng

### 2.1. Observation–Entity–Link model

Namespace hiện tại:

```text
s3://silver/
├── observations/
├── entities/
├── entity_links/
├── quarantine/
└── audit_logs/
```

Ý nghĩa đúng:

```text
observations/:
    Giữ toàn bộ lịch sử observation đã chuẩn hóa từ từng nguồn.
    Không xóa observation vì lý do trùng lặp.

entities/:
    Canonical record đại diện cho vị trí tuyển dụng hợp nhất.
    Là đầu vào của Gold.

entity_links/:
    Liên kết canonical entity với observation nguồn.
    Lưu bằng chứng và rule matching.
```

Đây là kiến trúc đúng hơn việc chỉ giữ một bản ghi sau dedup rồi xóa các bản ghi còn lại.

### 2.2. Phân vùng thời gian

Cách chọn partition hiện tại là hợp lý:

```text
observations/  → observed_at
entities/      → posted_date
entity_links/  → matched_at
```

Lý do đúng:

- observation cần partition theo ngày hệ thống nhìn thấy/xử lý dữ liệu;
- entity phục vụ phân tích nghiệp vụ theo ngày đăng tin;
- entity link cần audit theo thời điểm matching;
- tin cũ có thể được cập nhật hôm nay;
- một số nguồn thiếu `posted_date`;
- cùng entity có thể xuất hiện trong nhiều ngày quan sát.

### 2.3. Identity và matching

Thiết kế đã phân biệt đúng:

```text
source_job_id:
    ID gốc trong phạm vi một platform.

observation_id:
    ID của một lần quan sát đã chuẩn hóa.

unified_job_id:
    ID ổn định của canonical entity xuyên nhiều nguồn.
```

Thứ tự matching cũng hợp lý:

```text
1. platform + source_job_id
2. canonical_url
3. exact normalized identity
4. fuzzy candidate matching
```

Công thức điểm được đặc tả rõ:

```text
match_score =
    0.40 * title_similarity
  + 0.25 * company_similarity
  + 0.15 * location_similarity
  + 0.10 * salary_overlap
  + 0.10 * description_similarity
```

Ngưỡng được chốt:

```text
match_score >= 0.90:
    AUTO_MATCH

0.75 <= match_score < 0.90:
    REVIEW_REQUIRED

match_score < 0.75:
    DISTINCT_ENTITY
```

Đây là mức đủ rõ để triển khai MVP.

### 2.4. False-positive guard

Các guard đã bao phủ những trường hợp dễ gộp sai:

- công ty khác nhau;
- địa điểm khác nhau;
- cấp bậc khác nhau;
- internship so với senior;
- Full-time so với Part-time;
- salary mâu thuẫn;
- mô tả khác bản chất;
- title chỉ giống vì cùng chứa từ khóa phổ biến.

Đây là phần quan trọng vì false positive nguy hiểm hơn việc bỏ sót một candidate match.

### 2.5. Lifecycle và history

Thiết kế đã phân biệt đúng giữa snapshot lặp và thay đổi nghiệp vụ:

```text
same source_job_id + same content_hash:
    Observation lặp/snapshot giống nhau.

same source_job_id + content_hash khác:
    Observation mới của cùng entity.
    entity_status = UPDATED.

Không thấy tin một lần:
    Chưa đánh dấu CLOSED.

Không thấy trong grace period:
    entity_status = CLOSED.

Tin CLOSED xuất hiện lại:
    entity_status = REOPENED.
```

Việc cấu hình grace period theo source là hợp lý vì vòng đời tin ở mỗi nền tảng không giống nhau.

### 2.6. Quality Gate và quarantine

Các rule quality gate hiện có bao phủ các lỗi chính:

- thiếu `source_job_id`;
- thiếu `job_title`;
- thiếu `company_name`;
- salary min lớn hơn salary max;
- salary outlier thấp/cao;
- posted date ở tương lai;
- posted date quá cũ.

Việc lưu quarantine kèm:

```text
rejection_reason
quarantined_at
audit_run_id
raw_payload_snippet
```

là phù hợp cho audit và điều tra dữ liệu.

### 2.7. Audit và idempotency

Thiết kế đã có các chỉ số quan trọng:

```text
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

và có `resolution_version` để phân biệt các phiên bản thuật toán.

18 acceptance tests cũng đã bao phủ:

- source identity;
- canonical URL;
- normalized identity;
- false positive;
- salary overlap;
- thiếu salary;
- content update;
- CLOSED/REOPENED;
- fuzzy threshold;
- rerun idempotency;
- resolution version;
- giữ observation lịch sử;
- entity nhiều platform.

---

## 3. Các điểm bắt buộc phải sửa trước khi code

### 3.1. Mâu thuẫn số lượng canonical fields

Tài liệu ghi:

```text
Schema observations: 41 trường Canonical
```

nhưng bảng thực tế đang đánh số đến:

```text
46 platform_specific_attributes
```

Ngoài ra tài liệu còn có các trường bổ sung như:

```text
observation_id

description_normalized
```

Vì vậy số lượng hiện tại không còn là 41.

#### Khuyến nghị

Đổi thành:

```text
Schema observations: 46 trường canonical
```

Nếu có 4 partition columns:

```text
platform
year
month
day
```

thì ghi rõ đây là partition metadata, không tính vào business schema.

Nếu quarantine có thêm 4 audit fields thì tổng số cột là:

```text
46 + 4 = 50 trường
```

Không nên tiếp tục ghi `41 trường` và `45 trường` nếu bảng thực tế đã có 46 trường.

### 3.2. `observation_id` cần delimiter cố định

Công thức hiện tại:

```text
observation_id = SHA256(
    platform
    + source_job_id
    + observed_at
    + content_hash
)
```

Nên ghi rõ phải có delimiter và UTF-8:

```python
observation_id = sha256(
    f"{platform}|{source_job_id}|{observed_at}|{content_hash}".encode("utf-8")
).hexdigest()
```

Nếu không có delimiter, các chuỗi khác nhau có thể tạo cùng input string trước khi hash.

### 3.3. Entity registry cần schema và transaction contract

Tài liệu đã nói có `entity_registry`, nhưng cần bổ sung các trường tối thiểu:

```text
entity_registry:
    unified_job_id PRIMARY KEY
    identity_fingerprint UNIQUE
    canonical_title
    canonical_company
    canonical_city
    entity_status
    first_seen_at
    last_seen_at
    resolution_version
    created_at
    updated_at
```

Cần chốt thêm:

- `unified_job_id` chỉ được tạo một lần;
- registry phải được đọc/ghi transactionally;
- rerun ETL không tạo entity mới;
- hai task Silver chạy đồng thời không tạo hai entity cho cùng identity;
- `identity_fingerprint` phải ổn định qua các lần cập nhật title/salary/description.

### 3.4. DAG đang minh họa thiếu nền tảng

Phần DAG hiện chỉ minh họa 4 crawler cũ:

```text
crawl_itviec
crawl_topcv
crawl_topdev
crawl_vietnamworks
```

Trong khi dự án hiện có 9 platform:

```text
careerviet
upzi
vietnamdevs
vieclam24h
devwork
itviec
topcv
topdev
vietnamworks
```

Cần sửa sơ đồ và mô tả thành đủ 9 task hoặc dùng dấu `...` kèm bảng danh sách đủ 9 nguồn.

### 3.5. Thứ tự Bronze audit đang sai

Flow hiện tại đặt:

```text
crawl
    ↓
transform observations
    ↓
entity resolution
    ↓
quality gate
    ↓
bronze audit
```

Nên sửa thành:

```text
preflight
    ↓
9 crawler tasks
    ↓
bronze_audit_after_crawl
    ↓
transform_bronze_to_observations
    ↓
entity_resolution_matching
    ↓
entity_lifecycle
    ↓
quality_gate_and_quarantine
    ↓
Silver writer
    ↓
summary
```

Nếu Bronze audit thất bại, không nên tiếp tục đọc Bronze để tạo Silver.

### 3.6. Cần chốt policy partial run

Tài liệu hiện dùng:

```text
trigger_rule = NONE_FAILED_MIN_ONE_SUCCESS
```

Điều này cho phép Silver chạy khi chỉ một phần crawler thành công. Với entity resolution toàn hệ thống, điều này có thể tạo kết quả không đầy đủ và dễ đánh dấu entity `CLOSED` sai.

#### Khuyến nghị MVP

Dùng:

```text
trigger_rule = ALL_SUCCESS
```

cho Bronze audit và Silver resolution toàn hệ thống.

Nếu muốn cho phép partial run, phải đặc tả thêm:

```text
partial_run = true
failed_sources
blocked_sources
coverage_warning
```

và bắt buộc:

- không đánh dấu `CLOSED` dựa trên source bị lỗi;
- audit ghi rõ source bị thiếu;
- alert người vận hành;
- không kết luận dữ liệu đã đồng bộ đầy đủ.

Không nên dùng `NONE_FAILED_MIN_ONE_SUCCESS` mà không có partial-run semantics.

### 3.7. Idempotency claim của `overwrite_or_ignore` quá mạnh

Tài liệu hiện ghi:

```text
existing_data_behavior="overwrite_or_ignore"
```

và kết luận rằng nó đảm bảo idempotency tuyệt đối.

Nên đổi thành:

```text
Ghi idempotent ở mức partition/file khi sử dụng deterministic output key.
Entity registry và entity_links phải có identity key và upsert riêng.
```

`overwrite_or_ignore` không tự đảm bảo transaction giữa:

- local temp;
- MinIO upload;
- entity registry;
- entity links;
- audit log.

Cần có:

```text
run_id
resolution_version
deterministic partition key
record count verification
manifest/audit commit
```

### 3.8. `pandas.DataFrame` cần giới hạn batch

Tài liệu ghi:

```text
Tập hợp records hợp lệ → pd.DataFrame
```

Điều này chấp nhận được ở MVP với dataset nhỏ, nhưng không nên đồng thời khẳng định memory bounded tuyệt đối.

Nên ghi:

```text
MVP:
    Pandas được phép dùng theo batch bounded.

Scale-up:
    chuyển sang PyArrow Table, PySpark hoặc DuckDB streaming.
```

Phải có giới hạn batch rõ ràng nếu dữ liệu tăng.

### 3.9. Quality Gate cần phân loại hard failure và warning

Các rule salary outlier có thể loại nhầm dữ liệu hợp lệ như:

- internship;
- part-time;
- hourly salary;
- nguồn ghi salary không đầy đủ.

Nên chia thành:

```text
Hard failure:
    thiếu source_job_id
    thiếu job_title
    thiếu company_name
    salary_min > salary_max

Warning hoặc review/quarantine:
    salary quá thấp
    salary quá cao
    posted_date quá cũ
```

Tài liệu cần ghi rõ rule nào là quarantine cứng và rule nào là cảnh báo.

### 3.10. SSIS và PostgreSQL Gold cần thống nhất

Flow hiện tại vẫn có:

```text
export_silver_staging_for_ssis
```

Trong khi kiến trúc đích đang hướng tới:

```text
Silver Parquet → Gold PostgreSQL → Power BI
```

Nếu SSIS không còn là pipeline chính, đổi task thành:

```text
load_gold_postgresql
```

hoặc:

```text
publish_silver_for_gold
```

SSIS có thể ghi là adapter tùy chọn, nhưng không nên để là bước bắt buộc trong pipeline chính nếu Gold dùng PostgreSQL.

### 3.11. Timeout chưa đồng bộ

Tài liệu Silver ghi:

```text
execution_timeout: 60 phút
```

DAG Bronze hiện đang thiết kế crawler task với timeout khoảng 2 giờ.

Cần chọn một giá trị duy nhất và đồng bộ giữa:

- Notion;
- DAG code;
- systemd/Airflow runtime;
- acceptance test.

### 3.12. Grace period cần đủ 9 nền tảng

Tài liệu mới chỉ liệt kê một phần source. Cần chốt đủ 9 nền tảng:

```python
SOURCE_GRACE_PERIOD_DAYS = {
    "careerviet": 14,
    "upzi": 14,
    "vietnamdevs": 14,
    "vieclam24h": 14,
    "devwork": 14,
    "itviec": 7,
    "topcv": 14,
    "topdev": 10,
    "vietnamworks": 21,
}
```

Đây là giá trị MVP ban đầu, không phải giá trị bất biến. Cần ghi rõ có thể điều chỉnh sau khi quan sát thực tế.

### 3.13. Placeholder trong acceptance test

Trong test entity nhiều platform, tài liệu đang ghi:

```text
source_count = NNN
```

Cần đổi thành:

```text
source_count bằng số platform thực tế trong entity_links.
```

### 3.14. Số lượng pipeline step chưa thống nhất

Tài liệu ghi:

```text
Tái cấu trúc pipeline ETL 7 trạm
```

nhưng flow thực tế có nhiều task hơn. Nên đổi thành:

```text
Tái cấu trúc pipeline ETL nhiều bước tích hợp Entity Resolution
```

hoặc liệt kê chính xác số task.

---

## 4. Phạm vi MVP và Production Enhancement

### 4.1. MVP bắt buộc

Silver MVP phải có:

- observations  chuẩn hóa;
- `source_job_id`;
- `observation_id`;
- `unified_job_id`;
- exact source identity;
- canonical URL matching;
- normalized company/title/location matching;
- fuzzy matching ở mức candidate review;
- `entity_links`;
- lifecycle `NEW`, `ACTIVE`, `UPDATED`, `CLOSED`, `REOPENED`;
- quality gate;
- quarantine;
- audit log;
- rerun idempotency;
- acceptance tests;
- Gold handoff rõ ràng.

### 4.2. Production enhancement, chưa bắt buộc

Có thể để phase sau:

- `MERGED`/`SPLIT` workflow nâng cao;
- giao diện manual review;
- embedding similarity;
- LLM entity resolution;
- cross-language semantic matching;
- active learning từ quyết định manual;
- distributed entity registry;
- real-time streaming resolution;
- nhiều phiên bản matcher chạy song song.

Không nên triển khai các phần production này trong MVP nếu chưa có nhu cầu thực tế.

---

## 5. Checklist triển khai sau khi sửa tài liệu

Trước khi Hermes bắt đầu code, cần sửa tối thiểu:

- [ ] Thống nhất số lượng canonical fields: 41 hay 46; khuyến nghị dùng 46.
- [ ] Sửa công thức `observation_id` có delimiter UTF-8.
- [ ] Bổ sung schema và transaction contract cho `entity_registry`.
- [ ] Sửa DAG đủ 9 crawler.
- [ ] Đưa `bronze_audit_after_crawl` lên trước Silver ETL.
- [ ] Chốt `ALL_SUCCESS` hoặc đặc tả đầy đủ partial-run semantics.
- [ ] Sửa claim `overwrite_or_ignore` thành idempotency ở mức partition/file.
- [ ] Ghi rõ Pandas chỉ dùng bounded batch trong MVP.
- [ ] Phân loại quality rules thành hard failure và warning/review.
- [ ] Thống nhất Gold PostgreSQL với SSIS là adapter tùy chọn.
- [ ] Đồng bộ `execution_timeout` giữa Notion và DAG.
- [ ] Bổ sung grace period cho đủ 9 platform.
- [ ] Sửa placeholder `NNN` trong acceptance test.
- [ ] Đổi “ETL 7 trạm” thành mô tả đúng số bước.

Sau khi hoàn tất checklist, tài liệu đủ rõ để Hermes triển khai Silver MVP.

# Bổ sung thiết kế Silver — Cross-source Entity Resolution & Deduplication

> **Vị trí chèn vào tài liệu Silver:** đặt ngay sau phần `GIAI ĐOẠN 2: HỢP ĐỒNG DỮ LIỆU — SCHEMA 3 LỚP`, trước Quality Gate, ETL output và Gold handoff.
>
> **Phạm vi:** 9 nền tảng tuyển dụng IT.
>
> **Nguyên tắc:** Silver không xóa observation lịch sử. Silver tạo canonical entity và lưu liên kết giữa entity với các observation nguồn.

---

## GIAI ĐOẠN 3: CHUẨN HÓA IDENTITY VÀ ENTITY RESOLUTION ĐA NGUỒN

### 3.1. Mục tiêu

Tầng Silver phải xử lý hai vấn đề khác nhau:

1. Giữ toàn bộ lịch sử quan sát của từng nguồn.
2. Nhận diện và hợp nhất các observation đại diện cho cùng một tin tuyển dụng giữa nhiều nền tảng.

Silver không được xóa observation gốc chỉ vì phát hiện trùng lặp.

Luồng xử lý:

```text
Bronze observation
    → Silver observation chuẩn hóa
    → Chuẩn hóa identity
    → Exact matching
    → Normalized matching
    → Fuzzy candidate matching
    → Gán unified_job_id
    → Ghi entities
    → Ghi entity_links
    → Quality Gate
    → Gold
```

Phải phân biệt rõ:

```text
Duplicate observation:
    Cùng một tin được crawl nhiều lần.

Cross-source duplicate:
    Cùng một vị trí tuyển dụng xuất hiện trên nhiều nền tảng.

Updated observation:
    Cùng một tin nhưng nội dung, lương hoặc deadline thay đổi.
```

Ba trường hợp trên không được xử lý như nhau.

### 3.2. Mô hình ba lớp dữ liệu

#### Lớp 1 — `observations/`

`observations/` chứa toàn bộ bản ghi Silver đã chuẩn hóa từ từng nguồn.

Mỗi observation vẫn giữ thông tin nguồn gốc:

```text
platform
source_job_id
source_url
canonical_url
observed_at
ingested_at
content_hash
job_title
job_title_normalized
company_name
company_name_normalized
city
city_normalized
salary_min_vnd
salary_max_vnd
job_description_clean
```

Một tin được crawl lại vào ngày khác vẫn tạo observation mới nếu đó là một lần quan sát hợp lệ.

Không xóa observation lịch sử vì lý do dedup.

#### Lớp 2 — `entities/`

`entities/` chứa canonical record đại diện cho một tin tuyển dụng hoặc một vị trí tuyển dụng duy nhất trên toàn hệ thống.

Một entity có thể liên kết tới một hoặc nhiều platform:

```text
unified_job_id = uj_abc123

Nguồn liên kết:
    vieclam24h / source_job_id=200975007
    devwork    / source_job_id=2759
```

`entities/` là nguồn dữ liệu chính cho Tầng Gold.

#### Lớp 3 — `entity_links/`

`entity_links/` lưu quan hệ giữa canonical entity và các observation nguồn.

Mục đích:

- biết entity được tạo từ observation nào;
- biết những nguồn nào đã được gộp;
- biết rule nào đã quyết định match;
- biết điểm tương đồng là bao nhiêu;
- phục vụ audit và sửa lại matching trong tương lai.

---

## 4. Các loại định danh

### 4.1. `source_job_id`

`source_job_id` là ID nguyên bản của tin trên từng nền tảng.

Ví dụ:

```text
platform = vieclam24h
source_job_id = 200975007
```

```text
platform = devwork
source_job_id = 2759
```

`source_job_id` chỉ có ý nghĩa duy nhất trong phạm vi một platform.

Không được dùng riêng `source_job_id` để dedup xuyên các nguồn.

### 4.2. `observation_id`

`observation_id` định danh một lần quan sát đã chuẩn hóa:

```text
observation_id = SHA256(
    platform
    + source_job_id
    + observed_at
    + content_hash
)
```

Mục đích:

- phân biệt cùng tin ở các ngày khác nhau;
- phân biệt hai phiên bản nội dung khác nhau;
- hỗ trợ idempotency khi chạy lại ETL.

### 4.3. `unified_job_id`

`unified_job_id` là ID ổn định của canonical entity.

Không tạo `unified_job_id` trực tiếp từ title, salary hoặc description hiện tại vì các trường này có thể thay đổi.

`unified_job_id` phải được quản lý qua entity registry hoặc identity mapping table:

```text
entity_registry:
    unified_job_id
    identity_fingerprint
    created_at
    updated_at
    resolution_version
```

Khi title, salary hoặc description thay đổi, entity vẫn giữ nguyên `unified_job_id`.

Chỉ tạo `unified_job_id` mới khi hệ thống xác định đó là một vị trí tuyển dụng khác.

---

## 5. Schema của `entities/`

`entities/` phải có tối thiểu các trường:

| Trường | Mô tả |
|---|---|
| `unified_job_id` | ID canonical ổn định |
| `canonical_title` | Tiêu đề đại diện |
| `canonical_company` | Công ty đại diện |
| `canonical_location` | Địa điểm đại diện |
| `canonical_salary_min_vnd` | Lương tối thiểu |
| `canonical_salary_max_vnd` | Lương tối đa |
| `canonical_currency` | Đơn vị tiền tệ |
| `canonical_description` | Mô tả đại diện |
| `source_count` | Số nền tảng chứa tin |
| `observation_count` | Số observation lịch sử |
| `first_seen_at` | Thời điểm nhìn thấy entity lần đầu |
| `last_seen_at` | Thời điểm nhìn thấy entity gần nhất |
| `entity_status` | Trạng thái entity |
| `canonical_updated_at` | Thời điểm canonical record cập nhật |
| `resolution_version` | Phiên bản thuật toán matching |

Các giá trị hợp lệ của `entity_status`:

```text
NEW
ACTIVE
UPDATED
CLOSED
REOPENED
MERGED
SPLIT
```

---

## 6. Schema của `entity_links/`

`entity_links/` phải có tối thiểu các trường:

| Trường | Mô tả |
|---|---|
| `unified_job_id` | ID entity canonical |
| `observation_id` | ID observation nguồn |
| `platform` | Nền tảng tuyển dụng |
| `source_job_id` | ID gốc trên nền tảng |
| `source_url` | URL gốc |
| `canonical_url` | URL đã normalize |
| `match_method` | Rule đã dùng để match |
| `match_score` | Điểm tương đồng từ 0 đến 1 |
| `match_status` | Trạng thái quyết định |
| `matched_at` | Thời điểm matching |
| `matcher_version` | Phiên bản matcher |
| `review_reason` | Lý do cần review |
| `previous_unified_job_id` | ID cũ nếu entity được tách/gộp lại |
| `resolution_version` | Phiên bản resolution |

Các giá trị hợp lệ của `match_status`:

```text
AUTO_MATCH
REVIEW_REQUIRED
DISTINCT_ENTITY
UNMATCHED
MANUAL_MATCH
```

Các giá trị hợp lệ của `match_method`:

```text
SOURCE_JOB_ID
CANONICAL_URL
EXACT_NORMALIZED_IDENTITY
FUZZY_TITLE_COMPANY_LOCATION
MANUAL_REVIEW
```

---

## 7. Chuẩn hóa identity trước khi matching

Trước khi matching, phải tạo các trường normalized:

```text
job_title_normalized
company_name_normalized
city_normalized
canonical_url
description_normalized
salary_min_vnd
salary_max_vnd
```

Quy tắc normalize:

- chuyển thành lowercase;
- chuẩn hóa Unicode;
- loại bỏ hoặc chuẩn hóa dấu tiếng Việt cho mục đích so sánh;
- chuẩn hóa whitespace;
- loại bỏ punctuation không có ý nghĩa;
- chuẩn hóa tên pháp nhân như `Công ty TNHH`, `JSC`, `Ltd`;
- chuẩn hóa alias thành phố như `TP.HCM`, `Hồ Chí Minh`, `Ho Chi Minh City`;
- loại bỏ tracking query parameters như `utm_*`, `ref`, `source`, `search_id`;
- giữ lại path định danh tin tuyển dụng;
- không thay thế giá trị gốc; luôn giữ cả raw field và normalized field.

---

## 8. Thứ tự matching

Matching phải chạy theo thứ tự từ chắc chắn đến không chắc chắn.

### 8.1. Rule 1 — Source-native identity

Trong cùng một platform:

```text
platform + source_job_id
```

Nếu đã tồn tại:

- coi là cùng source job;
- không tạo entity mới;
- nếu content hash thay đổi thì đánh dấu `UPDATED`;
- tạo observation mới nếu đây là lần quan sát mới.

```text
match_method = SOURCE_JOB_ID
match_score = 1.0
match_status = AUTO_MATCH
```

### 8.2. Rule 2 — Canonical URL

Nếu hai observation có cùng `canonical_url`:

```text
match_method = CANONICAL_URL
match_score = 1.0
match_status = AUTO_MATCH
```

Chỉ sử dụng rule này khi URL thực sự xác định cùng một tin. Không gộp chỉ vì hai URL có cùng hostname hoặc cùng category path.

### 8.3. Rule 3 — Exact normalized identity

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

- company normalized phù hợp;
- title normalized phù hợp;
- city normalized phù hợp;
- không có mâu thuẫn rõ ràng về cấp bậc hoặc loại hình công việc.

```text
match_method = EXACT_NORMALIZED_IDENTITY
match_score = 0.98
match_status = AUTO_MATCH
```

### 8.4. Rule 4 — Fuzzy candidate matching

Chỉ chạy fuzzy matching khi ba rule phía trên không tìm được match.

Công thức điểm:

```text
match_score =
    0.40 * title_similarity
  + 0.25 * company_similarity
  + 0.15 * location_similarity
  + 0.10 * salary_overlap
  + 0.10 * description_similarity
```

Các thành phần:

```text
title_similarity:
    Độ tương đồng token sau normalize.

company_similarity:
    Độ tương đồng tên công ty đã normalize.

location_similarity:
    Độ tương đồng tỉnh/thành phố và địa điểm.

salary_overlap:
    1.0 nếu hai khoảng lương giao nhau.
    0.5 nếu một bên không có dữ liệu lương.
    0.0 nếu khoảng lương mâu thuẫn rõ ràng.

description_similarity:
    Độ tương đồng token trên mô tả đã làm sạch.
```

Ngưỡng:

```text
match_score >= 0.90:
    AUTO_MATCH

0.75 <= match_score < 0.90:
    REVIEW_REQUIRED

match_score < 0.75:
    DISTINCT_ENTITY
```

Không được auto-match chỉ dựa trên title giống nhau.

---

## 9. False-positive guard

Không được tự động gộp nếu có một trong các điều kiện:

- công ty khác nhau rõ ràng;
- thành phố khác nhau và cả hai không phải remote;
- cấp bậc khác nhau nghiêm trọng;
- một tin là internship còn tin kia là senior;
- loại hình Full-time và Part-time mâu thuẫn rõ ràng;
- khoảng lương mâu thuẫn rõ ràng;
- mô tả công việc khác nhau về bản chất;
- title chỉ giống do cùng chứa từ khóa phổ biến như `Developer`, `Engineer`, `Manager`;
- một tin tuyển nhiều vị trí khác nhau nhưng bị gom nhầm từ title chung.

Trường hợp không chắc chắn phải dùng:

```text
match_status = REVIEW_REQUIRED
```

Không được ép tự động thành `AUTO_MATCH`.

---

## 10. Quản lý lịch sử entity

Cùng một tin được crawl nhiều lần không phải là duplicate semantic cần xóa.

Ví dụ:

```text
2026-11-15:
    salary = 20,000,000

2026-11-16:
    salary = 25,000,000
```

Đây là hai observation của cùng `unified_job_id`, trong đó observation mới là phiên bản cập nhật.

Quy tắc:

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

Grace period phải cấu hình theo source, không hardcode một giá trị chung nếu hành vi các sàn khác nhau.

---

## 11. Canonical record selection

Khi nhiều observation thuộc cùng một entity, canonical record được chọn theo thứ tự:

1. observation mới nhất và vượt Quality Gate;
2. có description đầy đủ hơn;
3. có salary hợp lệ;
4. có deadline hợp lệ;
5. có location chuẩn hóa;
6. source có độ tin cậy cao hơn theo cấu hình;
7. nếu vẫn hòa, chọn observation có `observed_at` mới nhất.

Các observation không được chọn làm canonical vẫn được giữ trong:

```text
observations/
entity_links/
```

Không được xóa observation chỉ vì không được chọn làm canonical record.

---

## 12. Quality Gate sau entity resolution

Một entity chỉ được ghi vào:

```text
s3://silver/entities/
```

khi:

- có `unified_job_id`;
- có canonical title;
- có canonical company;
- có entity status hợp lệ;
- liên kết tới ít nhất một observation hợp lệ;
- không vi phạm false-positive guard;
- có source link và resolution metadata;
- không bị đánh dấu `REVIEW_REQUIRED` nếu policy chưa cho phép đưa vào canonical entities.

Các entity hoặc observation không đạt phải ghi vào:

```text
s3://silver/quarantine/
```

kèm:

```text
quarantine_reason
quality_rule
resolution_status
```

---

## 13. Audit và idempotency

Mỗi lần chạy entity resolution phải ghi vào `audit_logs/`:

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

Không tạo entity mới chỉ vì ETL chạy lại.

Nếu resolution algorithm thay đổi, tăng phiên bản:

```text
resolution_version = v2
```

và lưu audit thay đổi:

```text
old_unified_job_id
new_unified_job_id
old_match_score
new_match_score
change_reason
changed_at
```

---

## 14. Acceptance tests bắt buộc

Trước khi chạy trên toàn bộ dữ liệu 9 nền tảng, phải có test cho:

1. Cùng `platform + source_job_id` qua hai lần crawl.
2. Cùng canonical URL giữa hai nguồn.
3. Cùng company/title/location sau normalize.
4. Title giống nhưng company khác.
5. Cùng company/title nhưng location khác.
6. Internship và senior có title tương tự.
7. Hai khoảng salary giao nhau.
8. Một record thiếu salary.
9. Content thay đổi nhưng vẫn là cùng entity.
10. Entity chuyển từ `ACTIVE` sang `CLOSED`.
11. Entity `CLOSED` xuất hiện lại và chuyển sang `REOPENED`.
12. Fuzzy score trong vùng `REVIEW_REQUIRED`.
13. Fuzzy score dưới ngưỡng `DISTINCT_ENTITY`.
14. Chạy lại cùng input không tạo entity mới.
15. Thay đổi `resolution_version` có audit.
16. Observation vẫn tồn tại sau khi entity được merge.
17. Một entity liên kết tới nhiều platform.
18. Hai tin khác nhau không bị false-positive merge.

Chỉ sau khi các test trên đạt mới được nối Silver vào DAG Airflow.

---

## 15. Ranh giới với Bronze và Gold

```text
Bronze:
    Giữ toàn bộ extracted observations theo crawl run.
    Không dedup semantic đa nguồn.

Silver observations:
    Chuẩn hóa từng source record.
    Giữ toàn bộ lịch sử observation.

Silver entities:
    Hợp nhất cùng một tin tuyển dụng.
    Tạo canonical record.

Silver entity_links:
    Lưu liên kết và bằng chứng matching.

Gold:
    Đọc canonical entities.
    Tạo fact/dimension phục vụ SQL, Power BI và phân tích.
```

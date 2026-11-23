# Sửa dữ liệu dashboard: vì sao phần lớn là "Chưa xác định"

Ngày: 2026-11-23 · Phạm vi: Silver transform + TopCV crawler + Gold release

## 1. Triệu chứng

Dashboard (release `gold_20261119_…`) hiển thị:

| Trục | Giá trị lớn nhất | Số tin |
| --- | --- | --- |
| Chuyên môn | (null) | 919 / 3.092 |
| Địa điểm | Khác | 1.293 |
| Địa điểm | UNKNOWN | 1.285 |
| Cấp bậc | (null) | 3.052 / 3.092 (98,7%) |
| Công ty | `{{ company?.name }}` | 716 |

## 2. Nguyên nhân (đo trên Bronze, không suy đoán)

Truy vấn trực tiếp 60 object Bronze và 5.683 record:

1. **TopCV (765 tin) là trang Angular chưa render.** Crawler đọc `div.company-name` và nhận
   đúng chuỗi template `{{ company?.name }}`; `job_location`, `job_level`, `experience`,
   `industry_knowledge` đều rỗng. Đây là lỗi bóc tách phía crawler, không phải Gold.
2. **VietnamWorks (506 tin) có địa điểm nhưng Silver không đọc.** Payload có
   `job_locations: "Hanoi / HCMC"`; transform chỉ đọc `job_location` / `location` / `address`.
3. **Chuẩn hoá địa điểm quá hẹp.** Hàm chỉ so khớp đúng vài alias; mọi giá trị khác
   ("Quận Cầu Giấy, Hà Nội", "TP.HCM", "Ho Chi Minh City", "Tây Hồ, Hà Nội") rơi vào "Khác".
4. **Cấp bậc chỉ so khớp 7 nhãn tiếng Anh.** Nguồn ghi "Experienced (non-manager)",
   "Chuyên viên cấp cao", "Nhân viên", "Fresher/Entry level" → không khớp → NULL.
5. **itviec trả `experience_requirements` dạng dict** `{"monthsOfExperience": 37}`;
   `_parse_experience` đọc số 37 thành *37 năm* → 1.200 kỹ sư bị xếp "Lead".
6. **Chuyên môn chỉ itviec có `job_domain`.** Tám nền tảng còn lại NULL hoặc nhãn tiếng Anh
   rời rạc (20 nhóm khác nhau) nên biểu đồ vừa thiếu vừa không so sánh được.
7. **Registry giữ observation cũ.** Replay dùng lại `observation_id` cũ nên entity vẫn mang
   company/city/level của lần transform trước — sửa transform mà dashboard không đổi.

## 3. Thay đổi

`src/etl/transformers.py`

- `normalize_city`: bỏ dấu, tìm thành phố trong chuỗi địa chỉ, chọn thành phố xuất hiện sớm
  nhất (Hanoi / HCMC → Hà Nội), ngoài VN → "Khác", rỗng → NULL.
- `classify_job_level`: đọc nhãn nguồn (kể cả tiếng Việt), rồi tới title (regex có word
  boundary), rồi số năm kinh nghiệm; hiểu `monthsOfExperience`.
- `classify_job_domain`: gom mọi tín hiệu (domain/industry/function/title/skills) về 8 nhóm:
  Phát triển phần mềm, Dữ liệu & AI, Hạ tầng & Cloud, An ninh mạng, QA & Kiểm thử,
  Phân tích & Quản lý, Di động & Nhúng, Khác.
- `is_placeholder_company`: `{{ … }}`, `${ … }`, "Công ty đang tuyển dụng", "UNKNOWN" →
  `company_name = "UNKNOWN"`, `company_name_normalized = ""`, giữ nguyên `company_name_raw`.
- `city_raw` đọc thêm `job_locations` và `company_location`.
- Version bump: `city-normalization-v2`, `company-normalization-v2`,
  `bronze-to-silver-transform-v2`.

`src/crawlers/topcv_crawler.py`

- Trang template chưa render (company `{{ … }}` và location/level/experience rỗng) → raise
  `ValueError`, record bị bỏ và run kết thúc `PARSER_FAILURE` thay vì ghi rác vào Bronze.

`src/etl/bronze_to_silver.py` + `src/storage/entity_registry.py`

- Replay thay thế bản ghi observation cũ bằng bản mới cùng `observation_id`.
- Entity mirror lại các facet từ canonical observation, và registry được cập nhật cùng lúc
  (`refresh_entity_canonical`) để Parquet và SQLite không lệch nhau.

`src/gold/silver_release_reader.py`, `src/gold/data_quality.py`

- So sánh description sau khi chuẩn hoá bullet/khoảng trắng.
- Lệch description (trường hiển thị, registry thắng theo D-G06) là `DESCRIPTION_DIVERGENCE`
  + check WARN `DQ-24b`, không chặn publish; title/company/city/status/salary vẫn BLOCK.

## 4. Kết quả sau khi replay + publish

Silver release `silver_release_manual_facet_fix_20261123_162842` (5.490 observation,
3.957 entity, 198 quarantine) → Gold `gold_20261123_facet_fix_v4`, 33/33 DQ, snapshot
2026-11-23, đang là `active_release`.

| Trục | Trước (`gold_20261119_…`) | Sau (`gold_20261123_facet_fix_v4`) |
| --- | --- | --- |
| Chuyên môn | 919 null (30%) + 20 nhãn tiếng Anh rời rạc | 8 nhóm; "Phát triển phần mềm" 1.269 (32%), "Dữ liệu & AI" 666 (17%), chưa xác định 711 (18%) |
| Địa điểm | HCM 253 · HN 252 · "Khác" 1.293 · UNKNOWN 1.285 | HCM 1.294 (32%) · HN 1.096 (27%) · Đà Nẵng 21 · Khác 201 · UNKNOWN 1.345 (33%) |
| Cấp bậc | null 3.052 (98,7%) | Mid 1.102 (27%) · Lead 466 · Junior 406 · Senior 390 · Fresher 217 · Intern 126 · Director 39 · chưa xác định 1.211 (30%) |
| Công ty | `{{ company?.name }}` 716 tin | 0 tin; nhóm "UNKNOWN" 1.310 tin, còn lại là tên công ty thật |

Số tin hiển thị đổi từ 3.092 → 3.957 vì trước đây 716 tin TopCV dùng chung chuỗi
`{{ company?.name }}` nên bị gộp nhầm thành một số ít entity; bỏ placeholder thì các tin
được tách đúng và không còn entity giả.

`UNKNOWN` còn lại tập trung ở: (a) 765 observation TopCV không có company/city/level trong
Bronze, (b) entity đang mở nhưng không có observation mới trong release này.

## 5. Giới hạn còn lại

- 765 tin TopCV cũ vẫn thiếu company/city/level vì dữ liệu Bronze đã lưu là trang chưa render.
  Muốn đầy đủ phải crawl lại TopCV khi hết 403 (crawler nay fail-closed, không ghi rác).
- `work_mode` vẫn thiếu nhiều: 6/9 nền tảng không công bố hình thức làm việc.
- Domain/cấp bậc là suy luận từ tiêu đề + nhãn nguồn, không phải nhãn do nguồn công bố; mọi
  thay đổi quy tắc đều bump version và ghi vào `gold_control.load_run`.

## 6. Vì sao "Công ty nhiều tin nhất" vẫn có UNKNOWN

Đo trên release `silver_release_manual_facet_fix_20261123_162842`: 1.310 entity không có
tên công ty, trong đó **1.280 đến từ TopCV** và **30 từ Devwork** — không có nền tảng nào
khác. Nguyên nhân:

- TopCV: **lỗi selector ở crawler**. HTML của TopCV có tên công ty thật trong
  `.box-company-info-detail .company-name-label a.name`, nhưng parser cũ tìm
  `div.company-name` — node template Angular — nên lưu đúng chuỗi `{{ company?.name }}`.
  Đã sửa: đọc đúng block công ty (tên, quy mô, địa chỉ, ngành nghề) và đọc địa điểm/cấp
  bậc từ `.job-detail__info--section-content`; fixture lấy từ trang thật nằm ở
  `tests/fixtures/topcv_job_detail.html`.
- Devwork: nền tảng này ẩn nhà tuyển dụng, ghi literal "Công ty đang tuyển dụng".

Dữ liệu Bronze cũ chỉ lưu field đã bóc tách (không lưu HTML gốc), nên 1.280 tin đã cào
không thể sửa hồi tố — phải crawl lại. TopCV hiện trả **HTTP 403** kể cả khi gửi cookie
phiên (`cf_clearance`, `topcv_session`) và header trình duyệt đầy đủ, nên lần crawl lại
đang bị chặn.

Đã sửa ở Gold: `resolve_company_key` coi marker `UNKNOWN`/`N/A`/rỗng là thành viên
`is_unknown` thay vì tạo một công ty thật tên "UNKNOWN". Vì vậy:

- `dim_company` gắn cờ `is_unknown = true`;
- API `/api/companies` và khối "Công ty nhiều tin nhất" loại thành viên này khỏi bảng xếp
  hạng (nay bắt đầu bằng Bosch 87, VNPT 41, Vinsmart 31…);
- KPI "Doanh nghiệp" vẫn báo 1.228 công ty thật.

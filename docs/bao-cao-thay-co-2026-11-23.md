# Báo cáo dự án: Hệ thống Data Lakehouse phân tích thị trường việc làm IT Việt Nam

**Ngày báo cáo:** 23/11/2026
**Sản phẩm công khai:** http://womtech.io.vn:8010/
**Mã nguồn:** https://github.com/quanbao2603/IT-Job-Data-Lakehouse

---

## 1. Mục tiêu và phạm vi

Xây dựng một pipeline dữ liệu hoàn chỉnh thu thập tin tuyển dụng IT từ các trang
tuyển dụng Việt Nam, chuẩn hoá thành mô hình phân tích, và công bố kết quả trên một
dashboard web đọc trực tiếp từ tầng Gold.

Yêu cầu cốt lõi:

1. Thu thập tự động, có thể chạy lại, không làm hỏng nguồn dữ liệu.
2. Ba tầng dữ liệu có hợp đồng rõ ràng: Bronze (thô, bất biến), Silver (chuẩn hoá),
   Gold (mô hình phân tích cho báo cáo).
3. Dashboard web công khai, chỉ đọc, mọi con số truy vết được về một release.
4. Chất lượng dữ liệu phải đo được, không chỉ "chạy được".

Không nằm trong phạm vi: Power BI, mô hình AI dự đoán, dữ liệu lương nội bộ doanh nghiệp.

---

## 2. Kiến trúc hệ thống

```
9 nguồn tuyển dụng
        │  crawler (pacing, circuit breaker, fail-closed)
        ▼
   Bronze  ── MinIO S3, JSONL nén, bất biến, có checksum
        │     mỗi object ghi kèm run_id, watermark, sha256
        ▼
   Silver  ── Parquet, chuẩn hoá + entity resolution + quality gate
        │     release bất biến, verify checksum trước khi dùng
        ▼
    Gold   ── PostgreSQL, star schema (dim_job, dim_company, fact_*)
        │     33 kiểm tra chất lượng chặn publish nếu fail
        ▼
  Web API  ── FastAPI, transaction READ ONLY + REPEATABLE READ
        │
        ▼
 Dashboard  ── React 19 + Vite, 6 trang, tiếng Việt
```

### Nguyên tắc thiết kế

- **Bronze bất biến:** dữ liệu thô không bao giờ bị sửa; mọi lần cào tạo object mới.
- **Silver release là đơn vị nguyên tử:** một release hoặc `COMPLETE` hoặc không dùng được.
  Reader kiểm tra checksum từng object trước khi đọc (fail-closed).
- **Gold chỉ publish khi đạt 33/33 kiểm tra**; nếu fail thì giữ release trước đó.
- **Web là read-only tuyệt đối:** principal `gold_reader` chỉ có `SELECT`, transaction
  `REPEATABLE READ, READ ONLY`, mọi response mang `release_id` + `snapshot_date`.

---

## 3. Các nguồn dữ liệu

| Nền tảng | Vai trò | Ghi chú |
|---|---|---|
| TopCV | Nguồn lớn nhất | Có cơ chế chống bot (Rate limit 429 / 403) |
| CareerViet | Nguồn lớn thứ hai | Server-rendered |
| VietnamWorks | Nguồn lớn | Có cấu trúc `job_locations` riêng |
| ITviec | Chuyên IT | Có `monthsOfExperience`, `job_domain` |
| Upzi, TopDev, VietnamDevs, Vieclam24h | Nguồn bổ sung | |
| DevWork | Nguồn bổ sung | Ẩn nhà tuyển dụng theo thiết kế của họ |

**DevWork:** nền tảng này không công bố entity công ty (ẩn danh nhà tuyển dụng có chủ
đích). Nhóm ghi nhận đây là giới hạn của nguồn, không phải lỗi pipeline; các tin từ
DevWork được giữ lại với nhãn "chưa xác định" và ghi chú rõ trong báo cáo.

---

## 4. Xử lý thách thức kỹ thuật đáng chú ý

### 4.1 TopCV chống bot — chẩn đoán và thiết kế lại crawler

Đây là phần khó nhất của dự án. Triệu chứng ban đầu: crawler nhận HTTP 403 dù cookie
còn hợp lệ, sau đó là HTTP 429 khi tăng tốc độ.

Quá trình chẩn đoán (đo trên site thật, không suy đoán):

| Cách gọi request | Kết quả |
|---|---|
| `curl_cffi` + cookie, không thêm header | **200 OK** |
| `curl_cffi` + cookie + `Referer`/`Accept-Language`/`User-Agent` | 403 |
| `curl_cffi` + cookie gắn vào session cookie jar | 403 |

**Nguyên nhân:** `curl_cffi` tái tạo TLS/HTTP2 fingerprint theo profile Chrome. Khi ta
chèn thêm header hoặc đưa cookie vào jar, fingerprint lệch với profile đã chọn, và
Cloudflare từ chối. Đây là lỗi *của crawler*, không phải cookie hết hạn.

**Giải pháp:**

1. Giữ cookie dạng `dict`, truyền **theo từng request**, không đụng vào session jar.
2. Không set bất kỳ header nào — để `curl_cffi` tự sinh toàn bộ profile.
3. Pacing: 1 tin → nghỉ 2,0–2,5 giây; 8–10 request → nghỉ 15–20 giây.
4. HTTP 403 → dừng ngay, ghi circuit breaker 15 phút, không ghi dữ liệu rác vào Bronze.
5. Có hai chế độ: cào trang chi tiết (đầy đủ) và chế độ chỉ đọc trang danh sách.

**Phát hiện quan trọng thứ hai:** trang *danh sách* của TopCV (server-rendered) đã chứa
sẵn tên công ty, địa điểm, kinh nghiệm, lương trong mỗi card. Vì vậy chỉ cần **25
request** là lấy được company/city/experience cho toàn bộ ~1.120 tin, thay vì 1.100
request trang chi tiết — đúng điểm mà TopCV giới hạn tốc độ.

Kết quả: **1.122 tin TopCV** vào Bronze, không bị chặn lần nào.

### 4.2 Dữ liệu rác từ trang chưa render

Trước khi sửa, 716 tin TopCV lưu chuỗi template `{{ company?.name }}` — dấu hiệu crawler
đọc phải HTML Angular chưa render. Hệ quả: 1.310 entity có công ty "UNKNOWN" hiện lên
dashboard, chiếm vị trí đầu bảng xếp hạng.

Xử lý ở ba lớp:

1. **Crawler:** đọc đúng block `.box-company-info-detail` (tên công ty, quy mô, địa chỉ,
   ngành nghề) thay vì text tuỳ ý.
2. **Transform:** `_fold()` chuẩn hoá tiếng Việt (NFD → bỏ dấu) trước khi so khớp; mọi
   placeholder (`{{...}}`, `N/A`, rỗng) quy về `UNKNOWN` thay vì tạo công ty giả.
3. **Gold:** `resolve_company_key()` không bao giờ tạo dimension company từ marker
   `UNKNOWN` — nhờ đó bảng "Công ty nhiều tin nhất" chỉ còn tên thật.

### 4.3 Chuẩn hoá địa điểm, cấp bậc, chuyên môn

- **Địa điểm:** đọc thêm `job_locations` (VietnamWorks) và `company_location`; pattern
  thành phố có thứ tự ưu tiên (HCM, Hà Nội, Đà Nẵng, Hải Phòng, Cần Thơ…). Tỷ lệ chưa
  xác định giảm từ 33% → 13,8%.
- **Cấp bậc:** suy từ nhãn gốc của nền tảng, từ title, và từ số tháng kinh nghiệm.
  Sửa lỗi `monthsOfExperience` dạng dict của ITviec bị đọc thành "năm" khiến ~1.200 kỹ
  sư bị xếp nhầm Lead. Tỷ lệ chưa xác định 30% → 20%.
- **Chuyên môn:** taxonomy 8 nhóm tiếng Việt (Phát triển phần mềm, Dữ liệu & AI, Hạ tầng
  & Cloud, QA & Kiểm thử, An ninh mạng, Phân tích & Quản lý, Di động & Nhúng, chưa xác
  định) áp dụng cho mọi nền tảng, không chỉ ITviec.

### 4.4 Gold báo "dữ liệu phân kỳ" sau khi replay

Sau khi sửa facet ở Silver và replay, Gold báo `SOURCE_DIVERGENCE` cho 14 entity. Nguyên
nhân: bản ghi lịch sử cũ thắng bản replay khi dedupe, và lương rỗng ghi đè lương đã biết.

Sửa: release hiện tại thắng lịch sử; chỉ ghi đè lương khi có giá trị thật; `dim_job`
fallback địa điểm từ entity. Đây là ví dụ của việc **quality gate bắt được lỗi thật** —
hệ thống không âm thầm publish dữ liệu sai.

---

## 5. Kết quả dữ liệu hiện tại

**Release:** `gold_20261123_listing_fix` · snapshot 23/11/2026 · **33/33 kiểm tra đạt**

| Chỉ số | Giá trị |
|---|---|
| Tin tuyển dụng (entity) | **4.444** |
| Công ty | 1.853 |
| Mẫu lương hợp lệ | 1.509 |
| Trung vị lương | 20 triệu VND |
| Kỹ năng trích được | 129 |
| Observation (quan sát) | 8.244 |
| Nền tảng | 9 |

### Phân bố theo chuyên môn

| Nhóm | Số tin | Tỷ lệ |
|---|---|---|
| Phát triển phần mềm | 1.417 | 31,9% |
| Dữ liệu & AI | 712 | 16,0% |
| Chưa xác định | 834 | 18,8% |
| Phân tích & Quản lý | 406 | 9,1% |
| Hạ tầng & Cloud | 371 | 8,3% |
| QA & Kiểm thử | 282 | 6,3% |
| An ninh mạng | 252 | 5,7% |
| Di động & Nhúng | 170 | 3,8% |

### Phân bố theo địa điểm và cấp bậc

| Địa điểm | Số tin | | Cấp bậc | Số tin |
|---|---|---|---|---|
| Hà Nội | 1.778 (40,0%) | | Mid | 1.471 (33,1%) |
| Hồ Chí Minh | 1.663 (37,4%) | | Chưa xác định | 889 (20,0%) |
| Chưa xác định | 615 (13,8%) | | Junior | 635 (14,3%) |
| Khác | 364 (8,2%) | | Lead | 528 (11,9%) |
| Đà Nẵng | 24 (0,5%) | | Senior | 495 (11,1%) |

### Top công ty tuyển dụng

Bosch Global Software Technologies (89) · VNPT (41) · FPT Software (31) ·
Vinsmart Future (31) · Nasani (25) · Bảo Việt IT Center (24) · MB Bank (24) ·
Navigos Search (23)

### Top kỹ năng được yêu cầu

SQL (433) · Python (352) · Security (336) · Testing (315) · Agile (272) ·
Linux (205) · AWS (196) · Java (196) · Scrum (189) · Azure (180)

---

## 6. Kiểm chứng chất lượng

| Hạng mục | Kết quả |
|---|---|
| Unit + integration test (root) | **323 test pass** (6 skip có lý do) |
| Test web backend | 30 test pass |
| Test DOM frontend (Vitest) | 4 test pass |
| Kiểm tra chất lượng Gold | 33/33 đạt, 0 blocking |
| Đối soát web ↔ Gold | 19/19 kiểm tra (`verify_live.py`) |
| Smoke test bundle trong venv sạch | PASS (cài từ `requirements.lock.txt`) |
| Kiểm tra HEAD/GET, 404 docs | Đúng |
| Trang công khai | 200 từ DE/UA/US |

Mọi con số trên dashboard đều đối soát được: `verify_live.py` gọi API và so trực tiếp
với PostgreSQL Gold, không so với snapshot cứng.

---

## 7. Hạn chế đã biết (báo cáo trung thực)

1. **606 tin TopCV cũ** (chỉ xuất hiện trong dữ liệu 07/11) có observation lưu
   placeholder. Đang được backfill chậm từ trang chi tiết (6–9s/tin, có checkpoint).
   Đây là dữ liệu lịch sử; phần lớn tin đã hết hạn.
2. **DevWork không có entity công ty** — giới hạn của nguồn, chấp nhận nhãn "chưa xác
   định" và ghi rõ trong báo cáo (~41 tin).
3. **Vieclam24h** chặn truy cập (403) ở lần cào gần nhất; dữ liệu cũ vẫn còn trong
   pipeline (178 tin).
4. **Cảnh báo vận hành tự động chưa bật** (cần bot token/channel của người vận hành).
5. **Review queue:** 1.079 cặp observation cần người nghiệp vụ duyệt ACCEPT/REJECT;
   workflow đã có, chưa có người duyệt.
6. Dashboard mô tả **nguồn cung tin quan sát được**, không phải tổng số việc làm thực
   tế trên thị trường.

---

## 8. Bài học kỹ thuật

- **Chẩn đoán bằng đo lường, không bằng phỏng đoán.** Lỗi TopCV tưởng là cookie hết hạn,
  thực tế là fingerprint TLS lệch do chính code thêm header. Chỉ phép so sánh A/B trên
  site thật mới phân biệt được.
- **Fail-closed quan trọng hơn "chạy cho xong".** Crawler gặp 403 thì dừng và giữ
  checkpoint, thay vì ghi dữ liệu rỗng vào Bronze.
- **Sửa ở nguồn gốc, không vá dashboard.** Facet sai được sửa ở tầng transform rồi replay,
  không hard-code trên giao diện.
- **Quality gate phải chặn được publish.** 14 entity phân kỳ đã bị chặn thay vì lọt ra
  dashboard.
- **Đo được mới báo cáo được.** Mỗi lần cải thiện đều có bảng trước/sau.

---

## 9. Hướng phát triển

1. Hoàn tất backfill 606 tin TopCV cũ và chạy lại Silver/Gold.
2. Bật cảnh báo vận hành (Telegram/webhook) khi DAG fail hoặc release quá cũ.
3. Đưa review queue vào vận hành thật (người duyệt ACCEPT/REJECT).
4. Bổ sung nguồn mới (LinkedIn VN, JobStreet) nếu được phép.
5. Mô hình dự đoán xu hướng lương/kỹ năng — giai đoạn sau, khi dữ liệu lịch sử đủ dài.

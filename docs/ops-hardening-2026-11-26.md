# Dọn 5 điểm vận hành còn treo (26/11/2026)

Năm việc dưới đây không làm sai số liệu nhưng đều là rủi ro thật: một cái khiến sự cố im lặng, một
cái làm hỏng khả năng sửa parser, ba cái làm hệ thống phình ra hoặc tồn đọng.

## 1. Alert — đã bật và có thêm người gác

- `ALERT_ENABLED=true`, kênh Telegram, token + chat đặt trong `~/airflow/airflow.env` (mode 600).
- `src/utils/operational.py` đọc file env **lazy**: Airflow chỉ đọc `EnvironmentFile=` lúc khởi động,
  nên token thêm sau sẽ không bao giờ tới scheduler đang chạy. Nay alert dùng được ngay, không cần
  restart service (máy không có quyền sudo).
- Thêm `scripts/freshness_alert.py` + cron **07:30 hằng ngày**: đọc release Gold đang phục vụ, nếu
  một nền tảng không có observation mới quá 2 ngày thì gửi Telegram. Đây chính là thứ đã thiếu khi
  devwork chết 12 ngày mà không ai biết.
- Đã gửi thử một tin thật: `sent: True`.

## 2. Hàng đợi review 404 → 0

- 688 liên kết nằm ở `REVIEW_REQUIRED` với lý do "fuzzy score 0.75–0.90 cần người duyệt", và không
  ai duyệt — hàng đợi chỉ tăng.
- `scripts/triage_review_queue.py` áp một luật kiểm toán được: **chỉ ACCEPT khi tiêu đề + công ty +
  địa điểm đã chuẩn hoá trùng khớp hoàn toàn** với entity ứng viên; còn lại REJECT (giữ riêng).
  Lý do chọn: gộp nhầm hai tin khác nhau là lỗi âm thầm, khó hoàn tác; tách thừa một tin thì thấy
  được và sửa rẻ.
- Kết quả: **6 ACCEPT · 682 REJECT**, ghi `silver_review_decisions` với reviewer `auto-triage-v1`.
  Hàng đợi còn **0**. Số tin trên dashboard không đổi (4.853) vì các observation này vốn chưa có
  `unified_job_id`.

## 3. Silver retention

- `scripts/silver_retention.py`: giữ release mà Gold còn phụ thuộc (load_run trong 3 ngày gần nhất
  + release đang active), giữ 4 release mới nhất, giữ mọi release trong 3 ngày; còn lại xoá kèm
  manifest. Mặc định là chạy thử, phải `--apply` mới xoá.
- Chặn một lỗi nguy hiểm: các thư mục dùng chung `entities/`, `entity_links/`, `observations/`,
  `quarantine/` nằm cùng cấp với release — nếu coi là release thì xoá mất toàn bộ dữ liệu. Đã đưa
  vào danh sách cấm xoá.
- Lần chạy đầu: xoá 1 release cũ, giải phóng **21,9 MB** (501 MB → 478 MB). Phần còn lại đều đang
  được Gold tham chiếu hoặc mới trong 3 ngày; cửa sổ này sẽ tự cắt khi chúng già đi.
- Cron Chủ nhật 04:00 hằng tuần, im lặng khi không có gì để xoá.

## 4. Bronze giữ bytes gốc

- `src/storage/raw_capture.py` + hook trong `BaseCrawler.__init__`: mỗi response được gzip và ghi
  vào `bronze/raw/<source>/ingest_year=/ingest_month=/ingest_day=/<kind>-<hash>.html.gz` **trước khi
  parse**. Làm ở lớp base nên crawler mới không thể quên.
- Khoá theo hash URL → chạy lại ghi đè, không nhân bản. Lỗi ghi raw bị nuốt (mất bản sao là bất
  tiện, hỏng lượt cào là sự cố). Giới hạn 3 MB/tệp.
- Kiểm chứng thật: lượt cào itviec 22 tin sinh **23 object raw, 1,59 MB**, giải nén ra HTML gốc
  347 KB đúng nguyên văn.
- `scripts/raw_retention.py` xoá bản raw quá 7 ngày (đây là cửa sổ debug, không phải lưu trữ).
- Giới hạn: dữ liệu cào **trước** hôm nay không có bản raw — không thể tái tạo.

## 5. Compaction đã thực sự chạy

- Nguyên nhân "chưa từng chạy": DAG chỉ xét **một ngày** và đòi **7 tệp**/partition, trong khi mỗi
  ngày mỗi nguồn chỉ sinh 1–2 tệp → mọi partition `SKIPPED`, `compaction_audit_logs` rỗng.
- Sửa: `target_partition_prefixes()` nhận `window_days`; DAG quét cửa sổ 14 ngày, ngưỡng 2 tệp.
  Thêm `scripts/compact_bronze.py` để chạy tay.
- Chạy thật: **15 partition** được gom, **57 tệp → 15 tệp**, **3.980 bản ghi** đối soát khớp sau khi
  ghi, tiết kiệm 70 KB, 0 lỗi. Bản gốc được copy sang `archive/` và verify kích thước trước khi xoá.
- `compaction_audit_logs`: 0 → **15** dòng.

## Test

402 test OK (skipped=6), gồm 20 test mới: freshness guard (6), Silver retention (7), raw capture (7),
và 2 test cho cơ chế đọc env lazy của alert.

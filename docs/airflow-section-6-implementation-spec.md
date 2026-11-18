# Đặc tả nâng cấp Giai đoạn 6 — Điều phối, Quan sát & Cảnh báo Bronze

**Dự án:** IT Job Data Lakehouse  
**Mục tiêu:** vận hành tự động, fail-closed và có cảnh báo cho pipeline Bronze trên VPS  
**Phạm vi:** 9 nền tảng tuyển dụng, Airflow 2.10.4, LocalExecutor, PostgreSQL metadata, MinIO Bronze, SQLite State Store  
**Ngày:** 2026-11-15  
**Tài liệu giao cho:** Hermes Agent xử lý trực tiếp trên VPS sau khi xác nhận workspace và branch

> Đây là đặc tả triển khai. Không chạy lệnh destructive, không xóa Bronze object, không reset State Store và không đổi schema Silver nếu chưa có yêu cầu riêng.

---

## 1. Trạng thái hiện tại đã xác nhận

Airflow đang hoạt động trên VPS:

```text
Airflow: 2.10.4
Executor: LocalExecutor
Scheduler: systemd active/enabled
Webserver: systemd active/enabled
Metadata DB: PostgreSQL loopback
DAG: daily_nine_platform_bronze_incremental
DAG import errors: 0
DAG task count: 10
  - 9 crawler tasks
  - 1 summary task
max_active_runs: 1
max_active_tasks: 3
schedule: 02:00 Asia/Ho_Chi_Minh
```

Chín crawler hiện tại:

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

Bronze sau các run kiểm tra gần nhất:

```text
Physical records: 4,548
SHA-256 valid: 4,548/4,548
Schema drift: 0
Temporary landing objects: 0
RUNNING crawler runs: 0
```

Các cơ chế đã có và phải giữ nguyên:

- `BaseCrawler.run()` quản lý vòng đời ingestion.
- `StateManager` quản lý source lock, watermark, heartbeat và run status.
- `AtomicWriter` quản lý staged write, checksum, manifest và Bronze commit.
- Crawler chạy bằng `~/IT-Job-Data-Lakehouse/lakehouse_env/bin/python`.
- Airflow chỉ điều phối; không được tự viết lại watermark hoặc ghi trực tiếp Bronze.

---

## 2. Vấn đề cần sửa trước khi gọi là vận hành 24/7

### P1 — Lỗi HTTP nghiêm trọng đang làm task xanh giả

Hermes đã quan sát TopCV và Vieclam24h nhận HTTP 403 nhiều lần nhưng process vẫn exit code 0. Kết quả:

```text
topcv: SUCCESS, 0 records
vieclam24h: SUCCESS, 0 records
```

Đây là lỗi nghiêm trọng hơn việc chỉ thiếu cảnh báo. Airflow không thể tự biết crawler đã bị chặn nếu crawler kết thúc bình thường.

**Nguyên tắc bắt buộc:**

- Lỗi listing trang đầu tiên phải làm run `FAILED` và process exit non-zero.
- HTTP 403, 429 sau khi hết retry, 5xx sau khi hết retry, timeout và lỗi parse listing nghiêm trọng phải là lỗi nguồn.
- Không được commit watermark mới nếu run chưa hoàn tất hợp lệ.
- Không được để task `SUCCESS` chỉ vì Python process thoát code 0.
- Không coi `0 records` là thành công nếu nguồn đáng lẽ có dữ liệu hoặc request listing đã lỗi.
- Nếu lỗi giữa phân trang, trạng thái phải là `FAILED` hoặc `PARTIAL` theo policy được triển khai; không được giả vờ `SUCCESS`.

### P1 — Chưa có cảnh báo tự động

Cần có cảnh báo khi:

- task retry;
- task failed;
- crawler gặp HTTP 403/429;
- parser không tìm thấy listing data;
- parser trả 0 bất thường;
- preflight không đạt;
- Bronze audit/checksum thất bại;
- run bị timeout hoặc stale.

Không đưa cookie, API key, password, token, HTML payload hoặc nội dung nhạy cảm vào cảnh báo.

### P1 — Airflow webserver đang bind toàn bộ interface

Runtime đang listen trên:

```text
0.0.0.0:8080
```

Phải bảo vệ UI bằng một trong hai cách, ưu tiên cách A:

- **A — Khuyến nghị:** bind `127.0.0.1:8080`, truy cập qua SSH tunnel.
- **B:** giữ bind ngoài nhưng firewall chỉ cho IP quản trị cụ thể; không mở public không giới hạn.

Không thay đổi firewall từ xa nếu chưa xác nhận IP quản trị và phương án rollback.

### P2 — Chưa có preflight task riêng

Cần kiểm tra trước khi khởi chạy 9 crawler:

- disk còn trống tối thiểu 15%;
- RAM khả dụng đạt ngưỡng an toàn;
- mạng/DNS ra ngoài hoạt động;
- MinIO/S3 truy cập được;
- State Store SQLite đọc/ghi được;
- không còn run source ở trạng thái `RUNNING` bất thường;
- Airflow đang dùng đúng project root và Python environment.

### P2 — Chưa có DLQ/audit lỗi nguồn

Cần lưu lỗi có cấu trúc để điều tra sau, không chỉ in log. DLQ không được làm mất Bronze và không được chứa secret.

### P2 — Chưa trigger Silver

Silver/dedup đa nguồn chưa hoàn thiện. Vì vậy giai đoạn này **chưa tự động trigger Silver**. Chỉ tạo hook/config rõ ràng để bật sau khi Silver có acceptance test.

---

## 3. Kiến trúc mục tiêu

```text
Airflow DAG: daily_nine_platform_bronze_incremental
        |
        v
preflight_bronze
        |
        +--> crawl_careerviet_incremental
        +--> crawl_upzi_incremental
        +--> crawl_vietnamdevs_incremental
        +--> crawl_vieclam24h_incremental
        +--> crawl_devwork_incremental
        +--> crawl_itviec_incremental
        +--> crawl_topcv_incremental
        +--> crawl_topdev_incremental
        +--> crawl_vietnamworks_incremental
                         |
                         v
             bronze_audit_after_crawl
                         |
              +----------+----------+
              |                     |
              v                     v
       pipeline_summary       alert/operational log

Silver trigger: DISABLED until Silver acceptance gate is implemented.
```

Concurrency:

```text
max_active_runs = 1
max_active_tasks = 3
```

Không tăng `max_active_tasks` lên 9 nếu chưa có benchmark RAM/CPU/SQLite lock trên VPS.

---

## 4. Phân tách trách nhiệm

### Airflow

Airflow chịu trách nhiệm:

- lập lịch;
- giới hạn concurrency;
- retry task;
- timeout;
- chạy preflight;
- gọi crawler;
- gom trạng thái task;
- cảnh báo retry/failure;
- chạy Bronze audit sau ingest;
- lưu log task và run history.

Airflow không chịu trách nhiệm:

- tự tính watermark;
- tự dedup semantic;
- tự ghi record vào Bronze;
- tự sửa `data/state_store.db` ngoài API hiện có;
- tự đánh dấu crawler thành công khi crawler không xác nhận thành công.

### Crawler/BaseCrawler

Crawler chịu trách nhiệm:

- source-native request/retry;
- phát hiện HTTP 403/429/5xx/timeout;
- phát hiện parser failure;
- source lock;
- watermark;
- heartbeat;
- atomic Bronze write;
- manifest/checksum;
- commit State Store chỉ sau khi write hợp lệ;
- trả exit code non-zero khi run không đạt điều kiện thành công.

### StateManager

State Store phải phân biệt rõ:

```text
SUCCESS
FAILED
PARTIAL (nếu policy triển khai hỗ trợ)
ABANDONED
RUNNING
```

Nếu code hiện tại chưa hỗ trợ `PARTIAL` an toàn, dùng `FAILED` cho mọi lỗi chưa hoàn tất. Không thêm trạng thái mới chỉ để làm đẹp báo cáo mà không cập nhật mọi truy vấn/audit liên quan.

---

## 5. Preflight task

Tạo task:

```text
preflight_bronze
```

Task phải fail-fast trước 9 crawler.

### 5.1 Disk

Dùng `shutil.disk_usage(PROJECT_ROOT)` hoặc tương đương. Điều kiện:

```text
free_percent >= 15
```

Nên cảnh báo sớm khi:

```text
free_percent < 25
```

Không tự xóa file, log, archive, MinIO object hoặc database để giải phóng disk.

### 5.2 RAM

Đọc `/proc/meminfo` hoặc thư viện chuẩn. Dùng `MemAvailable`, không dùng riêng `MemFree`.

Ngưỡng đề xuất ban đầu:

```text
MemAvailable >= 1 GiB: cho phép chạy
MemAvailable < 1 GiB: fail preflight và alert
```

Ngưỡng phải cấu hình qua environment, không hardcode không giải thích:

```text
LAKEHOUSE_MIN_AVAILABLE_MEMORY_MB=1024
```

### 5.3 Network

Kiểm tra DNS và HTTPS tới một endpoint ổn định. Không coi ping ICMP là đủ.

Ví dụ kiểm tra:

```text
https://www.google.com/generate_204
```

Nếu môi trường không muốn phụ thuộc Google, dùng endpoint HTTPS đã được thống nhất trong config.

### 5.4 MinIO

Kiểm tra bằng credential hiện tại và operation read-only hoặc bucket stat an toàn. Không tạo/xóa bucket để probe.

### 5.5 State Store

Kiểm tra:

- file `data/state_store.db` tồn tại;
- SQLite mở được;
- WAL/connection hoạt động;
- query `ingestion_state` đọc được;
- thư mục database có quyền ghi.

### 5.6 Runtime contract

Preflight phải in hoặc ghi log:

```text
project_root
python executable
airflow DAG id
available disk
available memory
MinIO endpoint redacted
state DB path
```

Không ghi secret vào log.

---

## 6. Fail-closed cho crawler

### 6.1 Kết quả chạy phải có contract

Mỗi crawler CLI phải trả:

```text
exit 0 = ingestion thành công và đã commit Bronze/State hợp lệ
exit non-zero = lỗi nguồn, lỗi parser, lỗi storage, lỗi state hoặc run partial
```

Không dùng stdout text làm nguồn duy nhất để Airflow đoán thành công.

Nếu phù hợp với code hiện tại, `BaseCrawler.run()` nên trả summary nội bộ gồm:

```json
{
  "status": "SUCCESS",
  "source": "topcv",
  "run_id": "...",
  "records_ingested": 123,
  "bronze_uri": "...",
  "checksum_sha256": "..."
}
```

CLI vẫn phải exit non-zero khi `status != SUCCESS`.

### 6.2 Phân loại lỗi

```text
BLOCKED:
  HTTP 403 hoặc anti-bot rõ ràng

RETRYABLE:
  timeout, connection reset, 429, 5xx

PARSER_FAILURE:
  HTTP 200 nhưng không tìm thấy payload/listing expected

STORAGE_FAILURE:
  MinIO write/copy/verify/manifest failure

STATE_FAILURE:
  StateManager commit/lock/heartbeat failure

PARTIAL:
  đã ghi một phần nhưng chưa đạt điều kiện hoàn tất toàn run
```

Sau khi retry hết:

- `BLOCKED`, `PARSER_FAILURE`, `STORAGE_FAILURE`, `STATE_FAILURE` → exit non-zero;
- `RETRYABLE` chưa khôi phục → exit non-zero;
- không commit watermark cho run không thành công;
- nếu đã tạo object Bronze hợp lệ trước khi lỗi tiếp theo, object phải được giữ/audit, không xóa tùy tiện; State phải phản ánh run không hoàn tất.

### 6.3 Retry policy

Không retry vô hạn ở cả hai lớp. Policy đề xuất:

```text
Crawler HTTP retry: tối đa 3 lần
Backoff: 2, 4, 8 phút hoặc jitter tương đương
Airflow task retry: tối đa 2 lần
Airflow retry delay: 5 phút
```

Nếu crawler đã retry 3 lần và Airflow tiếp tục retry 2 lần, tổng request có thể tăng đáng kể. Log phải phân biệt:

```text
crawler_attempt
airflow_task_attempt
```

HTTP 403 không nên retry dài vô hạn; sau một số lần hợp lý phải fail và alert để tránh làm nặng anti-bot.

---

## 7. Bronze audit sau crawl

Tạo task:

```text
bronze_audit_after_crawl
```

Task chỉ chạy khi tất cả 9 crawler đạt success thực tế. Nếu muốn cho phép nguồn không có tin mới, crawler phải trả `SUCCESS, records_ingested=0`; trường hợp đó khác với HTTP failure.

Audit tối thiểu:

- tất cả upstream task success;
- không có State Store run tương ứng ở `FAILED`, `PARTIAL`, `RUNNING` hoặc `ABANDONED`;
- manifest object tồn tại;
- file checksum đọc lại được;
- record count đọc stream khớp manifest;
- không còn landing temporary object của run;
- không có schema drift fatal;
- không có object bị ghi nhưng không có manifest.

Không dedup cross-source trong audit Bronze. Bronze vẫn giữ observation theo run.

Nếu audit fail:

```text
bronze_audit_after_crawl = FAILED
pipeline_summary không được SUCCESS
alert được gửi
Silver trigger không được chạy
```

---

## 8. Alerting

### 8.1 Kênh

Triển khai một kênh đầu tiên, ưu tiên Telegram hoặc Discord webhook. Có thể hỗ trợ cả hai qua environment nhưng không hardcode secret.

Environment đề xuất:

```text
ALERT_ENABLED=true
ALERT_CHANNEL=telegram
TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHAT_ID=...
DISCORD_WEBHOOK_URL=...
```

Secret phải nằm ngoài Git:

```text
~/airflow/airflow.env
```

File permission:

```text
chmod 600 ~/airflow/airflow.env
```

Không in token trong log và không đưa token vào exception message.

### 8.2 Callback

Cài callback ở mức DAG/task:

```text
on_retry_callback
on_failure_callback
```

Alert failure phải có:

```text
DAG ID
Task ID
Platform/source
Run ID
Task attempt
Thời điểm Asia/Ho_Chi_Minh
Exception đã redacted
Link Airflow log nếu tạo được
```

Alert retry phải ghi rõ đây là retry, không báo như failure cuối cùng.

### 8.3 Alert nghiệp vụ

Ngoài callback Airflow, crawler cần phát tín hiệu có cấu trúc cho:

- HTTP 403;
- parser failure;
- records=0 bất thường;
- Bronze audit failure;
- disk/RAM preflight failure.

Không alert `records=0` nếu đó là kết quả hợp lệ đã được source xác nhận không có tin mới. Cần phân biệt:

```text
EMPTY_SUCCESS
SOURCE_FAILURE
```

### 8.4 Chống spam

Không gửi hàng chục alert cho cùng một lỗi retry. Policy:

- retry: tối đa một alert cho mỗi task attempt;
- failure cuối: một alert tổng hợp;
- 403 nhiều lần trong cùng task: gộp thành một alert;
- summary cuối run: một message tổng hợp số source success/failed/blocked.

---

## 9. Dead Letter Queue / lỗi nguồn

DLQ ở giai đoạn này là **operational failure ledger**, không phải nơi chứa toàn bộ raw job.

Đề xuất lưu tại:

```text
data/operational/source_failures.jsonl
```

hoặc một bảng SQLite riêng:

```text
source_failure_events
```

Mỗi event tối thiểu:

```json
{
  "event_id": "uuid",
  "dag_id": "daily_nine_platform_bronze_incremental",
  "run_id": "...",
  "task_id": "crawl_topcv_incremental",
  "source": "topcv",
  "failure_class": "BLOCKED",
  "http_status": 403,
  "attempt": 3,
  "message_redacted": "listing request blocked",
  "created_at": "...",
  "resolved_at": null
}
```

Không lưu:

- cookie;
- bearer token;
- password;
- API key;
- toàn bộ HTML response nếu có thông tin session.

DLQ phải append-only và có retention rõ ràng. Không tự xóa event để làm dashboard xanh.

---

## 10. Summary và trigger downstream

Task hiện tại `crawl_pipeline_summary` không được báo `SUCCESS` nếu có source bị block hoặc task failure.

Summary cần in/gửi:

```text
run_id
9 source success count
0-record valid count
failed count
blocked count
partial count
records ingested per source
Bronze audit result
```

Silver trigger:

```text
DISABLED_NOW = true
```

Chỉ bật `TriggerDagRunOperator` hoặc cơ chế tương đương khi đủ:

- Silver ETL đã có trong repository active;
- Silver schema và quality gate có test;
- cross-source dedup có acceptance test;
- Bronze audit pass;
- không source nào ở `FAILED`, `BLOCKED`, `PARTIAL`;
- có policy rõ ràng khi một nguồn không có tin mới hợp lệ.

Không trigger Silver chỉ vì Airflow DAG Bronze màu xanh.

---

## 11. Bảo vệ Airflow webserver

Ưu tiên sửa service từ:

```text
airflow webserver --port 8080
```

thành bind loopback nếu chỉ dùng SSH tunnel:

```text
airflow webserver --host 127.0.0.1 --port 8080
```

Sau đó:

```bash
sudo systemctl daemon-reload
sudo systemctl restart airflow-webserver
ss -ltnp | grep 8080
```

Kết quả mong muốn:

```text
127.0.0.1:8080
```

Truy cập từ máy cá nhân:

```bash
ssh -L 8080:127.0.0.1:8080 womcloud@<VPS_IP>
```

Không tự mở firewall/public port nếu chưa có yêu cầu vận hành cụ thể.

---

## 12. Cấu hình Airflow rõ ràng

Environment là nguồn cấu hình runtime chính. Không cần sửa trực tiếp `airflow.cfg` nếu systemd đã nạp `airflow.env`, nhưng phải xác nhận các giá trị runtime:

```text
AIRFLOW__CORE__EXECUTOR=LocalExecutor
AIRFLOW__CORE__LOAD_EXAMPLES=False
AIRFLOW__CORE__DAGS_ARE_PAUSED_AT_CREATION=True
LAKEHOUSE_MAX_ACTIVE_TASKS=3
LAKEHOUSE_INCREMENTAL_MAX_PAGES=5
```

Nếu giữ `airflow.cfg` có giá trị khác, ghi rõ environment override trong tài liệu vận hành hoặc đồng bộ các giá trị không nhạy cảm. Không commit password/Fernet key/web secret lên Git.

Graphviz chỉ là tiện ích hiển thị; không phải dependency vận hành. Có thể cài sau nếu cần `airflow dags show` render graph.

---

## 13. Thứ tự triển khai bắt buộc

Thực hiện theo thứ tự nhỏ nhất và có rollback:

### Bước A — Baseline

1. `git status`, ghi nhận local changes.
2. Kiểm tra DAG import.
3. Kiểm tra service và current run.
4. Không sửa/xóa Bronze.

### Bước B — Fail-closed

1. Sửa crawler/BaseCrawler để lỗi nghiêm trọng exit non-zero.
2. Thêm test cho 403, timeout, parser-empty và storage failure.
3. Chạy full test suite.
4. Chạy một test task/source trên VPS.
5. Chỉ khi source test đạt mới deploy toàn DAG.

### Bước C — Preflight

1. Thêm `preflight_bronze`.
2. Test disk/RAM/network/MinIO/SQLite với mock và test thật read-only.
3. Xác nhận preflight fail đúng khi hạ ngưỡng giả lập.

### Bước D — Alert và failure ledger

1. Thêm module alert redaction.
2. Cấu hình secret ngoài Git.
3. Test message giả lập; không gửi token/payload.
4. Test retry/failure/403/alert dedup.

### Bước E — Bronze audit gate

1. Thêm audit task sau 9 crawler.
2. Test checksum mismatch và missing manifest.
3. Đảm bảo Silver chưa được trigger khi audit fail.

### Bước F — Web security

1. Bind webserver loopback hoặc xác nhận firewall.
2. Kiểm tra bằng `ss -ltnp`.
3. Test SSH tunnel.

### Bước G — Nghiệm thu 24/7

Chỉ gọi Giai đoạn 6 hoàn tất khi:

- 403 làm task failed/non-zero;
- retry có giới hạn;
- alert nhận được;
- preflight block đúng;
- audit block đúng;
- không có stale RUNNING sau run thành công;
- checksum/manifest đạt;
- webserver không public ngoài policy;
- 9 source có summary rõ ràng;
- full test suite pass;
- một scheduled run và một manual run pass;
- test restart service pass.

---

## 14. Tiêu chí nghiệm thu cuối cùng

| Hạng mục | Điều kiện đạt |
|---|---|
| 9 crawler | Có task riêng, đúng module active, incremental mode |
| Concurrency | Không quá 3 crawler đồng thời |
| Preflight | Disk, RAM, network, MinIO, SQLite fail-fast |
| 403 | Crawler retry hữu hạn rồi exit non-zero |
| Parser failure | Không ghi nhận SUCCESS giả |
| Watermark | Không commit watermark cho run thất bại |
| Bronze | Manifest/checksum/record count hợp lệ |
| State | Không còn run RUNNING/stale sau khi kết thúc |
| Retry | Có retry ở crawler và Airflow, không vô hạn |
| Alert | Có retry/failure/403/preflight/audit alert |
| Secrets | Không nằm trong Git hoặc log |
| DLQ | Failure event append-only, có redaction |
| Summary | Không xanh khi có source failure/block |
| Silver | Chưa trigger khi chưa có acceptance gate |
| Webserver | Loopback hoặc firewall policy được xác nhận |
| Recovery | Restart scheduler/webserver không làm mất DAG/run state |

---

## 15. Báo cáo cần trả lại sau khi triển khai

Hermes phải trả lại các thông tin sau, không chỉ nói “đã xong”:

```text
Git commit SHA
Airflow DAG import result
DAG task count
max_active_tasks
Preflight result
9 crawler task results
records_ingested theo source
Bronze audit result
403/failure test result
Alert test result
DLQ sample đã redacted
webserver bind result
service status
full test result
rollback note nếu có
```

Nếu có lỗi hoặc quyền truy cập thiếu, dừng tại bước đó và báo chính xác blocker. Không đánh dấu hoàn tất khi chỉ scheduler chạy nhưng source failure vẫn bị ghi SUCCESS.

# Nhận xét triển khai Airflow – Phần 6 Bronze

**Dự án:** IT-Job-Data-Lakehouse
**Phạm vi:** Đối chiếu phần 6 “Điều phối, Quan sát & Cảnh báo” trong Notion với Airflow và runtime trên VPS.
**Thời điểm kiểm tra:** 2026-11-15 (UTC+07).
**Nguồn thiết kế:** [Chi tiết triển khai tầng Bronze – Notion](https://app.notion.com/p/CHI-TI-T-TRI-N-KHAI-T-NG-BRONZE-40bb26ce423e80ddac32d4c96b75dc31?source=copy_link)

> Đây là baseline review trước remediation. Các kết luận “chưa đạt” bên dưới mô tả trạng thái tại thời điểm review; acceptance evidence sau remediation được xác nhận bằng test/runtime checks trước commit.

## 1. Kết luận ngắn

Airflow đã được triển khai và đang điều phối được DAG Bronze ở mức MVP. Scheduler, webserver, PostgreSQL metadata database và DAG đều hoạt động.

Tuy nhiên, phần 6 **chưa hoàn thiện về vận hành an toàn**. Hai vấn đề cần ưu tiên là:

1. HTTP 403 từ TopCV và Vieclam24h vẫn làm task Airflow kết thúc `SUCCESS`.
2. Chưa có Telegram/Discord alert, Dead Letter Queue hoặc callback cảnh báo lỗi nguồn.

Ngoài ra, DAG chưa trigger downstream Silver và Airflow webserver đang bind trên `0.0.0.0:8080`.

**Verdict:** dùng được cho demo/MVP tiểu luận; chưa nên gọi là pipeline 24/7 production-ready.

## 2. Phần 6 trong thiết kế yêu cầu gì?

Phần 6 đặt mục tiêu vận hành Bronze tự động trên VPS với tự phục hồi, quan sát và cảnh báo. Các bước chính được mô tả gồm:

1. Pre-flight: kiểm tra mạng và dung lượng ổ đĩa.
2. Lookup State: đọc watermark từ State Store.
3. Resilient ingest: retry với exponential backoff.
4. Checksum & atomic commit: kiểm tra toàn vẹn và ghi vào `s3://bronze/`.
5. Update State: ghi watermark mới vào sổ cái.
6. Trigger downstream: phát tín hiệu cho Silver.
7. Alerting: nếu HTTP 403 hoặc HTML đổi cấu trúc thì gửi cảnh báo Telegram/Discord.

## 3. Bằng chứng runtime

### Airflow

- Airflow: **2.10.4**.
- `airflow-scheduler.service`: `active/running`, `enabled`.
- `airflow-webserver.service`: `active/running`, `enabled`.
- PostgreSQL metadata database: **healthy**.
- Health endpoint: HTTP **200**.
- Scheduler heartbeat: **healthy**.
- DAG processor không ghi nhận import error.
- DAG đã được scheduler nạp và đang **unpaused**.
- DAG source deployed bằng symlink tới repository; SHA-256 source deployed khớp source trong repository.
- Hai DAG run đã hoàn tất với trạng thái Airflow `success`: một scheduled run và một manual run.

### DAG

DAG ID:

```text
daily_nine_platform_bronze_incremental
```

DAG có 10 task:

- 9 task crawler: CareerViet, Upzi, VietnamDevs, Vieclam24h, Devwork, ITViec, TopCV, TopDev, VietnamWorks.
- 1 task `crawl_pipeline_summary`.

Cấu hình chính:

```text
Executor: LocalExecutor
max_active_runs: 1
max_active_tasks: 3
retries: 2
retry_delay: 5 phút
execution_timeout: 2 giờ
DAG schedule: 02:00 mỗi ngày
incremental max pages: 5
```

### Bronze sau các run Airflow

- Tổng object: **19**.
- Tổng bản ghi vật lý: **4.548**.
- SHA-256 hợp lệ: **4.548/4.548**.
- Schema drift: **0**.
- Landing object tạm: **0**.
- Không còn run crawler ở trạng thái `RUNNING`.

### VPS

- Disk còn khoảng **46 GB/73 GB**, đã dùng khoảng 38%; vượt ngưỡng tối thiểu 15% trống.
- RAM khả dụng khoảng **6,7 GB**.
- PostgreSQL chỉ bind loopback `127.0.0.1:5432`.

## 4. Những phần đã đạt

### 4.1 Điều phối cơ bản — Đạt

Scheduler nhận DAG, tạo task và chạy được cả scheduled run lẫn manual run.

### 4.2 Retry task — Đạt MVP

DAG có retry ở cấp Airflow:

```python
retries = 2
retry_delay = timedelta(minutes=5)
```

Crawler cũng có retry HTTP riêng.

### 4.3 State Store, watermark và lock — Đạt ở crawler layer

DAG gọi crawler hiện có; crawler tự quản lý watermark, source lock, heartbeat, trạng thái run và Bronze commit thông qua `BaseCrawler`/`StateManager`.

### 4.4 Checksum và Bronze commit — Đạt

Bronze audit sau các run vẫn đạt:

```text
SHA-256: 4548/4548 hợp lệ
Schema drift: 0
Landing tạm: 0
```

### 4.5 Tự khởi động — Đạt

Hai systemd service đều `enabled`, `active/running` và hiện không có restart loop.

### 4.6 Tài nguyên pre-flight hiện tại — Đạt tại thời điểm kiểm tra

Disk và RAM hiện đủ để chạy theo ngưỡng đã nêu trong thiết kế. Tuy nhiên, các kiểm tra này chưa được đóng gói thành task pre-flight độc lập trong DAG.

## 5. Khoảng trống và rủi ro

## 5.1 P1 — HTTP 403 vẫn bị ghi nhận là SUCCESS

Trong cả scheduled run và manual run:

- TopCV nhận **4 lần HTTP 403**, sau đó task vẫn `SUCCESS`.
- Vieclam24h nhận **4 lần HTTP 403**, sau đó task vẫn `SUCCESS`.

State Store ghi các run tương ứng là:

```text
topcv       SUCCESS, 0 records
vieclam24h  SUCCESS, 0 records
```

Nguyên nhân là crawler retry xong rồi trả về luồng kết thúc bình thường; process thoát code `0`, nên Airflow hiểu là thành công.

### Tác động

- DAG có thể xanh dù nguồn không được thu thập.
- Task tổng kết `crawl_pipeline_summary` vẫn chạy vì các task upstream đều bị đánh dấu `SUCCESS`.
- Không có cảnh báo tự động cho người vận hành.
- Watermark có nguy cơ tạo cảm giác pipeline đã đồng bộ dù thực tế không lấy được trang.

### Cách sửa tối thiểu

- Nếu trang listing đầu tiên thất bại do 403/5xx/request failure: raise exception và thoát non-zero.
- Nếu lỗi xảy ra giữa phân trang: đánh dấu run `FAILED` hoặc `PARTIAL`, không ghi `SUCCESS`.
- Không cập nhật watermark nếu run chưa hoàn tất.
- Chỉ để task Airflow thành công khi đồng thời thỏa:
  - process exit code 0;
  - State Store là `SUCCESS`;
  - không có lỗi nguồn nghiêm trọng;
  - nếu có dữ liệu thì manifest/checksum hợp lệ.

## 5.2 P1 — Chưa có Telegram/Discord alert

DAG hiện chỉ có task Python in ra một dòng tổng kết. Chưa thấy:

- `on_failure_callback`;
- `on_retry_callback`;
- Telegram webhook;
- Discord webhook;
- cảnh báo riêng khi HTTP 403;
- cảnh báo parser trả 0 bất thường;
- Dead Letter Queue.

Điều này chưa đáp ứng yêu cầu cảnh báo trong phần 6.

### Cách sửa tối thiểu

Thêm callback cảnh báo ở DAG hoặc task level. Alert nên bao gồm:

- DAG ID và task ID;
- source/platform;
- run ID;
- lỗi HTTP hoặc lỗi parser;
- thời điểm;
- link tới Airflow log;
- số record đã ghi nếu có.

Không đưa cookie, API key, token hoặc payload nhạy cảm vào alert.

## 5.3 P1/P2 — Chưa trigger downstream Silver

DAG hiện kết thúc ở `crawl_pipeline_summary`. Chưa thấy:

- `TriggerDagRunOperator`;
- task Silver;
- event/queue downstream;
- quality gate Bronze trước khi kích hoạt Silver.

Nếu Silver chưa được triển khai thì đây là P2. Nếu Silver đã sẵn sàng chạy tự động thì đây là P1.

### Cách sửa tối thiểu

Thêm một task downstream chỉ chạy khi:

1. tất cả crawler đạt điều kiện thành công thực tế;
2. Bronze audit/manifest/checksum đạt;
3. không có source bị đánh dấu partial/blocked.

Sau đó trigger DAG Silver hoặc phát event tương ứng.

## 5.4 P2 — Chưa có pre-flight task độc lập trong DAG

Thiết kế yêu cầu kiểm tra network và disk trước khi crawl. Hiện các kiểm tra đã được thực hiện trong quá trình vận hành/review, nhưng chưa có một task Airflow độc lập để kiểm tra:

- disk còn trên 15%;
- MinIO/S3 đọc được;
- State Store truy cập được;
- không có crawler trùng source đang chạy;
- không có stale `RUNNING` chưa được xử lý.

### Cách sửa tối thiểu

Thêm task `preflight_bronze` làm upstream của 9 crawler. Task fail-fast nếu không đạt điều kiện.

## 5.5 P2 — Chính sách retry chưa đồng nhất với thiết kế

DAG đang dùng:

```text
retries = 2
retry_delay = 5 phút
```

Trong Notion mô tả retry mạng 3 lần với exponential backoff khoảng 2 phút. Cấu hình hiện tại không sai tuyệt đối, nhưng cần thống nhất tài liệu và runtime để tránh retry quá ít hoặc retry trùng quá nhiều giữa Airflow và crawler.

## 5.6 P1 — Webserver bind trên toàn bộ interface

Airflow webserver đang listen:

```text
0.0.0.0:8080
```

Chưa có bằng chứng đủ để xác nhận firewall đang chặn truy cập Internet tới cổng này. Vì vậy không nên coi giao diện Airflow đã được bảo vệ hoàn chỉnh.

### Khuyến nghị

Một trong các phương án:

- bind webserver vào `127.0.0.1` và đi qua Nginx/HTTPS;
- firewall chỉ cho phép IP quản trị;
- đặt sau VPN hoặc reverse proxy có authentication.

Không mở Airflow UI trực tiếp ra Internet nếu không cần.

## 5.7 P2 — `airflow.cfg` và environment còn lệch nhau

`airflow.cfg` còn giá trị cũ như:

```text
executor = SequentialExecutor
load_examples = True
```

Trong runtime, environment đang override thành:

```text
executor = LocalExecutor
load_examples = False
```

Runtime hiện chạy đúng, nhưng cấu hình cũ dễ gây nhầm khi bảo trì hoặc khởi động thủ công.

## 5.8 P2 — Thiếu Python Graphviz

Lệnh render graph DAG báo thiếu package `graphviz`. Việc này không ảnh hưởng scheduler hoặc task execution, chỉ ảnh hưởng hiển thị sơ đồ DAG qua CLI.

## 6. Ma trận đánh giá phần 6

| Tiêu chí | Đánh giá | Bằng chứng/nhận xét |
|---|---|---|
| Pre-flight network/disk | Một phần | VPS đạt ngưỡng hiện tại, nhưng chưa có task pre-flight độc lập |
| Lookup watermark | Đạt ở crawler layer | Crawler đọc State Store và dùng watermark riêng |
| Retry resilient ingest | Đạt MVP | Có retry ở Airflow và crawler, nhưng chưa fail-closed với 403 |
| Checksum & Bronze commit | Đạt | 4.548/4.548 SHA-256 hợp lệ; landing rỗng |
| Update State | Đạt ở crawler layer | State Store ghi run/watermark/manifest |
| Lock/heartbeat | Đạt ở crawler layer | Crawler sở hữu source lock và heartbeat |
| Trigger Silver | Chưa đạt | Chưa thấy task/operator downstream |
| Telegram/Discord alert | Chưa đạt | Chưa thấy callback/webhook |
| Dead Letter Queue | Chưa đạt | Chưa có cơ chế DLQ |
| Phát hiện HTTP 403 | Một phần | Có log 403 nhưng task vẫn SUCCESS |
| Auto-start | Đạt | Hai systemd service enabled và running |
| Airflow web security | Một phần | Bind `0.0.0.0:8080`; firewall chưa được xác nhận |
| DAG import/scheduler | Đạt | DAG loaded, không có import error |
| Bronze audit | Đạt | 4.548 records, checksum 100%, schema drift 0 |
| Downstream quality gate | Chưa đạt | Chưa có task kiểm tra trước khi trigger Silver |

## 7. Lộ trình sửa đề xuất

### P0

Hiện chưa thấy P0 làm hệ thống mất dữ liệu ngay lập tức. Tuy nhiên, không được coi task Airflow xanh là bằng chứng nguồn đã crawl thành công.

### P1 — nên sửa trước khi chạy 24/7

1. Sửa crawler/BaseCrawler để 403, request failure hoặc parser failure nghiêm trọng làm run `FAILED/PARTIAL` và process non-zero.
2. Thêm Telegram alert cho task failure/retry và lỗi HTTP 403.
3. Chặn hoặc bảo vệ Airflow webserver `0.0.0.0:8080`.
4. Thêm quality gate để không trigger downstream khi một nguồn partial/blocked.

### P2 — hoàn thiện vận hành

1. Thêm `preflight_bronze` task.
2. Thêm trigger Silver sau Bronze audit.
3. Thêm DLQ hoặc bảng lỗi nguồn chi tiết.
4. Đồng nhất retry policy giữa Notion, Airflow và crawler.
5. Đồng bộ lại `airflow.cfg` với environment hoặc ghi rõ environment là nguồn cấu hình duy nhất.
6. Cài Python Graphviz nếu cần render DAG bằng CLI.

## 8. Cách diễn đạt phù hợp cho tiểu luận

Có thể viết:

> Apache Airflow 2.10.4 được triển khai trên VPS để điều phối DAG incremental cho chín nền tảng tuyển dụng. DAG sử dụng LocalExecutor, giới hạn số task chạy đồng thời, hỗ trợ retry ở cấp orchestration và ghi log theo từng task. Logic watermark, source lock, heartbeat và verified Bronze commit vẫn do crawler và State Store quản lý. Các run kiểm thử đã chứng minh scheduler/webserver hoạt động, DAG được nạp thành công và Bronze audit đạt toàn vẹn checksum.

Nên ghi thêm giới hạn:

> Cơ chế cảnh báo Telegram/Discord, Dead Letter Queue, downstream trigger cho Silver và fail-closed khi nguồn trả HTTP 403 vẫn cần hoàn thiện. Vì vậy triển khai hiện tại phù hợp với mức MVP/thesis deployment, chưa phải production-grade observability.

## 9. Kết luận cuối

Airflow hiện **đã chạy được và kết nối đúng với Bronze pipeline**. Phần triển khai lõi là ổn cho tiểu luận.

Nhưng để phần 6 được xem là hoàn thiện, cần tối thiểu:

1. Fail-closed khi gặp HTTP 403 hoặc lỗi nguồn.
2. Telegram alert khi task retry/fail hoặc parser thay đổi.
3. Không để DAG summary báo xanh khi nguồn thực tế không thu thập được dữ liệu.
4. Bảo vệ port 8080.
5. Thêm downstream Silver trigger nếu Silver đã sẵn sàng.

### 10. Acceptance addendum sau remediation

Các mục dưới đây là bằng chứng nghiệm thu sau khi áp dụng các thay đổi trong repository; các mục đánh giá ở phần trước giữ nguyên vai trò baseline review.

- Fail-closed: `188` test pass, `1` skip có chủ đích; HTTP 403, request/parser failure nghiêm trọng và listing rỗng không có xác nhận nguồn đều đi qua lỗi non-zero; watermark không commit khi run thất bại.
- DAG contract: `1/1` pass; có `preflight_bronze`, chín crawler, `bronze_audit_after_crawl` và `crawl_pipeline_summary`; `max_active_runs=1`, `max_active_tasks<=3`; audit `all_done`, summary `all_success`.
- Airflow import/runtime: DAG `daily_nine_platform_bronze_incremental` loaded, import errors `0`, scheduler và webserver active/enabled.
- Preflight thật: PASS; disk trống `62.78%`, RAM khả dụng `6355 MB`; kiểm tra network, MinIO và SQLite hoàn tất.
- Bronze audit thật: PASS cho chín source gần nhất; `9/9` source terminal `SUCCESS`, `8` empty-success có xác nhận, `37` records trong run gần nhất, `1` object được kiểm checksum/envelope; deep audit toàn Bronze vẫn ghi nhận `4,548/4,548` SHA-256 hợp lệ, schema drift `0`, landing tạm `0`.
- Redaction/DLQ: ledger JSONL append-only đã được test với HTTP 403 và payload chứa secret/HTML; giá trị nhạy cảm bị thay bằng `[REDACTED]`. Alert delivery thật chưa chạy vì runtime chưa cấu hình channel/credential (`ALERT_ENABLED` unset).
- Webserver: unit trong repository đã bind `127.0.0.1:8080`; runtime listener vẫn `0.0.0.0:8080` vì tài khoản hiện tại không có quyền sudo để daemon-reload/restart service.
- Full DAG/full crawl không chạy trong acceptance để tránh ghi thêm Bronze ngoài phạm vi và tránh tác động nguồn thật; không dùng điều này để tuyên bố full crawl đã thành công.

**Verdict cập nhật:** phần code và kiểm thử của Giai đoạn 6 đã đạt acceptance gate an toàn ở mức repository/runtime read-only. Hai giới hạn triển khai còn mở là reload service webserver cần quyền sudo và cấu hình/kiểm thử alert delivery thật cần credential do vận hành cung cấp.

## Nguồn

[4] [Chi tiết triển khai tầng Bronze – Notion](https://app.notion.com/p/CHI-TI-T-TRI-N-KHAI-T-NG-BRONZE-40bb26ce423e80ddac32d4c96b75dc31?source=copy_link)

## Sources

[4] https://app.notion.com/p/CHI-TI-T-TRI-N-KHAI-T-NG-BRONZE-40bb26ce423e80ddac32d4c96b75dc31?source=copy_link — Thiết kế triển khai tầng Bronze

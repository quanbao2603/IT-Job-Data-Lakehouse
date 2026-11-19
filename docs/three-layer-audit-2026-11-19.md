# Báo cáo rà soát tổng thể Bronze–Silver–Gold

- **Dự án:** `IT-Job-Data-Lakehouse`
- **Thời điểm kiểm tra:** 2026-11-19 (UTC+07)
- **Phạm vi:** đối chiếu toàn bộ ba plan `bronze-design-stages.md`, `silver-design-stages.md`, `gold-design-stages.md` với code, tests và runtime production.
- **Git baseline:** `main` tại `18ef8de`; sau đợt rà soát đã phát hiện thêm các vấn đề Silver và đang xử lý trong commit series sau baseline này.
- **An toàn:** không chạy lại DDL production, không chạy `mark_missing_day` chủ động, không reset State Store, không xóa Bronze/Silver, không in credentials/cookie/raw payload.

## 1. Kết luận ngắn

Ba tầng **đã chạy được theo đường chính Bronze → Silver → Gold**, nhưng **chưa đạt production acceptance**. Rà soát độc lập bổ sung phát hiện các release Silver cũ có checksum sai và duplicate canonical entity; Gold hiện không được phép chọn các release đó.

### Trạng thái hiện tại

- **Bronze:** đường crawl, envelope, manifest, checksum, State Store, audit gate và Airflow hoạt động; runtime có 46 object Bronze, 5.376 records đăng ký, landing không còn `.uploading`. Tuy nhiên compaction chưa được lịch gọi, alert chưa bật, timeout code chưa khớp plan và runtime State Store còn 3 `ABANDONED`/9 `FAILED` lịch sử.
- **TopCV:** đã bổ sung pacing trước burst kế tiếp và circuit breaker HTTP 403 lưu state atomically để Airflow retry không gửi request lại ngay từ process mới. 403 vẫn fail-closed; chưa thể coi TopCV healthy cho tới khi một request production thành công sau cooldown.
- **Silver:** đã replay từ 46 Bronze objects bằng code `bc10f45`, không crawl lại TopCV. Release mới `silver_release_manual_silver_replay_bc10f45` ở `COMPLETE`, 208 objects, 13.748 records; local verify 0 lỗi, remote MinIO verify 208/208 object 0 lỗi, logical-key duplicates 0, đủ 9 platform.
- **Gold:** đã chạy no-publish và publish thật từ release mới. 32/32 DQ checks đạt; active release mới `gold_20261119_silver_release_manual_silver_replay_bc10f45`; `daily_snapshot=LOADED`.

## 2. Bằng chứng runtime đã thu được

### Bronze

- `ingestion_runs`: 87 `SUCCESS`, 9 `FAILED`, 3 `ABANDONED` trong State Store hiện tại.
- `ingestion_state`: 9 source đều có `last_run_status=SUCCESS` ở lần trạng thái mới nhất.
- `ingestion_objects`: 50 objects, 5.435 records, khoảng 6.322.683 bytes; `is_compacted=0` là 50; `compaction_audit_logs=0`.
- MinIO read-only probe:
  - `landing`: 0 objects, 0 `.uploading`.
  - `bronze`: 46 objects.
  - `silver`: 1.517 objects.
- Airflow:
  - DAG import errors: `[]`.
  - `daily_nine_platform_bronze_incremental`: unpaused.
  - scheduler/webserver đang active ở lần kiểm tra trước.

### Silver

- Release: `silver_release_manual_silver_replay_bc10f45`.
- `status=COMPLETE`, `is_consumable=True`.
- 208 objects trong manifest; local và remote MinIO checksum/size verify: 0 lỗi.
- Logical keys: observations 5.221/5.221, entities 3.092/3.092, links 5.221/5.221, quarantine 214/214.
- 9 platform: `careerviet`, `devwork`, `itviec`, `topcv`, `topdev`, `upzi`, `vieclam24h`, `vietnamdevs`, `vietnamworks`.
- Manifest metrics: 5.435 observations xử lý, 5.221 valid, 214 quarantine, quarantine rate 3,9374%, 1.079 review-required links.
- Object MinIO Silver tồn tại và đã được đọc kiểm tra thông qua release mirror/local mirror.

### Gold

- `active_release`: `gold_20261119_silver_release_manual_silver_replay_bc10f45`.
- `load_run` active release: `SUCCESS`.
- Row counts active release:
  - `dim_job=3.092`, `dim_company=1.224`, `dim_platform=10`, `dim_skill=132`, `dim_date=2.343`.
  - `fact_job_daily_snapshot=3.092`.
  - `fact_job_platform_daily=3.092`.
  - `fact_job_skill_daily=10.695`.
  - `fact_job_change_event=96`.
  - `fact_job_market_daily=405`.
  - `fact_company_hiring_daily=1.198`.
  - `fact_skill_pair_daily=5.201`.
- DQ production: 32 checks, 0 failed cho active release.
- Blocked publish probe: kết quả `BLOCKED`; active pointer trước/sau giữ nguyên.
- Control row đã được verify thành `daily_snapshot.status=LOADED`, trỏ cùng `gold_release_id` và `silver_release_id` với active successful release.

## 3. Các lỗi đã sửa trong đợt rà soát này

### 3.1. Silver cross-run object overwrite — P1

**Nguyên nhân:** `SilverWriter` dùng object key deterministic chỉ theo dataset/partition/content digest. Hai run khác nhau xử lý cùng logical row có thể cùng ghi vào key và manifest local/MinIO, làm một release mới tham chiếu object đã bị run khác thay thế.

**Sửa:**

- Thêm `object_namespace` cho `SilverWriter`.
- Namespace được thêm vào local dataset path, Silver object key và manifest mirror key.
- DAG truyền `object_namespace=run_id`, nên mỗi release Airflow có vùng object riêng.
- Thêm regression test `test_run_namespace_prevents_cross_release_object_overwrite`.

**Lưu ý:** các release cũ được sinh trước sửa có thể vẫn dùng layout cũ; không tự động rewrite production history. Gold release hiện tại đã verify đạt.

### 3.2. Gold blocked/missing attempt ghi đè daily snapshot — P1

**Nguyên nhân:** `publish()` và `mark_missing_day()` dùng `ON CONFLICT (snapshot_date) DO UPDATE` không phân biệt row đã `LOADED`. Blocked probe đã làm row của ngày 2026-11-19 trỏ sang `gold_blocked_probe`, trạng thái `FAILED`, dù `active_release` và `load_run` thành công vẫn đúng.

**Sửa:**

- Thêm `record_blocked_snapshot()` với điều kiện chỉ update nếu status hiện tại khác `LOADED`.
- `mark_missing_day()` cũng không thể hạ một ngày `LOADED` về `MISSING`.
- Thêm regression tests cho blocked và missing snapshot.
- Đã đọc lại active/load state và reconciliate duy nhất control row bị lệch về release thành công; không chạy lại DDL/`mark_missing_day`, không reload Gold.

### 3.3. Silver release integrity và entity uniqueness — P0

Rà soát độc lập bằng release manifest + Parquet readback đã xác nhận:

- Các release cũ vẫn được giữ nguyên làm lịch sử và không được chọn.
- Release mới `silver_release_manual_silver_replay_bc10f45`: 208 objects, remote verify 0 lỗi; `entities` 3.092 rows / 3.092 unique `unified_job_id`.

Nguyên nhân là writer chỉ dedupe trong một lần gọi `write()`; pipeline phát entity theo nhiều batch/partition nên cùng entity xuất hiện ở nhiều file. Đã sửa theo hướng defer entity write tới cuối release và kiểm tra duplicate logical key khi Gold verify release. Các release cũ không được tự ý rewrite/xóa.

Đã reprocess sau khi có xác nhận. Vì TopCV đang HTTP 403, replay đọc Bronze objects đã commit thay vì crawl lại nguồn.

## 4. Ma trận đánh giá theo tầng

### 4.1. Bronze

| Hạng mục | Đánh giá | Mức |
|---|---|---|
| Envelope/checksum/schema contract | Đã có code và tests | Đạt |
| Atomic writer/landing/manifest | Đã có, runtime landing sạch | Đạt |
| State Store/watermark/lock | Có lock, heartbeat, stale recovery; lịch sử vẫn có failed/abandoned | Đạt có điều kiện |
| Bronze audit gate | Có kiểm tra object, checksum, envelope và row count | Đạt |
| Airflow DAG/crawl gate | Importable, unpaused, Silver sau audit | Đạt có điều kiện |
| Compaction | Engine có nhưng không có DAG/cron/CLI gọi | **P1 còn tồn tại** |
| Alerting | Callback/ledger có; `ALERT_ENABLED` unset nên delivery chưa xác minh | **P1 còn tồn tại** |
| Timeout | Code 2h/task + 4h/DAG, plan ghi 45m crawler + 120m DAG | P2/P1 vận hành |
| Lifecycle expiration | Chưa thấy cấu hình MinIO lifecycle tương ứng plan | P2 |
| Cookie refresh | Fail-closed khi 403, chưa có refresh workflow | P2 |
| Runtime completeness | Chưa có bằng chứng source-total/full exhaustion cho mọi source | P1 evidence gap |

### 4.2. Silver

| Hạng mục | Đánh giá | Mức |
|---|---|---|
| 4 schemas và validation | Code/test có | Đạt |
| Identity/observation preservation | Release mới có 5.221 valid observations | Đạt |
| Entity resolution/false-positive guard | Code và tests có; 1.079 review-required được ghi | Đạt có điều kiện |
| Lifecycle/grace/partial-run | Có code; DAG hiện dùng ALL_SUCCESS nên partial semantics chưa là đường production chính | Đạt MVP |
| Writer checksum/manifest | Release mới local/remote verify 0 lỗi | Đạt |
| MinIO path isolation | Đã thêm namespace cho run mới; release cũ cần giữ nguyên layout | Đạt sau sửa |
| Airflow full handoff | DAG crawl reprocess bị TopCV 403; replay từ Bronze đã tạo release COMPLETE | Đạt có điều kiện |
| Gold handoff | Gold đã đọc release mới và publish thành công | Đạt |
| Acceptance documentation | Plan/checklist còn chứa nhiều trạng thái “cần viết mới” cũ | **P1 tài liệu** |
| Review queue resolution | Có pending/review-required nhưng chưa có workflow vận hành đã chứng minh | P1 |

### 4.3. Gold

| Hạng mục | Đánh giá | Mức |
|---|---|---|
| PostgreSQL control/schema | Đã chạy production | Đạt |
| Release reader/checksum/row count | Release mới 208 objects, remote verify 0 lỗi, logical keys unique | Đạt |
| Dimensions/SCD2/facts | Active load SUCCESS, FK lỗi trước đây đã sửa | Đạt |
| DQ/publish gate | 32/32 checks pass active release | Đạt |
| Active release atomicity | Blocked probe giữ pointer cũ | Đạt sau sửa |
| Historical Silver lookback/audit | Runtime strict=false/lookback=90, audit code có | Đạt có điều kiện |
| Snapshot control row safety | Đã phát hiện và sửa overwrite bug; regression đã thêm | Đạt sau sửa |
| MERGED/SPLIT history | Code phòng vệ có nhưng Silver chưa sinh dữ liệu để acceptance thật | P1 evidence gap |
| BI/Power BI external acceptance | Chưa có bằng chứng kết nối Power BI qua SSH tunnel | P2/evidence gap |
| Gold DAG enabled | DAG tồn tại nhưng đang paused; `LAKEHOUSE_GOLD_ENABLED=false` | Chủ ý, chưa tự động hóa daily |

## 5. Các gap còn lại và thứ tự xử lý

### P1 — cần xử lý trước khi gọi hệ thống “chạy đều production”

1. **Lịch compaction Bronze chưa tồn tại.** Engine có test nhưng không được gọi. Cần thêm một job/DAG riêng chạy partition đủ cũ, mặc định archive + verify, không chạy compaction đồng thời với crawler.
2. **Alert delivery chưa bật.** Cần cấu hình channel/credential trong runtime secret store và test một alert redacted trên channel thật; hiện chỉ có ledger/redaction, chưa có bằng chứng gửi.
3. **Định nghĩa “full/complete” của crawler chưa đủ bằng chứng runtime.** `max-pages=5` là ceiling an toàn, không phải bằng chứng đã cào hết; cần lưu stop reason/source total reconciliation cho từng source.
4. **Review queue chưa có quy trình đóng vòng.** `REVIEW_REQUIRED=1.076` được ghi nhận, nhưng chưa có workflow owner/decision/apply/replay đã kiểm chứng.
5. **Production release namespace migration.** Code mới cô lập run mới; cần policy để Gold đọc cả layout cũ và mới trong thời gian chuyển tiếp, sau đó không rewrite/xóa lịch sử nếu chưa có kế hoạch retention.

### P2 — hardening và bằng chứng

1. Đồng bộ timeout giữa ba plan và DAG; hiện code đang rộng hơn plan.
2. Cấu hình/kiểm chứng MinIO lifecycle expiration cho landing/archive theo retention đã chốt.
3. Cập nhật các checklist trong plan Silver/Gold và README để không còn nói Silver/Gold chưa nằm trong snapshot hoặc “cần viết mới” khi code đã có.
4. Kiểm chứng Power BI/semantic views qua kênh truy cập thực tế.
5. Sinh acceptance fixture thật cho `MERGED`/`SPLIT` hoặc ghi rõ đây là scenario phòng vệ chưa thể nghiệm thu bằng dữ liệu production.
6. Có quy trình cookie/session refresh; hiện 403 sẽ fail-closed nhưng không tự khôi phục.

### P0

Không còn P0 mở trong release mới. Các release Silver cũ lỗi checksum/duplicate vẫn được giữ nguyên lịch sử và bị reader loại khỏi Gold; không được dùng lại.

## 6. Vấn đề tài liệu cần chỉnh

Ba plan hiện là tài liệu thiết kế kiêm lịch sử triển khai, nên có các đoạn cũ mâu thuẫn với runtime hiện tại:

- `README.md` vẫn ghi “Silver/Gold chưa nằm trong source snapshot”, trong khi source và release thật đã có.
- `docs/silver-design-stages.md` checklist vẫn đánh dấu schema, resolver, lifecycle, registry, pipeline và DAG là “cần viết mới/cần refactor”, không còn đúng với code hiện tại.
- `docs/gold-design-stages.md` phần kết luận vẫn liệt kê DB/Silver/Gold run là việc còn lại dù production evidence đã có; một số baseline commit/status là lịch sử cũ.
- `docs/bronze-design-stages.md` đã ghi rõ các gap vận hành thật; các gap này vẫn đúng, không được đánh dấu hoàn tất chỉ vì tests pass.

Bản báo cáo này là addendum hiện trạng; không sửa lịch sử trong ba plan bằng cách xóa các baseline cũ. Khi cập nhật, nên thêm mục `Current acceptance addendum` có timestamp, commit và evidence thay vì thay đổi âm thầm.

## 7. Kiểm thử đã chạy

- Focused regression sau sửa:
  - blocked snapshot không overwrite `LOADED`: pass.
  - missing snapshot không overwrite `LOADED`: pass.
  - Silver run namespace ngăn object overwrite: pass.
- Full suite sau các sửa mới: **293 tests, OK (skipped=2)**.
- `py_compile` toàn bộ `src/etl`, `src/storage`, `src/utils`, `src/gold`, `dags`: pass.
- `bash -n infrastructure/postgres/create_gold_db.sh`: pass.
- `git diff --check`: pass tại thời điểm tạo báo cáo.
- Airflow DAG import errors: `[]`.

## 8. Phạm vi chưa thể khẳng định

- Đã chạy Silver replay từ Bronze đã commit; không crawl lại TopCV do HTTP 403.
- Chưa bật alert thật vì cần secret/channel approval; không đoán credential.
- Chưa chạy compaction production vì là thao tác thay đổi object lifecycle.
- Chưa chạy lại DDL production hoặc `mark_missing_day`, theo yêu cầu đã chốt.
- Chưa coi test suite là bằng chứng source completeness; tests chỉ chứng minh contracts/fixtures.

## 9. Khuyến nghị vận hành

Trước khi bật Gold DAG daily và gọi pipeline “chạy đều”: xử lý compaction schedule, alert delivery, crawler stop-reason/total reconciliation và review queue. Sau mỗi thay đổi, chạy acceptance gate: full tests, compile, DAG import check, read-only Bronze/Silver/Gold reconciliation, staged secret scan, commit/push và remote SHA readback.

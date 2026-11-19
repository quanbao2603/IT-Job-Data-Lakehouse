# Báo cáo rà soát tổng thể Bronze–Silver–Gold

- **Dự án:** `IT-Job-Data-Lakehouse`
- **Thời điểm kiểm tra:** 2026-11-19 (UTC+07)
- **Phạm vi:** đối chiếu toàn bộ ba plan `bronze-design-stages.md`, `silver-design-stages.md`, `gold-design-stages.md` với code, tests và runtime production.
- **Git baseline:** `main` tại `18ef8de` trước đợt rà soát; các sửa lỗi và báo cáo hiện nằm trong commit series sau baseline này.
- **An toàn:** không chạy lại DDL production, không chạy `mark_missing_day` chủ động, không reset State Store, không xóa Bronze/Silver, không in credentials/cookie/raw payload.

## 1. Kết luận ngắn

Ba tầng **đã chạy được theo đường chính Bronze → Silver → Gold**, nhưng **chưa nên gọi là hoàn tất production-ready** vì còn các gap vận hành và một lỗi dữ liệu control đã được phát hiện trong quá trình kiểm tra.

### Trạng thái hiện tại

- **Bronze:** đường crawl, envelope, manifest, checksum, State Store, audit gate và Airflow hoạt động; runtime có 46 object Bronze, 5.376 records đăng ký, landing không còn `.uploading`. Tuy nhiên compaction chưa được lịch gọi, alert chưa bật, timeout code chưa khớp plan và runtime State Store còn 3 `ABANDONED`/9 `FAILED` lịch sử.
- **Silver:** release thật `silver_release_manual_goldprep_d279d1a` ở `COMPLETE`, consumable, 456 objects trong manifest, verify checksum/row count **0 lỗi**, đủ 9 platform, 5.162 observation hợp lệ, quarantine rate 3,9807% dưới ngưỡng 5%. Phát hiện rủi ro writer: các run khác nhau trước đây có thể ghi đè cùng object key deterministic; đã thêm namespace theo `run_id` cho DAG và regression test.
- **Gold:** production load thành công với 32 DQ check đạt, active release đang trỏ đúng release thành công. Blocked probe chứng minh `active_release` không đổi, nhưng probe trước đây đã ghi đè `gold_control.daily_snapshot` của cùng ngày thành `FAILED`. Đã sửa code để blocked/missing không ghi đè snapshot `LOADED` và đã reconciliate control row về release thành công, không reload dữ liệu.

## 2. Bằng chứng runtime đã thu được

### Bronze

- `ingestion_runs`: 87 `SUCCESS`, 9 `FAILED`, 3 `ABANDONED` trong State Store hiện tại.
- `ingestion_state`: 9 source đều có `last_run_status=SUCCESS` ở lần trạng thái mới nhất.
- `ingestion_objects`: 46 objects, 5.376 records, khoảng 6.237.284 bytes; `is_compacted=0` là 46; `compaction_audit_logs=0`.
- MinIO read-only probe:
  - `landing`: 0 objects, 0 `.uploading`.
  - `bronze`: 46 objects.
  - `silver`: 1.100 objects.
- Airflow:
  - DAG import errors: `[]`.
  - `daily_nine_platform_bronze_incremental`: unpaused.
  - scheduler/webserver đang active ở lần kiểm tra trước.

### Silver

- Release: `silver_release_manual_goldprep_d279d1a`.
- `status=COMPLETE`, `is_consumable=True`.
- 456 objects trong manifest, verify checksum và Parquet row count: 0 lỗi.
- 9 platform: `careerviet`, `devwork`, `itviec`, `topcv`, `topdev`, `upzi`, `vieclam24h`, `vietnamdevs`, `vietnamworks`.
- Manifest metrics: 5.376 observations xử lý, 5.162 valid, 214 quarantine, quarantine rate 3,9807%, 1.076 review-required links.
- Object MinIO Silver tồn tại và đã được đọc kiểm tra thông qua release mirror/local mirror.

### Gold

- `active_release`: `gold_20261119_silver_release_manual_goldprep_d279d1a`.
- `load_run` active release: `SUCCESS`.
- Row counts active release:
  - `dim_job=3.077`, `dim_company=1.217`, `dim_platform=10`, `dim_skill=132`, `dim_date=2.343`.
  - `fact_job_daily_snapshot=3.077`.
  - `fact_job_platform_daily=3.077`.
  - `fact_job_skill_daily=10.656`.
  - `fact_job_change_event=95`.
  - `fact_job_market_daily=403`.
  - `fact_company_hiring_daily=1.191`.
  - `fact_skill_pair_daily=5.201`.
- DQ production: 32 checks, 0 failed cho active release.
- Blocked publish probe: kết quả `BLOCKED`; active pointer trước/sau giữ nguyên.
- Control row đã được sửa lại thành `daily_snapshot.status=LOADED`, trỏ cùng `gold_release_id` và `silver_release_id` với active successful release. Không nạp lại fact/dimension.

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
| Identity/observation preservation | Release thật có 5.162 valid observations | Đạt |
| Entity resolution/false-positive guard | Code và tests có; 1.076 review-required được ghi | Đạt có điều kiện |
| Lifecycle/grace/partial-run | Có code; DAG hiện dùng ALL_SUCCESS nên partial semantics chưa là đường production chính | Đạt MVP |
| Writer checksum/manifest | Release verify 0 lỗi | Đạt |
| MinIO path isolation | Đã thêm namespace cho run mới; release cũ cần giữ nguyên layout | Đạt sau sửa |
| Airflow full handoff | DAG đã chạy thật và release COMPLETE | Đạt |
| Gold handoff | Gold đã đọc release và publish thành công | Đạt |
| Acceptance documentation | Plan/checklist còn chứa nhiều trạng thái “cần viết mới” cũ | **P1 tài liệu** |
| Review queue resolution | Có pending/review-required nhưng chưa có workflow vận hành đã chứng minh | P1 |

### 4.3. Gold

| Hạng mục | Đánh giá | Mức |
|---|---|---|
| PostgreSQL control/schema | Đã chạy production | Đạt |
| Release reader/checksum/row count | Active Silver release verify 0 lỗi | Đạt |
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

### Không phát hiện P0

Không phát hiện lỗi làm mất dữ liệu Bronze, expose release Gold sai active pointer, hoặc cho phép publish Gold khi DQ BLOCK trong lần audit này. Các lỗi P1 nêu trên vẫn đủ để không gọi hệ thống là fully operational/zero-touch.

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
- Full suite: **289 tests, OK (skipped=2)**.
- `py_compile` toàn bộ `src/etl`, `src/storage`, `src/utils`, `src/gold`, `dags`: pass.
- `bash -n infrastructure/postgres/create_gold_db.sh`: pass.
- `git diff --check`: pass tại thời điểm tạo báo cáo.
- Airflow DAG import errors: `[]`.

## 8. Phạm vi chưa thể khẳng định

- Chưa chạy full crawl mới chỉ để audit, vì đó là side effect không cần thiết và có thể thay đổi watermark/source state.
- Chưa bật alert thật vì cần secret/channel approval; không đoán credential.
- Chưa chạy compaction production vì là thao tác thay đổi object lifecycle.
- Chưa chạy lại DDL production hoặc `mark_missing_day`, theo yêu cầu đã chốt.
- Chưa coi test suite là bằng chứng source completeness; tests chỉ chứng minh contracts/fixtures.

## 9. Khuyến nghị vận hành

Trước khi bật Gold DAG daily và gọi pipeline “chạy đều”: xử lý compaction schedule, alert delivery, crawler stop-reason/total reconciliation và review queue. Sau mỗi thay đổi, chạy acceptance gate: full tests, compile, DAG import check, read-only Bronze/Silver/Gold reconciliation, staged secret scan, commit/push và remote SHA readback.

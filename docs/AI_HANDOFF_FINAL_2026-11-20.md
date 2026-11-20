# Báo cáo bàn giao dự án IT-Job-Data-Lakehouse

**Thời điểm:** 2026-11-20 (UTC+07)
**Phạm vi:** Bronze, Silver, Gold, Airflow, MinIO, PostgreSQL và các workflow vận hành.
**Mục đích:** Bàn giao cho AI/kỹ sư khác tiếp tục dự án mà không phải suy đoán lại.

> Báo cáo không chứa credential, cookie, token, raw payload hoặc dữ liệu cá nhân. Các tài liệu design cũ vẫn giữ vai trò lịch sử; khi có mâu thuẫn, ưu tiên code và bằng chứng runtime mới nhất.

---

## 1. Kết luận điều hành

Đường chính đã được triển khai và kiểm chứng:

```text
9 crawler
  → Bronze MinIO + SQLite State Store
  → Silver Parquet release
  → Gold PostgreSQL release/publish
```

### Trạng thái hiện tại

- **Bronze:** ingestion, manifest, checksum, watermark, lock, heartbeat, audit gate và MinIO đã triển khai.
- **Silver:** release mới đã verify local và remote MinIO, logical keys không trùng.
- **Gold:** DQ đạt, load thành công, active release trỏ đúng release mới.
- **Gold DAG:** đã bật và unpause.
- **Bronze compaction DAG:** đã triển khai và unpause.
- **Silver review queue DAG:** đã triển khai, unpause và chạy thành công.
- **Alert thật:** chưa bật vì chưa có credential/destination được cấp trong secret store.
- **Review queue:** còn bản ghi cần người nghiệp vụ duyệt; không tự động quyết định thay nghiệp vụ.

Kết luận chính xác: **phần triển khai kỹ thuật 3 tầng đã hoàn tất; zero-touch vận hành vẫn còn phụ thuộc alert credential và xử lý review queue.**

---

## 2. Git và repository

- Project root: `/home/womcloud/IT-Job-Data-Lakehouse`
- Branch: `main`
- Commit code/runtime gần nhất trước khi tạo file này: `f979de00a561f710661a5d65322ce0a4ee1cd415` (sẽ được supersede bởi commit sửa env sau khi acceptance gate hoàn tất).
- Remote: `origin/main`
- Runtime files như `data/`, `.env`, cookies, logs, virtualenv và MinIO data không thuộc Git.

Các commit triển khai gần nhất:

```text
bc10f45  fix: add TopCV circuit breaker pacing
b5238ea  fix: reject corrupt Silver releases
ec9f4fe  fix: harden three-layer runtime contracts
0999389  docs: record clean Silver and Gold replay
64304ad  feat: operationalize compaction review and Gold
f979de0  fix: repair operational DAG imports
```

Sau khi commit file bàn giao này, phải xác nhận lại HEAD, remote SHA và worktree.

---

## 3. Bronze

### 3.1. Thành phần đã triển khai

- 9 crawler và `BaseCrawler`.
- `MetadataEnvelope` với checksum, observed time, ingestion time, format version và schema-drift rescue.
- `AtomicWriter` ghi landing → verify → Bronze.
- SQLite State Store gồm:
  - `ingestion_state`
  - `ingestion_runs`
  - `ingestion_objects`
  - `compaction_audit_logs`
- Lock theo source/run bằng `BEGIN IMMEDIATE`.
- Heartbeat và stale/dead-PID recovery.
- Bronze audit gate kiểm tra manifest, size, checksum, envelope integrity, record count và dangling landing objects.

### 3.2. Runtime evidence

```text
landing objects: 0
Bronze objects: 50
Bronze registered records: 5.435
State Store RUNNING rows: 0
```

Lịch sử State Store có các run `SUCCESS`, `FAILED` và `ABANDONED`; cần phân biệt lịch sử lỗi với trạng thái hiện hành.

### 3.3. TopCV

TopCV từng trả HTTP 403 trong live crawl. Đã sửa cơ chế:

- Burst nhỏ 1–2 request.
- Cooldown chạy trước burst kế tiếp.
- HTTP 403 kích hoạt circuit breaker.
- Cooldown state được ghi atomically, nên Airflow retry/process mới không gửi request lại ngay.
- 403 vẫn fail-closed, không biến thành `SUCCESS` giả.
- Có regression tests cho burst, cooldown và process mới.

Silver replay thành công bằng Bronze objects đã commit, không crawl lại TopCV. Vì vậy downstream replay đã được kiểm chứng nhưng live source health của TopCV sau sửa chưa có bằng chứng thành công mới.

### 3.4. Compaction

DAG:

```text
bronze_compaction_daily
Schedule: 03:30 Asia/Ho_Chi_Minh
Age safety window: 2 ngày
Minimum files: 7
Lifecycle: archive + verify trước khi remove active copy
```

DAG đã unpause. Manual run `manual_compaction_64304ad` có trạng thái `success`, nhưng không có partition đủ điều kiện để gom:

```text
compaction_audit_logs: 0
is_compacted=0: 50 objects
```

Đây là `SKIPPED` an toàn, không phải lỗi và không phải bằng chứng đã compact một file production.

---

## 4. Silver

### 4.1. Release hiện hành

```text
silver_release_id: silver_release_manual_silver_replay_bc10f45
status: COMPLETE
is_consumable: True
objects: 208
records: 13.748
platforms: 9
```

Metrics:

```text
total_observations_processed: 5.435
valid_observations: 5.221
quarantine_count: 214
quarantine_rate_pct: 3.9374
review_required_count: 1.079
canonical_entity_count: 3.092
```

### 4.2. Integrity verification

Đã kiểm tra:

- Local manifest/file checksum: 0 lỗi.
- Local Parquet row count: 0 lỗi.
- Remote MinIO read-back: 208/208 objects, 0 lỗi.
- Logical-key uniqueness:
  - observations: 5.221 rows / 5.221 unique `observation_id`;
  - entities: 3.092 rows / 3.092 unique `unified_job_id`;
  - entity links: 5.221 rows / 5.221 unique `(observation_id, resolution_version)`;
  - quarantine: 214 rows / 214 unique `observation_id`.

Các release Silver cũ có checksum mismatch hoặc duplicate canonical entity vẫn được giữ làm lịch sử nhưng release reader loại khỏi Gold.

### 4.3. Review queue

Đã triển khai:

- SQLite audit table `silver_review_decisions`.
- DAG `silver_review_queue_daily`, schedule 06:15.
- CSV queue `data/operational/pending_review.csv`.
- Lệnh `sync` để tạo lại queue từ registry.
- Lệnh `ACCEPT`/`REJECT` ghi quyết định atomically cùng reviewer và timestamp.

DAG run đã verify:

```text
manual_review_queue_f979de0 = success
pending queue = 1.079 rows
silver_review_decisions = 0
```

Lệnh xử lý một review:

```bash
lakehouse_env/bin/python -m src.etl.review_queue \
  --state-db data/state_store.db \
  --queue data/operational/pending_review.csv \
  decide <observation_id> \
  --decision ACCEPT \
  --reviewer <operator> \
  --candidate-entity-id <unified_job_id> \
  --note 'verified by operator'
```

Dùng `REJECT` khi candidate không phải cùng entity. Không tự động ACCEPT/REJECT nếu chưa có quyết định nghiệp vụ.

---

## 5. Gold

### 5.1. Release hiện hành

```text
gold_release_id: gold_20261119_silver_release_manual_silver_replay_bc10f45
silver_release_id: silver_release_manual_silver_replay_bc10f45
load_run: SUCCESS
DQ: 32 total, 0 failed
snapshot: LOADED
```

Control state đã read-back:

- `active_release` trỏ release mới.
- `daily_snapshot` trỏ cùng release và có `LOADED`.
- Release trước đó được lưu trong `previous_release`.

### 5.2. Gold DAG

Runtime flag đã bật:

```text
LAKEHOUSE_GOLD_ENABLED=true
```

DAG `daily_gold_mart_build` đã unpause. Manual run:

```text
manual_gold_64304ad = success
manual_gold_post_password_check_125640 = success
manual_gold_final_validation_160142 = success
```

Run `manual_gold_post_password_check_125640` được thực hiện sau khi cập nhật PostgreSQL password và sau khi Airflow restart. Cả `gold_preflight`, `gold_build_and_publish` và `gold_pipeline_summary` đều `success`; Gold subprocess được ép dùng environment file runtime và `--no-schema --no-mark-missing-day`, vì production DDL đã được xác nhận chạy trước đó.

Gold chỉ publish khi Silver release consumable và các blocking DQ checks đạt.

---

## 6. Automation và services

### 6.1. DAG hiện hành

```text
daily_nine_platform_bronze_incremental
  Bronze crawl → Bronze audit → Silver guard

bronze_compaction_daily
  archive-verified Bronze compaction

silver_review_queue_daily
  refresh pending human-review queue

daily_gold_mart_build
  Gold preflight → build/publish → summary
```

Trạng thái lần kiểm tra cuối:

```text
daily_gold_mart_build       unpaused
bronze_compaction_daily     unpaused
silver_review_queue_daily   unpaused
airflow-scheduler            active
airflow-webserver            active
DAG import errors             none
```

### 6.2. Test gate

```text
300 tests, OK (skipped=5)
Airflow DAG contract tests: 5 passed
py_compile: pass
bash -n infrastructure/postgres/create_gold_db.sh: pass
git diff --check: pass
Gold final manual validation: `manual_gold_final_validation_160142 = success` (all 3 tasks success)
```

Hai skipped là nhóm test cần Airflow virtualenv trong môi trường test hiện tại.

---

## 7. Alerting

Đã có code:

- Airflow failure/retry callbacks.
- Redaction secret/HTML/Bearer.
- Append-only failure ledger.
- Telegram/Discord delivery adapter.
- `alert_configuration_status()` không in giá trị nhạy cảm.

Runtime hiện tại:

```text
ALERT_ENABLED=false
ready=false
reason=ALERT_ENABLED=false
```

Alert thật chưa bật vì chưa có credential/destination được cấp trong secret store. Không đưa bot token/webhook vào Git, shell history hoặc Telegram.

Để hoàn tất alert production:

1. Cấp credential qua secret store được phê duyệt.
2. Cấu hình channel và bật `ALERT_ENABLED` trong runtime environment.
3. Reload Airflow services.
4. Gửi một controlled test alert.
5. Xác nhận delivery thật từ channel.

Unit test redaction không phải bằng chứng alert delivery.

---

### 8.1. PostgreSQL password / Airflow restart validation

Sau khi password PostgreSQL Gold được người vận hành cập nhật và Airflow restart:

```text
GOLD_DB_CONNECT=OK
principal = gold_writer / lakehouse
manual_gold_post_password_check_125640 = success
active_release = gold_20261119_silver_release_manual_silver_replay_bc10f45
snapshot = LOADED
DQ current release = 32 total / 0 failed
```

Không ghi lại hoặc so sánh giá trị password. Phát hiện kỹ thuật: LocalExecutor worker không luôn giữ các biến từ systemd `EnvironmentFile`; DAG Bronze/Silver và Gold đã được sửa để đọc lại file runtime khi tạo subprocess. Gold production command dùng `--no-schema --no-mark-missing-day` để không chạy lại production DDL hoặc mutate `daily_snapshot` khi thiếu Silver release.


### Cần người dùng/nghiệp vụ

- Cấp credential và destination để bật alert thật.
- Chỉ định reviewer/owner và xử lý 1.079 review records bằng `ACCEPT`/`REJECT`.

### Đã tự động hóa, chờ dữ liệu đủ điều kiện

- Bronze compaction sẽ tự chạy theo lịch. Không ép compact khi partition chưa đủ 2 ngày và 7 files.

### Hardening/evidence tùy chọn

- Xác minh một live TopCV crawl thành công sau circuit breaker.
- Kiểm chứng Power BI/SSH tunnel.
- Bổ sung source-total/stop-reason evidence cho full pagination.
- Kiểm chứng MinIO lifecycle expiration.
- Sinh acceptance data thật cho `MERGED/SPLIT` nếu nghiệp vụ yêu cầu.

---

## 9. Quy tắc tiếp tục dự án

- Luôn source `/home/womcloud/airflow/airflow.env` trước Airflow CLI.
- Không chạy `airflow db init` để chữa lỗi thiếu environment.
- Không chạy lại production DDL hoặc `mark_missing_day` nếu không có yêu cầu riêng.
- Không force-push và không tự revert các commit Gold/Silver đã chốt.
- Không đoán credential.
- Không biến HTTP 403 thành crawler success.
- Không chọn Silver release chỉ dựa vào `status=COMPLETE`; phải verify checksum, row count và logical-key uniqueness.
- Không chạy Gold khi Silver chưa `is_consumable=True`.
- Không tự quyết định review queue thay nghiệp vụ.
- Trước khi báo hoàn tất, read-back parent DAG run, task states, release manifest, MinIO objects, Gold control rows và Git remote SHA.

---

## 10. Lệnh kiểm tra nhanh

```bash
cd /home/womcloud/IT-Job-Data-Lakehouse

git status --short --branch
git rev-parse HEAD
git ls-remote origin refs/heads/main

lakehouse_env/bin/python -m unittest discover -s tests -p 'test_*.py' -q
lakehouse_env/bin/python -m py_compile src/crawlers/*.py src/etl/*.py src/storage/*.py src/utils/*.py src/gold/*.py dags/*.py

set -a && . ~/airflow/airflow.env && set +a
/home/womcloud/airflow_env/bin/airflow dags list-import-errors
/home/womcloud/airflow_env/bin/airflow dags list

lakehouse_env/bin/python -m src.etl.review_queue \
  --state-db data/state_store.db \
  --queue data/operational/pending_review.csv sync
```

Không đưa output chứa credential, cookie hoặc raw payload vào handoff tiếp theo.

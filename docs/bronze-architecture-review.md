# Đánh giá lại kiến trúc tầng Bronze — IT-Job-Data-Lakehouse

> **Bản review lần 3 sau đợt cải tiến mới nhất**
>
> **Phạm vi:** chỉ đánh giá tầng Bronze/ingestion. Silver, Gold, dashboard và lớp phân tích chưa được đánh giá.
>
> **Phương pháp:** đối chiếu source code, unit/integration tests, SQLite State Store, MinIO runtime, manifest, backup artifact, Compose configuration và network binding. Credential/API key/cookie không được in vào báo cáo.

---

## 1. Kết luận điều hành

Đợt cải tiến mới đã xử lý hai vấn đề nghiêm trọng của lần review trước:

1. **Credential runtime:** credential trong `.env` hiện khớp với credential của MinIO container đang chạy; S3 read-only probe hoạt động.
2. **Concurrency lock:** `BEGIN IMMEDIATE` và partial unique index đã được bổ sung; probe hai process thật chỉ cho phép một process thắng.

Các cải tiến mới khác:

- bổ sung `heartbeat_at` và phương thức `heartbeat()`;
- thêm test race condition giữa hai process;
- thêm test heartbeat lifecycle;
- backup có staging directory, fail-closed và atomic rename;
- có `verify_backup_drill()`;
- test suite tăng lên 14 test và tất cả đều pass;
- manifest runtime khớp toàn bộ object Bronze;
- backup payload và restore verification đều pass.

### Điểm đánh giá mới

| Phương diện | Đánh giá |
|---|---:|
| Thiết kế kiến trúc Bronze | **9.0/10** |
| Implementation hiện tại | **8.4/10** |
| Production readiness | **khoảng 7.6/10** |
| Phù hợp tiểu luận/MVP trên VPS | **Đạt tốt** |

### Verdict

> Tầng Bronze hiện đã **đủ ổn để chốt phạm vi MVP/tiểu luận** và đã tiến gần tới mức vận hành đáng tin cậy trên một VPS đơn. Tuy nhiên, hệ thống vẫn **chưa phải raw immutable production Bronze hoàn chỉnh** vì chưa lưu raw HTML/API byte-level, compaction còn xóa object cũ, watermark chưa source-native và chưa có idempotency crash-after-copy được chứng minh.

Không nên tuyên bố tuyệt đối:

```text
raw 100%
zero data loss
WORM immutable tuyệt đối
atomic transaction hoàn chỉnh
idempotency production-grade
```

---

## 2. Bằng chứng kiểm tra lần này

### 2.1. Compile và test suite

Đã chạy:

```bash
lakehouse_env/bin/python -m compileall -q src tests
lakehouse_env/bin/python -m unittest discover -s tests -v
```

Kết quả:

```text
compile_exit=0
Ran 14 tests
OK
14 passed
0 failed
```

Test mới/đáng chú ý:

```text
test_multiprocess_concurrency_race: PASS
test_heartbeat_lifecycle:          PASS
test_manifest_failure_aborts_commit: PASS
test_dead_pid_instant_recovery:    PASS
```

Test race trong suite tạo hai process OS thật, dùng database tạm và xác nhận:

```text
chỉ một process acquire thành công
process còn lại bị từ chối
chỉ có một RUNNING row
```

**Đánh giá:** unit/integration suite đang xanh; coverage Bronze đã tốt hơn rõ rệt, dù vẫn còn thiếu crash-after-copy và MinIO failure-path test.

### 2.2. MinIO runtime

Read-only S3 probe bằng chính credential từ `.env` đã hoạt động.

```text
container: datalake-minio
status:   Up
S3 API:   127.0.0.1:9000
Console:  127.0.0.1:9001
```

Credential reconciliation chỉ so sánh hash, không in secret:

```text
.env credential hash       = container credential hash
loaded application hash    = container credential hash
S3 list/stat probe         = PASS
```

Compose render:

```yaml
environment:
  MINIO_ROOT_USER: ${MINIO_ROOT_USER}
  MINIO_ROOT_PASSWORD: ${MINIO_ROOT_PASSWORD}
```

Network binding:

```text
127.0.0.1:9000
127.0.0.1:9001
```

Không còn bằng chứng MinIO đang bind trên `0.0.0.0` hoặc `[::]`.

Permission:

```text
.env:                       600
src/crawlers/cookies/*.txt: 600
```

### 2.3. Bronze và landing live

```text
Bronze objects:       17
Bronze records:       5.444
Landing objects:      0
```

Phân bổ record:

```text
ITviec:        690
TopCV:       2.618
TopDev:      1.245
VietnamWorks:  891
```

Envelope integrity:

```text
Checksum hợp lệ: 5.444 / 5.444
Checksum lỗi:    0
```

### 2.4. Manifest reconciliation

Đã đối chiếu object thực tế trong MinIO với `ingestion_objects`:

```text
Bronze objects:              17
Manifest rows:               17
Unmanifested objects:         0
Manifest-only missing keys:   0
Size mismatch:                0
Record-count mismatch:        0
```

SQLite:

```text
PRAGMA integrity_check: ok
RUNNING rows hiện tại: 0
```

Phân bổ manifest:

```text
itviec:        4 objects
topcv:         5 objects
topdev:        4 objects
vietnamworks:  4 objects
```

### 2.5. Concurrency lock live probe

Probe hai process thật chạy đồng thời trên SQLite database tạm:

```text
run_1 → acquire thành công
run_2 → bị từ chối
RUNNING rows → 1
```

Code hiện có:

```text
BEGIN IMMEDIATE
partial unique index trên ingestion_runs(source_name)
WHERE status = 'RUNNING'
```

Đây là bằng chứng tốt hơn unit test tuần tự của bản trước.

### 2.6. Heartbeat

State Store hiện có:

```text
heartbeat_at
heartbeat(run_id)
```

Test lifecycle đã pass:

```text
acquire run
→ heartbeat()
→ heartbeat_at được cập nhật
```

Cần phân biệt:

```text
cơ chế heartbeat đã tồn tại và có test
```

với:

```text
mọi crawler đều gọi heartbeat định kỳ trong suốt thời gian chạy
```

Hiện chưa có bằng chứng đầy đủ rằng `BaseCrawler` có background heartbeat loop hoặc gọi `heartbeat()` trong các vòng fetch dài.

### 2.7. Backup và restore verification

Backup mới nhất:

```text
backups/lakehouse_backup_20261106_150149
```

Artifact đã kiểm tra:

```text
SQLite quick_check:       ok
Manifest rows:            17
Object manifest entries:  17
Payload files:            17
Missing files:             0
Hash/size errors:          0
Empty/invalid records:     0
Decompressed records:  5.444
Bytes:               5.183.222
```

Đã chạy:

```bash
lakehouse_env/bin/python -m src.utils.backup_bronze \
  --verify-drill backups/lakehouse_backup_20261106_150149
```

Kết quả:

```text
SQLite Integrity Check: PASS
Payload Check: 17/17 objects khớp SHA-256 và size
RESTORE DRILL: PASS
```

#### Giới hạn cách gọi

`verify_backup_drill()` hiện là **snapshot restore verification**: kiểm tra SQLite integrity, object tồn tại, kích thước và SHA-256 trong artifact backup.

Chưa có bằng chứng về:

```text
restore thật vào một MinIO instance/bucket độc lập
copy backup sang host khác
restore rồi chạy S3 list/stat trên instance khôi phục
```

Vì vậy nên gọi chính xác là:

```text
local backup snapshot + restore verification drill
```

chưa phải full off-host disaster recovery drill.

---

## 3. Những phần đã đạt sau cải tiến

### 3.1. Secret interpolation và runtime authentication

Đã xử lý gap trước:

```text
Compose dùng biến môi trường
.env được load đúng
container dùng cùng credential
S3 read-only probe PASS
```

**Trạng thái: ĐẠT cho single-host MVP.**

Lưu ý: vẫn nên rotate root credential định kỳ và tạo user MinIO least-privilege cho crawler trước production.

### 3.2. Network exposure

MinIO hiện chỉ bind localhost:

```text
127.0.0.1:9000
127.0.0.1:9001
```

**Trạng thái: ĐẠT cho kiến trúc crawler và MinIO cùng VPS.**

Nếu cần truy cập Console từ máy khác, nên dùng SSH tunnel hoặc reverse proxy HTTPS có allowlist thay vì mở trực tiếp port.

### 3.3. Concurrency lock liên process

Đã bổ sung:

- transaction `BEGIN IMMEDIATE`;
- partial unique index chỉ cho một `RUNNING` row/source;
- xử lý `IntegrityError`/`OperationalError`;
- test hai process thật.

Probe lần này pass:

```text
exactly one winner
exactly one rejection
one RUNNING row
```

**Trạng thái: ĐẠT ở lock acquisition.**

#### Giới hạn còn lại

`mark_running()` vẫn là một API cũ có đường ghi trực tiếp, không mạnh bằng `acquire_run_lock()`. `BaseCrawler` hiện dùng `acquire_run_lock()`, nhưng nên loại bỏ hoặc chuyển `mark_running()` thành wrapper của lock atomic để không tạo lối bypass trong tương lai.

### 3.4. Heartbeat state và stale recovery

Đã có:

```text
heartbeat_at
heartbeat()
process_id
PID liveness check
heartbeat-aware stale timeout
```

**Trạng thái: ĐẠT cơ chế nền; integration vào mọi crawler chưa có bằng chứng đầy đủ.**

### 3.5. Manifest

Runtime hiện khớp hoàn toàn:

```text
17 object MinIO
17 manifest rows
0 missing
0 mismatch
```

**Trạng thái: ĐẠT theo live reconciliation.**

### 3.6. Backup fail-closed và staging

Backup utility hiện:

```text
ghi vào staging directory
→ kiểm tra size/hash
→ lỗi thì xóa staging
→ thành công thì rename atomically
```

Đã có option:

```bash
--include-objects
```

và verification:

```bash
--verify-drill <backup_dir>
```

**Trạng thái: ĐẠT ở local snapshot và verification.**

### 3.7. Schema contract TopCV

Test hiện tại xác nhận `company_industry` nằm trong payload chính và không bị rescue nhầm.

**Trạng thái: ĐẠT.**

### 3.8. Streaming ở storage/core path

`AtomicWriter` vẫn ghi gzip qua file tạm trên disk. `BaseCrawler` truyền envelope bằng generator. `CompactionEngine` đọc/ghi theo generator.

**Trạng thái: ĐẠT ở storage/core path.**

Chưa nên gọi toàn bộ crawler là O(1) RAM vì một số source vẫn có list discovery/detail.

### 3.9. Checksum và data integrity

Đã xác minh:

```text
5.444/5.444 envelope checksum pass
17/17 object manifest size/checksum match
17/17 backup payload size/hash match
```

**Trạng thái: ĐẠT.**

---

## 4. Vấn đề còn lại

### P1 — Chưa lưu raw HTML/API response byte-level

Crawler vẫn parse HTML/API và lưu payload đã trích xuất. Chưa thấy cơ chế lưu nguyên bản:

```text
raw HTML bytes
raw JSON response
HTTP headers
HTTP status
response body nguyên bản
```

Do đó semantics chính xác hiện tại là:

```text
immutable extracted payload
```

Không phải:

```text
raw source payload 100%
```

#### Hai hướng hợp lệ

**Giữ MVP nhẹ:**

```text
Bronze lưu payload parsed/extracted bất biến theo từng crawl run.
```

**Muốn raw replay:**

```text
bronze/raw/<source>/
bronze/extracted/<source>/
```

**Trạng thái: CHƯA ĐẠT theo nghĩa raw byte-level.**

### P1 — Compaction vẫn xóa object Bronze cũ

`CompactionEngine` vẫn có flow:

```text
tạo compacted object
→ verify record count
→ ghi audit
→ register object mới
→ mark object cũ compacted
→ remove object cũ
```

Điều này không tương thích với claim:

```text
Bronze immutable append-only tuyệt đối
```

#### Cần chốt một semantics

**Immutable raw/extracted:**

```text
bronze/extracted/ không xóa
bronze/compact/ là derived copy
```

**Retention-managed:**

```text
Bronze có compaction/retention policy;
object cũ được xóa sau khi verify và audit.
```

**Trạng thái: Có audit và verify, chưa immutable tuyệt đối.**

### P1 — Watermark timestamp vẫn là ingestion-time

`BaseCrawler` vẫn ghi:

```python
new_watermark = datetime.utcnow().isoformat() + "Z"
```

Đây là timestamp pipeline hoàn tất, không phải source-native event/update timestamp.

Crawler chủ yếu dùng:

```text
last_record_id
```

VietnamWorks có `approvedOn`, nhưng chưa được tách thành trường source watermark riêng trong State Store.

#### Khuyến nghị

Tách:

```text
source_watermark
ingestion_watermark
last_record_id
watermark_strategy
```

Nếu source không có cursor ổn định:

```text
overlap window
→ crawl lại vùng gần watermark
→ dedup bằng source + source_job_id + content_hash
```

**Trạng thái: Một phần.**

### P1 — Idempotency sau crash chưa được chứng minh

Đã có manifest, checksum, run ID và verified copy. Nhưng chưa có integration test cho boundary:

```text
copy Bronze thành công
→ process chết trước khi register manifest/state
→ retry cùng logical work
→ phát hiện object đã tồn tại
→ so size/checksum
→ không tạo duplicate
```

Object key hiện chứa UUID/run ID, nên retry toàn run có thể tạo object key khác nếu không có logical ingestion identity riêng.

#### Khuyến nghị

- dùng deterministic logical batch key hoặc source cursor range;
- existing-object check theo key;
- so sánh size/checksum;
- trạng thái `ALREADY_COMMITTED` khi trùng;
- test crash-after-copy;
- không coi verified copy tự động là idempotent.

**Trạng thái: Có nền tảng, chưa đạt production-grade.**

### P1 — Heartbeat chưa chứng minh được integration vào crawler loop

State Manager đã có `heartbeat()` và test lifecycle. Tuy nhiên source path hiện vẫn chưa thể hiện rõ:

```text
fetch dài
→ heartbeat định kỳ
→ renew heartbeat
→ tiếp tục crawl
```

Nếu một crawl dài hơn `STALE_LEASE_SECONDS` mà không gọi heartbeat, nó vẫn có nguy cơ bị đánh dấu stale.

**Trạng thái: Cơ chế ĐẠT; orchestration integration CHƯA CÓ BẰNG CHỨNG.**

### P1 — Restore drill chưa phải restore độc lập/off-host

Đã pass kiểm tra snapshot:

```text
SQLite integrity
object tồn tại
size
SHA-256
```

Nhưng chưa có:

```text
restore sang MinIO instance/bucket độc lập
restore từ host khác
S3 list/stat sau restore
```

**Trạng thái: Local backup verification ĐẠT; full DR CHƯA ĐẠT.**

### P1 — VietnamWorks còn dùng MD5 cho content hash

Các source khác dùng SHA-256, nhưng VietnamWorks còn:

```python
hashlib.md5(...).hexdigest()
```

Đây không phải checksum file Bronze, nhưng làm `content_hash` giữa source không đồng nhất.

**Khuyến nghị:** chuyển sang SHA-256 trên canonical business fields.

**Trạng thái: Cần sửa để đồng nhất.**

### P2 — Một số crawler còn buffer danh sách

Code còn thấy:

```text
TopDev:       raw_jobs = []
TopDev:       scraped_jobs = []
VietnamWorks: scraped_jobs = []
```

Vì vậy:

```text
True Streaming O(1) RAM toàn pipeline
```

chưa chính xác.

Mô tả đúng hơn:

```text
Storage path streaming qua disk; BaseCrawler truyền record bằng generator;
peak RAM của discovery/detail phụ thuộc từng source crawler.
```

**Trạng thái: Đạt ở storage/core, chưa end-to-end O(1).**

### P2 — Utility backfill/audit/backup vẫn có buffer toàn danh sách

Một số utility còn:

```python
objects = list(client.list_objects(...))
file_bytes = resp.read()
```

Với 17 object hiện tại không phải blocker. Khi Bronze tăng quy mô, nên chuyển sang iterator/chunked read.

**Trạng thái: Không chặn MVP; cần cải thiện khi scale.**

### P2 — Single-node/single-disk

MinIO vẫn là single-node/single-disk với bind mount local. Điều này phù hợp VPS/MVP nhưng không có HA hoặc disk redundancy.

**Trạng thái: Chấp nhận được cho MVP; chưa production HA.**

---

## 5. Phân tích duplicate và snapshot semantics

Live Bronze hiện có:

```text
Tổng record:              5.444
Unique job IDs:            5.096
Duplicate job-ID records:    348
Unique URLs:               5.159
Duplicate URL records:      285
Unique content hashes:      1.010
Duplicate content records: 1.282
```

Các số này không tự động chứng minh dữ liệu sai. Với Bronze snapshot/observation store, cùng một job xuất hiện qua nhiều lần crawl có thể là lịch sử quan sát hợp lệ.

Semantics nên ghi rõ:

```text
Bronze giữ observation theo crawl run.
Dedup latest/history không thực hiện ở Bronze.
Dedup và temporal history xử lý ở lớp downstream.
```

Lưu ý thêm: VietnamWorks dùng MD5 trong content hash, nên phân tích hash giữa các source chưa hoàn toàn đồng nhất cho tới khi chuyển sang SHA-256.

---

## 6. Ma trận tiêu chí Bronze cập nhật

| Tiêu chí | Trạng thái | Nhận xét |
|---|---|---|
| Landing/Bronze separation | Đạt | 17 Bronze object, landing 0 |
| Disk streaming writer | Đạt | Gzip qua temporary file |
| Crawler generator path | Đạt một phần | Core path generator; discovery vẫn buffer ở một số source |
| Metadata envelope | Đạt | Có metadata, run ID, format version |
| Envelope SHA-256 | Đạt | 5.444/5.444 pass |
| File SHA-256 | Đạt | Writer/backup hash |
| Post-copy verification | Đạt | Bronze size được stat sau copy |
| Schema drift handling | Đạt | Có `_rescued_data` và test |
| TopCV schema contract | Đạt | `company_industry` test pass |
| Current state | Đạt | Có `ingestion_state` |
| Run history | Đạt | Có SUCCESS/FAILED/ABANDONED |
| Object manifest | Đạt | 17/17 key khớp live MinIO |
| Manifest live reconciliation | Đạt | 0 unmanifested, 0 missing, 0 mismatch |
| Compose secret interpolation | Đạt | Dùng `.env` variables |
| Runtime credential alignment | Đạt | Hash khớp, S3 probe PASS |
| Local network exposure | Đạt | Bind localhost |
| Exclusive lock tuần tự | Đạt | Unit test pass |
| Exclusive lock liên process | Đạt | Probe và test 2 process pass |
| Partial unique active-run invariant | Đạt | Chỉ một RUNNING/source |
| Dead-PID recovery | Đạt cơ bản | Có PID check |
| Heartbeat storage/method | Đạt | Có field, method và test |
| Heartbeat crawler integration | Chưa có bằng chứng | Chưa thấy loop gọi định kỳ |
| Raw response preservation | Chưa đạt | Chỉ extracted payload |
| Append-only Bronze tuyệt đối | Chưa đạt | Compaction xóa object cũ |
| Compaction streaming | Đạt | Generator/file streaming |
| Compaction audit | Đạt | Có SQLite audit log |
| Source-native watermark | Chưa đạt đầy đủ | Timestamp hiện là ingestion time |
| ID watermark | Đạt một phần | Có `last_record_id` theo source |
| Idempotent retry | Chưa chứng minh | Thiếu crash-after-copy test |
| Local backup snapshot | Đạt | 17/17 payload verified |
| Fail-closed backup staging | Đạt | Staging + atomic rename |
| Restore verification drill | Đạt | SQLite + hash/size pass |
| Independent restore | Chưa đạt | Chưa restore vào instance khác |
| Off-host backup | Chưa đạt | Chưa có bằng chứng |
| Unit/integration tests | Đạt MVP | 14/14 pass |
| Failure-path tests | Đạt một phần | Manifest, PID, race, heartbeat đã có; thiếu crash-after-copy |
| Content hash consistency | Chưa đạt | VietnamWorks còn MD5 |
| Test data separation | Chưa recheck đầy đủ live | Cần kiểm tra prefix sau mỗi test run |
| Physical redundancy | Chưa đạt | Single-node/single-disk |

---

## 7. Kiến trúc Bronze nên chốt

Cho mục tiêu tiểu luận/MVP trên VPS tiết kiệm tài nguyên:

```text
Source crawler/API
    → source-specific cursor/overlap logic
    → ingestion controller + atomic source lock
    → MetadataEnvelope + content hash
    → disk-streaming AtomicWriter
    → landing temporary object
    → verified server-side copy
    → bronze/extracted JSONL.gz
    → object manifest + State Store
    → audit/backup
```

Prefix khuyến nghị:

```text
landing/<source>/
bronze/extracted/<source>/
bronze/compact/<source>/
bronze/quarantine/<source>/
```

Nếu thật sự cần raw replay:

```text
bronze/raw/<source>/
bronze/extracted/<source>/
```

### Semantics nên dùng ngay bây giờ

```text
Bronze là immutable extracted observation store theo từng crawl run.
Mỗi record có metadata ingestion, run_id, content_hash và source identity.
Các record lặp giữa nhiều lần crawl được giữ như snapshot/observation;
dedup latest/history để ở lớp downstream.
```

Nếu compaction tiếp tục xóa object cũ, phải gọi chính xác:

```text
retention-managed append-oriented Bronze
```

không gọi là WORM immutable.

---

## 8. Roadmap còn lại

### P0 — Không còn blocker MVP rõ ràng

Hai P0 của review trước đã được xử lý:

```text
credential/runtime alignment: PASS
inter-process lock race:      PASS
```

### P1 — Nên hoàn tất trước production thật

1. Chuyển VietnamWorks `content_hash` từ MD5 sang SHA-256.
2. Tách `source_watermark` khỏi `ingestion_watermark`.
3. Thêm source-native cursor/timestamp hoặc overlap window rõ ràng.
4. Tích hợp heartbeat định kỳ vào `BaseCrawler`/fetch loop.
5. Xóa hoặc biến `mark_running()` thành wrapper của `acquire_run_lock()`.
6. Thêm crash-after-copy idempotency test.
7. Dùng logical batch identity/deterministic key cho retry.
8. Quyết định raw-vs-extracted semantics trong code và tài liệu.
9. Đổi compaction thành derived copy không xóa Bronze gốc, hoặc ghi retention policy chính thức.
10. Restore thật sang MinIO instance/bucket độc lập.
11. Sao lưu off-host.
12. Tạo MinIO user crawler least-privilege thay vì dùng root account.

### P2 — Cải thiện khi scale

13. Streaming discovery/detail thay cho list buffer.
14. Backfill/audit/backup dùng iterator và chunked read.
15. Bổ sung metrics:

```text
records_discovered
records_written
records_failed
parse_success_rate
duplicate_count
retry_count
bytes_written
run_duration
```

16. Nếu cần HA, chuyển khỏi single-node/single-disk hoặc bổ sung chiến lược replication phù hợp.

---

## 9. Cách trình bày trung thực trong tiểu luận

### Có thể viết

```text
Hệ thống triển khai Bronze ingestion theo mô hình landing-to-Bronze.
Crawler truyền record qua generator tới writer ghi JSONL/Gzip bằng temporary
file trên disk. Mỗi record được bọc metadata và checksum SHA-256. State Store
lưu ingestion state, run history, object manifest, heartbeat và compaction
audit. Bronze object chỉ được coi là committed sau khi landing/Bronze được
kiểm tra. Hệ thống có schema drift handling, inter-process source lock,
backup snapshot fail-closed và restore verification drill.
```

### Phải ghi rõ giới hạn

```text
Phiên bản hiện tại chạy trên single-node/single-disk MinIO.
Payload là parsed/extracted payload, chưa lưu toàn bộ raw HTML/API.
Compaction hiện có thể xóa object cũ nên Bronze chưa phải WORM immutable.
Watermark timestamp hiện phản ánh ingestion time; cursor source-specific
chủ yếu dựa trên last_record_id. Heartbeat đã có ở State Manager nhưng cần
được tích hợp vào crawler loop dài. Backup đã có local snapshot và restore
verification, nhưng chưa có independent restore/off-host disaster recovery.
```

### Không nên tuyên bố

```text
Raw response 100%
Zero Data Loss tuyệt đối
Bronze immutable tuyệt đối
Atomic transaction hoàn chỉnh
Idempotency production-grade
Full off-host disaster recovery
End-to-end O(1) RAM cho mọi source crawler
```

---

## 10. Verdict cuối cùng

### Đã đạt

- Credential `.env` và MinIO runtime đã khớp.
- S3 read-only probe hoạt động.
- Compose không hardcode secret.
- MinIO chỉ bind localhost.
- `.env` và cookie permissions là `600`.
- Concurrency lock liên process đã pass probe thật.
- Partial unique index bảo vệ một `RUNNING` row/source.
- Heartbeat field/method và lifecycle test đã có.
- Manifest live khớp 17/17 object.
- Bronze có 17 object và 5.444 record.
- Landing rỗng.
- Envelope checksum 5.444/5.444 pass.
- Backup 17/17 payload pass size/hash.
- Restore verification pass.
- Backup staging/fail-closed/atomic rename đã có.
- TopCV schema contract đã pass.
- Unit/integration suite 14/14 pass.
- Storage writer và compactor streaming qua disk/generator.

### Còn cần hoàn thiện

```text
1. Chưa lưu raw HTML/API byte-level.
2. Compaction vẫn xóa object Bronze cũ.
3. Watermark timestamp chưa source-native.
4. Heartbeat chưa chứng minh được tích hợp vào crawler loop dài.
5. Idempotency crash-after-copy chưa được test/chứng minh.
6. Restore mới là verification local, chưa restore độc lập/off-host.
7. VietnamWorks còn MD5 content_hash.
8. mark_running() còn là đường bypass lock atomic.
9. Một số crawler vẫn buffer list.
10. MinIO vẫn single-node/single-disk.
```

### Kết luận cuối

> Sau đợt cải tiến này, hai blocker nghiêm trọng của review trước đã được giải quyết có bằng chứng: **runtime credential đã hoạt động** và **lock liên process đã pass race probe**. Manifest, checksum, backup payload, heartbeat nền, schema contract và test suite cũng đã được nâng cấp rõ rệt.
>
> Tầng Bronze hiện **đạt tốt cho tiểu luận/MVP**. Để gọi là production-ready hơn, ưu tiên tiếp theo là chốt semantics raw/compaction, tích hợp heartbeat vào crawler thật, tách source watermark, chứng minh idempotent retry sau crash và thực hiện restore độc lập/off-host.

---

## 11. Files và runtime đã đối chiếu

```text
src/storage/atomic_writer.py
src/storage/compaction_engine.py
src/storage/state_manager.py
src/storage/metadata_envelope.py
src/crawlers/base_crawler.py
src/crawlers/itviec_crawler.py
src/crawlers/topcv_crawler.py
src/crawlers/topdev_crawler.py
src/crawlers/vietnamworks_crawler.py
src/utils/backfill_manifest.py
src/utils/backup_bronze.py
src/utils/bronze_audit_reporter.py
src/utils/clean_test_bronze.py
tests/test_bronze_layer.py
infrastructure/docker-compose.yml
data/state_store.db
backups/lakehouse_backup_20261106_150149/
datalake-minio container
```

Không đưa vào báo cáo:

- password;
- API key;
- cookie/session token;
- raw payload nhạy cảm;
- secret value hoặc credential có thể sử dụng trực tiếp.

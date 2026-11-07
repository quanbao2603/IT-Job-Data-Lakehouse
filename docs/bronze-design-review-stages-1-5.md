# Nhận xét thiết kế tầng Bronze — Giai đoạn 1 đến 5

> Phạm vi: chỉ đánh giá 5 giai đoạn đầu trong bản thiết kế Notion được đọc ngày 2026-11-07. Giai đoạn 6 — orchestration/scheduling/observability — **chưa đánh giá** theo yêu cầu.
>
> Đây là tài liệu góp ý để AI sinh báo cáo. Không phải thay đổi code hay kết luận nghiệm thu runtime.

## 1. Kết luận ngắn

Thiết kế 5 giai đoạn đầu có hướng đi đúng và bao phủ hầu hết các mối quan tâm cốt lõi của Bronze: tách landing/Bronze, state store, manifest, checksum, schema drift, ghi streaming, retry và compaction. Tuy nhiên, bản hiện tại đang dùng một số từ quá mạnh hoặc mô tả cơ chế chưa khớp hoàn toàn với semantics thực tế của code/object storage.

Khuyến nghị dùng kết luận sau trong báo cáo:

> Kiến trúc đạt mức **Bronze MVP/thesis-ready về mặt cấu trúc**, với các cơ chế chính đã được định nghĩa rõ. Để gọi là production-grade hoặc khẳng định "raw/atomic/zero-loss/WORM/O(1) tuyệt đối", cần bổ sung hoặc thu hẹp một số tuyên bố về raw preservation, atomic commit, idempotency, watermark, SQLite WAL, streaming end-to-end, schema contract, compaction và failure recovery.

## 2. Các điểm cần sửa bắt buộc trong nội dung báo cáo

### 2.1. Phân biệt `extracted payload` với `raw data`

Thiết kế hiện nói lưu "dữ liệu thô bất biến" và "bảo toàn dữ liệu", nhưng payload hiện tại là dữ liệu đã parser/API trích xuất, không phải byte HTTP response hoặc HTML/JSON nguyên bản.

Nên đổi thuật ngữ:

- `Immutable Raw Lake` → `Immutable Extracted Observation Lake` hoặc `Immutable Parsed Payload Lake`.
- `raw_payload` → giữ tên nếu cần tương thích schema, nhưng giải thích rõ đây là payload đã trích xuất/chuẩn hóa từ nguồn.
- Không tuyên bố có khả năng replay parser từ source bytes nếu không lưu response bytes.
- `content_hash` của từng record không thay thế cho bản raw source; nó chỉ hỗ trợ nhận diện thay đổi/toàn vẹn payload đã trích xuất.

Nếu muốn tuyên bố "raw Bronze" theo nghĩa chặt, phải lưu thêm response bytes hoặc payload gốc trước parser. Với ổ 75GB, lựa chọn hợp lý hơn là giữ tuyên bố **immutable extracted Bronze** và nêu rõ giới hạn.

### 2.2. Không gọi copy S3 là atomic commit/atomic promotion tuyệt đối

S3/MinIO server-side copy rồi xóa object staging là quy trình **verified copy-and-cleanup**, không phải transaction/rename nguyên tử xuyên hai bucket. Có thể xảy ra trạng thái:

- Bronze copy đã thành công nhưng SQLite chưa ghi manifest.
- Bronze copy thành công nhưng cleanup landing thất bại.
- Process chết giữa copy và state commit.
- Object đích tồn tại nhưng cùng kích thước mà nội dung/checksum khác.

Nên sửa cách diễn đạt:

- `2-Phase Atomic Commit Protocol` → `Verified Staging-to-Bronze Commit Protocol`.
- `Atomic Promotion & Commit` → `Server-side Copy + Post-copy Verification + Manifest Commit`.
- Chỉ dùng từ `atomic` cho các thao tác thật sự atomic trong SQLite hoặc cho write-to-temp cục bộ; không dùng cho toàn bộ giao dịch MinIO + SQLite.

Acceptance criterion nên là: không có object chưa verify lọt vào prefix committed; mọi trạng thái copy/state dở dang phải phát hiện và replay được.

### 2.3. Idempotency hiện chưa đủ mạnh nếu chỉ so kích thước

Kiểm tra object đích đã tồn tại và có cùng `size` chưa chứng minh cùng nội dung. Hai file khác nhau có thể có cùng kích thước.

Sửa yêu cầu retry:

1. Tính checksum của payload/file dự kiến.
2. `stat` object đích.
3. So sánh checksum thực tế hoặc checksum metadata được lưu cùng object; nếu không lấy được checksum đáng tin cậy thì tải/stream để hash lại.
4. Chỉ trả `ALREADY_COMMITTED` khi checksum, kích thước và key/run identity phù hợp.
5. Nếu cùng key nhưng checksum khác, phải fail-closed và tạo cảnh báo điều tra; không bỏ qua, không overwrite âm thầm.

Cũng cần xác định object key là deterministic theo **logical batch/content identity** hay có UUID theo mỗi run. Nếu key luôn chứa UUID mới, retry sẽ tạo key mới và không đạt idempotency tuyệt đối dù writer có logic `stat`.

### 2.4. Sửa tuyên bố về SQLite WAL và timeout

Bản thiết kế ghi `PRAGMA journal_mode=WAL` và `busy_timeout=5000`, nhưng implementation hiện tại cần được kiểm tra/triển khai rõ ràng cho mọi connection. Không nên đưa đây như fact nếu runtime chưa chứng minh.

Nội dung nên yêu cầu:

- Mỗi connection thiết lập `PRAGMA busy_timeout=5000` hoặc giá trị được cấu hình.
- Khởi tạo/kiểm tra `journal_mode=WAL` một cách có chủ đích.
- Có retry/backoff cho `database is locked` ở các đường ghi phù hợp.
- Không nhầm WAL với lock nghiệp vụ: WAL cải thiện concurrency đọc/ghi, còn unique partial index + transaction mới quyết định một source có một run active.
- Acceptance test phải kiểm tra journal mode, busy timeout, unique partial index và hai process thật tranh lock.

Nếu cố ý dùng rollback journal trong deployment MVP, hãy sửa tài liệu thành "SQLite ACID với exclusive run lock" thay vì khẳng định WAL.

### 2.5. Sửa tuyên bố `O(1) RAM tuyệt đối` và `< 50MB`

Writer có thể stream records vào gzip, nhưng toàn pipeline chỉ thật sự streaming nếu crawler cũng trả iterator và không gom toàn bộ candidates/jobs vào list.

Trong code hiện tại, một số crawler vẫn gom danh sách trước khi ghi:

- TopCV gom `candidates_urls` rồi mới cào detail.
- TopDev gom `scraped_jobs` thành list.
- VietnamWorks gom `candidates` và `scraped_jobs` thành list.
- ITViec cần được đánh giá cùng tiêu chí, không suy luận từ tên hàm.

Vì vậy nên đổi:

- `True Streaming ... O(1) RAM tuyệt đối` → `Writer hỗ trợ streaming; end-to-end memory còn phụ thuộc crawler`.
- Không cam kết `< 50MB bất kể 100.000 tin` nếu chưa có benchmark trên dataset đại diện.
- Acceptance test cần đo peak RSS với các kích thước input tăng dần và phân biệt memory của search candidate list, detail parser, gzip writer và MinIO client.

Nếu muốn đạt streaming end-to-end, cho từng crawler yield từng record ngay sau khi parse, hoặc stream theo batch giới hạn cố định; không giữ toàn bộ candidates/payloads trong RAM.

### 2.6. Làm rõ checksum ở hai cấp

Thiết kế đang trộn các khái niệm:

- checksum của `raw_payload` trong envelope;
- `content_hash` của bản ghi do crawler tự tạo;
- SHA-256 của file `.jsonl.gz` trong manifest;
- checksum/object ETag của MinIO.

Nên định nghĩa rõ:

- `record_payload_checksum`: hash canonical của payload trước khi envelope tách trường drift.
- `object_file_sha256`: hash toàn bộ bytes file gzip, dùng đối soát object/manifest.
- `content_hash`: hash nghiệp vụ của nội dung job nếu còn cần dedup/change detection; không dùng thay cho file checksum.
- Chọn canonicalization ổn định: JSON sort keys, UTF-8, quy tắc number/date rõ ràng.

Nếu `_rescued_data` bị tách khỏi `raw_payload`, hàm verify phải chứng minh checksum đang hash phần nào. Tốt nhất lưu rõ checksum của payload gốc trước khi tách và có test drift thực tế.

### 2.7. Schema drift: contract phải có version và đường đi vận hành

`_rescued_data` là cơ chế tốt, nhưng chỉ được gọi là active schema-drift gate khi:

- mỗi source có contract version;
- có chính sách field bắt buộc/nullable/type, không chỉ danh sách tên field;
- unknown fields được rescue mà không làm mất dữ liệu;
- record bị lỗi parse/type được phân loại rõ, không silently biến thành chuỗi rỗng;
- có metric/count cho rescued fields và đường điều tra;
- có test với payload thật hoặc fixture đại diện cho từng source;
- có quy trình cập nhật contract sau khi drift được chấp nhận.

Nên thêm vào envelope:

```json
{
  "_metadata": {
    "format_version": "1.0",
    "schema_contract_version": "topcv-v1",
    "source": "topcv"
  }
}
```

Không nên khẳng định "bảo toàn 100% dữ liệu mới" nếu các lỗi nằm ngoài dict, lỗi parser hoặc field bị parser bỏ qua không được giữ lại.

### 2.8. Watermark phải là source-native, không dùng ingestion time thay thế

`last_watermark_ts` được cập nhật bằng thời điểm ingest/run completion là **ingestion watermark**, không phải source event watermark. `source_watermark` chỉ đáng tin khi lấy từ field/cursor của source và có semantics monotonic.

Nên viết rõ:

- Mỗi source khai báo strategy: `id_cursor`, `source_timestamp`, API cursor hoặc overlap window.
- Lưu timezone/precision và quy tắc so sánh.
- Nếu source sắp xếp không ổn định hoặc có chỉnh sửa tin cũ, dùng overlap window + dedup theo source job ID/content hash.
- `last_record_id` chỉ an toàn khi API đảm bảo ordering ổn định.
- Không nói "tiết kiệm 95%" nếu chưa benchmark; đổi thành "giảm request theo điều kiện watermark hit".
- Full mode phải ghi rõ bỏ qua watermark nhưng vẫn chịu giới hạn `max_pages`.

### 2.9. Compaction đang mâu thuẫn với `immutable`, WORM và audit trail

Nếu compaction copy các file cũ sang `archive/` rồi xóa bản gốc ở vùng active Bronze, dữ liệu không còn WORM/immutable theo nghĩa vật lý ở cùng key. Đây là **retention-managed Bronze**.

Cần chọn một trong hai mô hình:

**Mô hình A — giữ raw bất biến:**

- Không xóa file gốc.
- Ghi compacted object ở prefix/view riêng.
- Silver đọc compacted view; raw vẫn tồn tại.
- Tốn dung lượng hơn nhưng replay/audit rõ ràng.

**Mô hình B — retention-managed Bronze:**

- Cho phép di chuyển/xóa file cũ sau khi compact.
- Gọi đúng tên là retention/lifecycle, không gọi là WORM immutable.
- Manifest phải giữ source object key, compacted key, checksum, record count, thời điểm và policy.
- Archive phải được kiểm tra checksum sau copy trước khi xóa source.
- Có restore/replay procedure từ archive.

Code hiện tại có nguy cơ lệch tài liệu: compactor mặc định `purge_small_files=True`, trong khi `archive_small_files=False`, và ngưỡng mặc định là 2 file. Tài liệu đang mô tả hơn 5 file và từng file dưới 32MB. Cần thống nhất policy/config và fail-safe default.

### 2.10. Compaction cần thêm điều kiện an toàn

Trước khi compact một partition:

- chỉ chọn object đã có manifest và checksum hợp lệ;
- loại trừ object đã compacted/archive;
- xác định partition chính xác, không gom nhầm nhiều ngày/source;
- kiểm tra tổng record input bằng manifest và bằng stream thực tế;
- tạo output, verify size/checksum/record count;
- đăng ký manifest output;
- chỉ sau đó đánh dấu input compacted và di chuyển theo policy;
- log compaction phải có input keys/checksums hoặc bảng quan hệ audit chi tiết;
- có lock riêng để hai compactor không xử lý cùng partition.

Không nên đăng ký output/đánh dấu input trước khi toàn bộ lifecycle copy/archive đã verify.

### 2.11. Landing cleanup cần khớp tên object và chính sách

Thiết kế dùng `staging/{run_id}.tmp`, trong khi writer hiện dùng key dạng `temp_{run_id}.uploading` và cleanup quét `.uploading`. Cần chọn một naming convention duy nhất.

Ngoài lifecycle rule của MinIO, application cleanup nên:

- chỉ xóa object staging quá tuổi và không có run active tương ứng;
- ghi log/audit số object đã dọn;
- không nuốt mọi exception im lặng ở đường bảo toàn dữ liệu;
- có probe đảm bảo landing rỗng sau run thành công;
- phân biệt orphan do crash với upload đang active.

Retention 7 ngày cần căn cứ vào SLA retry/điều tra và dung lượng thực tế; không nên coi con số này là an toàn mặc định cho ổ 75GB.

### 2.12. Manifest/state commit cần mô tả recovery rõ hơn

"Không Manifest = Không Commit" là nguyên tắc tốt, nhưng MinIO copy không rollback được nếu SQLite commit lỗi. Cách diễn đạt chính xác hơn:

- Bronze object có thể tồn tại trước manifest;
- object không có manifest phải được phát hiện bằng reconciliation;
- retry phải nhận diện object theo checksum/key;
- có trạng thái `ORPHANED` hoặc bảng reconciliation, thay vì chỉ FAILED ở run;
- watermark chỉ commit sau manifest;
- cleanup không được xóa object Bronze chỉ vì run FAILED nếu chưa có quyết định recovery.

Nên thêm một periodic/read-only reconciliation command cho `Bronze objects ↔ ingestion_objects ↔ ingestion_runs`.

## 3. Đánh giá theo 5 giai đoạn

### Giai đoạn 1 — Storage & partitioning

**Đánh giá: Đạt MVP, cần làm rõ.**

Điểm tốt:

- MinIO/S3 API phù hợp với object lake trên VPS.
- Tách landing và Bronze là đúng.
- Localhost-only binding là hướng bảo mật đúng.
- Hive-style partition theo source và UTC ingestion time dễ query.

Cần sửa:

- Nêu rõ bucket là namespace; `s3://bronze/archive/...` là prefix trong bucket, không phải vùng storage vật lý độc lập.
- Bổ sung object naming version/schema nếu cần evolution.
- Dùng `uuid` đủ dài hoặc key deterministic; 4 ký tự quá ngắn khi có nhiều object/ghi song song.
- Không gọi timestamp + UUID ngắn là chống collision tuyệt đối.
- Tách `ingest_time` khỏi source event time; nếu cần query theo source date thì thêm field/partition riêng, không thay thế ingestion partition.
- Mô tả backup, disk-full, permissions và quyền MinIO tối thiểu ở phần deployment/runtime, dù chưa bàn Giai đoạn 6.

### Giai đoạn 2 — State store & concurrency

**Đánh giá: Đạt về mô hình, chưa đủ bằng chứng cho mọi claim.**

Điểm tốt:

- Có run history, object manifest, watermark và heartbeat.
- Unique partial index là hướng đúng để chống active run trùng source.
- Fail-closed trước khi cập nhật watermark là đúng.

Cần sửa:

- Dùng tên schema nhất quán: tài liệu ghi `watermarks`, code hiện có `ingestion_state`.
- Dùng tên cột nhất quán: tài liệu `bronze_uri`, code `bronze_s3_uri`; tài liệu `created_at/updated_at`, code `started_at/finished_at`.
- Tránh gọi PID chết là "zombie" một cách chung chung; stale run có thể là process còn sống nhưng mất heartbeat.
- `pid_exists`/`os.kill(pid, 0)` chỉ chứng minh có process với PID, không chứng minh đó là đúng process/run. Nên lưu process start time hoặc lease token để chống PID reuse.
- Thời gian stale 2 giờ cần cấu hình theo source/runtime; không hardcode như guarantee.
- Thiết lập SQLite PRAGMA thực sự trên từng connection và test.

### Giai đoạn 3 — Metadata envelope & schema drift

**Đánh giá: Đạt MVP.**

Điểm tốt:

- Envelope có metadata, payload, rescued fields và format version.
- Contract theo source và test unknown fields là nền tảng tốt.
- Checksum canonical JSON hỗ trợ kiểm tra nội dung.

Cần sửa:

- Đổi `raw` thành `extracted` trong phần mô tả.
- Lưu schema contract version.
- Quy định required/type/nullability và metrics cho rescued data.
- Mô tả checksum trước/sau rescue thật chính xác.
- Xác nhận parser không làm mất field trước khi envelope nhận payload.

### Giai đoạn 4 — Persistent atomic writer & streaming

**Đánh giá: Một phần.**

Điểm tốt:

- Writer ghi qua file tạm cục bộ, upload landing, stat, copy sang Bronze, stat lại và cleanup.
- Gzip JSONL phù hợp cho payload lớn.
- Có heartbeat trong fetch/storage path.
- Có kiểm tra record count > 0.

Cần sửa:

- Đổi "atomic" thành "verified copy/commit protocol".
- Bổ sung checksum verification của object đích, không chỉ size.
- Không gọi end-to-end O(1) khi crawler còn list-buffering.
- Xác định cleanup/error path: exception sau copy nhưng trước landing cleanup phải được reconciliation xử lý.
- Không nuốt exception trong bucket init/stale cleanup ở đường data-integrity nếu muốn production-grade.
- `ALREADY_COMMITTED` phải dựa trên checksum, không chỉ size.

### Giai đoạn 5 — Compaction & retention

**Đánh giá: Một phần; cần chốt policy.**

Điểm tốt:

- Có threshold, stream merge, output manifest, compaction audit và cờ source object.
- Ý tưởng giữ archive thay vì xóa thẳng là tốt hơn purge không kiểm soát.
- Đối soát record count là acceptance criterion đúng.

Cần sửa:

- Thống nhất threshold tài liệu với code.
- Chọn rõ raw-retained hay retention-managed.
- Không gọi WORM nếu có move/delete source object.
- Verify archive copy trước delete.
- Thêm input object list/checksum vào audit.
- Thêm lock/idempotency cho compaction.
- Không để mặc định purge trước khi policy được cấu hình rõ.

## 4. Ma trận acceptance tối thiểu trước khi gọi thiết kế hoàn tất

| Nhóm | Acceptance evidence |
|---|---|
| Landing boundary | Upload lỗi/crash không tạo object committed chưa verify; orphan cleanup có test |
| Extracted/raw semantics | Tài liệu nói rõ extracted payload hay response bytes; không trộn hai khái niệm |
| Partition | Key convention thống nhất, UTC rõ, collision strategy đủ mạnh |
| State | Schema tài liệu khớp schema code; migration và PRAGMA được test |
| Lock | Hai process thật: đúng 1 winner, không có duplicate RUNNING |
| Recovery | Dead process, stale heartbeat, PID reuse và retry-after-copy có test |
| Envelope | Contract version, checksum semantics, rescued field fixture |
| Streaming | Đo peak RSS; không gom toàn bộ candidates/jobs nếu claim end-to-end |
| Commit | Post-copy checksum/size verification; object orphan reconciliation |
| Watermark | Source-native strategy từng source, overlap/dedup khi ordering không ổn định |
| Compaction | Input/output record counts, checksums, policy, archive verify, audit input keys |
| Retention | SLA retry/restore và dung lượng là cơ sở của retention days |
| Security | Secret không nằm trong Git/log; ports admin localhost-only; runtime auth đã probe |
| Durability | Backup, restore drill và phạm vi off-host/local snapshot được phân biệt |

## 5. Cách viết lại các claim quan trọng

### Nên dùng

- "Bronze lưu **immutable extracted observations** từ parser/API, không lưu source HTTP bytes."
- "Writer thực hiện **staged upload, post-copy verification và manifest commit**."
- "Hệ thống hỗ trợ retry theo object identity/checksum; orphan object được reconciliation."
- "Memory bounded theo implementation hiện tại; end-to-end streaming cần benchmark và loại bỏ list buffering."
- "Compaction là **retention-managed lifecycle** nếu source objects được archive/delete."
- "Watermark gồm ingestion watermark và source-native watermark; semantics khác nhau."

### Nên tránh

- "Raw data được bảo toàn 100%" nếu không lưu response bytes.
- "Atomic commit tuyệt đối" cho copy MinIO + SQLite.
- "Idempotency tuyệt đối" nếu chỉ so kích thước hoặc key có UUID mới mỗi run.
- "O(1) RAM tuyệt đối/<50MB" khi crawler còn gom list.
- "WORM immutable" nếu compaction xóa/move object nguồn.
- "Tiết kiệm 95% request" nếu chưa có benchmark.

## 6. Đề xuất thứ tự chỉnh sửa nhỏ nhất

1. Sửa thuật ngữ và claim trong báo cáo: extracted payload, verified copy, retention-managed compaction.
2. Đồng bộ schema tài liệu với code: bảng/cột/PRAGMA/key convention.
3. Chốt checksum contract: record payload, file SHA-256, content hash và object verification.
4. Chốt watermark strategy cho từng source và giới hạn của last ID.
5. Chốt compaction policy: raw-retained hoặc retention-managed; threshold/config phải thống nhất.
6. Bổ sung acceptance tests cho crash-after-copy, orphan reconciliation, checksum mismatch, stale run/PID reuse và compaction archive verification.
7. Chỉ sau các bước trên mới nâng ngôn ngữ từ MVP/thesis-ready lên production-grade nếu có bằng chứng runtime tương ứng.

## 7. Giới hạn của nhận xét này

- Không đánh giá Giai đoạn 6.
- Không đánh giá chất lượng dữ liệu nghiệp vụ của từng job field ngoài các semantics Bronze nêu trên.
- Không coi một process crawl đang chạy, object count hoặc process exit code là bằng chứng full crawl đã hoàn tất.
- Các nhận xét về runtime được đối chiếu với code hiện tại và một inventory read-only tại thời điểm review; cần chạy lại audit sau khi crawl kết thúc.

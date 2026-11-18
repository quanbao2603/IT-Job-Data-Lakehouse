# Briefing Review Tầng Gold — gửi Hermes

> **Mục đích:** tài liệu này đi kèm `docs/gold-design-stages.md` (v4.0). Đọc thiết kế để hiểu *tại sao*, đọc file này để biết *review cái gì, theo thứ tự nào, và không được phá cái gì*.
> **Trạng thái repo khi viết:** `main` = `08b9eb8`, gồm cả commit Silver của Hermes và toàn bộ tầng Gold.
> **Kiểm thử:** 88 test pass, 2 skipped. `compileall` sạch.
> **Người viết:** agent triển khai Gold (không phải Hermes). Mọi nhận định dưới đây đều kèm bằng chứng để Hermes tự kiểm chứng, không yêu cầu tin tưởng.

---

## 1. Bối cảnh: ai đã làm gì

```text
1fa115e  feat: add Silver MVP foundation                    (agent)
b806218  feat: complete Silver lakehouse pipeline            (Hermes)
7c7ee94  feat: enable Silver after Bronze audit              (Hermes)
e0359c0  fix: activate verified Silver production path       (Hermes)
fa31f59  fix: pace TopCV requests with cooldowns             (Hermes)
65cf737  fix: seal Silver releases and widen job domain      (agent)
3656513  docs: add Gold layer design                         (agent)
9d43e05  fix: keep Silver releases consumable by Gold        (agent)
08b7d59  feat: add Gold mart on PostgreSQL                   (agent)
ebeb19f  fix: read Gold enable flag from Airflow env file    (agent)
08b9eb8  feat: preserve Bronze lineage in Silver metadata    (Hermes)
```

Hai bên đã sửa chồng lên cùng khu vực `src/etl/` nhưng **không xung đột**: commit `08b9eb8` của Hermes là bổ sung thuần túy (thêm `build_bronze_metadata()`), không đụng `seal_silver_release()`, cờ `partial_run`, chuỗi fallback `job_domain`, `is_primary_source` hay các hằng số version.

---

## 2. Việc phải làm ĐẦU TIÊN: chạy SQL trên PostgreSQL thật

**Đây là khoảng trống kiểm chứng lớn nhất và duy nhất.**

```text
ĐÃ kiểm chứng:  toàn bộ luật nghiệp vụ (hàm thuần), 88 test.
CHƯA kiểm chứng: apply_ddl(), apply_semantic_views(), load_bundle(),
                 publish(), record_dq(), mark_missing_day().
```

Ba mươi test Gold chạy **không có PostgreSQL**. Chúng kiểm chứng logic thuần và kiểm tra tĩnh script SQL (đếm đủ 20 bảng, 10 view, không fragment comment, constraint mã hóa đúng quyết định). Nhưng **chưa một câu SQL nào được thực thi chống lại database sống**.

Lệnh kiểm chứng, chạy theo đúng thứ tự:

```bash
cd ~/IT-Job-Data-Lakehouse && set -a && . ~/airflow/airflow.env && set +a

# Bước 1: DDL + views có chạy được không
lakehouse_env/bin/python -c "from src.gold.connection import connect, apply_ddl, apply_semantic_views; c=connect(); print('ddl', apply_ddl(c)); print('views', apply_semantic_views(c)); c.close()"

# Bước 2: đếm đối tượng thật trong DB
sudo -u postgres psql -d lakehouse -tc "SELECT table_schema, count(*) FROM information_schema.tables WHERE table_schema IN ('gold','gold_control','gold_staging') GROUP BY table_schema ORDER BY 1;"
sudo -u postgres psql -d lakehouse -tc "SELECT count(*) FROM information_schema.views WHERE table_schema='gold';"

# Bước 3: verify constraint thật sự tồn tại
sudo -u postgres psql -d lakehouse -tc "SELECT conname FROM pg_constraint WHERE conname LIKE 'chk_%' ORDER BY 1;"
```

Kỳ vọng: 3 schema; `gold` có 12 bảng + 10 view; `gold_control` 4 bảng; `gold_staging` 4 bảng; các constraint `chk_snapshot_open_status`, `chk_snapshot_closed_not_open`, `chk_market_city`, `chk_pair_ordered`, `chk_pair_positive` đều có mặt.

**Nếu bước 1 lỗi:** đó là lỗi thật, gửi nguyên văn traceback. Đừng tự sửa DDL rồi bỏ qua, vì DDL mã hóa các quyết định nghiệp vụ D-G05 và GAP-9.

Những rủi ro SQL tôi tự đánh giá là có khả năng lỗi nhất khi chạy thật:

```text
1. FK lồng nhau: fact_job_platform_daily có FK composite
   (gold_release_id, snapshot_date, unified_job_id) → fact_job_daily_snapshot.
   PostgreSQL yêu cầu UNIQUE ở cột được tham chiếu — PK của snapshot thỏa mãn,
   nhưng nếu Hermes đổi PK thì FK này vỡ.
2. CHECK constraint chứa ký tự tiếng Việt ('Hà Nội', 'Hồ Chí Minh', 'Đà Nẵng', 'Khác').
   Cần database encoding UTF8. Nếu collation/encoding không phải UTF8 sẽ không khớp.
3. partial unique index uq_dim_job_current ... WHERE is_current — hợp lệ PostgreSQL,
   nhưng nếu sau này đổi engine thì phải thay bằng cách khác.
4. apply_ddl tách lệnh theo dấu chấm phẩy và bỏ qua dòng comment bắt đầu bằng '--'.
   Nếu Hermes thêm statement có chấm phẩy bên trong string literal hoặc block $$,
   bộ tách sẽ cắt sai. Đây là lý do ddl.sql không dùng $$ và không có trigger.
```

---

## 3. Hai bug tôi đã sửa — XIN ĐỪNG REVERT

Cả hai đều **không thể phát hiện bằng test Silver**, vì Silver không tự đọc lại sản phẩm của mình. Chính `verify_release()` của Gold bắt được. Nếu Hermes thấy code "dư thừa" và muốn đơn giản hóa, hãy đọc kỹ cơ chế trước.

### BUG-1: cờ `partial_run` nội bộ rò rỉ vào release

```text
process_bronze_objects() gọi process(batch, partial_run=True) cho MỖI batch.
Mục đích ĐÚNG: hoãn lifecycle closure khi Bronze còn đang stream
    (có test test_incremental_manifest_batches_do_not_close_historical_entities khóa hành vi này).

Report tổng trước đây gộp:
    last_report["partial_run"] = bool(failed_platforms) or last_report.get("partial_run", False)
                                                        ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
                                                        luôn True vì batch nào cũng partial

→ release manifest luôn có partial_run=true
→ SilverRelease.is_consumable luôn False
→ GOLD KHÔNG BAO GIỜ ĐỌC ĐƯỢC RELEASE NÀO, dù Silver chạy thành công.
```

Đã sửa bằng cách tách bạch hai khái niệm, **không bỏ** cờ nội bộ:

```python
last_report["lifecycle_deferred"] = True            # thông tin vận hành, không chặn Gold
last_report["partial_run"] = bool(failed_platforms) # chỉ phản ánh nguồn cào lỗi thật
```

Nếu Hermes muốn lifecycle chạy thật trong DAG (hiện `mark_stale_entities`/`close_stale_entities` không bao giờ chạy trên đường `process_bronze_objects` vì luôn `partial_run=True`), thì đó là **một thay đổi riêng, có chủ đích**, không phải sửa lại dòng này. Tôi cố ý không tự làm vì nó đổi hành vi đóng tin thật và nằm ngoài phạm vi Gold.

### BUG-2: manifest ghi checksum stale khi partition bị ghi đè trong cùng một phiên

```text
Batch 1: resolve job A → ghi entities/year=2026/month=11/day=16/part-<identityDigest>-00000-<p>.parquet
Batch 2: resolve job B nhưng fuzzy-match vào CÙNG entity A
    → cùng dataset, cùng partition, cùng identity_digest (vì cùng unified_job_id)
    → CÙNG TÊN TỆP → bị ghi đè với nội dung mới
Manifest vẫn giữ cả hai entry; entry đầu mang sha256 của nội dung CŨ
→ verify_release() báo "checksum mismatch" → Gold từ chối release.
```

Đã sửa trong `seal_silver_release()`: khử trùng theo `object_key`, chỉ seal lần ghi cuối (đúng với nội dung thật trên đĩa), và ghi `superseded_object_count` để không giấu việc có ghi đè.

**Câu hỏi mở cho Hermes:** cách sửa này đúng ở tầng manifest, nhưng bản chất là *hai batch cùng ghi một tệp*. Có nên đổi writer để dồn rows theo `(dataset, partition)` trước khi ghi một lần? Tôi không làm vì sẽ phá bounded-batch 5.000 dòng (giới hạn RAM trên VPS). Nếu Hermes có cách giữ cả hai ràng buộc thì tốt hơn.

---

## 4. Các bất biến KHÔNG ĐƯỢC PHÁ

Đây là những chỗ Gold và Silver đang khớp nhau. Đổi một trong số này sẽ làm Gold sai âm thầm, và test hiện tại **có thể không bắt được**.

```text
1. choose_canonical() trong src/etl/entity_lifecycle.py
   Gold TÁI DÙNG đúng thứ tự khóa này trong src/gold/load_dim_job.py.
   Nếu Silver đổi tie-break mà Gold không đổi theo → dim_job và
   entities.canonical_* sẽ chọn hai observation khác nhau cho cùng một tin.
   Thứ tự hiện tại: observed_at → len(job_description_clean) → salary_min_vnd not null
   → expired_date not null → city_normalized not null → SOURCE_PRIORITY
   (itviec 1, vietnamworks 2, topcv 3, khác 99) → observation_id.

2. observed_at được đóng băng trong _metadata lúc bọc Bronze
   (MetadataEnvelope.wrap đọc raw_payload.observed_at hoặc scraped_at).
   canonicalize_bronze_record ưu tiên metadata.observed_at.
   Đây là nền tảng idempotency của observation_id và của cutoff bên Gold.
   KHÔNG được đổi sang thời điểm chạy Silver.

3. canonicalize_bronze_record KHÔNG đọc _rescued_data cho mapping canonical.
   Đây là chủ đích: giữ cơ chế phát hiện schema drift.
   HỆ QUẢ phải nhớ: chuỗi fallback job_domain chỉ có tác dụng với trường
   NẰM TRONG SCHEMA_CONTRACTS của nền tảng đó. Thêm trường mới cho crawler
   thì PHẢI cập nhật SCHEMA_CONTRACTS, nếu không trường rơi vào
   _rescued_data và không được map.

4. is_primary_source là cờ TIME-SCOPED trên link bất biến.
   Nó đánh dấu "observation là canonical tại thời điểm link được ghi",
   không phải "observation canonical hiện tại". Link cũ vẫn giữ True.
   Gold phải lấy link mới nhất theo matched_at, không lấy bất kỳ link nào có True.

5. Link REVIEW_REQUIRED / UNMATCHED không bao giờ có unified_job_id.
   quality_gate.validate_link đã chặn; Gold kiểm lại và biến vi phạm thành
   conflict REJECTED_LINK_HAS_JOB + DQ-07 BLOCK.

6. open_job_count chỉ suy từ entity_status (D-G05), không bao giờ từ is_active.
   Tập mở: NEW, ACTIVE, UPDATED, REOPENED, STALE.
   Tập đóng: CLOSED, EXPIRED, MERGED, SPLIT.
   Lý do loại MERGED: nếu đếm sẽ trùng với entity đích → tăng ảo, phá DQ-03.
   Lý do loại EXPIRED: expire_entities() chỉ thăng cấp từ CLOSED/STALE
   và "never infer expiry from absence" → tin EXPIRED bản chất đã đóng.
   Ràng buộc này có cả trong DDL (chk_snapshot_open_status,
   chk_snapshot_closed_not_open) lẫn DQ-22/DQ-23b.

7. Release dedupe theo object_key khi seal (BUG-2). Đừng bỏ.

8. Trục city là 5 nhóm tường minh: Hà Nội, Hồ Chí Minh, Đà Nẵng, Khác, UNKNOWN.
   'Khác' = đã xác định là tỉnh ngoài 3 thành phố lớn (normalize_city sinh ra).
   'UNKNOWN' = nguồn không cung cấp địa điểm.
   KHÔNG được gộp hai nhóm này: sẽ mất khả năng phân biệt "tin ở tỉnh" với "tin thiếu dữ liệu".
   Nếu mở rộng CITY_ALIASES thì PHẢI sửa chk_market_city trong ddl.sql.
```

---

## 5. Danh sách review theo thứ tự rủi ro giảm dần

Tôi xếp theo khả năng có lỗi thật khi chạy trên dữ liệu thật, không theo thứ tự file.

### Rủi ro cao

**5.1 `fact_job_change_event` hiện chỉ thấy thay đổi TRONG MỘT RELEASE.**
`build_bundle()` mặc định `change_event_scope = "release_only"`: nó chỉ so sánh các observation có trong release hiện tại. Thay đổi giữa hai ngày cào khác nhau **sẽ không bị phát hiện**, trừ khi caller truyền `historical_observations`.
→ Fact 4 sẽ gần như rỗng trong vận hành thật, vì mỗi release thường chỉ chứa một observation cho mỗi tin nguồn.
→ Cần quyết định: đọc lịch sử observations từ đâu (Parquet nhiều ngày, hay `silver_observation_registry` trong SQLite vốn đã tích lũy toàn bộ). Tôi nghiêng về SQLite vì đã có sẵn `observed_at`, salary, city, description và không cần quét Parquet nhiều ngày. **Chưa triển khai vì nó đổi đáng kể chi phí I/O và cần Hermes đồng ý.**

**5.2 `release_controller.publish()` trộn `with connection:` và `connection.commit()`.**
psycopg2: `with connection:` là transaction block (commit/rollback khi thoát), còn các hàm khác gọi `connection.commit()` tường minh. Tôi đã kiểm tra logic nhưng **chưa chạy trên DB thật**. Đây là chỗ dễ sai nhất về semantics transaction.
→ Yêu cầu: sau khi chạy DDL thành công, test thử một publish thật và một publish bị BLOCK, rồi xác nhận bằng query rằng `active_release` KHÔNG đổi khi BLOCK.

**5.3 `load_bundle()` idempotency theo kiểu DELETE-then-INSERT cho fact.**
Đúng với retry cùng `gold_release_id`. Nhưng nếu tiến trình chết giữa DELETE và INSERT, release đó mất một phần dữ liệu. Không ảnh hưởng BI (vì `active_release` chưa đổi), nhưng `load_run` sẽ không có dấu vết.
→ Cân nhắc ghi `load_run.status = 'RUNNING'` TRƯỚC khi load, để crash để lại dấu.

### Rủi ro trung bình

**5.4 `build_effective_link()` chọn link mới nhất theo `(matched_at, resolution_version)`.**
So sánh tuple chuỗi. Với `resolution_version` dạng `v1`, `v2`, ..., `v10` thì so chuỗi sẽ xếp `v10 < v2`. Chưa thành vấn đề vì chỉ có `v1`, nhưng sẽ sai khi lên `v10`.
→ Đề xuất Hermes sửa thành so sánh số, hoặc đổi quy ước version zero-padded.

**5.5 `plan_dim_job()` nhánh "unchanged" trả về nguyên row cũ rồi upsert lại.**
Không sai, nhưng ghi lại `valid_from_at` cũ và không cập nhật `updated_at`. Nếu Hermes muốn theo dõi "lần cuối job được thấy", chỗ này cần chỉnh.

**5.6 `percentile_cont` tự viết bằng Python thay vì dùng SQL.**
Đã test khớp semantics nội suy tuyến tính (1,2,3,4 → P25 1.75, P50 2.5, P75 3.25). Lý do tự viết: giữ luật nghiệp vụ trong hàm thuần để test được không cần DB. Nếu Hermes chuyển sang SQL `percentile_cont` thì phải giữ nguyên kết quả này.

**5.7 Ngưỡng WARN tôi tự đặt, chưa có ai chốt nghiệp vụ.**
```text
DQ-17  job_domain null rate      <= 90%
DQ-19  representative_fallback   <= 50%
DQ-25  job_domain UNKNOWN share  <= 50%
```
Đây là con số tôi chọn để không chặn vô lý trong giai đoạn đầu, **không phải ngưỡng nghiệp vụ đã duyệt**. Cần chủ dữ liệu chốt.

**5.8 `build_skill_pair_daily(max_skills_per_job=40)` chỉ BÁO CÁO outlier, không loại trừ.**
Job có 41 skill vẫn sinh 820 cặp. Với k skill là k(k−1)/2 cặp, một vài job bất thường có thể thổi phồng bảng. Cần quyết định: cảnh báo rồi vẫn tạo, hay cắt ngọn.

### Rủi ro thấp

```text
5.9  Gold DAG có 3 task thay vì 11 task như sơ đồ thiết kế (Giai đoạn 11 §1
     trong gold-design-stages.md giải thích lý do). Nếu Hermes muốn quan sát
     tiến độ từng fact, có thể tách run_gold_mart thành lệnh con mà không đổi luật.
5.10 src/utils/publish_silver_for_gold.py vẫn ghi SQLite gold_entities.
     NAY KHÔNG nằm trên đường đi của Gold (Gold đọc thẳng Silver release).
     Nên xóa hoặc ghi rõ là bridge kiểm thử local, để người sau không tưởng
     đó là đường publish thật.
5.11 dim_date sinh theo khoảng min..max của các ngày thực sự được tham chiếu,
     cộng 1 ngày. Nếu fact tham chiếu một ngày ngoài khoảng đó, FK sẽ vỡ.
     Hiện đã bao phủ snapshot_date, canonical_posted_date và event_date.
5.12 build_dim_company chỉ nhóm theo tên chuẩn hóa, không fuzzy merge (đúng thiết kế).
     Báo cáo ambiguous trả về danh sách tên có nhiều cách viết thô khác nhau,
     nhưng chưa được ghi ra đâu cả — chỉ nằm trong bundle.reports.
```

---

## 6. Cơ hội mới từ commit `08b9eb8` của Hermes

`build_bronze_metadata()` giờ nhúng lineage mức dòng vào `platform_specific_attributes._bronze_metadata`:

```text
bronze_source, bronze_ingested_at, bronze_observed_at, bronze_payload_checksum,
bronze_pipeline_run_id, bronze_format_version, bronze_schema_drift_fields
```

Gold **chưa dùng** trường này. Ba chỗ có thể dùng ngay, đều làm Gold mạnh hơn:

```text
1. DQ-16 (lineage): hiện chỉ kiểm tra row có gold_release_id và silver_release_id.
   Có thể nâng cấp thành đối soát bronze_payload_checksum về tận Bronze object.

2. Giám sát schema drift theo thời gian: bronze_schema_drift_fields cho biết
   trường nào đang rơi vào _rescued_data. Đây chính là tín hiệu sớm cho
   ràng buộc số 3 ở mục 4 — nếu một nền tảng bắt đầu đẩy job_domain vào
   rescued, trục ngành của Fact 5 sẽ âm thầm thoái hóa về UNKNOWN.

3. bronze_pipeline_run_id cho phép nối một fact Gold về đúng phiên cào Bronze,
   mạnh hơn silver_release_id một bậc.
```

Tôi cố ý chưa triển khai vì nó mở rộng phạm vi DQ và cần Hermes đồng ý về chi phí đọc thêm cột `platform_specific_attributes` (hiện Gold không parse cột này).

---

## 7. Câu hỏi mở cần Hermes hoặc chủ dữ liệu trả lời

```text
Q-G05  MERGED/SPLIT: có trong VALID_ENTITY_STATUSES nhưng KHÔNG đường code nào
       sinh ra. Gold đã xử lý phòng vệ (không đếm open, có constraint) nhưng
       không thể viết test thật. Có định triển khai merge/split không?

Q-G07  Retention dữ liệu nóng: đề xuất 24 tháng. Chưa ai chốt.
       Gold hiện KHÔNG xóa release cũ — mỗi ngày một gold_release_id mới,
       bảng sẽ phình. Cần chính sách dọn.

GAP-2  publish_silver_for_gold (SQLite) — xóa hay giữ làm bridge?

Q-G11  Trục city: có mở rộng CITY_ALIASES ngoài 4 nhóm không?
       Nếu có, phải sửa chk_market_city trong DDL.

MỚI    evidence_window_days = 14 đang là hằng số trong contract.py và là
       DEFAULT trong gold_control.load_run. Cần đo flickering thật ở G01
       trước khi coi 14 ngày là đúng.

MỚI    change_event nên đọc lịch sử từ SQLite silver_observation_registry
       hay từ Parquet nhiều ngày? (mục 5.1)
```

---

## 8. Những gì CHƯA làm, để Hermes không tưởng đã xong

```text
1. Chưa chạy bất kỳ câu SQL nào trên PostgreSQL thật (mục 2).
2. Chưa có dữ liệu thật: LAKEHOUSE_SILVER_ENABLED vẫn false, chưa có release
   Silver nào từ 9 nền tảng, nên N_d vẫn là ước lượng.
3. Chưa đo SLA thật (2 giờ Gold / 3 giờ BI chỉ là đề xuất).
4. Chưa viết docs/powerbi-gold-connection.md (chờ DB thật để ghi click-path chính xác).
5. Chưa triển khai historical_observations cho change_event (mục 5.1).
6. Chưa dùng _bronze_metadata (mục 6).
7. Chưa có backfill/revision release — hiện chỉ có đường chạy thuận.
8. Chưa có retention/dọn release cũ.
9. Gold chưa từng chạy qua Airflow trên VPS (LAKEHOUSE_GOLD_ENABLED=false).
10. Chưa kiểm tra encoding PostgreSQL có phải UTF8 không, trong khi DDL có
    CHECK constraint chứa chuỗi tiếng Việt.
```

---

## 9. Quy ước để không kẹt pull lần nữa

Lần này VPS có thay đổi chưa commit trên đúng ba file tôi đang sửa, làm `git pull` bị chặn. Hermes đã xử lý bằng cách commit và push (`08b9eb8`) — kết quả tốt, nhưng cần thành quy ước:

```text
1. Mọi thay đổi trên VPS phải được commit và push TRƯỚC khi rời phiên làm việc.
2. Trước khi sửa file, chạy: git pull --ff-only origin main
3. Không bao giờ force push lên main — đây là nhánh chung.
4. Không dùng git checkout . / git reset --hard / git clean -f để "dọn" xung đột.
   Thay vào đó: git diff > ~/backup-<ngày>.patch rồi mới quyết định.
5. Commit message ngắn 1-2 dòng.
6. Không commit secret. Credential Gold nằm trong ~/airflow/airflow.env (chmod 600).
```

Biến môi trường Gold cần có trong `~/airflow/airflow.env`:

```text
GOLD_DB_HOST=127.0.0.1
GOLD_DB_PORT=5432
GOLD_DB_NAME=lakehouse
GOLD_DB_USER=gold_writer
GOLD_DB_PASSWORD=<không commit>
LAKEHOUSE_GOLD_ENABLED=false     ← chỉ bật sau khi mục 2 pass
```

---

## 10. Bằng chứng Hermes cần trả lại sau khi review

```text
1. Output ba lệnh ở mục 2 (DDL, đếm bảng, đếm constraint) — nguyên văn.
2. Kết quả một lần chạy thật: LAKEHOUSE_SILVER_ENABLED=true, một phiên Silver,
   rồi src.gold.run_gold_mart --no-publish. Gửi JSON contract dòng cuối.
3. Danh sách check DQ nào FAIL/WARN trên dữ liệu thật, kèm expected/actual.
4. Số N_d thật (số unified_job_id hợp lệ trong một ngày) để thay ước lượng
   trong gold-design-stages.md.
5. Tỷ lệ job_domain phủ thật theo từng nền tảng (kiểm chứng GAP-8 đã sửa có tác dụng).
6. Quyết định cho các câu hỏi ở mục 7.
7. Danh sách mục ở phần 5 mà Hermes đồng ý/không đồng ý, kèm lý do.
```

Không ghi secret hoặc raw payload nhạy cảm vào bằng chứng. Không đánh dấu hoàn thành chỉ vì code chạy hết mà chưa đối soát dữ liệu.

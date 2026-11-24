# Trợ lý AI — sửa theo yêu cầu 1.2 (FIX-00 … FIX-08)

**Ngày:** 24/11/2026. **Phạm vi:** trang Trợ lý AI và hợp đồng response của nó. Không thay đổi
dữ liệu, pipeline, schema Gold hay tài khoản DB.

## 1. FIX-00 — Kiểm kê bản đang chạy

| Hạng mục | Giá trị |
| --- | --- |
| Repo | `/home/womcloud/IT-Job-Data-Lakehouse`, nhánh `main`, HEAD `708bf98` trước khi sửa |
| Web service | `it-insight-web.service` (user unit), `0.0.0.0:8010`, release `20261124T142110Z` |
| Agent service | `it-insight-agent.service`, `127.0.0.1:8021`, chạy trực tiếp từ repo (`lakehouse_env`) |
| Frontend | `apps/gold-web/frontend` (React 19 + Vite), trang `assistant` trong `App.tsx` |
| API | web proxy `POST /api/assistant/ask` → agent `POST /ask`; `GET /api/assistant/status` |
| Truy vấn | `src/agent/tools.py` → `run_query` → `src/agent/guard.py` → `src/gold/connection.py` (read-only) |
| Release Gold đang phục vụ | `gold_20261124_2024_salary`, snapshot 2026-11-24, 4.526 tin |

**Luồng một câu hỏi (trước khi sửa):** LLM gọi `run_gold_sql` (tool duy nhất) → guard → SELECT
trong transaction READ ONLY → tool trả JSON `{rows, sql, row_count}` → LLM viết câu trả lời →
`/ask` trả `{answer, sql, rows, steps, provider, model}` → UI in câu trả lời dạng plain text và
mở `<details>` "SQL đã chạy".

**Nguyên nhân đã xác minh của AI-01…AI-06:**

| Mã | Nguyên nhân trong code |
| --- | --- |
| AI-01 | `_extract_sql_and_rows` chỉ giữ **payload tool cuối**, không lưu từng truy vấn; response chỉ có một `sql` |
| AI-02 | Bộ lọc do LLM tự viết trong SQL; server không có cấu trúc `applied_filters` nên UI không đối chiếu được |
| AI-03 | `message.content` render trong `<p>{text}</p>` + `white-space: pre-wrap` → markdown/bảng/URL thành chữ thô |
| AI-04 | Không có `result` cấu trúc; UI không có nút mở tin/danh sách |
| AI-05 | Không có nút hủy; timeout client 180 s; server không có deadline |
| AI-06 | `snapshot_date` lấy từ biến môi trường `AGENT_SNAPSHOT_DATE`, ngày/release còn lại do LLM viết |

## 2. Hợp đồng response công khai (sau sửa)

```json
{
  "request_id": "req_20261124T155755_acb12f87",
  "answer_type": "salary_comparison",
  "answer_markdown": "**Lương công bố trung vị tại Hà Nội**\n**Junior**\n- Lương trung vị: **15 triệu VND/tháng**…",
  "data_context": {"snapshot_date": "2026-11-24", "snapshot_cutoff_at": "...", "timezone": "Asia/Ho_Chi_Minh"},
  "applied_filters": {"city": "Hà Nội", "job_levels": ["Junior", "Senior"], "salary_only": true},
  "result": {"kind": "table", "columns": ["job_level", "salary_samples", "median_mid", "p25", "p75"], "rows": [], "row_count": 2, "truncated": false},
  "warnings": []
}
```

`result.kind` là `jobs` (có `job_id`, `title`, `company`, `city`, `level`, lương, `last_seen_at`,
`source_url`), `table`, hoặc `none`. **Không** có `sql`, `rows` thô, `steps`, `provider`, `model`,
`query_id` trong payload này.

**Trace nội bộ** (`data/agent_trace/agent-YYYYMMDD.jsonl`, git-ignored): mỗi request một dòng gồm
`request_id`, `gold_release_id`, `snapshot_date`, `snapshot_cutoff_at`, `applied_filters`, và
**toàn bộ** truy vấn với `query_id`, `sql`, `status` (`executed` / `blocked` / `failed`),
`row_count`, `truncated`, `duration_ms`, `error`, `rows_preview` (5 dòng), cùng `duration_ms`
tổng và thời điểm kết thúc.

## 3. Thay đổi theo từng FIX

| FIX | Nội dung | File |
| --- | --- | --- |
| 00 | Kiểm kê, xác định nguyên nhân, chốt phạm vi | tài liệu này |
| 01 | `src/agent/trace.py` mới: `QueryRecord` + `Trace`; `run_query` ghi mọi lần chạy, kể cả câu bị guard chặn (status `blocked`); SQL lưu là câu **server đã chạy** (sau guard, có LIMIT) | `trace.py`, `tools.py` |
| 02 | Tool thứ hai `set_filters` để LLM khai báo bộ lọc có cấu trúc; server chuẩn hoá về 8 khoá hợp lệ, bỏ giá trị rỗng/false; `set_filters` + prompt buộc tính metric trên đúng cohort, không cộng top-N | `tools.py`, `prompts.py` |
| 03 | Gỡ SQL khỏi UI; renderer Markdown riêng (đậm, danh sách, bảng, link http/https, chặn HTML); kết quả chia nhóm/từng dòng; thẻ tin có nút | `Markdown.tsx`, `App.tsx`, `style.css` |
| 04 | `result.jobs` từ truy vấn cuối → thẻ tin có `Xem chi tiết` (mở modal hiện có), `Xem tin gốc` (URL thật, chỉ http/https), `Xem tất cả tin phù hợp` (mang bộ lọc sang trang Tin tuyển dụng, tự đổi tên kỹ năng sang mã) | `agent.py`, `App.tsx` |
| 05 | `data_context` do server đọc từ Gold (`snapshot_date`, `snapshot_cutoff_at`, release chỉ lưu trong trace); mọi truy vấn của một câu trả lời dùng cùng một context | `service.py`, `agent.py` |
| 06 | Deadline server 90 s (`AGENT_REQUEST_TIMEOUT_S`), client 100 s; nút **Hủy** hủy request và mở lại ô nhập; lỗi/timeout có **Thử lại**; nhãn chờ đúng "đang truy vấn" | `service.py`, `App.tsx` |
| 07 | Prompt: ngoài phạm vi (view/apply/doanh thu/TTH) trả lời ngắn + gợi ý câu hỏi đang hỗ trợ; scrubber xoá footer nguồn, SQL, tên bảng, tên model trước khi trả về | `prompts.py`, `sanitize.py` |
| 08 | Test, deploy, tài liệu | xem mục 6–8 |

## 4. COPY01 – COPY07

| Mã | Trước | Sau |
| --- | --- | --- |
| COPY01 | "…Trợ lý tự sinh SQL, chạy trên Gold ở chế độ chỉ đọc rồi diễn giải kết quả." | "Đặt câu hỏi về lương, kỹ năng, địa điểm hoặc doanh nghiệp." |
| COPY02 | "Hỏi thẳng vào dữ liệu Gold" | đã gỡ |
| COPY03 | "Trợ lý dùng openai · chib/deepseek-v4.1-flash…" | đã gỡ (status trả "Trợ lý đang hoạt động…") |
| COPY04 | "Đang truy vấn Gold…" | "đang truy vấn" (+ giây, tách riêng) |
| COPY05 | `<details>SQL đã chạy` | đã gỡ khỏi component, CSS và bundle |
| COPY06 | Thẻ "Câu trả lời gắn với dữ liệu" (aside) | đã gỡ, chat dùng hết chiều rộng |
| COPY07 | "Nguồn: gold.v_job_snapshot + gold.v_dim_job_current · cập nhật …" | đã gỡ ở prompt **và** scrubber phía server |

`scrub_answer()` chạy trên **mọi** câu trả lời trước khi rời server, nên nội dung cũ được
hiển thị lại (nếu có) cũng không mang SQL/nguồn kỹ thuật. Nó chỉ xoá phần kỹ thuật; số liệu,
nhãn và đơn vị giữ nguyên.

## 5. Bằng chứng đối soát (release `gold_20261124_2024_salary`)

| Câu hỏi | Kết quả AI | Đối soát độc lập | Thời gian |
| --- | --- | --- | --- |
| T01 — Tin Backend ở Hà Nội | 77 tin, bộ lọc `city=Hà Nội`, `title_keyword=Backend` | `/api/jobs?q=Backend&city=Hà Nội` → 77 | 12,9 s |
| T03 — Lương Junior/Senior ở Hà Nội | Junior 15 tr (158 mẫu, IQR 13,5–20), Senior 35 tr (49 mẫu, IQR 30–40) | SQL trực tiếp cùng grain → 15000000 / 35000000, 158 / 49, p25–p75 khớp | 12,6 s |
| T04 — View → Apply | "Chưa có dữ liệu lượt xem và ứng tuyển…" + 4 gợi ý; **không** chạy truy vấn thay thế | — | 10,3 s |
| T05 — 5 tin Backend + Python ở Hà Nội | 5 thẻ tin, 5 URL nguồn, 5 nút `Xem chi tiết`; "Xem tất cả tin phù hợp" → 10 tin cùng bộ lọc | `/api/jobs?q=Backend&city=Hà Nội&skill=python` → 10 | 12–31 s |

Trace của lượt T05 cho thấy **hai** bản ghi: một câu bị guard chặn (`blocked`) và một câu chạy
thành công (`executed`, 5 dòng) — đúng yêu cầu "không ghi đè bằng SQL cuối cùng".

## 6. Kiểm thử

```text
backend   69 passed  (agent flow/guard/evidence + service contract + web API)
frontend  16 passed  (9 unit với API stub + 7 live DOM, gồm 2 lượt chat thật)
typecheck sạch · build index-BZIpx0Of.js 289,6 kB (gzip 87,6 kB)
```

Test mới đáng chú ý: payload công khai không chứa `sql|gold.|v_job|deepseek`; scrubber giữ số
liệu và bỏ footer; `run_query` ghi `blocked`/`failed` vào trace; `/ask` trả 504 khi quá hạn và
502 không lộ exception; renderer chặn `javascript:` và HTML thô; nút "Xem tất cả tin phù hợp"
gửi `skill=python` (mã) chứ không phải `Python`.

## 7. Triển khai

```text
web release   20261124T162044Z (build từ cây làm việc, stamp HEAD 708bf98)
              assets index-BZIpx0Of.js · index-pcpqut_h.css
agent         restart it-insight-agent.service (chạy từ repo)
probes        /api/health 200 · /api/assistant/status enabled=ready · DOM suite 16/16 trên :8010
rollback web  ln -sfn releases/20261124T142110Z current && systemctl --user restart it-insight-web
rollback agent  git checkout <commit cũ của src/agent> && restart it-insight-agent
```

**Lưu ý phạm vi:** cây làm việc tại thời điểm build còn có thay đổi chưa commit của một phiên
khác (trang Xu hướng, company profile, `gold.job_detail`). Bảng `gold.job_detail` đã tồn tại
trong DB và `/api/trends` trả 200, nên release này chứa cả phần đó; phần AI không sửa các file
liên quan ngoài `App.tsx`/`api.ts`/`style.css` (dùng chung).

## 8. Giới hạn còn lại

- `applied_filters` do model khai báo qua `set_filters` và server chỉ chuẩn hoá khoá/kiểu, không
  suy ra ngược từ SQL; nếu model khai sai, nhãn bộ lọc trên UI có thể lệch nội dung câu trả lời.
- Trace lưu SQL, tham số, kết quả và thời gian, nhưng **chưa** lưu công thức của các phép tính
  dẫn xuất do LLM viết trong văn bản (ví dụ "gấp 2,3 lần").
- Một câu trả lời dùng các view gắn release hiện hành; nếu release đổi giữa hai truy vấn trong
  cùng một lượt, server không phát hiện và chạy lại (chưa có ràng buộc phiên bản theo request).
- Độ trễ quan sát 10–31 s mỗi câu (một model call + 1–2 truy vấn). Chưa có streaming; chưa đo p95.
- Dữ liệu không có lượt xem/ứng tuyển, nên T04 luôn từ chối — đúng phạm vi, không phải lỗi.
- Tiêu đề tin trong Gold có bản ghi bị dính chữ ("kỹ subackend") hoặc viết thường; đây là dữ liệu
  nguồn, không sửa ở tầng web.

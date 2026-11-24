# Text-to-SQL Agent và Web App Streamlit

## 1. Kiến trúc

```
Người dùng
  ├── React (web hiện có)  ──POST /api/assistant/ask──►  Web API (8010)  ──proxy──┐
  └── Streamlit (8020)     ──POST /ask────────────────────────────────────────────┤
                                                                                  ▼
                                                        Agent service (8021, loopback)
                                                          ├── LangChain tool calling
                                                          ├── guard.py  (SQL whitelist)
                                                          └── run_gold_sql (READ ONLY)
                                                                                  │
                                                                       Gold PostgreSQL
```

Ba tiến trình, đều là systemd **user** unit:

| Unit | Cổng | Vai trò |
|---|---|---|
| `it-insight-web.service` | 8010 | Web tra cứu + proxy trợ lý |
| `it-insight-agent.service` | 8021 (loopback) | Text-to-SQL agent |
| `it-insight-streamlit.service` | 8020 | Dashboard + Chatbot Streamlit |

## 2. Agent

- **LangChain** `create_agent` + tool calling. Model chọn qua env: Gemini (`gemini-2.0-flash`)
  hoặc OpenAI (`gpt-4o-mini`).
- **Prompt** (`src/agent/prompts.py`) chứa schema Gold rút gọn (19 view/bảng), định nghĩa
  chỉ số và các quy tắc nghiệp vụ: `UNKNOWN` → "chưa xác định", lương phải kèm số mẫu,
  loại `is_unknown` khi đếm công ty/kỹ năng, phạm vi là "nguồn cung quan sát được".
- **Tool duy nhất**: `run_gold_sql(sql)`. Không có tool nào khác, nên model không thể
  làm gì ngoài đọc Gold.

### Guard (lớp bảo vệ)

`src/agent/guard.py` chạy trước mọi truy vấn:

1. Bóc string literal, dollar-quote và comment trước khi kiểm tra (không thể giấu lệnh ghi).
2. Chỉ một câu lệnh, bắt đầu bằng `SELECT` hoặc `WITH`.
3. Chặn ~30 từ khoá ghi/leo thang: insert, update, delete, drop, alter, grant, copy,
   truncate, into, pg_sleep, dblink…
4. Chặn `pg_catalog`, `information_schema`, `gold_control`, `gold_staging`, `public`.
5. Mọi bảng phải ghi rõ schema và nằm trong danh sách trắng 19 quan hệ.
6. Tự thêm `LIMIT` và hạ `LIMIT` quá lớn về `AGENT_MAX_ROWS` (mặc định 200).

Kết nối dùng `gold_reader` với `set_session(readonly=True, REPEATABLE READ)` và
`statement_timeout` mỗi transaction.

## 3. Streamlit app

`apps/insight-streamlit/app.py`, hai tab:

- **Tổng quan**: KPI (tin, doanh nghiệp, kỹ năng, trung vị lương), biểu đồ theo chuyên môn,
  địa điểm, cấp bậc, kỹ năng, bảng công ty và nền tảng. Số liệu đọc từ `/api/dashboard`
  nên khớp tuyệt đối với web.
- **Trợ lý dữ liệu**: khung chat, hiển thị câu trả lời, SQL đã chạy và bảng kết quả.

## 4. Cấu hình

`/home/womcloud/services/it-insight-agent/config/agent.env` (mode 600):

```bash
GEMINI_API_KEY=...        # hoặc OPENAI_API_KEY=...
AGENT_LLM_PROVIDER=gemini # gemini | openai
AGENT_LLM_MODEL=gemini-2.0-flash
AGENT_MAX_ROWS=200
AGENT_SQL_TIMEOUT_MS=8000
```

Không có key → `/api/assistant/status` trả `not_configured` và UI hiển thị hướng dẫn,
không giả vờ hoạt động.

## 5. Kiểm chứng

| Hạng mục | Kết quả |
|---|---|
| Guard (12 test) | chặn ghi, đa câu lệnh, schema lạ, LIMIT quá lớn |
| Agent wiring (7 test) | lấy lại SQL/rows từ transcript, lỗi rõ khi thiếu key |
| Streamlit (4 test) | render KPI thật từ Gold, đủ 2 tab, đủ 6 mục dashboard |
| Toàn bộ root suite | 346 test OK (6 skip) |
| `/ready` | schema khớp 19/19 quan hệ, đọc được 4.447 tin |

## 6. Lệnh vận hành

```bash
systemctl --user status it-insight-agent it-insight-streamlit
systemctl --user restart it-insight-agent      # sau khi đổi key
journalctl --user -u it-insight-agent -n 50
```

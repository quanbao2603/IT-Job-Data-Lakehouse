# Web/API acceptance (W11)

Scope: **web + API reading Gold**. AI (AI00–AI05) is out of scope and not started.
The web service is installed, running, and reachable at **`http://womtech.io.vn:8010/`**.

## Acceptance checklist

| Item | Status | Evidence |
| --- | --- | --- |
| Reads real Gold, `demo=false` | ✅ | `verify_live.py` exit 0, 19/19 checks |
| Every data page shows snapshot/release, including direct navigation | ✅ | `meta` in every response; DOM test reads `.release-note` and the stale banner |
| Headline counts reproducible in PostgreSQL | ✅ | jobs 3.092, companies 1.198, skills 131, samples 926, median 20.000.000 |
| Filters, company/skill drill-through, pagination correct | ✅ | live drill-through checks + DOM test |
| Job count not inflated by skill/platform joins | ✅ | `COUNT(DISTINCT unified_job_id)` / `EXISTS`; multi-skill check |
| Missing salary / small sample interpreted correctly | ✅ | `null` salary, sample count always shown, no group-median averaging |
| Release change does not mix responses | ✅ | `expected_release_id` → 409, client reloads; API tests |
| DB error, missing release and 0 results are three different states | ✅ | 503 codes, empty-state UI, tests |
| Reader-only session; no secret in frontend/bundle/log | ✅ | `readonly=True`, no secret in built assets, secret scan |
| Desktop/mobile/keyboard/CSV reviewed | ✅ | real Chromium (CDP) at 390/768/1440: 6 pages, no horizontal overflow, 0 console errors, modal Escape + focus, nav, sort, pagination, CSV, drill-through (`ui-redesign.md` §4) |
| UI matches the IT Insight reference design | ✅ | light theme + dark teal sidebar per design tokens; 8 before/after renders in `docs/web/evidence/` (`ui-redesign.md` §2–4) |
| Public URL serves the app and API | ✅ | `http://womtech.io.vn:8010/` 200 from DE/UA/US; index, asset, `/api/*` 200 |
| Service self-recovers per configuration | ✅ | user systemd unit, `SIGKILL` → new PID, `NRestarts=1`, health 200 |
| Bronze/Silver/Gold and other web paths unchanged | ✅ | no DB/ETL/DAG/nginx change in this task |
| AI shows "not configured" and calls no model | ✅ | `/api/assistant/status`, disabled UI button |

## Performance

Measured manually, single user, 3.092-row snapshot:

```text
/api/dashboard   ~0.4–1.0 s
/api/jobs        ~0.3–0.6 s
/api/skills      ~0.4–0.9 s
/api/ready       ~0.05 s
```

No benchmark, load test or SLA is claimed. One uvicorn worker with
`--limit-concurrency 16` is the starting configuration; revisit after measuring real
traffic and while leaving headroom for Airflow/PostgreSQL.

## Known limits (not defects)

- Snapshot served is 2026-11-19 while today is 2026-11-22 → `stale=true`. This is correct
  behaviour: the UI says the data is old rather than pretending it is fresh. Freshness
  depends on the Bronze/Silver/Gold pipeline, not on the web.
- No verified job URL/description, so the UI does not invent a source link.
- `entity_status` is not presented as "currently hiring".
- AI is not configured; the assistant tab is a status page only.

## What remains before calling the web "in production"

1. Owner approval for the deployment window.
2. Bundle built and checksum-verified (`scripts/package_release.sh`).
3. ~~`web.env` placed on the server with `gold_reader` credentials~~ — done, mode 600.
4. ~~systemd unit enabled, service healthy on `127.0.0.1:8010`~~ — done (user unit + linger).
5. Public route verified end to end — done via the dedicated port (no Nginx change).
6. Optional: web access protection (Basic Auth account prepared by the operator).
7. Rollback rehearsal: repoint `current`, restart only the web service, restore the Nginx
   snippet — service restart already exercised; Nginx rollback is a single `cp` + reload.

## Giao diện — đợt chỉnh sửa 23/11/2026

| Yêu cầu | Trạng thái | Bằng chứng |
|---|---|---|
| `Snapshot` → `cập nhật ngày` ở mọi tab và sidebar | Đạt | ảnh `?page=overview`, `?page=jobs`, `?page=skills`, `?page=quality` |
| Ngày lấy từ dữ liệu, không hard-code | Đạt | `formatDay(meta.snapshot_date)`; DOM test so với `/api/dashboard` |
| Bỏ dòng `Release gold_… cutoff …` | Đạt | đã xoá `ReleaseNote` khỏi Tổng quan, Tin tuyển dụng, Kỹ năng |
| Bỏ đoạn định nghĩa lương ở Tổng quan | Đạt | không còn `.definition` |
| `Nền tảng có bằng chứng gần đây` → `nền tảng` | Đạt | tiêu đề biểu đồ |
| `Quan sát gần nhất` → `cập nhật gần nhất` | Đạt | thẻ tin và modal |
| Sidebar: bỏ khối "Dữ liệu có nguồn gốc" | Đạt | CSS `.sidebar-note` đã xoá, không còn khoảng trống |
| `Trong ngưỡng mới` → `cập nhật mới` | Đạt | trạng thái sidebar |
| Mỗi tin có liên kết "Xem tin gốc" | Đạt | 750/750 tin lấy mẫu có `source_url`; DOM test `links every job card` |
| Tin thiếu URL ghi rõ trạng thái | Đạt | "Chưa có liên kết nguồn", không render link rỗng |
| Tin nhiều nguồn xem được từng nguồn | Đạt | modal liệt kê `source_urls[]` theo nền tảng |
| Tên kỹ năng đúng chuẩn viết | Đạt | SQL, AWS, JavaScript, PostgreSQL |
| "0 kỹ năng" → "Chưa ghi nhận kỹ năng" | Đạt | thẻ tin |
| Tách "Đạt quy tắc" khỏi "Mức độ đầy đủ" | Đạt | panel completeness: lương 34%, địa điểm 87,4%, cấp bậc 80%, chuyên môn 81,4% |

Kiểm chứng tự động: 323 test root, 30 test API, 5 test DOM live, 19/19 `verify_live.py`,
`npm run typecheck`/`build` sạch.

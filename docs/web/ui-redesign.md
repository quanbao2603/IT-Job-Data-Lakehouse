# IT Insight — Sửa giao diện theo mẫu tham chiếu (FIX01–FIX05)

**Ngày:** 23/11/2026. **Phạm vi:** chỉ giao diện frontend của web Gold đang triển khai.
Backend, API contract, SQL/reader, release, dữ liệu và trạng thái AI **không thay đổi**.

## 1. FIX01 — Bản đang triển khai và baseline

| Hạng mục | Giá trị ghi nhận |
| --- | --- |
| Service | `it-insight-web.service` (systemd user unit, linger), bind `0.0.0.0:8010` |
| Release đang chạy | `releases/20261122T160236Z`, symlink `current` |
| Web root | `/home/womcloud/services/it-insight/current`, frontend base `/` |
| Source | `apps/gold-web/frontend` trong repo `IT-Job-Data-Lakehouse` |
| Worktree trước khi sửa | sạch (`git status --short` rỗng), HEAD `78381b6` |
| Build đang phục vụ | `index-DnO21bDa.js` + `index-E7IewTVD.css` — trùng byte với `apps/gold-web/server/static` của HEAD |

**Sai lệch metadata phát hiện:** `VERSION.json` của release ghi `source_commit = b9a0fef`
(commit trước), trong khi asset đang phục vụ là bản build **sau** commit `78381b6`
(dark theme). Kết luận: release được đóng gói từ worktree đã build nhưng tem commit
không được cập nhật. Bản sửa này đóng gói lại bằng `scripts/package_release.sh` nên tem
`source_commit` sẽ đúng.

Baseline chụp bằng Chromium thật ở 1440 px, dữ liệu live (`gold_20261119_…`, snapshot
19/11/2026, 3.092 tin, 1.198 doanh nghiệp, 131 kỹ năng, trung vị 20.000.000 đ):

- `evidence/baseline-dark-overview.png`
- `evidence/baseline-dark-jobs.png`

Không sửa cấu hình VPS, không đổi bản production ở bước này.

## 2. FIX02 — Danh sách lệch so với mẫu

Đã mở HTML tham chiếu qua đủ 6 màn hình (Tổng quan, Tin tuyển dụng, Kỹ năng, Doanh
nghiệp, Trợ lý AI, Trạng thái dữ liệu) và đối chiếu 3 ảnh tĩnh.

| Trang | Thành phần lệch | Cách sửa | Chức năng giữ |
| --- | --- | --- | --- |
| Chung | Nền tối `#080b12`, accent tím `#7c9cff` | Đổi sang token mẫu: `--bg #f5f7f7`, `--sidebar #112d32`, `--accent #147e73`, card trắng, viền `#e4eae8` | — |
| Chung | Brand nằm trong topbar ngang, sidebar chỉ có menu | Brand vào sidebar; sidebar 242 px cố định, có nhãn "Không gian phân tích", note nguồn dữ liệu, khối trạng thái + profile chỉ đọc | Điều hướng, `aria-current`, URL `?page=` |
| Chung | Topbar chỉ có snapshot + pill freshness | Topbar 76 px chỉ còn breadcrumb "Phân tích / …", chip "Chỉ đọc", avatar | `meta` vẫn hiển thị đầy đủ |
| Chung | Nút refresh là icon tròn rời | Chip snapshot + nút "Làm mới" nằm trong heading | `reloadToken` |
| Chung | Typography 34/16, Inter | Heading 30 px, panel 17 px, body 13–15, meta 12, KPI 35 px, font Segoe UI theo bảng token | — |
| Tổng quan | 2 panel bar ngang cho chuyên môn & địa điểm | Chuyên môn giữ bar, địa điểm chuyển donut + legend % | Cùng dữ liệu `domains`/`cities` |
| Tổng quan | Không có nhãn tiến trình dữ liệu | Note "Dữ liệu có nguồn gốc" (Bronze → Silver → Gold) ở sidebar | Tĩnh, không gọi thêm API |
| Tin tuyển dụng | Thẻ chữ, không avatar, cột phải hẹp | Avatar initials, meta có nhãn, tag kỹ năng + `+N kỹ năng`, cột phải có lương/ngày/nút chi tiết | Modal, sort, phân trang, CSV |
| Kỹ năng | Danh sách rank 2 cột | Bảng trái (kỹ năng / số tin / thanh tỷ lệ) + panel cặp bên phải | Drill sang danh sách tin |
| Doanh nghiệp | Danh sách hàng | Lưới card 3 cột, 4 ô thống kê, nút xem tin | Drill theo `company_key`, phân trang |
| Trạng thái dữ liệu | Hàng check dạng text | Bảng có cột Kết quả / Quy tắc / Mức độ / Giá trị + bảng coverage | Dữ liệu DQ nguyên vẹn |
| Trợ lý AI | Card giữa trang | Bố cục 2 cột: khung hỏi đáp disabled + panel nguồn dữ liệu | `assistant/status` thật, không giả lập trả lời |
| Trạng thái phụ | Loading/error/empty dùng theme cũ | Cùng token mới, `role="alert"`/`role="status"` giữ nguyên | — |

**Không có mẫu riêng, ghi rõ phạm vi:**

- Hai tab **"Lương công bố"** và **"Nguồn & biến động"** của đặc tả 1.1 **chưa tồn tại**
  trong ứng dụng này → theo phạm vi, không tạo mới.
- Cột **"Lương trung vị"** và **"Mẫu lương"** trong bảng Kỹ năng của mẫu **không dựng được**
  vì `/api/skills` chỉ trả `job_count`; thêm cột này là thay đổi API → ghi issue, không làm.
- Nhãn menu giữ **"Kỹ năng"** (glossary hiện hành + DOM test), không đổi thành
  "Kỹ năng & công nghệ" như ảnh.

## 3. FIX03 — Thay đổi đã thực hiện

Chỉ 3 file frontend: `index.html`, `src/App.tsx`, `src/style.css`.

- `index.html`: `color-scheme light`, `theme-color #112d32`, nền trắng trước khi CSS tải,
  favicon SVG inline (hết 404 `/favicon.ico`).
- `style.css`: viết lại theo token mẫu; grid filter chống tràn (`minmax(0,1fr)`,
  `min-width:0` cho select); breakpoint 1320 / 1100 / 900 / 760; mobile: sidebar thành dải
  nav ngang, item chỉ icon nhưng **giữ accessible name** (visually-hidden, không `display:none`).
- `App.tsx`: chỉ đổi cấu trúc trình bày. Nguyên vẹn: `api.*` calls, `Filters` state,
  query param `?page=`, `expected_release_id`, `RELEASE_CHANGED` retry, drill handlers,
  CSV builder, modal focus trap, `aria-*`.

## 4. FIX04 — Kiểm tra thật trên bản build mới

Môi trường: staging `127.0.0.1:8011` chạy **cùng env production** (`config/web.env`),
dữ liệu live cùng release; Chromium thật (CDP) ở 390 / 768 / 1440 px.

**Đo tự động trên DOM (6 trang × 3 viewport):**

| Kiểm tra | Kết quả |
| --- | --- |
| Tràn ngang (`scrollWidth - clientWidth`) | 0 px ở cả 18 lượt |
| Text phụ < 4.5:1 (breadcrumb, meta, legend, tag, badge, footer, nav) | 0 phần tử |
| KPI xuống dòng | 1 dòng/cả 4 thẻ ở 390 / 768 / 1440 |
| Console error/warning | 0 |
| `{{`/`}}` lọt vào UI | 1 — là **dữ liệu thật** của Gold, xem mục 6 |

**Thao tác người dùng (browser thật):**

| Thao tác | Kết quả |
| --- | --- |
| Mở chi tiết tin từ danh sách | Dialog mở, 8 trường, focus vào nút đóng |
| `Escape` đóng dialog | Đóng, không lỗi |
| Gõ "python" vào ô tìm | 3.092 → 87 tin, chip lọc xuất hiện |
| Bỏ chip lọc | Về 3.092 tin, chip biến mất |
| Đổi sort "Lương cao" | Tin đầu 139.700.000 đ |
| Phân trang | Trang 1/258 → Trang 2/258 |
| Xuất CSV | Tải `it-insights-trang-1-2026-11-19.csv` |
| Drill Kỹ năng → Tin | Heading "Tin tuyển dụng", chip `sql` |
| Drill Doanh nghiệp → Tin | 716 tin trong cohort |
| Drill panel Tổng quan → Tin | Chuyển trang đúng |
| Trạng thái dữ liệu | 33 hàng check, summary 32/32 |
| Trợ lý AI | Ô hỏi disabled, 2 gợi ý, không gọi model |
| Nav mobile 390 | Bấm item thứ 3 → trang Kỹ năng, tên nút vẫn đọc được |

**Ảnh sau sửa** (`evidence/`):

`light-overview-1440.png` · `light-jobs-1440.png` · `light-skills-1440.png` ·
`light-companies-1440.png` · `light-quality-1440.png` · `light-assistant-1440.png` ·
`light-overview-390.png` · `light-jobs-390.png`

Build: `npm run typecheck` sạch, `npm run build` OK (JS 264.9 kB / gzip 81.3 kB).
`pytest` và verify_live không đổi vì không chạm backend.

## 5. FIX05 — Triển khai

**Đã cập nhật production frontend** (chủ dự án giao "triển khai chỉnh sửa giao diện"):

```text
bundle    it-insight-web-20261122T182710Z.tar.gz (sha256 OK)
release   releases/20261122T182710Z   (source_commit 5f358e0)
current   → release mới (release cũ 20261122T160236Z giữ nguyên)
service   it-insight-web.service active, bind 0.0.0.0:8010
live      /api/health 200 · /api/dashboard 200 · DOM suite 4/4 trên :8010
assets    index-BrQjBdmM.js + index-BUSAfcnn.css (đúng một build)
```

Rollback: `ln -sfn releases/20261122T160236Z current` rồi restart service; không đụng dữ
liệu, pipeline hay database. Chi tiết ở `deployment.md` §0b.

Rollback chỉ cần trỏ lại `current` về `releases/20261122T160236Z` và restart service;
không đụng dữ liệu, pipeline hay database.

## 6. Vấn đề phát hiện, ngoài phạm vi giao diện (không tự sửa)

| # | Hiện tượng | Ảnh hưởng | Ghi chú |
| --- | --- | --- | --- |
| 1 | Doanh nghiệp có `company_name` = `{{ company?.name }}` (716 tin — doanh nghiệp nhiều tin nhất) | Hiện nguyên chuỗi template trong danh sách và card | Lỗi dữ liệu Gold/Silver, không phải render. Cần sửa ở bước chuẩn hóa tên công ty |
| 2 | Một tin có lương `5 đ` | KPI trung vị và danh sách bị nhiễu | Nghi parse số từ nguồn (5 thay vì 5.000.000). Thuộc DQ của Silver/Gold |
| 3 | Snapshot 19/11/2026, cách 4 ngày, `stale = true` | Mọi trang hiện banner dữ liệu cũ | Đúng thiết kế; cần chạy lại pipeline để có snapshot mới |
| 4 | `VERSION.json` của release cũ ghi sai commit | Truy vết phiên bản khó | Khắc phục ở release mới |
| 5 | `job_domain` NULL 29,72 % | Panel "Theo chuyên môn" dẫn đầu bằng "Chưa xác định" (919) | Check DQ-17/DQ-25 đang WARN và vẫn pass theo ngưỡng; dữ liệu nguồn |

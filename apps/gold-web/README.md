# IT Insight Web

Web/API tra cứu **Gold PostgreSQL** bằng role `gold_reader` trong giao dịch **READ ONLY**.
Đây là giai đoạn web: AI chưa được cấu hình và không gọi mô hình nào.

Tài liệu chuẩn nằm ở `docs/web/` (inventory, data contract, API contract, test evidence,
acceptance, operations, deployment). File này chỉ là hướng dẫn chạy nhanh.

## Thành phần

- `server/app.py` — FastAPI routes, error contract, static SPA.
- `server/repository.py` — SQL read-only, release metadata, filter builder.
- `server/demo.py` — fixture 12 tin, chỉ bật khi `WEB_DEMO_MODE=true` ngoài production.
- `server/schemas.py` — filter/response models.
- `frontend/src` — React 19 + Vite, 6 trang, tiếng Việt.
- `scripts/verify_live.py` — đối soát API với Gold bằng SQL (19 kiểm tra).
- `scripts/package_release.sh` — đóng gói bundle + sha256.
- `deploy/` — template systemd, Nginx snippet, runbook.

## Chạy local (Python 3.10, cùng hệ với VPS)

```bash
cd apps/gold-web
/usr/bin/python3.10 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m pytest -q tests

cd frontend
npm ci
npm run typecheck
npm run build            # xuất vào ../server/static
cd ..

# Nạp secret từ môi trường được bảo vệ, không gõ mật khẩu vào lịch sử shell
set -a && . ~/airflow/airflow.env && set +a
WEB_ENV=production WEB_DEMO_MODE=false \
.venv/bin/python -m uvicorn server.app:app --app-dir . \
  --root-path /insights --host 127.0.0.1 --port 8010
```

Mở `http://127.0.0.1:8010/` (assets build theo base `/insights/`, nên khi test đúng prefix
cần proxy strip prefix — xem `docs/web/test-evidence.md`).

## Kiểm thử

```bash
.venv/bin/python -m pytest -q tests        # 29 passed
set -a && . ~/airflow/airflow.env && set +a
WEB_ENV=production WEB_DEMO_MODE=false .venv/bin/python scripts/verify_live.py
cd frontend && IT_INSIGHT_BASE_URL=http://127.0.0.1:8011/insights/ npm test   # 4 passed
```

DOM test yêu cầu app đang chạy sau một proxy strip `/insights/`; nếu không đặt biến môi
trường, bộ test được skip thay vì pass giả.

## Bảo mật

- Chỉ dùng `GOLD_DB_READER_USER` / `GOLD_DB_READER_PASSWORD`; không fallback sang writer.
- Thiếu mật khẩu → 503 `DB_NOT_CONFIGURED`, không chuyển sang demo hay bảng rỗng.
- Không có endpoint nhận SQL, tên bảng hay DSN từ trình duyệt.
- Bundle không chứa `.env`, `.venv`, `node_modules`, dữ liệu crawler hay MinIO.

## Ranh giới production

Repo này **chưa** cài systemd, **chưa** sửa Nginx, **chưa** mở port và **chưa** deploy
`/insights/`. Việc triển khai cần bundle đã duyệt, `web.env` bảo vệ trên máy chủ, unit
systemd riêng, location Nginx tối thiểu và đối soát Gold thật — theo
`docs/web/deployment.md` và `docs/web/operations.md`.

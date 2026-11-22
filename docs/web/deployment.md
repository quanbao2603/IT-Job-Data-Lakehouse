# Deployment plan — IT Insight Web (W08–W10)

**Status: live on the internet at `http://womtech.io.vn:8010/`.**

## 0a. Public deployment (2026-11-22, direct port)

The owner asked for the app to be reachable on its own port rather than under a
`/insights/` prefix, so the service now listens on `0.0.0.0:8010` and the frontend is
built with a root base (`VITE_BASE_PATH=/`).

```text
release     /home/womcloud/services/it-insight/releases/20261122T150924Z
current  →  that release
bind        0.0.0.0:8010 (no proxy, no --root-path)
asset base  /assets/…  ·  API base  /api/…
local       GET / 200 · GET+HEAD /api/health 200 · /api/dashboard 200
public      http://womtech.io.vn:8010/ → 200 from DE, UA, US (check-host)
            TCP 8010 reachable from IL, IR, UA nodes
dns         womtech.io.vn → 160.236.192.170 (direct A record, no CDN)
docs        /docs, /redoc, /openapi.json → 404 (disabled)
```

Known trade-off: plain HTTP, no authentication, read-only API. Anyone with the URL can
read the published job data and consume up to `limit-concurrency 16` concurrent requests.
The `/insights/` Nginx route in this document is **optional** and needs the prefixed build
(`VITE_BASE_PATH=/insights/` plus `WEB_ROOT_PATH=/insights`); do not mix the two builds.

## 0. Deployment log (2026-11-22)

```text
bundle        it-insight-web-20261122T140226Z.tar.gz (sha256 OK)
release       /home/womcloud/services/it-insight/releases/20261122T140226Z
current   ->  that release
web.env       /home/womcloud/services/it-insight/config/web.env (600, reader keys only)
unit          ~/.config/systemd/user/it-insight-web.service (enabled, linger=yes)
bind          127.0.0.1:8010, 1 worker, limit-concurrency 16, root-path /insights
probes        /api/health 200, /api/ready 200, /api/dashboard 200 (3.092 jobs)
crash test    SIGKILL → systemd restarted (new PID, NRestarts=1), health 200
snippet test  real nginx on 127.0.0.1:8088 with the block below:
              /insights 301, /insights/ 200, assets 200, /api/* 200, unknown /api 404
nginx site    /etc/nginx/sites-enabled/9router — NOT modified (root required)
```

Optional command (root) — only for the prefixed variant:

```bash
sudo bash /home/womcloud/services/it-insight/deploy/apply_nginx.sh
```

It is idempotent, backs up the site file, inserts the location before the TLS listener,
runs `nginx -t`, restores the backup on failure, reloads Nginx and probes the public URL.
Basic Auth reuses `/etc/nginx/.htpasswd`; remove the two `auth_basic` lines in the script
to publish the page openly, or create a dedicated web account with `htpasswd` first.

## 1. Bundle

```bash
cd apps/gold-web/frontend && npm ci && npm run build
cd .. && ./scripts/package_release.sh
```

Contents: `server/` (code + built static), `requirements.txt`, `requirements.lock.txt`,
`deploy/`, `README.md`, `VERSION.json` (commit, build time, `ai_enabled=false`).
Excluded by construction: `.venv`, `node_modules`, `.git`, `.env`, cookies, SQLite,
MinIO data, crawler output. `dist/` and `server/static/` are in `.gitignore`.

Verify:

```bash
sha256sum -c dist/it-insight-web-<stamp>.tar.gz.sha256
tar -tzf dist/it-insight-web-<stamp>.tar.gz | grep -E '\.(env|pem|key)$' || echo "no secrets in bundle"
```

## 2. Runtime (W09)

```text
/home/womcloud/services/it-insight/
├── releases/<release>/{server, requirements.lock.txt, VERSION.json, .venv, deploy}
├── current -> releases/<release>
└── config/web.env        # 600 womcloud:womcloud
```

- Python: `/usr/bin/python3.10` (3.10.12) — same interpreter family as the VPS system;
  do not copy a virtualenv from another machine.
- Install: `.venv/bin/python -m pip install -r requirements.lock.txt` (tested pins:
  fastapi 0.115.12, pydantic 2.10.6, uvicorn 0.34.0, psycopg2-binary 2.9.10).
- Bind `127.0.0.1:8010`, one worker, `--limit-concurrency 16`, `--root-path /insights`.
- Unit template: `apps/gold-web/deploy/it-insight-web.service` (`Restart=on-failure`,
  `NoNewPrivileges`, `PrivateTmp`, own `EnvironmentFile`). Enable only after local probes pass.

Acceptance for this step: `/api/health` and `/api/ready` 200 on localhost, `verify_live.py`
exit 0 against the installed release, log free of secrets.

## 3. Reverse proxy (W10)

Current state: only `/etc/nginx/sites-enabled/9router` is enabled and **no `insights`
location exists**; `/insights/` is unused. Keep the change additive.

```nginx
location = /insights { return 301 /insights/; }

location /insights/ {
    # Optional access protection decided by the owner:
    # auth_basic "IT Insight";
    # auth_basic_user_file /etc/nginx/it-insight.htpasswd;

    proxy_pass http://127.0.0.1:8010/;      # strips the /insights/ prefix
    proxy_http_version 1.1;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_read_timeout 30s;
    proxy_send_timeout 30s;
}
```

Procedure:

1. Back up the site file; add only this block.
2. `nginx -t`.
3. Reload Nginx (operator with privileges; no permission workaround).
4. Probe the checklist below plus the pre-existing routes of the domain.

Do not open 5432 or 8010 to the Internet to work around a proxy mistake.

## 4. Go-live checklist

```text
HTTPS /insights/                    → SPA shell
HTTPS /insights/assets/<file>       → CSS/JS with correct content type
HTTPS /insights/api/health          → JSON liveness
HTTPS /insights/api/ready           → 200 with active release
HTTPS /insights/api/dashboard       → data + meta
HTTPS /insights/?page=jobs          → jobs page (deep link works)
HTTPS /insights/?page=quality       → data-state page
HTTPS /insights/api/khong-ton-tai   → 404 JSON, never index.html
```

Also verify: existing site routes still work; `/api/*` is protected by the same access
mechanism as `/insights/`; `Cache-Control: no-store` on API responses survives the proxy.

Local prefix behaviour was already verified with a temporary proxy that strips
`/insights/` exactly like the snippet above (see `docs/web/test-evidence.md`).

## 5. Rollback

```bash
ln -sfn /home/womcloud/services/it-insight/releases/<prev> /home/womcloud/services/it-insight/current
systemctl restart it-insight-web          # web service only
curl -fsS http://127.0.0.1:8010/api/ready
# if the proxy block is the problem:
cp /root/backup/9router.<timestamp> /etc/nginx/sites-enabled/9router
nginx -t && systemctl reload nginx
```

Never roll back Gold data, restart Airflow or rotate database credentials to fix a web
issue.

## 6. AI phase

Not started. `GET /api/assistant/status` returns `enabled=false`; the UI disables the send
button. AI00–AI05 require a model/provider decision, cost limits, a tool contract that
reuses this read-only API, and a separate acceptance suite. No API key or local model is
installed by this work.

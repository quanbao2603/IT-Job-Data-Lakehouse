# Deployment plan — IT Insight Web (W08–W10)

**Status: prepared, not executed.** No systemd unit, Nginx location, port or credential
was changed on the VPS. Everything below requires explicit owner approval for the
deployment window.

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

# IT Insight deployment notes

This directory contains **templates only**. The current task does not enable a systemd service, edit Nginx, open a port, or restart shared services.

## Bundle/runtime layout

```text
/home/womcloud/services/it-insight/
├── releases/<release>/
│   ├── server/
│   ├── requirements.txt
│   ├── requirements.lock.txt
│   ├── VERSION.json
│   └── .venv/
├── current -> releases/<release>
└── config/web.env              # protected, not Git
```

`web.env` must contain `WEB_ENV=production`, `WEB_DEMO_MODE=false`, the `/insights` root path and only `gold_reader` credentials. Do not reuse the Gold writer password, Airflow environment wholesale, MinIO credentials or crawler cookies.

## Pre-apply checks

```bash
ss -ltn | grep ':8010\\b' || true
sudo nginx -t
systemctl is-active postgresql.service
```

## Apply only after approval

1. Copy an approved bundle to a new release directory and verify its checksum.
2. Create a Linux virtualenv inside that release and install the locked runtime requirements.
3. Put protected `web.env` in the separate config directory (`chmod 600`).
4. Run the service in the foreground on `127.0.0.1:8010` and verify `/api/health`, `/api/ready`, `/api/dashboard` and `/api/quality`.
5. Add the narrow Nginx location, run `nginx -t`, then reload Nginx through the authorized administrator.
6. Enable only the web service. Never restart Airflow/PostgreSQL/MinIO as part of this web deployment.
7. Verify `/insights/`, `/insights/api/health`, `/insights/api/ready`, `/insights/api/dashboard`, static assets and an unknown API path.

## Rollback

Point `current` back to the last verified web release, restart only `it-insight-web`, restore only the saved Nginx location if necessary, run `nginx -t`, reload, and repeat the probes. Do not rollback Gold data for a web/API issue.

# Operations runbook — IT Insight Web

Applies to the installed deployment at `/home/womcloud/services/it-insight/`.
The service runs as a **systemd user unit** (`it-insight-web.service`) with linger
enabled, so it starts at boot without root. Nginx exposure is the only root-owned step.

```bash
export XDG_RUNTIME_DIR=/run/user/$(id -u)
systemctl --user status it-insight-web.service
systemctl --user restart it-insight-web.service
journalctl --user -u it-insight-web.service -n 100 --no-pager
```

## Layout

```text
/home/womcloud/services/it-insight/
├── releases/<release>/{server, requirements.lock.txt, VERSION.json, .venv}
├── current -> releases/<release>
└── config/web.env          # chmod 600, owner womcloud, never in Git
```

## web.env (no real secret in Git)

```dotenv
WEB_ENV=production
WEB_DEMO_MODE=false
WEB_ROOT_PATH=/insights
WEB_STALE_AFTER_DAYS=1
WEB_QUERY_TIMEOUT_MS=5000
GOLD_DB_HOST=127.0.0.1
GOLD_DB_PORT=5432
GOLD_DB_NAME=lakehouse
GOLD_DB_READER_USER=gold_reader
GOLD_DB_READER_PASSWORD=__SET_ON_SERVER__
```

Do not pass the Airflow environment or MinIO/crawler credentials to this service.

## Install a release

```bash
# on the build host / repo
cd apps/gold-web/frontend && npm ci && npm run build
cd .. && ./scripts/package_release.sh            # writes dist/<bundle>.tar.gz + .sha256

# on the VPS
sha256sum -c it-insight-web-<stamp>.tar.gz.sha256
mkdir -p /home/womcloud/services/it-insight/releases/<stamp>
tar -xzf it-insight-web-<stamp>.tar.gz -C /home/womcloud/services/it-insight/releases/<stamp> --strip-components=1
/usr/bin/python3.10 -m venv /home/womcloud/services/it-insight/releases/<stamp>/.venv
/home/womcloud/services/it-insight/releases/<stamp>/.venv/bin/python -m pip install -r .../requirements.lock.txt
ln -sfn /home/womcloud/services/it-insight/releases/<prev> /home/womcloud/services/it-insight/current   # note previous for rollback
```

## Start / verify (localhost only)

```bash
systemctl start it-insight-web
curl -fsS http://127.0.0.1:8010/api/health
curl -fsS http://127.0.0.1:8010/api/ready
curl -fsS 'http://127.0.0.1:8010/api/dashboard' | head -c 300
```

`/api/health` = process alive. `/api/ready` = reader connected **and** an active release
with a SUCCESS load run and 0 failed DQ. Never treat health as data freshness.

## Monitoring

Minimum signals:

- process: systemd `active`, restart count, uptime;
- readiness: `/api/ready` status and error code (`DB_NOT_CONFIGURED`, `GOLD_UNAVAILABLE`,
  `GOLD_RELEASE_MISSING`);
- latency and HTTP 5xx rate per endpoint;
- served `gold_release_id` / `snapshot_date` / `age_days` (log one line per readiness probe);
- release changes: alert when `gold_release_id` changes but `snapshot_date` stays the same
  (republish of old data) and when `age_days` exceeds the threshold.

Alert delivery is not configured in this phase (see the lakehouse audit: no bot token /
channel). Monitoring must not claim coverage it does not have.

## Rollback

1. Keep the previous release directory and the previous Nginx snippet backup.
2. Point `current` back: `ln -sfn .../releases/<prev> .../current`.
3. `systemctl restart it-insight-web` (only the web service; never Airflow/PostgreSQL).
4. Probe `/api/health`, `/api/ready`, `/insights/`.
5. If the failure is the Nginx snippet: restore the saved file, `nginx -t`, reload.
6. Re-check the pre-existing site paths for regressions.
7. Keep logs. Do **not** roll back Gold data to fix a UI/API issue.

Any recursive delete or move must be checked against the absolute path of
`/home/womcloud/services/it-insight/releases/<release>` first.

## Common failures

| Symptom | Cause | Action |
| --- | --- | --- |
| `/api/ready` 503 `DB_NOT_CONFIGURED` | `web.env` missing password | fix file permissions/content on server |
| 503 `GOLD_UNAVAILABLE` | PostgreSQL down or reader role/password rotated | check `systemctl status postgresql`, reader login; do not swap in writer credentials |
| 503 `GOLD_RELEASE_MISSING` | no active release or failed DQ | check Gold control tables; the web must not invent a release |
| 404 on `/insights/api/...` | `proxy_pass` not stripping `/insights/` | fix the location block, `nginx -t`, reload |
| 409 `RELEASE_CHANGED` in logs | normal during publish | client reloads; investigate only if constant |
| Stale banner | snapshot older than `WEB_STALE_AFTER_DAYS` | pipeline freshness issue, not a web bug |

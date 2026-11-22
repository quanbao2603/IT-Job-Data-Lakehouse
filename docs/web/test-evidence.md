# Test evidence — IT Insight Web

Measured 2026-11-22 on the VPS (`/home/womcloud/IT-Job-Data-Lakehouse/apps/gold-web`).
Python 3.10.12 (`.venv`), Node v22.23.4 / npm 10.9.9.

## Backend unit + contract tests

```text
.venv/bin/python -m pytest -q tests
→ 29 passed

.venv/bin/python -m pip check
→ No broken requirements found.
```

Coverage by file:

- `tests/test_api.py` — liveness vs readiness, release metadata on every payload, expected
  release 409, validation 422 contract shape, structured 404 for unknown `/api` path,
  503 (not an empty table) when the repository is not ready, demo labels, production
  rejects `WEB_DEMO_MODE=true`, assistant explicitly disabled.
- `tests/test_repository.py` — `set_session(isolation_level="REPEATABLE READ", readonly=True)`,
  statement/idle timeouts, rollback + close on success and failure, driver text never
  surfaces publicly, missing password → `DB_NOT_CONFIGURED`, `%`/`_` escaping, `EXISTS`
  skill filter, exact `company_key`, latest `load_run` via `LEFT JOIN LATERAL … LIMIT 1`.
- `tests/test_demo.py` — 12 labelled demo jobs, cohort filters, exact company drill,
  unordered/unique skill pairs, stable pagination, `null` salary, demo release id,
  expected-release rejection.

## Frontend

```text
npm run typecheck        → PASS (tsc --noEmit)
npm run build            → PASS
  server/static/index.html                 0.54 kB
  server/static/assets/index-*.css        11.12 kB (gzip 3.21)
  server/static/assets/index-*.js        251.83 kB (gzip 78.20)
npm audit                → 0 vulnerabilities
```

Bundle is below the 500 kB warning threshold; no chart chunk split is needed yet.

## DOM integration tests against the running app + live Gold

`vitest` + `jsdom` mount the real `App` component against the real FastAPI instance on
`http://127.0.0.1:8011/insights/` (temporary local prefix proxy that strips `/insights/`,
mirroring the Nginx snippet):

```text
IT_INSIGHT_BASE_URL=http://127.0.0.1:8011/insights/ npm test
→ 4 passed
```

Covered flows: overview metrics + release note render; jobs list loads; job dialog opens
with `aria-modal` and closes on Escape; search filter updates the cohort total; skill row
drills into the job list with an active filter chip; stale banner states the snapshot age.

**Not covered:** pixel-level visual comparison and real mobile rendering. Chromium on this
VPS cannot start (missing `libatk` and 12 other system libraries, no sudo to install them),
so no screenshot-based browser evidence exists. The DOM tests above are the substitute;
mobile layout was reviewed in CSS media queries only, not rendered.

## Live Gold reconciliation (read-only)

```text
WEB_ENV=production WEB_DEMO_MODE=false .venv/bin/python scripts/verify_live.py
→ exit 0, 19/19 checks passed
```

```text
gold_release_id   gold_20261119_silver_release_manual_silver_replay_bc10f45
silver_release_id silver_release_manual_silver_replay_bc10f45
snapshot_date     2026-11-19
jobs              3092
companies         1198
skills            131
salary_samples    926
salary_median     20000000.0
quality_checks    32 (0 failed)
served_mode       live
```

Checks include: release id/snapshot match `active_release`; dashboard jobs = jobs total =
SQL row count; no duplicate `unified_job_id`; companies/skills/samples/median equal direct
SQL; city distribution sums to one bucket per job; DQ counts equal SQL; skill drill-through
total equals the per-skill count; company drill-through uses the exact key; pairs never
self-pair and have no duplicate unordered pair; multi-skill job counted once.

## HTTP probes through the `/insights/` prefix

```text
GET /insights/                    200 text/html (index.html, assets under /insights/assets/)
GET /insights/assets/index-*.js   200 text/javascript
GET /insights/api/health          200 application/json
GET /insights/api/ready           200
GET /insights/api/dashboard       200, 3.092 jobs, stale=true, mode=live
GET /insights/api/quality         200, 32 checks, 0 failed
GET /insights/api/filters         200, 5 cities / 316 domains / 4 levels / 131 skills
GET /insights/api/nope            404 {"error":{"code":"NOT_FOUND"}}
GET /insights/api/dashboard?expected_release_id=gold_old   409 RELEASE_CHANGED
GET /insights/api/jobs?page=0     422 INVALID_REQUEST
```

## Packaged bundle smoke test (clean environment)

```text
./scripts/package_release.sh → it-insight-web-<stamp>.tar.gz (95 KB) + sha256 OK
tar -xzf bundle → 20 files, no .env/.pem/.key
/usr/bin/python3.10 -m venv + pip install -r requirements.lock.txt  → OK
uvicorn server.app:app --port 8012 (production env, demo=false)
  /api/health     200
  /api/ready      200
  /api/dashboard  200, jobs=3092, release=gold_20261119_…
  /               200, index.html contains #root
SMOKE=PASS
```

This proves the bundle installs and runs from the lock file alone, without the repo
checkout, `.venv` or `node_modules`.

## Deployed service (VPS, 2026-11-22)

```text
release       /home/womcloud/services/it-insight/releases/20261122T140226Z
current    →  that release
unit          ~/.config/systemd/user/it-insight-web.service  (enabled, linger=yes)
web.env       600, 10 keys, reader-only (no GOLD_DB_USER / MINIO keys)
/api/health   200
/api/ready    200
/api/dashboard 200, jobs=3092
crash test    SIGKILL → new PID, NRestarts=1, health 200
snippet test  real nginx :8088 with the /insights/ block:
              /insights 301 · /insights/ 200 · asset 200 · /api/ready 200 · /api/nope 404
```

`/etc/nginx` was not modified: it is root-owned and the agent has no sudo password.
The apply script is ready at `deploy/apply_nginx.sh` (idempotent, backup + `nginx -t` +
auto-restore + reload + probe).

## Public URL (direct port, 2026-11-22)

```text
release       20261122T150924Z (root base build: VITE_BASE_PATH=/)
bind          0.0.0.0:8010
local         GET / 200 · HEAD /api/health 200 · GET /api/health 200 · /api/dashboard 200
public        http://womtech.io.vn:8010/ → 200 (check-host nodes de1, ua1, us1)
              http://womtech.io.vn:8010/api/health → 200 (de1, ua1, us1)
              TCP 8010 open from il1, ir6, ua3
dom tests     4 passed against http://127.0.0.1:8010/ (root base)
backend       30 passed (new: HEAD mirrors GET status without a body)
hidden        /docs /redoc /openapi.json → 404
```

Note: `grep /insights` still finds one reference in the bundle — that is the prefix
detection in `api.ts`, which lets the same build serve both `/` and `/insights/`.

## Limitations of this evidence

- The `/insights/` route was exercised through a local prefix proxy, not Nginx; the public
  HTTPS URL is **not** deployed or verified.
- Systemd supervision, restart-on-failure and concurrency limits are prepared as templates
  but not enabled.
- No long-running performance benchmark was run; response time observed manually in the
  0.3–1.0 s range for dashboard/jobs on this data volume. No SLA is claimed.

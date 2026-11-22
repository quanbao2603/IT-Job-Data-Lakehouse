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
| Desktop/mobile/keyboard/CSV reviewed | ⚠️ partial | DOM tests cover keyboard Escape and dialog semantics; no real browser/pixel/mobile rendering (Chromium cannot start on this VPS) |
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

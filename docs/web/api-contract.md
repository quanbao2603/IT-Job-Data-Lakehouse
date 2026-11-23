# API contract — IT Insight Web

Base path: `/api` on the app, `/insights/api` through the reverse proxy.
OpenAPI: `/api/openapi.json`, docs `/api/docs` (server started with `--root-path /insights`).
JSON is UTF-8; dates `YYYY-MM-DD`; timestamps ISO 8601 with offset; money is integer VND
or `null`. All data endpoints set `Cache-Control: no-store`.

## Common filter parameters

| Param | Rule |
| --- | --- |
| `q` | ≤ 200 chars, `ILIKE` over title/company, `%`/`_`/`\` escaped (literal search) |
| `city` | value from `/api/filters#cities` |
| `domain` | value from `/api/filters#domains` |
| `level` | value from `/api/filters#levels` |
| `skill` | `skill_code`, applied with `EXISTS` so snapshot rows are not multiplied |
| `company_key` | exact company key (company drill-through) |
| `page` | integer ≥ 1 |
| `page_size` | 1–50, default 12 |
| `sort` | allowlist `recent` \| `salary` \| `title`, stable secondary key `unified_job_id` |
| `expected_release_id` | pinned release; mismatch → 409 `RELEASE_CHANGED` |

Validation happens on the server (Pydantic) and returns the contract error shape.

## Endpoints

| Endpoint | Returns | Notes |
| --- | --- | --- |
| `GET /api/health` | `status, service, mode, ai_enabled` | liveness only; never claims DB access |
| `GET /api/ready` | `status=ready, meta` | requires active release with `SUCCESS` load run and 0 failed DQ; otherwise 503 |
| `GET /api/filters` | `meta, cities[], domains[], levels[], skills[]` | same release as the data being viewed |
| `GET /api/dashboard` | `meta, summary, domains, cities, levels, companies, skills, platforms` | every aggregate over the filtered cohort |
| `GET /api/jobs` | `meta, items[], total, page, page_size` | one row per job; skills/platforms/source links attached per page |
| `GET /api/jobs/{unified_job_id}` | `meta, job` | 404 when not in the served release |
| `GET /api/skills` | `meta, items[], pairs[]` | pairs computed on the filtered cohort, unordered, no self-pair |
| `GET /api/companies` | `meta, items[], total, page, page_size` | UNKNOWN company excluded |
| `GET /api/quality` | `meta, checks[], coverage[]` | active release DQ + coverage history; no raw exceptions |
| `GET /api/assistant/status` | `enabled=false, status, message, examples` | no model call in this phase |

Every job row carries `source_url` (representative listing URL, nullable) and
`source_urls[]` (`{platform, url, last_observed_at}`), read from `gold.v_job_source_url`.
A job with no URL is reported as null and the UI shows "Chưa có liên kết nguồn"; the
API never synthesises or guesses a link.

`GET /api/quality` also returns `completeness` (`jobs`, `with_salary/_pct`,
`with_city/_pct`, `with_level/_pct`, `with_domain/_pct`), which is deliberately separate
from the DQ verdict: passing every rule does not mean every field is populated.

`summary` minimum: `jobs, companies, skills, salary_samples, salary_median, missing_city,
last_seen_at`. Salary median is `null` when there is no valid sample.

## Error contract

```json
{"error": {"code": "DB_NOT_CONFIGURED", "message": "Chưa cấu hình kết nối Gold trên máy chủ."}}
```

| HTTP | Code | Behaviour |
| --- | --- | --- |
| 422 | `INVALID_REQUEST` | invalid page, page_size, sort, over-long id/q |
| 404 | `NOT_FOUND` | unknown job id or unknown API path (JSON, never `index.html`) |
| 409 | `RELEASE_CHANGED` | client pinned an older release; reload all related data |
| 503 | `DB_NOT_CONFIGURED`, `GOLD_UNAVAILABLE`, `GOLD_RELEASE_MISSING` | not-ready state; distinct from "0 results" |
| 500 | `INTERNAL_ERROR` | generic message; no traceback, DSN or password |

## Request examples

```bash
curl -s 'https://<host>/insights/api/dashboard?city=H%C3%A0%20N%E1%BB%99i&level=Senior'
curl -s 'https://<host>/insights/api/jobs?q=python&skill=python&sort=salary&page=2&page_size=24'
curl -s 'https://<host>/insights/api/companies?page=1&page_size=12'
curl -s 'https://<host>/insights/api/jobs/<unified_job_id>?expected_release_id=<gold_release_id>'
```

No endpoint accepts arbitrary SQL, table names or a connection string from the browser.

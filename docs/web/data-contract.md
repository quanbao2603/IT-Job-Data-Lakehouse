# Data contract — Gold → Web/API

## Scope wording

The app describes its content as **nguồn cung tin hệ thống quan sát được** (supply the
system observed). It never claims full market coverage, live listings, applicants,
applications, views or revenue.

## Release consistency

Every data response carries `meta`:

```text
gold_release_id, silver_release_id, snapshot_date, snapshot_cutoff_at, switched_at,
served_at, mode (live|demo), age_days, stale_after_days, stale, scope, salary_definition
```

Rules:

1. `gold_release_id` and `snapshot_date` come from `gold_control.active_release` (`id=1`).
2. `silver_release_id` / `snapshot_cutoff_at` come from the **latest** `gold_control.load_run`
   row for that release (`ORDER BY started_at DESC LIMIT 1` via `LEFT JOIN LATERAL`), so a
   retry with several run rows cannot duplicate the response or pick an arbitrary status.
3. Each response is one `REPEATABLE READ, READ ONLY` transaction: the active pointer is read
   first and every business query runs in the same snapshot.
4. `expected_release_id` mismatches return HTTP 409 `RELEASE_CHANGED`; the client drops its
   pinned release and reloads instead of mixing two releases.
5. `served_at` is server time, never used to infer data freshness.
6. `age_days` = business date (Asia/Ho_Chi_Minh) − `snapshot_date`; `stale` compares it to
   `WEB_STALE_AFTER_DAYS` (1 by default). This is a display threshold, not an SLA.

## Grain and joins

| Dataset | Grain | Join rule |
| --- | --- | --- |
| Snapshot | release + snapshot_date + unified_job_id | `s.job_key = j.job_key`, `s.company_key = c.company_key` |
| Job-platform | release + date + job + platform | cohort by `unified_job_id`, platform via `platform_key` |
| Job-skill | release + date + job + skill | cohort by `unified_job_id`, skill via `skill_key` |
| Change event | release + platform + source job + observation | not exposed as "activity today" |
| Company | one normalized key | exact `company_key`, never fuzzy name matching in SQL |

`v_job_snapshot` is already pinned to the active release. Dimensions are joined on the
snapshot's `job_key` (SCD2 version of that snapshot), never on "current version" alone.

Multi-row relations (skills, platforms) are filtered with `EXISTS` or aggregated with
`COUNT(DISTINCT unified_job_id)`, so a job with 10 skills is still one job.

## KPI definitions

| Metric | Definition | Wording constraint |
| --- | --- | --- |
| `jobs` | `COUNT(*)` over the snapshot cohort | observed records up to cutoff, not market size |
| `companies` | distinct `company_key` where `NOT is_unknown` | UNKNOWN excluded from KPI |
| `skills` | distinct valid skill keys present in the cohort | not the size of `dim_skill` |
| `salary_samples` | snapshot rows with `salary_sample_flag` | always shown next to the median |
| `salary_median` | `percentile_cont(0.5)` over `salary_midpoint_vnd` filtered on the sample flag | midpoint of advertised range, not take-home pay; `null` when no sample |
| `missing_city` | cohort rows with `city_bucket='UNKNOWN'` | shown as "Chưa xác định", not 0 jobs |
| `platforms` | distinct jobs with platform evidence in the release window | not proof the listing is online |
| skill pair | unordered pair inside one job (`a.skill_key < b.skill_key`), no self-pair | not a candidate–job match score |

`SMALL_SAMPLE_THRESHOLD = 5` in `src/gold/contract.py`: the UI always shows the sample
count next to the median; a small sample is labelled by its count rather than hidden.
Medians of groups are never averaged to fake a total median — every filtered request
recomputes from the fact rows.

`entity_status` and `observations.is_active` are **not** used to claim "đang tuyển".
`newly_seen_job_count` can be `NULL` at baseline and is not displayed as 0 or "new".

## Missing values

- Missing salary: `null` in JSON → "Chưa có dữ liệu lương" / "—" in the UI, never `0`.
- Missing location/domain/level: `UNKNOWN` bucket, rendered as "Chưa xác định".
- `UNKNOWN` and "Khác" are distinct values in filters and charts.

## Fields exposed per job

`unified_job_id, canonical_title, company_key, company_display_name, city_bucket,
job_domain, job_level, work_mode, entity_status, canonical_salary_min_vnd,
canonical_salary_max_vnd, salary_midpoint_vnd, salary_sample_flag, source_count,
observation_count (detail), last_seen_at, snapshot_cutoff_at (detail), skills[], platforms[]`.

## Not available from Gold today

- Verified source URL and full description per job. `gold_reader` has no `gold_staging`
  access by design; adding this needs a separate view/ETL change, not a web shortcut.
- Applicant, view, application, interview and payment data. No View→Apply, Time-To-Hire
  or revenue KPI can be derived.
- Historical trends from a single snapshot. `v_daily_coverage` exposes coverage history,
  not a full multi-snapshot fact history; trends require a new, approved contract.

## Demo mode

`WEB_DEMO_MODE=true` is allowed only outside production and returns 12 clearly labelled
fixture jobs (`gold_demo_fixture_…`, `meta.mode=demo`). It is never a fallback: a missing
password, failed connection or absent release returns 503, not demo data.

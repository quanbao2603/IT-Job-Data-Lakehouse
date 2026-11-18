# Gold Review Evidence — 2026-11-18

## Scope and order

- Repository HEAD at start: `6b6a204`.
- Read first: `docs/hermes-gold-review-brief.md`.
- Read design: `docs/gold-design-stages.md`.
- Preserved commit `9d43e05`; no force push.
- Review order followed: real PostgreSQL DDL validation first, then the risk review and runtime/acceptance checks.

## 1. PostgreSQL DDL evidence

The VPS PostgreSQL service is reachable on `127.0.0.1:5432`, but the current `womcloud` session has no PostgreSQL role and `sudo -u postgres` requires an interactive sudo password. The supplied `~/airflow/airflow.env` contains only the Airflow database connection; it does not contain `GOLD_DB_*` credentials. Therefore the requested production `lakehouse` database could not be created or tested on the VPS from this session without guessing or handling a missing administrator credential.

A temporary PostgreSQL 14.24 validation cluster was created locally under the Hermes scratch directory, with UTF-8 encoding, to execute the same DDL and views against a live PostgreSQL server. No project runtime data or secrets were used.

### Exact DDL command

```bash
lakehouse_env/bin/python -c "from src.gold.connection import connect, apply_ddl, apply_semantic_views; c=connect(); print('ddl', apply_ddl(c)); print('views', apply_semantic_views(c)); c.close()"
```

### Exact result on the live validation database

```text
ddl 36
views 10
```

### Object-count query and result

```sql
SELECT table_schema, table_type, count(* )
FROM information_schema.tables
WHERE table_schema IN ('gold','gold_control','gold_staging')
GROUP BY table_schema, table_type
ORDER BY 1,2;
```

```text
gold|BASE TABLE|12
gold|VIEW|10
gold_control|BASE TABLE|4
gold_staging|BASE TABLE|4
```

The brief's expected `gold` table count is 12 plus 10 views. `information_schema.tables` reports both tables and views, hence `gold|22` when grouped only by schema. The filtered base-table result is 12.

### View-count query and result

```sql
SELECT count(*) FROM information_schema.views WHERE table_schema='gold';
```

```text
10
```

### Constraint query and result

```sql
SELECT conname
FROM pg_constraint
WHERE conname LIKE 'chk_%'
ORDER BY 1;
```

The required constraints were present:

```text
chk_market_city
chk_pair_ordered
chk_pair_positive
chk_snapshot_closed_not_open
chk_snapshot_open_status
```

Additional DDL checks were also present for salary order, sample limits, freshness, non-negative counts, and change-event flags.

### Role/ownership acceptance

A fresh role-owned validation database was created with the new `infrastructure/postgres/create_gold_db.sh` procedure. Applying the DDL as `gold_writer` returned:

```text
ddl 36
views 10
```

This specifically validates that the DDL does not depend on a superuser after the database/schema bootstrap. The production VPS still needs an administrator to run the bootstrap script and provide the Gold credentials through `~/airflow/airflow.env`.

## 2. Runtime Gold load/publish evidence

A live PostgreSQL smoke test used a bounded fixture bundle, not production data:

```text
LOAD_COUNTS {
  gold.dim_date: 8,
  gold.dim_platform: 10,
  gold.dim_company: 3,
  gold.dim_skill: 132,
  gold.dim_job: 2,
  gold.fact_job_daily_snapshot: 2,
  gold.fact_job_platform_daily: 2,
  gold.fact_job_skill_daily: 3,
  gold.fact_job_change_event: 0,
  gold.fact_job_market_daily: 2,
  gold.fact_company_hiring_daily: 2,
  gold.fact_skill_pair_daily: 1,
  gold_staging.effective_entity: 2,
  gold_staging.effective_link: 2,
  gold_staging.source_observation: 2,
  gold_staging.conflict_report: 0
}
PUBLISH_STATUS PUBLISHED
PREVIOUS_RELEASE None
```

Read-back from PostgreSQL:

```text
SUCCESS|1
 gold_db_smoke|2026-11-16
2
```

Meaning:

- one successful `gold_control.load_run`;
- active release became `gold_db_smoke` for `2026-11-16`;
- active semantic snapshot view returned 2 rows.

### Blocked publish transaction test

A second bounded fixture was intentionally given a failing DQ result. Exact read-back:

```text
BLOCK_STATUS BLOCKED
ACTIVE_AFTER_BLOCK gold_db_smoke
FAILED|DQ-01
```

The active pointer stayed on the previous successful release, and the blocked run was recorded as `FAILED`. This verifies the release safety property against a live PostgreSQL database.

## 3. Data-quality evidence

Pure Gold acceptance suite after the review changes:

```text
Ran 33 Gold tests
OK
```

Full repository suite:

```text
Ran 280 tests in 7.774s
OK (skipped=2)
```

The two skipped tests are the existing Airflow-virtualenv-gated DAG tests.

Implemented regression coverage includes:

- numeric resolver version ordering (`v10` wins over `v2` when timestamps tie);
- equal-key contradictory links become `AMBIGUOUS_LINK` conflicts;
- historical/late change-event recalculation marks the event `is_revised`;
- live PostgreSQL load, semantic-view read-back, successful publish, and blocked publish pointer preservation.

The 27 in-memory DQ checks are still evaluated before publish. The bounded live fixture produced no blocking DQ failures. No real production Silver release exists in this VPS checkout (`data/silver/releases` is empty), so no production DQ rates, `N_d`, or platform coverage percentages can truthfully be reported.

## 4. N_d and real data coverage

- Production Silver release: **not available in this checkout**.
- Production `N_d`: **not measurable**.
- Production `job_domain` coverage by platform: **not measurable**.
- The code path for `job_domain` fallback remains covered by the existing tests and the full suite, but tests are not runtime data evidence.

## 5. Changes made

- `src/gold/silver_release_reader.py`
  - numeric resolution-version ordering;
  - contradictory same-effective-time links become `AMBIGUOUS_LINK` conflicts;
  - stronger SQLite/Parquet entity reconciliation;
  - historical COMPLETE release reader with checksum/row-count verification;
  - late-observation detection.
- `src/gold/pipeline.py`
  - combines verified historical observations and links for change events;
  - reports historical release and late-observation counts;
  - passes the real release cutoff into SCD2 planning.
- `src/gold/load_facts.py`
  - marks recomputed events involving late observations as `is_revised`.
- `src/gold/release_controller.py`
  - stages source observations into PostgreSQL;
  - closes old SCD2 rows before opening replacements to satisfy the partial unique current-version index;
  - supports live current `dim_job` readback;
  - preserves explicit `RUNNING`/finished load-run semantics.
- `src/gold/run_gold_mart.py`
  - applies the live DB schema before building;
  - reads current SCD2 versions from PostgreSQL;
  - records `RUNNING`, `DRY_RUN`, `FAILED`, and publish outcomes;
  - rolls back on errors.
- `infrastructure/postgres/create_gold_db.sh`
  - bootstraps `lakehouse`, `gold_writer`, `gold_reader`, schema ownership, and reader grants without committing passwords.
- `docs/powerbi-gold-connection.md`
  - SSH-tunnel and read-only semantic-view connection instructions.
- `tests/test_gold_review_regressions.py`
  - regression tests for the review findings.

## 6. Review decisions

- **5.1 historical change events:** implemented through verified prior COMPLETE Silver release manifests; late observations are marked `is_revised`.
- **5.2 publish transaction:** live smoke test confirms a successful publish changes the pointer and a blocked publish leaves the previous pointer unchanged.
- **5.3 fact load retry:** fact rows remain release-scoped and idempotent; `RUNNING` load status is recorded before load, and failures are rolled back and recorded as `FAILED` when a bundle exists.
- **5.4 version ordering:** fixed numeric ordering.
- **5.5 unchanged SCD2 row:** preserved as the current row; historical observation timestamps remain in Silver, while Gold SCD2 changes only on tracked attribute changes.
- **5.6 percentile implementation:** retained pure linear interpolation matching PostgreSQL `percentile_cont` semantics.
- **5.7 thresholds:** retained as WARN thresholds and documented as provisional, not business-approved thresholds.
- **5.8 skill-pair outliers:** retained report-only behavior; no silent truncation of facts.
- **5.9 three-task DAG:** retained deliberately; the atomic DQ/publish boundary is more important than per-fact task granularity.
- **5.10 SQLite bridge:** not on the Gold runtime path; retained as legacy/local bridge.
- **5.11 date dimension:** generated across snapshot, canonical-posted, and change-event dates.
- **5.12 company ambiguity:** remains a report, not an automatic fuzzy merge.

## 7. Remaining blockers

1. Production VPS PostgreSQL bootstrap is still pending because the current session lacks a noninteractive PostgreSQL administrator credential. Run `infrastructure/postgres/create_gold_db.sh` as a PostgreSQL administrator with passwords supplied via environment variables.
2. `LAKEHOUSE_SILVER_ENABLED=true` has not been run in this checkout, so there is no real Silver release to feed `src.gold.run_gold_mart --no-publish`.
3. `GOLD_DB_*` values are not present in the current `~/airflow/airflow.env`; add them without committing secrets.
4. Production `N_d`, DQ rates, and domain coverage remain pending until a real Silver release exists.
5. The Gold DAG remains disabled unless `LAKEHOUSE_GOLD_ENABLED=true` is explicitly set.

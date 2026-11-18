-- Gold semantic views for BI. Power BI must read these views only.
-- Every view is pinned to gold_control.active_release, so a dashboard can never mix two
-- releases and can never see a release that failed its data quality gate.

CREATE OR REPLACE VIEW gold.v_active_release AS
SELECT gold_release_id, snapshot_date, switched_at
FROM gold_control.active_release
WHERE id = 1;

CREATE OR REPLACE VIEW gold.v_job_snapshot AS
SELECT fact.*
FROM gold.fact_job_daily_snapshot fact
JOIN gold_control.active_release active ON active.id = 1
  AND active.gold_release_id = fact.gold_release_id;

CREATE OR REPLACE VIEW gold.v_job_platform AS
SELECT fact.*
FROM gold.fact_job_platform_daily fact
JOIN gold_control.active_release active ON active.id = 1
  AND active.gold_release_id = fact.gold_release_id;

CREATE OR REPLACE VIEW gold.v_job_skill AS
SELECT fact.*
FROM gold.fact_job_skill_daily fact
JOIN gold_control.active_release active ON active.id = 1
  AND active.gold_release_id = fact.gold_release_id;

CREATE OR REPLACE VIEW gold.v_change_event AS
SELECT fact.*
FROM gold.fact_job_change_event fact
JOIN gold_control.active_release active ON active.id = 1
  AND active.gold_release_id = fact.gold_release_id;

CREATE OR REPLACE VIEW gold.v_market_daily AS
SELECT fact.*
FROM gold.fact_job_market_daily fact
JOIN gold_control.active_release active ON active.id = 1
  AND active.gold_release_id = fact.gold_release_id;

CREATE OR REPLACE VIEW gold.v_company_hiring AS
SELECT fact.*
FROM gold.fact_company_hiring_daily fact
JOIN gold_control.active_release active ON active.id = 1
  AND active.gold_release_id = fact.gold_release_id;

CREATE OR REPLACE VIEW gold.v_skill_pair AS
SELECT fact.*
FROM gold.fact_skill_pair_daily fact
JOIN gold_control.active_release active ON active.id = 1
  AND active.gold_release_id = fact.gold_release_id;

-- Dimension views pinned to the current job version, for drill-through.
CREATE OR REPLACE VIEW gold.v_dim_job_current AS
SELECT * FROM gold.dim_job WHERE is_current;

CREATE OR REPLACE VIEW gold.v_daily_coverage AS
SELECT snapshot_date, status, gold_release_id, silver_release_id,
       silver_ready_at, gold_published_at, bi_refreshed_at, sla_breached
FROM gold_control.daily_snapshot
ORDER BY snapshot_date DESC;

-- Gold layer DDL for the IT Job Data Lakehouse (PostgreSQL, database `lakehouse`).
-- Source of truth: docs/gold-design-stages.md v3.0 (D-G01..D-G07).
-- Idempotent: every object uses IF NOT EXISTS, so re-running is safe.
-- Statements are separated by the semicolon character, and full-line comments are
-- stripped before splitting, so no statement may embed a statement terminator inside
-- a string literal.

CREATE SCHEMA IF NOT EXISTS gold;
CREATE SCHEMA IF NOT EXISTS gold_control;
CREATE SCHEMA IF NOT EXISTS gold_staging;

-- ---------------------------------------------------------------- dimensions

CREATE TABLE IF NOT EXISTS gold.dim_date (
    date_key      INTEGER PRIMARY KEY,
    full_date     DATE NOT NULL UNIQUE,
    year          SMALLINT NOT NULL,
    quarter       SMALLINT NOT NULL,
    month         SMALLINT NOT NULL,
    week_of_year  SMALLINT NOT NULL,
    day_of_week   SMALLINT NOT NULL,
    is_weekend    BOOLEAN NOT NULL,
    timezone_name TEXT NOT NULL DEFAULT 'Asia/Ho_Chi_Minh'
);

CREATE TABLE IF NOT EXISTS gold.dim_company (
    company_key             TEXT PRIMARY KEY,
    company_name_normalized TEXT NOT NULL,
    company_display_name    TEXT NOT NULL,
    company_alias_raw       TEXT,
    company_industry        TEXT,
    company_size            TEXT,
    normalization_version   TEXT NOT NULL,
    is_unknown              BOOLEAN NOT NULL DEFAULT FALSE,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_dim_company_name ON gold.dim_company(company_name_normalized);

CREATE TABLE IF NOT EXISTS gold.dim_platform (
    platform_key  SMALLINT PRIMARY KEY,
    platform_code TEXT NOT NULL UNIQUE,
    platform_name TEXT NOT NULL,
    is_active     BOOLEAN NOT NULL DEFAULT TRUE,
    is_unknown    BOOLEAN NOT NULL DEFAULT FALSE
);

CREATE TABLE IF NOT EXISTS gold.dim_skill (
    skill_key        TEXT PRIMARY KEY,
    taxonomy_version TEXT NOT NULL,
    skill_code       TEXT NOT NULL,
    skill_name       TEXT NOT NULL,
    skill_group      TEXT,
    is_unknown       BOOLEAN NOT NULL DEFAULT FALSE,
    UNIQUE (taxonomy_version, skill_code)
);

CREATE TABLE IF NOT EXISTS gold.dim_job (
    job_key                       TEXT PRIMARY KEY,
    unified_job_id                TEXT NOT NULL,
    version_seq                   INTEGER NOT NULL,
    canonical_title               TEXT NOT NULL,
    company_key                   TEXT NOT NULL REFERENCES gold.dim_company(company_key),
    canonical_location            TEXT,
    city_bucket                   TEXT NOT NULL DEFAULT 'UNKNOWN',
    job_domain                    TEXT,
    job_level                     TEXT,
    work_mode                     TEXT,
    canonical_salary_min_vnd      BIGINT,
    canonical_salary_max_vnd      BIGINT,
    canonical_currency            TEXT,
    canonical_posted_date         DATE,
    entity_status                 TEXT NOT NULL,
    representative_observation_id TEXT,
    representative_fallback       BOOLEAN NOT NULL DEFAULT FALSE,
    valid_from_release            TEXT NOT NULL,
    valid_to_release              TEXT,
    valid_from_at                 TIMESTAMPTZ NOT NULL,
    valid_to_at                   TIMESTAMPTZ,
    is_current                    BOOLEAN NOT NULL DEFAULT TRUE,
    UNIQUE (unified_job_id, version_seq)
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_dim_job_current ON gold.dim_job(unified_job_id) WHERE is_current;
CREATE INDEX IF NOT EXISTS idx_dim_job_company ON gold.dim_job(company_key);
CREATE INDEX IF NOT EXISTS idx_dim_job_status ON gold.dim_job(entity_status);

-- ------------------------------------------------------------ base facts

CREATE TABLE IF NOT EXISTS gold.fact_job_daily_snapshot (
    gold_release_id          TEXT NOT NULL,
    snapshot_date            DATE NOT NULL,
    date_key                 INTEGER NOT NULL REFERENCES gold.dim_date(date_key),
    unified_job_id           TEXT NOT NULL,
    job_key                  TEXT NOT NULL REFERENCES gold.dim_job(job_key),
    company_key              TEXT NOT NULL REFERENCES gold.dim_company(company_key),
    posted_date_key          INTEGER REFERENCES gold.dim_date(date_key),
    job_count                SMALLINT NOT NULL DEFAULT 1,
    open_job_count           SMALLINT NOT NULL DEFAULT 0,
    newly_seen_job_count     SMALLINT,
    entity_status            TEXT NOT NULL,
    canonical_salary_min_vnd BIGINT,
    canonical_salary_max_vnd BIGINT,
    salary_midpoint_vnd      NUMERIC(14,2),
    salary_sample_flag       BOOLEAN NOT NULL DEFAULT FALSE,
    source_count             INTEGER NOT NULL,
    observation_count        INTEGER NOT NULL,
    last_seen_at             TIMESTAMPTZ NOT NULL,
    snapshot_cutoff_at       TIMESTAMPTZ NOT NULL,
    silver_release_id        TEXT NOT NULL,
    loaded_at                TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (gold_release_id, snapshot_date, unified_job_id),
    CONSTRAINT chk_snapshot_job_count CHECK (job_count = 1),
    CONSTRAINT chk_snapshot_open_count CHECK (open_job_count IN (0, 1)),
    CONSTRAINT chk_snapshot_salary_order CHECK (
        canonical_salary_min_vnd IS NULL
        OR canonical_salary_max_vnd IS NULL
        OR canonical_salary_min_vnd <= canonical_salary_max_vnd
    ),
    CONSTRAINT chk_snapshot_open_status CHECK (
        open_job_count = 0
        OR entity_status IN ('NEW', 'ACTIVE', 'UPDATED', 'REOPENED', 'STALE')
    ),
    CONSTRAINT chk_snapshot_closed_not_open CHECK (
        entity_status NOT IN ('CLOSED', 'EXPIRED', 'MERGED', 'SPLIT')
        OR open_job_count = 0
    ),
    CONSTRAINT chk_snapshot_sample_flag CHECK (
        (salary_sample_flag = FALSE) OR (salary_midpoint_vnd IS NOT NULL)
    )
);
CREATE INDEX IF NOT EXISTS idx_snapshot_date ON gold.fact_job_daily_snapshot(snapshot_date);
CREATE INDEX IF NOT EXISTS idx_snapshot_release ON gold.fact_job_daily_snapshot(gold_release_id);

CREATE TABLE IF NOT EXISTS gold.fact_job_platform_daily (
    gold_release_id             TEXT NOT NULL,
    snapshot_date               DATE NOT NULL,
    date_key                    INTEGER NOT NULL REFERENCES gold.dim_date(date_key),
    unified_job_id              TEXT NOT NULL,
    job_key                     TEXT NOT NULL REFERENCES gold.dim_job(job_key),
    platform_key                SMALLINT NOT NULL REFERENCES gold.dim_platform(platform_key),
    company_key                 TEXT NOT NULL REFERENCES gold.dim_company(company_key),
    job_platform_count          SMALLINT NOT NULL DEFAULT 1,
    source_listing_count        INTEGER NOT NULL,
    observation_count_in_window INTEGER NOT NULL,
    last_observed_at            TIMESTAMPTZ NOT NULL,
    freshness_days              SMALLINT NOT NULL,
    evidence_window_days        SMALLINT NOT NULL,
    source_url                  TEXT,
    snapshot_cutoff_at          TIMESTAMPTZ NOT NULL,
    silver_release_id           TEXT NOT NULL,
    loaded_at                   TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (gold_release_id, snapshot_date, unified_job_id, platform_key),
    CONSTRAINT chk_platform_listing_count CHECK (source_listing_count >= 1),
    CONSTRAINT chk_platform_freshness CHECK (freshness_days >= 0),
    CONSTRAINT chk_platform_window CHECK (freshness_days <= evidence_window_days),
    FOREIGN KEY (gold_release_id, snapshot_date, unified_job_id)
        REFERENCES gold.fact_job_daily_snapshot(gold_release_id, snapshot_date, unified_job_id)
);
-- Existing deployments predate the column: a plain ADD COLUMN keeps the DDL idempotent.
ALTER TABLE gold.fact_job_platform_daily ADD COLUMN IF NOT EXISTS source_url TEXT;
CREATE INDEX IF NOT EXISTS idx_platform_daily_platform ON gold.fact_job_platform_daily(platform_key, snapshot_date);

CREATE TABLE IF NOT EXISTS gold.fact_job_skill_daily (
    gold_release_id           TEXT NOT NULL,
    snapshot_date             DATE NOT NULL,
    date_key                  INTEGER NOT NULL REFERENCES gold.dim_date(date_key),
    unified_job_id            TEXT NOT NULL,
    job_key                   TEXT NOT NULL REFERENCES gold.dim_job(job_key),
    skill_key                 TEXT NOT NULL REFERENCES gold.dim_skill(skill_key),
    taxonomy_version          TEXT NOT NULL,
    job_skill_count           SMALLINT NOT NULL DEFAULT 1,
    supporting_platform_count SMALLINT NOT NULL,
    latest_observed_at        TIMESTAMPTZ NOT NULL,
    snapshot_cutoff_at        TIMESTAMPTZ NOT NULL,
    silver_release_id         TEXT NOT NULL,
    loaded_at                 TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (gold_release_id, snapshot_date, unified_job_id, skill_key),
    CONSTRAINT chk_skill_platform_count CHECK (supporting_platform_count >= 1),
    FOREIGN KEY (gold_release_id, snapshot_date, unified_job_id)
        REFERENCES gold.fact_job_daily_snapshot(gold_release_id, snapshot_date, unified_job_id)
);
CREATE INDEX IF NOT EXISTS idx_skill_daily_skill ON gold.fact_job_skill_daily(skill_key, snapshot_date);

CREATE TABLE IF NOT EXISTS gold.fact_job_change_event (
    gold_release_id         TEXT NOT NULL,
    event_date              DATE NOT NULL,
    event_date_key          INTEGER NOT NULL REFERENCES gold.dim_date(date_key),
    platform_key            SMALLINT NOT NULL REFERENCES gold.dim_platform(platform_key),
    source_job_id           TEXT NOT NULL,
    observation_id          TEXT NOT NULL,
    previous_observation_id TEXT,
    unified_job_id          TEXT,
    job_key                 TEXT REFERENCES gold.dim_job(job_key),
    company_key             TEXT REFERENCES gold.dim_company(company_key),
    change_event_count      SMALLINT NOT NULL DEFAULT 1,
    title_changed           BOOLEAN NOT NULL DEFAULT FALSE,
    salary_changed          BOOLEAN NOT NULL DEFAULT FALSE,
    location_changed        BOOLEAN NOT NULL DEFAULT FALSE,
    work_mode_changed       BOOLEAN NOT NULL DEFAULT FALSE,
    skills_changed          BOOLEAN NOT NULL DEFAULT FALSE,
    salary_old_vnd          BIGINT,
    salary_new_vnd          BIGINT,
    salary_delta_vnd        BIGINT,
    skill_added_count       SMALLINT,
    skill_removed_count     SMALLINT,
    link_resolved           BOOLEAN NOT NULL DEFAULT TRUE,
    is_revised              BOOLEAN NOT NULL DEFAULT FALSE,
    observed_at             TIMESTAMPTZ NOT NULL,
    snapshot_cutoff_at      TIMESTAMPTZ NOT NULL,
    silver_release_id       TEXT NOT NULL,
    loaded_at               TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (gold_release_id, platform_key, source_job_id, observation_id),
    CONSTRAINT chk_change_event_has_flag CHECK (
        title_changed OR salary_changed OR location_changed
        OR work_mode_changed OR skills_changed
    )
);
CREATE INDEX IF NOT EXISTS idx_change_event_date ON gold.fact_job_change_event(event_date);
CREATE INDEX IF NOT EXISTS idx_change_event_job ON gold.fact_job_change_event(unified_job_id);

-- ------------------------------------------------------------ BI aggregates

CREATE TABLE IF NOT EXISTS gold.fact_job_market_daily (
    gold_release_id      TEXT NOT NULL,
    snapshot_date        DATE NOT NULL,
    date_key             INTEGER NOT NULL REFERENCES gold.dim_date(date_key),
    job_domain           TEXT NOT NULL DEFAULT 'UNKNOWN',
    city_bucket          TEXT NOT NULL DEFAULT 'UNKNOWN',
    job_level            TEXT NOT NULL DEFAULT 'UNKNOWN',
    observed_job_count   INTEGER NOT NULL,
    newly_seen_job_count INTEGER,
    open_job_count       INTEGER NOT NULL DEFAULT 0,
    salary_sample_count  INTEGER NOT NULL DEFAULT 0,
    salary_p25_vnd       NUMERIC(14,2),
    salary_p50_vnd       NUMERIC(14,2),
    salary_p75_vnd       NUMERIC(14,2),
    salary_avg_vnd       NUMERIC(14,2),
    snapshot_cutoff_at   TIMESTAMPTZ NOT NULL,
    silver_release_id    TEXT NOT NULL,
    loaded_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (gold_release_id, snapshot_date, job_domain, city_bucket, job_level),
    CONSTRAINT chk_market_city CHECK (
        city_bucket IN ('Hà Nội', 'Hồ Chí Minh', 'Đà Nẵng', 'Khác', 'UNKNOWN')
    ),
    CONSTRAINT chk_market_sample CHECK (salary_sample_count <= observed_job_count),
    CONSTRAINT chk_market_open CHECK (open_job_count <= observed_job_count),
    CONSTRAINT chk_market_no_negative CHECK (observed_job_count >= 0)
);
CREATE INDEX IF NOT EXISTS idx_market_daily_date ON gold.fact_job_market_daily(snapshot_date);

CREATE TABLE IF NOT EXISTS gold.fact_company_hiring_daily (
    gold_release_id       TEXT NOT NULL,
    snapshot_date         DATE NOT NULL,
    date_key              INTEGER NOT NULL REFERENCES gold.dim_date(date_key),
    company_key           TEXT NOT NULL REFERENCES gold.dim_company(company_key),
    observed_job_count    INTEGER NOT NULL,
    newly_seen_job_count  INTEGER,
    open_job_count        INTEGER NOT NULL DEFAULT 0,
    distinct_city_count   SMALLINT NOT NULL DEFAULT 0,
    distinct_domain_count SMALLINT NOT NULL DEFAULT 0,
    salary_sample_count   INTEGER NOT NULL DEFAULT 0,
    snapshot_cutoff_at    TIMESTAMPTZ NOT NULL,
    silver_release_id     TEXT NOT NULL,
    loaded_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (gold_release_id, snapshot_date, company_key),
    CONSTRAINT chk_company_sample CHECK (salary_sample_count <= observed_job_count),
    CONSTRAINT chk_company_open CHECK (open_job_count <= observed_job_count)
);
CREATE INDEX IF NOT EXISTS idx_company_daily_date ON gold.fact_company_hiring_daily(snapshot_date);

CREATE TABLE IF NOT EXISTS gold.fact_skill_pair_daily (
    gold_release_id        TEXT NOT NULL,
    snapshot_date          DATE NOT NULL,
    date_key               INTEGER NOT NULL REFERENCES gold.dim_date(date_key),
    skill_a_key            TEXT NOT NULL REFERENCES gold.dim_skill(skill_key),
    skill_b_key            TEXT NOT NULL REFERENCES gold.dim_skill(skill_key),
    taxonomy_version       TEXT NOT NULL,
    co_mentioned_job_count INTEGER NOT NULL,
    snapshot_cutoff_at     TIMESTAMPTZ NOT NULL,
    silver_release_id      TEXT NOT NULL,
    loaded_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (gold_release_id, snapshot_date, skill_a_key, skill_b_key),
    CONSTRAINT chk_pair_ordered CHECK (skill_a_key < skill_b_key),
    CONSTRAINT chk_pair_positive CHECK (co_mentioned_job_count >= 1)
);
CREATE INDEX IF NOT EXISTS idx_skill_pair_a ON gold.fact_skill_pair_daily(skill_a_key, snapshot_date);

-- ------------------------------------------------------------ release control

CREATE TABLE IF NOT EXISTS gold_control.load_run (
    gold_release_id               TEXT PRIMARY KEY,
    silver_release_id             TEXT NOT NULL,
    silver_manifest_fingerprint   TEXT NOT NULL,
    snapshot_date                 DATE NOT NULL,
    snapshot_cutoff_at            TIMESTAMPTZ NOT NULL,
    transform_version             TEXT NOT NULL,
    skill_taxonomy_version        TEXT NOT NULL,
    company_normalization_version TEXT NOT NULL,
    city_normalization_version    TEXT NOT NULL,
    evidence_window_days          SMALLINT NOT NULL DEFAULT 14,
    started_at                    TIMESTAMPTZ NOT NULL,
    finished_at                   TIMESTAMPTZ,
    status                        TEXT NOT NULL,
    row_counts                    JSONB,
    error_message                 TEXT
);

CREATE TABLE IF NOT EXISTS gold_control.daily_snapshot (
    snapshot_date     DATE PRIMARY KEY,
    gold_release_id   TEXT,
    silver_release_id TEXT,
    status            TEXT NOT NULL,
    silver_ready_at   TIMESTAMPTZ,
    gold_published_at TIMESTAMPTZ,
    bi_refreshed_at   TIMESTAMPTZ,
    sla_breached      BOOLEAN NOT NULL DEFAULT FALSE,
    notes             TEXT
);

CREATE TABLE IF NOT EXISTS gold_control.active_release (
    id               SMALLINT PRIMARY KEY DEFAULT 1 CHECK (id = 1),
    gold_release_id  TEXT NOT NULL,
    snapshot_date    DATE NOT NULL,
    switched_at      TIMESTAMPTZ NOT NULL,
    previous_release TEXT
);

CREATE TABLE IF NOT EXISTS gold_control.dq_result (
    gold_release_id TEXT NOT NULL,
    check_code      TEXT NOT NULL,
    check_name      TEXT NOT NULL,
    expected        TEXT,
    actual          TEXT,
    diff            TEXT,
    severity        TEXT NOT NULL,
    passed          BOOLEAN NOT NULL,
    checked_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (gold_release_id, check_code)
);

-- ------------------------------------------------------------ staging

CREATE TABLE IF NOT EXISTS gold_staging.effective_entity (
    silver_release_id         TEXT NOT NULL,
    unified_job_id            TEXT NOT NULL,
    canonical_title           TEXT NOT NULL,
    canonical_company         TEXT NOT NULL,
    canonical_location        TEXT,
    canonical_salary_min_vnd  BIGINT,
    canonical_salary_max_vnd  BIGINT,
    canonical_currency        TEXT,
    canonical_description     TEXT,
    canonical_posted_date     TEXT,
    canonical_job_level       TEXT,
    canonical_work_mode       TEXT,
    entity_status             TEXT NOT NULL,
    source_count              INTEGER NOT NULL,
    observation_count         INTEGER NOT NULL,
    first_seen_at             TEXT NOT NULL,
    last_seen_at              TEXT NOT NULL,
    resolution_version        TEXT NOT NULL,
    source_object_key         TEXT,
    PRIMARY KEY (silver_release_id, unified_job_id)
);

CREATE TABLE IF NOT EXISTS gold_staging.effective_link (
    silver_release_id  TEXT NOT NULL,
    observation_id     TEXT NOT NULL,
    unified_job_id     TEXT,
    platform           TEXT NOT NULL,
    source_job_id      TEXT NOT NULL,
    match_method       TEXT NOT NULL,
    match_status       TEXT NOT NULL,
    match_score        DOUBLE PRECISION,
    matched_at         TEXT NOT NULL,
    resolution_version TEXT NOT NULL,
    is_primary_source  BOOLEAN NOT NULL DEFAULT FALSE,
    accepted_for_gold  BOOLEAN NOT NULL,
    source_object_key  TEXT,
    PRIMARY KEY (silver_release_id, observation_id)
);

CREATE TABLE IF NOT EXISTS gold_staging.source_observation (
    silver_release_id     TEXT NOT NULL,
    observation_id        TEXT NOT NULL,
    platform              TEXT NOT NULL,
    source_job_id         TEXT NOT NULL,
    unified_job_id        TEXT,
    observed_at           TEXT NOT NULL,
    job_title_normalized  TEXT,
    company_name_normalized TEXT,
    city_normalized       TEXT,
    job_domain            TEXT,
    job_level             TEXT,
    work_mode             TEXT,
    salary_min_vnd        BIGINT,
    salary_max_vnd        BIGINT,
    skills_standardized   JSONB,
    source_object_key     TEXT,
    PRIMARY KEY (silver_release_id, observation_id)
);

CREATE TABLE IF NOT EXISTS gold_staging.conflict_report (
    gold_release_id          TEXT NOT NULL,
    conflict_type            TEXT NOT NULL,
    entity_or_observation_id TEXT NOT NULL,
    detail                   JSONB,
    detected_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (gold_release_id, conflict_type, entity_or_observation_id)
);

-- Job ad text lives apart from the dimensions: the BI reads facets from dim_job, while the
-- detail page needs the ad body. Keyed by release so an old release can be inspected later.
CREATE TABLE IF NOT EXISTS gold.job_detail (
    gold_release_id  TEXT NOT NULL,
    unified_job_id   TEXT NOT NULL,
    description      TEXT,
    requirements     TEXT,
    benefits         TEXT,
    expired_date     DATE,
    posted_date      DATE,
    source_url       TEXT,
    loaded_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (gold_release_id, unified_job_id)
);
CREATE INDEX IF NOT EXISTS idx_job_detail_job ON gold.job_detail(unified_job_id);

-- Serving query joins the platform fact by job_key alone, which the composite PK cannot serve.
CREATE INDEX IF NOT EXISTS idx_platform_daily_job ON gold.fact_job_platform_daily(job_key);

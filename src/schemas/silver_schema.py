"""PyArrow contracts for Silver observations and quarantine records."""
from __future__ import annotations

import pyarrow as pa

OBSERVATION_FIELDS = [
    ("source_job_id", pa.string(), False), ("observation_id", pa.string(), False),
    ("platform", pa.string(), False), ("source_url", pa.string(), True),
    ("canonical_url", pa.string(), True), ("content_hash", pa.string(), True),
    ("crawled_at", pa.string(), False), ("observed_at", pa.string(), False),
    ("ingested_at", pa.string(), False), ("silver_updated_at", pa.string(), False),
    ("is_active", pa.bool_(), False), ("job_title", pa.string(), False),
    ("job_title_normalized", pa.string(), True), ("job_level", pa.string(), True),
    ("job_expertise", pa.string(), True), ("job_domain", pa.string(), True),
    ("work_mode", pa.string(), True), ("working_time", pa.string(), True),
    ("overtime_policy", pa.string(), True), ("company_name", pa.string(), False),
    ("company_name_raw", pa.string(), True), ("company_name_normalized", pa.string(), True),
    ("company_size", pa.string(), True), ("company_industry", pa.string(), True),
    ("company_type", pa.string(), True), ("company_country", pa.string(), True),
    ("city", pa.string(), True), ("city_normalized", pa.string(), True),
    ("full_address", pa.string(), True), ("salary_raw", pa.string(), True),
    ("salary_min_vnd", pa.int64(), True), ("salary_max_vnd", pa.int64(), True),
    ("is_negotiable", pa.bool_(), False), ("salary_currency", pa.string(), True),
    ("exp_years_min", pa.float64(), True), ("exp_years_max", pa.float64(), True),
    ("vacancies_count", pa.int64(), False), ("posted_date", pa.string(), True),
    ("expired_date", pa.string(), True), ("skills_raw", pa.list_(pa.string()), True),
    ("skills_standardized", pa.list_(pa.string()), True),
    ("job_description_clean", pa.string(), True), ("description_normalized", pa.string(), True),
    ("requirements_clean", pa.string(), True), ("benefits_clean", pa.string(), True),
    ("platform_specific_attributes", pa.string(), True),
]

SILVER_OBSERVATIONS_SCHEMA = pa.schema([
    pa.field(name, dtype, nullable=nullable) for name, dtype, nullable in OBSERVATION_FIELDS
])

QUARANTINE_SCHEMA = pa.schema([
    *SILVER_OBSERVATIONS_SCHEMA,
    pa.field("rejection_reason", pa.string(), nullable=False),
    pa.field("quarantined_at", pa.string(), nullable=False),
    pa.field("audit_run_id", pa.string(), nullable=False),
    pa.field("raw_payload_snippet", pa.string(), nullable=True),
])

assert len(SILVER_OBSERVATIONS_SCHEMA) == 46
assert len(QUARANTINE_SCHEMA) == 50

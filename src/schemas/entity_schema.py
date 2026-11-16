"""PyArrow contract for canonical Silver entities."""
from __future__ import annotations

import pyarrow as pa

SILVER_ENTITY_SCHEMA = pa.schema([
    pa.field("unified_job_id", pa.string(), nullable=False),
    pa.field("canonical_title", pa.string(), nullable=False),
    pa.field("canonical_company", pa.string(), nullable=False),
    pa.field("canonical_location", pa.string(), nullable=True),
    pa.field("canonical_salary_min_vnd", pa.int64(), nullable=True),
    pa.field("canonical_salary_max_vnd", pa.int64(), nullable=True),
    pa.field("canonical_currency", pa.string(), nullable=True),
    pa.field("canonical_description", pa.string(), nullable=True),
    pa.field("canonical_posted_date", pa.string(), nullable=True),
    pa.field("source_count", pa.int64(), nullable=False),
    pa.field("observation_count", pa.int64(), nullable=False),
    pa.field("first_seen_at", pa.string(), nullable=False),
    pa.field("last_seen_at", pa.string(), nullable=False),
    pa.field("entity_status", pa.string(), nullable=False),
    pa.field("canonical_updated_at", pa.string(), nullable=False),
    pa.field("resolution_version", pa.string(), nullable=False),
])

assert len(SILVER_ENTITY_SCHEMA) == 16

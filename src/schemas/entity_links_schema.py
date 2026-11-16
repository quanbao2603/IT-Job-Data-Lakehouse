"""PyArrow contract for Silver entity-to-observation links."""
from __future__ import annotations

import pyarrow as pa

SILVER_ENTITY_LINKS_SCHEMA = pa.schema([
    pa.field("unified_job_id", pa.string(), nullable=True),
    pa.field("candidate_entity_id", pa.string(), nullable=True),
    pa.field("observation_id", pa.string(), nullable=False),
    pa.field("platform", pa.string(), nullable=False),
    pa.field("source_job_id", pa.string(), nullable=False),
    pa.field("source_url", pa.string(), nullable=True),
    pa.field("canonical_url", pa.string(), nullable=True),
    pa.field("match_method", pa.string(), nullable=False),
    pa.field("match_score", pa.float64(), nullable=True),
    pa.field("match_status", pa.string(), nullable=False),
    pa.field("matched_at", pa.string(), nullable=False),
    pa.field("matcher_version", pa.string(), nullable=False),
    pa.field("review_reason", pa.string(), nullable=True),
    pa.field("previous_unified_job_id", pa.string(), nullable=True),
    pa.field("resolution_version", pa.string(), nullable=False),
    pa.field("is_primary_source", pa.bool_(), nullable=False),
])

assert len(SILVER_ENTITY_LINKS_SCHEMA) == 16

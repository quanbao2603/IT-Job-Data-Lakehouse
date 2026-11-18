"""Gold business contract: the constants every loader must agree on.

These values come from docs/gold-design-stages.md decisions D-G01..D-G07. Keeping them
in one module means a business rule change is one edit, not a hunt across loaders.
"""
from __future__ import annotations

GOLD_TIMEZONE = "Asia/Ho_Chi_Minh"

# D-G02: only these link decisions may attach an observation to a canonical job.
ACCEPTED_MATCH_STATUSES = ("AUTO_MATCH", "MANUAL_MATCH", "DISTINCT_ENTITY")
REJECTED_MATCH_STATUSES = ("REVIEW_REQUIRED", "UNMATCHED")

# D-G05: open_job_count is derived from canonical entity_status only.
# EXPIRED is excluded because expire_entities() only promotes CLOSED/STALE,
# and MERGED is excluded because counting it would double-count the target entity.
OPEN_ENTITY_STATUSES = ("NEW", "ACTIVE", "UPDATED", "REOPENED", "STALE")
NON_OPEN_ENTITY_STATUSES = ("CLOSED", "EXPIRED", "MERGED", "SPLIT")

# GAP-9: normalize_city() emits only four values; UNKNOWN means the source had no city.
# "Khác" means a real, identified province outside the three large cities.
CITY_AXIS = ("Hà Nội", "Hồ Chí Minh", "Đà Nẵng", "Khác", "UNKNOWN")
UNKNOWN = "UNKNOWN"

# D-G03: how recent an observation must be to count as platform evidence.
EVIDENCE_WINDOW_DAYS = 14

# Nine platforms the Bronze layer crawls; dim_platform is seeded from this list.
PLATFORMS = (
    "careerviet", "upzi", "vietnamdevs", "vieclam24h", "devwork",
    "itviec", "topcv", "topdev", "vietnamworks",
)

# Business fields compared between two observations of the same source job.
# content_hash is deliberately NOT in this list: it changes for cosmetic edits too.
CHANGE_TRACKED_FIELDS = (
    "job_title_normalized",
    "salary_min_vnd",
    "salary_max_vnd",
    "city_normalized",
    "work_mode",
    "skills_standardized",
)

SALARY_MIDPOINT_MIN_VND = 0
SMALL_SAMPLE_THRESHOLD = 5


def is_open_status(entity_status: str | None) -> bool:
    """D-G05 gate: only canonical lifecycle statuses count as an open job."""
    return str(entity_status or "") in OPEN_ENTITY_STATUSES


def city_bucket(city_normalized: str | None) -> str:
    """Map a Silver city value onto the fixed five-group Gold axis."""
    value = str(city_normalized or "").strip()
    if not value:
        return UNKNOWN
    return value if value in CITY_AXIS else UNKNOWN


def accepted_link(match_status: str | None, unified_job_id: str | None) -> bool:
    """D-G02: a link only contributes when accepted AND it actually names a job."""
    return str(match_status or "") in ACCEPTED_MATCH_STATUSES and bool(unified_job_id)

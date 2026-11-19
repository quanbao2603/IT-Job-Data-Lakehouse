"""Gold persistence and release publishing.

Loading and publishing are deliberately separate. A release is loaded under its own
gold_release_id first, then gated by data quality, and only a passing release flips the
single-row `active_release` pointer. BI reads through views bound to that pointer, so a
failed release can never expose partial data and can never be mixed with another one.
"""
from __future__ import annotations

import json
from typing import Any, Iterable, Optional

from src.gold.data_quality import blocking_failures, summarize

# Column order must match src/gold/ddl.sql exactly.
DIM_SPECS: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "gold.dim_date": (
        ("date_key", "full_date", "year", "quarter", "month", "week_of_year",
         "day_of_week", "is_weekend", "timezone_name"),
        ("date_key",),
    ),
    "gold.dim_company": (
        ("company_key", "company_name_normalized", "company_display_name", "company_alias_raw",
         "company_industry", "company_size", "normalization_version", "is_unknown"),
        ("company_key",),
    ),
    "gold.dim_platform": (
        ("platform_key", "platform_code", "platform_name", "is_active", "is_unknown"),
        ("platform_key",),
    ),
    "gold.dim_skill": (
        ("skill_key", "taxonomy_version", "skill_code", "skill_name", "skill_group", "is_unknown"),
        ("skill_key",),
    ),
    "gold.dim_job": (
        ("job_key", "unified_job_id", "version_seq", "canonical_title", "company_key",
         "canonical_location", "city_bucket", "job_domain", "job_level", "work_mode",
         "canonical_salary_min_vnd", "canonical_salary_max_vnd", "canonical_currency",
         "canonical_posted_date", "entity_status", "representative_observation_id",
         "representative_fallback", "valid_from_release", "valid_to_release",
         "valid_from_at", "valid_to_at", "is_current"),
        ("job_key",),
    ),
}

FACT_SPECS: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "gold.fact_job_daily_snapshot": (
        ("gold_release_id", "snapshot_date", "date_key", "unified_job_id", "job_key", "company_key",
         "posted_date_key", "job_count", "open_job_count", "newly_seen_job_count", "entity_status",
         "canonical_salary_min_vnd", "canonical_salary_max_vnd", "salary_midpoint_vnd",
         "salary_sample_flag", "source_count", "observation_count", "last_seen_at",
         "snapshot_cutoff_at", "silver_release_id"),
        ("gold_release_id", "snapshot_date", "unified_job_id"),
    ),
    "gold.fact_job_platform_daily": (
        ("gold_release_id", "snapshot_date", "date_key", "unified_job_id", "job_key", "platform_key",
         "company_key", "job_platform_count", "source_listing_count", "observation_count_in_window",
         "last_observed_at", "freshness_days", "evidence_window_days", "snapshot_cutoff_at",
         "silver_release_id"),
        ("gold_release_id", "snapshot_date", "unified_job_id", "platform_key"),
    ),
    "gold.fact_job_skill_daily": (
        ("gold_release_id", "snapshot_date", "date_key", "unified_job_id", "job_key", "skill_key",
         "taxonomy_version", "job_skill_count", "supporting_platform_count", "latest_observed_at",
         "snapshot_cutoff_at", "silver_release_id"),
        ("gold_release_id", "snapshot_date", "unified_job_id", "skill_key"),
    ),
    "gold.fact_job_change_event": (
        ("gold_release_id", "event_date", "event_date_key", "platform_key", "source_job_id",
         "observation_id", "previous_observation_id", "unified_job_id", "job_key", "company_key",
         "change_event_count", "title_changed", "salary_changed", "location_changed",
         "work_mode_changed", "skills_changed", "salary_old_vnd", "salary_new_vnd",
         "salary_delta_vnd", "skill_added_count", "skill_removed_count", "link_resolved",
         "is_revised", "observed_at", "snapshot_cutoff_at", "silver_release_id"),
        ("gold_release_id", "platform_key", "source_job_id", "observation_id"),
    ),
    "gold.fact_job_market_daily": (
        ("gold_release_id", "snapshot_date", "date_key", "job_domain", "city_bucket", "job_level",
         "observed_job_count", "newly_seen_job_count", "open_job_count", "salary_sample_count",
         "salary_p25_vnd", "salary_p50_vnd", "salary_p75_vnd", "salary_avg_vnd",
         "snapshot_cutoff_at", "silver_release_id"),
        ("gold_release_id", "snapshot_date", "job_domain", "city_bucket", "job_level"),
    ),
    "gold.fact_company_hiring_daily": (
        ("gold_release_id", "snapshot_date", "date_key", "company_key", "observed_job_count",
         "newly_seen_job_count", "open_job_count", "distinct_city_count", "distinct_domain_count",
         "salary_sample_count", "snapshot_cutoff_at", "silver_release_id"),
        ("gold_release_id", "snapshot_date", "company_key"),
    ),
    "gold.fact_skill_pair_daily": (
        ("gold_release_id", "snapshot_date", "date_key", "skill_a_key", "skill_b_key",
         "taxonomy_version", "co_mentioned_job_count", "snapshot_cutoff_at", "silver_release_id"),
        ("gold_release_id", "snapshot_date", "skill_a_key", "skill_b_key"),
    ),
}

STAGING_SPECS: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "gold_staging.effective_entity": (
        ("silver_release_id", "unified_job_id", "canonical_title", "canonical_company",
         "canonical_location", "canonical_salary_min_vnd", "canonical_salary_max_vnd",
         "canonical_currency", "canonical_description", "canonical_posted_date",
         "canonical_job_level", "canonical_work_mode", "entity_status", "source_count",
         "observation_count", "first_seen_at", "last_seen_at", "resolution_version",
         "source_object_key"),
        ("silver_release_id", "unified_job_id"),
    ),
    "gold_staging.effective_link": (
        ("silver_release_id", "observation_id", "unified_job_id", "platform", "source_job_id",
         "match_method", "match_status", "match_score", "matched_at", "resolution_version",
         "is_primary_source", "accepted_for_gold", "source_object_key"),
        ("silver_release_id", "observation_id"),
    ),
    "gold_staging.source_observation": (
        ("silver_release_id", "observation_id", "platform", "source_job_id", "unified_job_id",
         "observed_at", "job_title_normalized", "company_name_normalized", "city_normalized",
         "job_domain", "job_level", "work_mode", "salary_min_vnd", "salary_max_vnd",
         "skills_standardized", "source_object_key"),
        ("silver_release_id", "observation_id"),
    ),
    "gold_staging.conflict_report": (
        ("gold_release_id", "conflict_type", "entity_or_observation_id", "detail"),
        ("gold_release_id", "conflict_type", "entity_or_observation_id"),
    ),
}

# Bundle attribute feeding each table.
BUNDLE_MAP: dict[str, str] = {
    "gold.dim_date": "dim_date",
    "gold.dim_company": "dim_company",
    "gold.dim_platform": "dim_platform",
    "gold.dim_skill": "dim_skill",
    "gold.dim_job": "dim_job",
    "gold.fact_job_daily_snapshot": "snapshot",
    "gold.fact_job_platform_daily": "platform",
    "gold.fact_job_skill_daily": "skill",
    "gold.fact_job_change_event": "change_event",
    "gold.fact_job_market_daily": "market",
    "gold.fact_company_hiring_daily": "company",
    "gold.fact_skill_pair_daily": "skill_pair",
}

# Load order is contractual: dimensions, then snapshot, then the facts that reference it.
LOAD_ORDER: tuple[str, ...] = (
    "gold.dim_date", "gold.dim_platform", "gold.dim_company", "gold.dim_skill", "gold.dim_job",
    "gold.fact_job_daily_snapshot", "gold.fact_job_platform_daily", "gold.fact_job_skill_daily",
    "gold.fact_job_change_event", "gold.fact_job_market_daily",
    "gold.fact_company_hiring_daily", "gold.fact_skill_pair_daily",
)

# Delete children before their release-scoped parent rows. PostgreSQL enforces the
# composite FKs from platform/skill facts to fact_job_daily_snapshot during retries.
FACT_DELETE_ORDER: tuple[str, ...] = (
    "gold.fact_skill_pair_daily",
    "gold.fact_company_hiring_daily",
    "gold.fact_job_market_daily",
    "gold.fact_job_change_event",
    "gold.fact_job_skill_daily",
    "gold.fact_job_platform_daily",
    "gold.fact_job_daily_snapshot",
)


def _jsonb(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def _row_values(row: dict[str, Any], columns: Iterable[str]) -> tuple:
    values = []
    for column in columns:
        value = row.get(column)
        if isinstance(value, (list, tuple, set, dict)):
            value = _jsonb(list(value) if isinstance(value, set) else value)
        values.append(value)
    return tuple(values)


def _upsert(cursor: Any, table: str, columns: tuple[str, ...], conflict: tuple[str, ...],
            rows: Iterable[dict[str, Any]]) -> int:
    rows = list(rows)
    if not rows:
        return 0
    placeholders = ", ".join(["%s"] * len(columns))
    updates = ", ".join(f"{column}=EXCLUDED.{column}" for column in columns if column not in conflict)
    statement = (
        f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({placeholders}) "
        f"ON CONFLICT ({', '.join(conflict)}) DO UPDATE SET {updates}"
    )
    cursor.executemany(statement, [_row_values(row, columns) for row in rows])
    return len(rows)


def load_bundle(connection: Any, bundle: Any, *, include_staging: bool = True) -> dict[str, int]:
    """Persist one Gold release. Re-running the same release is idempotent."""
    counts: dict[str, int] = {}
    with connection.cursor() as cursor:
        # Close the old SCD2 version before inserting the replacement. The partial
        # unique index on dim_job(unified_job_id) WHERE is_current rejects two current
        # rows, so the order is part of the database contract.
        for job_key_row in bundle.dim_job_closes:
            cursor.execute(
                "UPDATE gold.dim_job SET valid_to_release = %s, valid_to_at = %s, "
                "is_current = FALSE WHERE job_key = %s",
                (job_key_row["valid_to_release"], job_key_row["valid_to_at"], job_key_row["job_key"]),
            )
            counts["gold.dim_job_closed"] = counts.get("gold.dim_job_closed", 0) + 1
        for table in FACT_DELETE_ORDER:
            cursor.execute(f"DELETE FROM {table} WHERE gold_release_id = %s", (bundle.gold_release_id,))
        for table in LOAD_ORDER:
            columns, conflict = (DIM_SPECS.get(table) or FACT_SPECS[table])
            rows = getattr(bundle, BUNDLE_MAP[table])
            if table in FACT_SPECS:
                # Facts were deleted in dependency order above; this upsert is now safe
                # for a retry even when composite child FKs reference the snapshot.
                counts[table] = _upsert(cursor, table, columns, conflict, rows)
            else:
                counts[table] = _upsert(cursor, table, columns, conflict, rows)

        if include_staging:
            columns, conflict = STAGING_SPECS["gold_staging.effective_entity"]
            cursor.execute(
                "DELETE FROM gold_staging.effective_entity WHERE silver_release_id = %s",
                (bundle.silver_release_id,),
            )
            counts["gold_staging.effective_entity"] = _upsert(
                cursor, "gold_staging.effective_entity", columns, conflict,
                [{**row, "silver_release_id": bundle.silver_release_id} for row in bundle.effective_entity],
            )
            columns, conflict = STAGING_SPECS["gold_staging.effective_link"]
            cursor.execute(
                "DELETE FROM gold_staging.effective_link WHERE silver_release_id = %s",
                (bundle.silver_release_id,),
            )
            counts["gold_staging.effective_link"] = _upsert(
                cursor, "gold_staging.effective_link", columns, conflict,
                [{**row, "silver_release_id": bundle.silver_release_id} for row in bundle.effective_link],
            )
            columns, conflict = STAGING_SPECS["gold_staging.source_observation"]
            cursor.execute(
                "DELETE FROM gold_staging.source_observation WHERE silver_release_id = %s",
                (bundle.silver_release_id,),
            )
            counts["gold_staging.source_observation"] = _upsert(
                cursor, "gold_staging.source_observation", columns, conflict,
                [{**row, "silver_release_id": bundle.silver_release_id}
                 for row in bundle.source_observation],
            )
            columns, conflict = STAGING_SPECS["gold_staging.conflict_report"]
            cursor.execute(
                "DELETE FROM gold_staging.conflict_report WHERE gold_release_id = %s",
                (bundle.gold_release_id,),
            )
            counts["gold_staging.conflict_report"] = _upsert(
                cursor, "gold_staging.conflict_report", columns, conflict,
                [{
                    "gold_release_id": bundle.gold_release_id,
                    "conflict_type": conflict_row["conflict_type"],
                    "entity_or_observation_id": conflict_row["identifier"],
                    "detail": _jsonb(conflict_row.get("detail")),
                } for conflict_row in bundle.conflicts],
            )
    connection.commit()
    return counts


def load_current_dim_job(connection: Any) -> dict[str, dict[str, Any]]:
    """Read current SCD2 versions so a later release can close/open versions safely."""
    with connection.cursor() as cursor:
        cursor.execute("SELECT * FROM gold.dim_job WHERE is_current")
        columns = [description[0] for description in cursor.description]
        return {
            str(row[columns.index("unified_job_id")]): dict(zip(columns, row))
            for row in cursor.fetchall()
        }


def record_dq(connection: Any, gold_release_id: str, results: Iterable[dict[str, Any]]) -> int:
    with connection.cursor() as cursor:
        cursor.execute("DELETE FROM gold_control.dq_result WHERE gold_release_id = %s", (gold_release_id,))
        rows = [
            (gold_release_id, row["check_code"], row["check_name"], row["expected"],
             row["actual"], row["diff"], row["severity"], row["passed"])
            for row in results
        ]
        cursor.executemany(
            "INSERT INTO gold_control.dq_result (gold_release_id, check_code, check_name, expected, "
            "actual, diff, severity, passed) VALUES (%s, %s, %s, %s, %s, %s, %s, %s) "
            "ON CONFLICT (gold_release_id, check_code) DO UPDATE SET "
            "expected=EXCLUDED.expected, actual=EXCLUDED.actual, diff=EXCLUDED.diff, "
            "severity=EXCLUDED.severity, passed=EXCLUDED.passed, checked_at=now()",
            rows,
        )
    connection.commit()
    return len(rows)


def record_load_run(connection: Any, bundle: Any, status: str, row_counts: dict[str, int],
                    started_at: Any, error_message: Optional[str] = None,
                    *, finished: bool = True) -> None:
    versions = bundle.taxonomy_versions
    with connection.cursor() as cursor:
        cursor.execute(
            "INSERT INTO gold_control.load_run (gold_release_id, silver_release_id, "
            "silver_manifest_fingerprint, snapshot_date, snapshot_cutoff_at, transform_version, "
            "skill_taxonomy_version, company_normalization_version, city_normalization_version, "
            "evidence_window_days, started_at, finished_at, status, row_counts, error_message) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, "
            "CASE WHEN %s THEN now() ELSE NULL END, %s, %s, %s) "
            "ON CONFLICT (gold_release_id) DO UPDATE SET finished_at=EXCLUDED.finished_at, status=EXCLUDED.status, "
            "row_counts=EXCLUDED.row_counts, error_message=EXCLUDED.error_message",
            (bundle.gold_release_id, bundle.silver_release_id, bundle.manifest_fingerprint,
             bundle.snapshot_date, bundle.cutoff_at,
             versions.get("transform_version", "unknown"),
             versions.get("skill_taxonomy_version", "unknown"),
             versions.get("company_normalization_version", "unknown"),
             versions.get("city_normalization_version", "unknown"),
             bundle.evidence_window_days, started_at, finished, status, _jsonb(row_counts), error_message),
        )
    connection.commit()


def publish(connection: Any, bundle: Any, results: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """Flip the BI pointer, but only for a release that passed every BLOCK check.

    The pointer update and its control rows commit in one transaction, which is the
    property Iceberg on MinIO cannot offer across twelve tables.
    """
    result_list = list(results)
    summary = summarize(result_list)
    failures = blocking_failures(result_list)
    if not summary["publish_allowed"]:
        record_load_run(connection, bundle, "FAILED", bundle.row_counts(), bundle.cutoff_at,
                        error_message="; ".join(row["check_code"] for row in failures))
        with connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO gold_control.daily_snapshot (snapshot_date, gold_release_id, "
                "silver_release_id, status, notes) VALUES (%s, %s, %s, 'FAILED', %s) "
                "ON CONFLICT (snapshot_date) DO UPDATE SET gold_release_id=EXCLUDED.gold_release_id, "
                "silver_release_id=EXCLUDED.silver_release_id, status='FAILED', notes=EXCLUDED.notes",
                (bundle.snapshot_date, bundle.gold_release_id, bundle.silver_release_id,
                 "; ".join(row["check_code"] for row in failures)),
            )
        connection.commit()
        return {"status": "BLOCKED", "summary": summary,
                "blocking_checks": [row["check_code"] for row in failures]}

    with connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT gold_release_id FROM gold_control.active_release WHERE id = 1")
            existing = cursor.fetchone()
            previous = existing[0] if existing else None
            cursor.execute(
                "INSERT INTO gold_control.active_release (id, gold_release_id, snapshot_date, "
                "switched_at, previous_release) VALUES (1, %s, %s, now(), %s) "
                "ON CONFLICT (id) DO UPDATE SET gold_release_id=EXCLUDED.gold_release_id, "
                "snapshot_date=EXCLUDED.snapshot_date, switched_at=now(), "
                "previous_release=EXCLUDED.previous_release",
                (bundle.gold_release_id, bundle.snapshot_date, previous),
            )
            cursor.execute(
                "INSERT INTO gold_control.daily_snapshot (snapshot_date, gold_release_id, "
                "silver_release_id, status, silver_ready_at, gold_published_at) "
                "VALUES (%s, %s, %s, 'LOADED', %s, now()) "
                "ON CONFLICT (snapshot_date) DO UPDATE SET gold_release_id=EXCLUDED.gold_release_id, "
                "silver_release_id=EXCLUDED.silver_release_id, status='LOADED', "
                "silver_ready_at=EXCLUDED.silver_ready_at, gold_published_at=now()",
                (bundle.snapshot_date, bundle.gold_release_id, bundle.silver_release_id,
                 bundle.cutoff_at),
            )
            cursor.execute(
                "INSERT INTO gold_control.load_run (gold_release_id, silver_release_id, "
                "silver_manifest_fingerprint, snapshot_date, snapshot_cutoff_at, transform_version, "
                "skill_taxonomy_version, company_normalization_version, city_normalization_version, "
                "evidence_window_days, started_at, finished_at, status, row_counts) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, now(), 'SUCCESS', %s) "
                "ON CONFLICT (gold_release_id) DO UPDATE SET finished_at=now(), status='SUCCESS', "
                "row_counts=EXCLUDED.row_counts, error_message=NULL",
                (bundle.gold_release_id, bundle.silver_release_id, bundle.manifest_fingerprint,
                 bundle.snapshot_date, bundle.cutoff_at,
                 bundle.taxonomy_versions.get("transform_version", "unknown"),
                 bundle.taxonomy_versions.get("skill_taxonomy_version", "unknown"),
                 bundle.taxonomy_versions.get("company_normalization_version", "unknown"),
                 bundle.taxonomy_versions.get("city_normalization_version", "unknown"),
                 bundle.evidence_window_days, bundle.cutoff_at, _jsonb(bundle.row_counts())),
            )
    return {"status": "PUBLISHED", "summary": summary,
            "gold_release_id": bundle.gold_release_id, "previous_release": previous}


def mark_missing_day(connection: Any, snapshot_date: Any, notes: str = "") -> None:
    """A day without a valid Silver release is MISSING; it is never written as zero KPIs."""
    with connection.cursor() as cursor:
        cursor.execute(
            "INSERT INTO gold_control.daily_snapshot (snapshot_date, status, notes) "
            "VALUES (%s, 'MISSING', %s) "
            "ON CONFLICT (snapshot_date) DO UPDATE SET status='MISSING', notes=EXCLUDED.notes",
            (snapshot_date, notes),
        )
    connection.commit()

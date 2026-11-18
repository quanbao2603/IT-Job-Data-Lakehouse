"""Build and publish one Gold release from the newest sealed Silver release.

Exit contract mirrors the crawlers: one machine-readable JSON line, and exit code 0
only when a release was actually published. A MISSING day or a BLOCKED release is a
non-zero exit so Airflow surfaces it instead of reporting a false success.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from typing import Any, Optional


def _apply_sql_file(connection: Any, path: str) -> int:
    from src.gold.connection import split_sql_script

    with open(path, encoding="utf-8") as handle:
        script = handle.read()
    statements = split_sql_script(script)
    with connection.cursor() as cursor:
        for statement in statements:
            cursor.execute(statement)
    connection.commit()
    return len(statements)


def build_gold_release(
    *,
    silver_root: str,
    state_db_path: str,
    gold_release_id: Optional[str] = None,
    publish: bool = True,
    apply_schema: bool = True,
) -> dict[str, Any]:
    """Run the whole Gold path: read release, build bundle, gate on DQ, publish."""
    from src.gold.connection import apply_ddl, connect
    from src.gold.data_quality import blocking_failures, run_checks, summarize
    from src.gold.pipeline import build_bundle
    from src.gold.release_controller import (
        load_bundle,
        load_current_dim_job,
        mark_missing_day,
        publish as publish_release,
        record_dq,
        record_load_run,
    )
    from src.gold.silver_release_reader import latest_release
    from src.gold.timeutil import report_date
    from src.storage.entity_registry import EntityRegistry

    started_at = datetime.now(timezone.utc).isoformat()
    release = latest_release(silver_root)
    if release is None:
        # A day without a valid Silver release is MISSING; it is never published as zeros.
        result = {
            "status": "MISSING",
            "reason": "no consumable Silver release (COMPLETE, not partial, no failed platform)",
            "silver_root": silver_root,
        }
        try:
            connection = connect()
        except Exception as exc:  # noqa: BLE001 - a missing DB must not hide the MISSING day
            result["daily_snapshot_recorded"] = False
            result["database_error"] = type(exc).__name__
            return result
        try:
            mark_missing_day(connection, report_date(started_at), notes=result["reason"])
            result["daily_snapshot_recorded"] = True
        finally:
            connection.close()
        return result

    connection = connect()
    try:
        if apply_schema:
            apply_ddl(connection)
            views = os.path.join(os.path.dirname(os.path.abspath(__file__)), "semantic_views.sql")
            _apply_sql_file(connection, views)
        registry = EntityRegistry(state_db_path)
        bundle = build_bundle(
            root=silver_root,
            release=release,
            registry_entities=[dict(row) for row in registry.list_entities()],
            gold_release_id=gold_release_id,
            current_dim_job=load_current_dim_job(connection),
        )
        results = run_checks(bundle)
        summary = summarize(results)
        blocked = [row["check_code"] for row in blocking_failures(results)]
        record_load_run(connection, bundle, "RUNNING", {}, started_at, finished=False)
        row_counts = load_bundle(connection, bundle)
        record_dq(connection, bundle.gold_release_id, results)
        if not publish:
            record_load_run(
                connection, bundle, "DRY_RUN", row_counts, started_at,
                error_message="publish disabled by --no-publish",
            )
            outcome = {"status": "DRY_RUN", "publish_allowed": summary["publish_allowed"]}
        elif summary["publish_allowed"]:
            outcome = publish_release(connection, bundle, results)
        else:
            record_load_run(connection, bundle, "FAILED", row_counts, started_at,
                            error_message="blocked by " + ",".join(blocked))
            outcome = {"status": "BLOCKED", "blocking_checks": blocked}
    except Exception as exc:
        connection.rollback()
        try:
            failed_bundle = locals().get("bundle")
            if failed_bundle is not None:
                record_load_run(
                    connection, failed_bundle, "FAILED", locals().get("row_counts", {}), started_at,
                    error_message=type(exc).__name__ + ": " + str(exc),
                )
        except Exception:
            connection.rollback()
        raise
    finally:
        connection.close()

    return {
        "status": outcome.get("status", "FAILED"),
        "gold_release_id": bundle.gold_release_id,
        "silver_release_id": bundle.silver_release_id,
        "snapshot_date": bundle.snapshot_date.isoformat(),
        "manifest_fingerprint": bundle.manifest_fingerprint,
        "row_counts": bundle.row_counts(),
        "dq_summary": summary,
        "blocking_checks": blocked,
        "conflict_count": len(bundle.conflicts),
        "blocking_checks_detail": outcome.get("blocking_checks", blocked),
        "previous_release": outcome.get("previous_release"),
    }


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Build and publish one Gold release")
    parser.add_argument("--silver-root", default=os.environ.get("LAKEHOUSE_SILVER_ROOT", "data/silver"))
    parser.add_argument("--state-db", default=os.environ.get("LAKEHOUSE_STATE_DB_PATH", "data/state_store.db"))
    parser.add_argument("--gold-release-id", default=None)
    parser.add_argument("--no-publish", action="store_true", help="build and gate only")
    parser.add_argument("--no-schema", action="store_true", help="assume DDL already applied")
    args = parser.parse_args(argv)

    result = build_gold_release(
        silver_root=args.silver_root,
        state_db_path=args.state_db,
        gold_release_id=args.gold_release_id,
        publish=not args.no_publish,
        apply_schema=not args.no_schema,
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, default=str))
    return 0 if result.get("status") == "PUBLISHED" else 1


if __name__ == "__main__":
    sys.exit(main())

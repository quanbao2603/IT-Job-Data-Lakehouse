"""Durable Silver review queue export and decision workflow."""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any

from src.storage.entity_registry import EntityRegistry

FIELDS = (
    "observation_id", "platform", "source_job_id", "candidate_entity_id",
    "match_score", "match_status", "review_reason", "resolution_version",
)


def sync_pending_review_queue(csv_path: str | Path, state_db_path: str) -> int:
    """Export unresolved registry links to a deterministic, replaceable CSV."""
    path = Path(csv_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    registry = EntityRegistry(state_db_path)
    rows = [dict(row) for row in registry.list_pending_reviews()]
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows({field: row.get(field) for field in FIELDS} for row in rows)
    temporary.replace(path)
    return len(rows)


def resolve_review(
    state_db_path: str,
    observation_id: str,
    *,
    decision: str,
    reviewer: str,
    resolution_version: str = "v1",
    candidate_entity_id: str | None = None,
    note: str = "",
) -> dict[str, Any]:
    """Apply one ACCEPT/REJECT decision and refresh the pending queue."""
    registry = EntityRegistry(state_db_path)
    result = registry.resolve_review(
        observation_id=observation_id,
        resolution_version=resolution_version,
        decision=decision,
        reviewer=reviewer,
        candidate_entity_id=candidate_entity_id,
        note=note,
    )
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Manage the Silver human-review queue")
    parser.add_argument("--state-db", default="data/state_store.db")
    parser.add_argument("--queue", default="data/operational/pending_review.csv")
    sub = parser.add_subparsers(dest="command", required=True)
    sync = sub.add_parser("sync")
    sync.set_defaults(func=lambda args: {"status": "SYNCED", "pending": sync_pending_review_queue(args.queue, args.state_db)})
    decide = sub.add_parser("decide")
    decide.add_argument("observation_id")
    decide.add_argument("--decision", choices=("ACCEPT", "REJECT"), required=True)
    decide.add_argument("--reviewer", required=True)
    decide.add_argument("--resolution-version", default="v1")
    decide.add_argument("--candidate-entity-id")
    decide.add_argument("--note", default="")
    decide.set_defaults(func=lambda args: resolve_review(
        args.state_db, args.observation_id, decision=args.decision, reviewer=args.reviewer,
        resolution_version=args.resolution_version, candidate_entity_id=args.candidate_entity_id, note=args.note,
    ))
    args = parser.parse_args()
    result = args.func(args)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Close the fuzzy-match review queue with an auditable rule instead of a human backlog.

688 links sat in REVIEW_REQUIRED because the matcher scored 0.75-0.90 — close enough to suspect a
duplicate, far enough that merging would be a guess. Left alone, the queue grows every day and no
one reads it, which is worse than either decision.

The rule: the pair is the same posting only when the normalised job title, company and city are
all identical to the candidate entity. Anything that merely looks similar stays separate —
splitting a duplicate is visible and cheap to fix, merging two different jobs is a silent,
hard-to-undo data error.

    lakehouse_env/bin/python scripts/triage_review_queue.py            # dry run, prints counts
    lakehouse_env/bin/python scripts/triage_review_queue.py --apply
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sqlite3
import sys
import unicodedata
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

REVIEWER = "auto-triage-v1"


def normalize(value: Optional[str]) -> str:
    """Case/accent/whitespace-insensitive form used only for exact comparison."""
    if not value:
        return ""
    text = unicodedata.normalize("NFD", str(value)).replace("đ", "d").replace("Đ", "D")
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Mn")
    return " ".join(text.lower().split())


def decide(
    observation: Dict[str, Any],
    candidate: Dict[str, Any],
    *,
    score: float,
) -> Tuple[str, str]:
    """Return (ACCEPT|REJECT, note) for one fuzzy pair."""
    same_company = normalize(observation.get("company_name_normalized") or observation.get("company_name")) == normalize(
        candidate.get("canonical_company")
    )
    same_title = normalize(observation.get("job_title_normalized") or observation.get("job_title")) == normalize(
        candidate.get("canonical_title")
    )
    same_city = normalize(observation.get("city_normalized") or observation.get("city")) == normalize(
        candidate.get("canonical_city")
    )
    if same_company and same_title and same_city and observation.get("company_name_normalized"):
        return "ACCEPT", f"auto: tiêu đề + công ty + địa điểm trùng khớp (score {score:.3f})"
    return "REJECT", f"auto: chỉ trùng mờ {score:.3f}, tên/địa điểm khác — giữ riêng"


def _latest_silver_release(root: Path) -> Optional[Path]:
    candidates = [p for p in root.iterdir() if p.is_dir() and p.name not in {"releases", "audit_logs", "entities", "entity_links", "observations", "quarantine"}]
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.stat().st_mtime)


def _observations(release: Path, needed: Iterable[str]) -> Dict[str, Dict[str, Any]]:
    """Read only the observations under review from the newest Silver release."""
    import pyarrow.parquet as pq

    wanted = set(needed)
    found: Dict[str, Dict[str, Any]] = {}
    pattern = str(release / "observations" / "**" / "*.parquet")
    for file_path in glob.glob(pattern, recursive=True):
        if not wanted - set(found):
            break
        table = pq.read_table(
            file_path,
            columns=["observation_id", "job_title", "job_title_normalized", "company_name_normalized", "city_normalized"],
        )
        for row in table.to_pylist():
            if row["observation_id"] in wanted:
                found[row["observation_id"]] = row
    return found


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Auto-triage the fuzzy review queue")
    parser.add_argument("--state-db", default=os.getenv("LAKEHOUSE_STATE_DB_PATH", str(PROJECT_ROOT / "data/state_store.db")))
    parser.add_argument("--silver-root", default=os.getenv("LAKEHOUSE_SILVER_ROOT", str(PROJECT_ROOT / "data/silver")))
    parser.add_argument("--apply", action="store_true", help="write decisions; default is a dry run")
    parser.add_argument("--limit", type=int, default=0, help="cap the number of decisions (0 = all)")
    args = parser.parse_args(argv)

    conn = sqlite3.connect(args.state_db)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """
        select l.observation_id, l.resolution_version, l.candidate_entity_id, l.match_score
        from silver_entity_links l
        where l.match_status = 'REVIEW_REQUIRED'
        order by l.observation_id
        """
    ).fetchall()
    if args.limit:
        rows = rows[: args.limit]
    if not rows:
        print(json.dumps({"status": "EMPTY", "pending": 0}, ensure_ascii=False))
        return 0

    release = _latest_silver_release(Path(args.silver_root))
    if release is None:
        print(json.dumps({"status": "NO_SILVER_RELEASE"}, ensure_ascii=False))
        return 1
    observations = _observations(release, [r["observation_id"] for r in rows])

    entities = {
        row["unified_job_id"]: dict(row)
        for row in conn.execute("select unified_job_id, canonical_title, canonical_company, canonical_city from entity_registry")
    }

    plan: List[Dict[str, Any]] = []
    missing = 0
    for row in rows:
        observation = observations.get(row["observation_id"])
        candidate = entities.get(row["candidate_entity_id"])
        if observation is None or candidate is None:
            missing += 1
            continue
        decision, note = decide(observation, candidate, score=float(row["match_score"] or 0))
        plan.append(
            {
                "observation_id": row["observation_id"],
                "resolution_version": row["resolution_version"],
                "candidate_entity_id": row["candidate_entity_id"],
                "decision": decision,
                "note": note,
            }
        )

    summary = {
        "status": "APPLIED" if args.apply else "DRY_RUN",
        "silver_release": release.name,
        "pending": len(rows),
        "decisions": len(plan),
        "accept": sum(1 for item in plan if item["decision"] == "ACCEPT"),
        "reject": sum(1 for item in plan if item["decision"] == "REJECT"),
        "skipped_missing_data": missing,
        "examples": plan[:5],
    }

    if args.apply and plan:
        from src.storage.entity_registry import EntityRegistry

        registry = EntityRegistry(args.state_db)
        applied = 0
        for item in plan:
            registry.resolve_review(
                observation_id=item["observation_id"],
                resolution_version=item["resolution_version"],
                decision=item["decision"],
                reviewer=REVIEWER,
                note=item["note"],
            )
            applied += 1
        summary["applied"] = applied

    print(json.dumps(summary, ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())

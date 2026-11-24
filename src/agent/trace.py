"""Internal evidence trace for the Text-to-SQL agent.

Every answer keeps the complete record of what actually ran: each guarded query with its
id, status, row count and duration, plus the filters and the release the numbers came from.
The trace is written to disk for reconciliation and is **never** part of the public chat
payload — the browser gets business results only.

Two rules make the record trustworthy:

* the SQL stored with ``status = "executed"`` is the statement the server really ran
  (post-guard, with the injected LIMIT), not the text the model proposed;
* a statement the guard rejected is stored with ``status = "blocked"`` and its error, so a
  reviewer can see that the model tried something and the server refused it.
"""
from __future__ import annotations

import json
import os
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

TRACE_ENV = "AGENT_TRACE_DIR"
DEFAULT_TRACE_DIR = Path(__file__).resolve().parents[2] / "data" / "agent_trace"


def trace_dir() -> Path:
    return Path(os.getenv(TRACE_ENV) or DEFAULT_TRACE_DIR)


def new_request_id() -> str:
    return f"req_{datetime.now(timezone.utc):%Y%m%dT%H%M%S}_{uuid.uuid4().hex[:8]}"


@dataclass
class QueryRecord:
    query_id: str
    sql: str
    status: str                      # executed | blocked | failed
    columns: list[str] = field(default_factory=list)
    row_count: int = 0
    truncated: bool = False
    duration_ms: int = 0
    error: str | None = None
    warnings: list[str] = field(default_factory=list)
    rows_preview: list[dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        payload = {
            "query_id": self.query_id,
            "sql": self.sql,
            "status": self.status,
            "columns": self.columns,
            "row_count": self.row_count,
            "truncated": self.truncated,
            "duration_ms": self.duration_ms,
            "warnings": self.warnings,
            "rows_preview": self.rows_preview,
        }
        if self.error:
            payload["error"] = self.error
        return payload


@dataclass
class Trace:
    """One answer's evidence, assembled while the agent runs."""

    request_id: str = field(default_factory=new_request_id)
    question: str = ""
    history_turns: int = 0
    started_at: float = field(default_factory=time.monotonic)
    snapshot_date: str | None = None
    snapshot_cutoff_at: str | None = None
    gold_release_id: str | None = None
    filters: dict[str, Any] = field(default_factory=dict)
    queries: list[QueryRecord] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    status: str = "started"          # answered | empty | error | timeout
    error: str | None = None
    answer_chars: int = 0

    def add_query(self, record: QueryRecord) -> QueryRecord:
        self.queries.append(record)
        return record

    def last_executed(self) -> QueryRecord | None:
        for record in reversed(self.queries):
            if record.status == "executed":
                return record
        return None

    def as_dict(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "question": self.question,
            "history_turns": self.history_turns,
            "status": self.status,
            "error": self.error,
            "snapshot_date": self.snapshot_date,
            "snapshot_cutoff_at": self.snapshot_cutoff_at,
            "gold_release_id": self.gold_release_id,
            "applied_filters": self.filters,
            "query_count": len(self.queries),
            "queries": [record.as_dict() for record in self.queries],
            "warnings": self.warnings,
            "answer_chars": self.answer_chars,
            "duration_ms": int((time.monotonic() - self.started_at) * 1000),
            "finished_at": datetime.now(timezone.utc).isoformat(),
        }

    def write(self) -> Path | None:
        """Append one JSON line. A failed write must never break an answer."""
        try:
            directory = trace_dir()
            directory.mkdir(parents=True, exist_ok=True)
            path = directory / f"agent-{datetime.now():%Y%m%d}.jsonl"
            with path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(self.as_dict(), ensure_ascii=False, default=str) + "\n")
            return path
        except Exception:  # noqa: BLE001 - evidence must not take the answer down
            return None

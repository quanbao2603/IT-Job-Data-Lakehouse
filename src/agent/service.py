"""FastAPI service exposing the Text-to-SQL agent.

Runs beside the read-only web app so the dashboard bundle stays dependency-light: the
web tier proxies /api/assistant/ask here. The service refuses to answer when no LLM key
is configured instead of pretending to be offline-capable.

Public contract (what the browser receives):

    {request_id, answer_type, answer_markdown, data_context, applied_filters, result, warnings}

The SQL, the query ids, the provider/model and the row previews stay in the trace written
to ``data/agent_trace`` for reconciliation — they are never part of this payload.
"""
from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from src.agent.agent import AgentAnswer, AgentNotConfigured, ask
from src.agent.config import get_settings
from src.agent.sanitize import scrub_answer
from src.agent.schema import ALLOWED_RELATIONS, verify_schema
from src.agent.tools import run_query
from src.agent.trace import Trace

app = FastAPI(title="IT Insight Text-to-SQL Agent", version="1.1.0", docs_url=None, redoc_url=None, openapi_url=None)

MAX_QUESTION_CHARS = 500
MAX_HISTORY_TURNS = 6
# A question may legitimately need a model round trip plus two queries. Past this the
# browser has already given up, so the server stops waiting and returns a readable error.
REQUEST_TIMEOUT_S = max(int(os.getenv("AGENT_REQUEST_TIMEOUT_S", "90")), 10)

TIMEOUT_MESSAGE = "Câu hỏi này mất nhiều thời gian hơn dự kiến. Bạn thử lại hoặc hỏi ngắn hơn nhé."
ERROR_MESSAGE = "Chưa thể tải kết quả. Bạn thử lại sau nhé."
EMPTY_MESSAGE = (
    "Mình chưa tạo được câu trả lời cho câu hỏi này. Bạn thử hỏi cụ thể hơn, "
    "ví dụ: “lương trung vị của nhóm Dữ liệu & AI là bao nhiêu?”."
)

_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="agent")


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=MAX_QUESTION_CHARS)
    history: list[dict[str, str]] = Field(default_factory=list)


class AppliedFilters(BaseModel):
    city: str | None = None
    title_keyword: str | None = None
    job_domain: str | None = None
    job_levels: list[str] | None = None
    skill: str | None = None
    company: str | None = None
    salary_only: bool | None = None
    recent_days: int | None = None


class AskResponse(BaseModel):
    request_id: str
    answer_type: str = "answer"
    answer_markdown: str
    data_context: dict[str, Any] = Field(default_factory=dict)
    applied_filters: dict[str, Any] = Field(default_factory=dict)
    result: dict[str, Any] = Field(default_factory=lambda: {"kind": "none"})
    warnings: list[str] = Field(default_factory=list)


@app.get("/health")
def health() -> dict[str, Any]:
    settings = get_settings()
    return {
        "status": "ok",
        "llm_configured": settings.configured,
        "provider": settings.provider if settings.configured else None,
        "model": settings.model if settings.configured else None,
    }


@app.get("/ready")
def ready() -> dict[str, Any]:
    """Prove the guard, the reader connection and the schema description all agree."""
    problems: list[str] = []
    try:
        from src.gold.connection import connect

        connection = connect(readonly=True)
        try:
            with connection.cursor() as cursor:
                problems = verify_schema(cursor)
        finally:
            connection.close()
    except Exception as exc:  # noqa: BLE001
        problems.append(f"database: {type(exc).__name__}: {exc}")
    probe = run_query("SELECT COUNT(*) AS jobs FROM gold.v_job_snapshot", max_rows=1)
    if probe.error:
        problems.append(f"query: {probe.error}")
    return {
        "status": "ready" if not problems else "degraded",
        "problems": problems,
        "relations": len(ALLOWED_RELATIONS),
        "jobs_visible": (probe.rows[0]["jobs"] if probe.rows else None),
    }


def _data_context() -> dict[str, Any]:
    """Snapshot context the answer was computed against (server-side, never model-written)."""
    context: dict[str, Any] = {"timezone": "Asia/Ho_Chi_Minh"}
    try:
        from src.gold.connection import connect

        connection = connect(readonly=True)
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    "select snapshot_date, snapshot_cutoff_at, gold_release_id"
                    " from gold.v_job_snapshot limit 1"
                )
                row = cursor.fetchone()
                if row:
                    context.update({
                        "snapshot_date": row[0].isoformat() if hasattr(row[0], "isoformat") else row[0],
                        "snapshot_cutoff_at": row[1].isoformat() if hasattr(row[1], "isoformat") else row[1],
                        # Kept for the trace only; stripped from the public payload below.
                        "_gold_release_id": row[2],
                    })
        finally:
            connection.close()
    except Exception:  # noqa: BLE001 - the answer still works without the header stamp
        pass
    return context


def _public_context(context: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in context.items()
            if key in {"snapshot_date", "timezone", "snapshot_cutoff_at"}}


def _respond(result: AgentAnswer, context: dict[str, Any]) -> AskResponse:
    answer = scrub_answer(result.answer) or EMPTY_MESSAGE
    public_context = _public_context(context or result.context)
    return AskResponse(
        request_id=result.request_id,
        answer_type=result.answer_type,
        answer_markdown=answer,
        data_context=public_context,
        applied_filters=result.filters,
        result=result.result,
        warnings=result.warnings,
    )


@app.post("/ask", response_model=AskResponse)
def ask_endpoint(body: AskRequest) -> AskResponse:
    settings = get_settings()
    if not settings.configured:
        raise HTTPException(
            status_code=503,
            detail={"code": "LLM_NOT_CONFIGURED", "message": ERROR_MESSAGE},
        )
    history = [
        {"role": turn.get("role", "user"), "content": str(turn.get("content", ""))[:MAX_QUESTION_CHARS]}
        for turn in body.history[-MAX_HISTORY_TURNS:]
        if turn.get("role") in {"user", "assistant"}
    ]
    context = _data_context()
    trace = Trace(question=body.question, history_turns=len(history),
                  snapshot_date=context.get("snapshot_date"),
                  snapshot_cutoff_at=context.get("snapshot_cutoff_at"),
                  gold_release_id=context.get("_gold_release_id"))

    future = _executor.submit(
        ask, body.question,
        history=history,
        settings=settings,
        snapshot_date=context.get("snapshot_date"),
        context=context,
        trace=trace,
    )
    try:
        result = future.result(timeout=REQUEST_TIMEOUT_S)
    except FutureTimeout:
        trace.status = "timeout"
        trace.error = f"deadline {REQUEST_TIMEOUT_S}s"
        trace.write()
        raise HTTPException(
            status_code=504,
            detail={"code": "REQUEST_TIMEOUT", "message": TIMEOUT_MESSAGE,
                    "request_id": trace.request_id},
        ) from None
    except AgentNotConfigured as exc:
        trace.status = "error"
        trace.error = str(exc)
        trace.write()
        raise HTTPException(status_code=503, detail={"code": "LLM_NOT_CONFIGURED",
                                                     "message": ERROR_MESSAGE}) from exc
    except Exception as exc:  # noqa: BLE001
        trace.status = "error"
        trace.error = f"{type(exc).__name__}: {exc}"
        trace.write()
        # A model or transport hiccup must read as an application error, not a fake answer.
        raise HTTPException(status_code=502, detail={"code": "AGENT_ERROR",
                                                     "message": ERROR_MESSAGE,
                                                     "request_id": trace.request_id}) from exc

    if not result.answer.strip():
        result.answer = EMPTY_MESSAGE
        trace.status = "empty"
    trace.write()
    return _respond(result, context)

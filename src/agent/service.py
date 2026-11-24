"""FastAPI service exposing the Text-to-SQL agent.

Runs beside the read-only web app so the dashboard bundle stays dependency-light: the
web tier proxies /api/assistant/ask here. The service refuses to answer when no LLM key
is configured instead of pretending to be offline-capable.
"""
from __future__ import annotations

import os
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from src.agent.agent import AgentNotConfigured, ask
from src.agent.config import get_settings
from src.agent.schema import ALLOWED_RELATIONS, verify_schema
from src.agent.tools import run_query

app = FastAPI(title="IT Insight Text-to-SQL Agent", version="1.0.0", docs_url=None, redoc_url=None, openapi_url=None)

MAX_QUESTION_CHARS = 500
MAX_HISTORY_TURNS = 6


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=MAX_QUESTION_CHARS)
    history: list[dict[str, str]] = Field(default_factory=list)


class AskResponse(BaseModel):
    answer: str
    sql: str | None = None
    rows: list[dict[str, Any]] = Field(default_factory=list)
    steps: int = 0
    provider: str = ""
    model: str = ""


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


@app.post("/ask", response_model=AskResponse)
def ask_endpoint(body: AskRequest) -> AskResponse:
    settings = get_settings()
    if not settings.configured:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "LLM_NOT_CONFIGURED",
                "message": "Chưa cấu hình GEMINI_API_KEY hoặc OPENAI_API_KEY cho trợ lý.",
            },
        )
    history = [
        {"role": turn.get("role", "user"), "content": str(turn.get("content", ""))[:MAX_QUESTION_CHARS]}
        for turn in body.history[-MAX_HISTORY_TURNS:]
        if turn.get("role") in {"user", "assistant"}
    ]
    try:
        result = ask(body.question, history=history, settings=settings,
                     snapshot_date=os.getenv("AGENT_SNAPSHOT_DATE") or None)
    except AgentNotConfigured as exc:
        raise HTTPException(status_code=503, detail={"code": "LLM_NOT_CONFIGURED", "message": str(exc)}) from exc
    except Exception as exc:  # noqa: BLE001
        # A model or transport hiccup must read as an answer, not as a silent failure:
        # the user always gets text back that explains what happened.
        return AskResponse(
            answer=(
                "Xin lỗi, mình chưa xử lý được câu này ngay bây giờ "
                f"({type(exc).__name__}). Bạn thử hỏi lại hoặc hỏi cụ thể hơn giúp mình nhé."
            ),
            sql=None,
            rows=[],
            steps=0,
            provider=settings.provider,
            model=settings.model,
        )

    if not result.answer.strip():
        # One retry with an explicit nudge beats an empty bubble.
        try:
            retry = ask(
                f"{body.question}\n\nHãy trả lời trực tiếp bằng tiếng Việt, nêu số liệu cụ thể.",
                history=history, settings=settings,
                snapshot_date=os.getenv("AGENT_SNAPSHOT_DATE") or None,
            )
            if retry.answer.strip():
                result = retry
        except Exception:  # noqa: BLE001
            pass
    if not result.answer.strip():
        result.answer = (
            "Mình chưa tạo được câu trả lời cho câu hỏi này. Bạn thử hỏi cụ thể hơn, "
            "ví dụ: “lương trung vị của nhóm Dữ liệu & AI là bao nhiêu?”."
        )
    return AskResponse(**result.as_dict())

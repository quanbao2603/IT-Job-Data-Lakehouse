"""Text-to-SQL agent built on LangChain tool calling.

The model never sees a database handle: it can only call ``run_gold_sql`` (guarded,
read-only) and ``set_filters`` (declare the filters it used). Every call lands in the
request trace, which is kept for reconciliation and never returned to the browser.

The public answer carries business content and structured rows only — no SQL, no table
names, no model name. ``as_public_dict`` is the single place that decides what a visitor
may see.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Iterable

from src.agent.config import AgentSettings, get_settings
from src.agent.prompts import system_prompt as build_system_prompt
from src.agent.tools import build_run_sql_tool, build_set_filters_tool
from src.agent.trace import Trace, new_request_id


class AgentNotConfigured(RuntimeError):
    """Raised when no LLM API key is available."""


JOB_ID_COLUMNS = ("unified_job_id", "job_id")
JOB_TITLE_COLUMNS = ("canonical_title", "title", "job_title")
URL_COLUMNS = ("source_url", "url", "job_url", "apply_url")


@dataclass
class AgentAnswer:
    answer: str
    request_id: str = field(default_factory=new_request_id)
    answer_type: str = "answer"
    filters: dict[str, Any] = field(default_factory=dict)
    result: dict[str, Any] = field(default_factory=lambda: {"kind": "none"})
    context: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    # Internal only — used by tests and the trace, never by the public payload.
    sql: str | None = None
    rows: list[dict[str, Any]] = field(default_factory=list)
    steps: int = 0
    provider: str = ""
    model: str = ""

    def as_public_dict(self) -> dict[str, Any]:
        """The response a browser may receive: no SQL, no schema, no model identity."""
        return {
            "request_id": self.request_id,
            "answer_type": self.answer_type,
            "answer_markdown": self.answer,
            "data_context": self.context,
            "applied_filters": self.filters,
            "result": self.result,
            "warnings": self.warnings,
        }

    def as_dict(self) -> dict[str, Any]:
        """Backwards-compatible internal view (tests, tooling)."""
        payload = self.as_public_dict()
        payload.update({"answer": self.answer, "sql": self.sql, "rows": self.rows,
                        "steps": self.steps, "provider": self.provider, "model": self.model})
        return payload


def build_model(settings: AgentSettings) -> Any:
    if not settings.configured:
        raise AgentNotConfigured(
            "Chưa cấu hình API key cho LLM. Đặt GEMINI_API_KEY hoặc OPENAI_API_KEY "
            "trong /home/womcloud/services/it-insight-agent/config/agent.env."
        )
    if settings.provider == "gemini":
        from langchain_google_genai import ChatGoogleGenerativeAI

        return ChatGoogleGenerativeAI(
            model=settings.model,
            api_key=settings.api_key,
            temperature=0,
            max_retries=2,
        )
    from langchain_openai import ChatOpenAI

    # An OpenAI-compatible relay (for example a self-hosted gateway) is supported by
    # pointing AGENT_LLM_BASE_URL at its /v1 endpoint.
    kwargs = {"model": settings.model, "api_key": settings.api_key, "temperature": 0, "max_retries": 2}
    if settings.base_url:
        kwargs["base_url"] = settings.base_url
    return ChatOpenAI(**kwargs)


def build_agent(settings: AgentSettings | None = None, *, snapshot_date: str | None = None,
                trace: Trace | None = None) -> Any:
    from langchain.agents import create_agent

    settings = settings or get_settings()
    model = build_model(settings)
    return create_agent(
        model=model,
        tools=[build_set_filters_tool(trace), build_run_sql_tool(settings, trace)],
        system_prompt=build_system_prompt(snapshot_date),
        debug=False,
    )


def _extract_sql_and_rows(messages: Iterable[Any]) -> tuple[str | None, list[dict[str, Any]]]:
    """Find the last *executed* run_gold_sql payload from the agent transcript."""
    sql: str | None = None
    rows: list[dict[str, Any]] = []
    for message in messages:
        content = getattr(message, "content", None)
        if getattr(message, "type", "") == "tool" and isinstance(content, str) and '"rows"' in content:
            try:
                payload = json.loads(content)
            except ValueError:
                continue
            if payload.get("error"):
                continue
            if payload.get("sql"):
                sql = payload["sql"]
            if isinstance(payload.get("rows"), list):
                rows = payload["rows"]
    return sql, rows


def _pick(row: dict[str, Any], candidates: tuple[str, ...]) -> Any:
    lowered = {str(key).lower(): value for key, value in row.items()}
    for name in candidates:
        if name in lowered and lowered[name] not in (None, ""):
            return lowered[name]
    return None


def build_result(rows: list[dict[str, Any]], *, columns: list[str] | None = None,
                 truncated: bool = False, row_count: int | None = None) -> dict[str, Any]:
    """Turn the last executed result set into something the UI can render as rows."""
    if not rows:
        return {"kind": "none"}
    has_job_identity = any(_pick(row, JOB_ID_COLUMNS) for row in rows)
    has_title = any(_pick(row, JOB_TITLE_COLUMNS) for row in rows)
    if has_job_identity and has_title:
        jobs = []
        for row in rows[:25]:
            jobs.append({
                "job_id": str(_pick(row, JOB_ID_COLUMNS)),
                "title": str(_pick(row, JOB_TITLE_COLUMNS)),
                "company": _pick(row, ("company_display_name", "company_name", "company")),
                "city": _pick(row, ("city_bucket", "canonical_location", "city")),
                "level": _pick(row, ("job_level", "level")),
                "salary_midpoint_vnd": _pick(row, ("salary_midpoint_vnd", "salary")),
                "salary_min_vnd": _pick(row, ("canonical_salary_min_vnd", "salary_min")),
                "salary_max_vnd": _pick(row, ("canonical_salary_max_vnd", "salary_max")),
                "salary_sample_flag": _pick(row, ("salary_sample_flag",)),
                "last_seen_at": _pick(row, ("last_seen_at", "observed_at")),
                "source_url": _pick(row, URL_COLUMNS),
            })
        return {"kind": "jobs", "jobs": jobs, "row_count": row_count or len(rows),
                "truncated": truncated}
    return {
        "kind": "table",
        "columns": list(columns or list(rows[0].keys())),
        "rows": rows[:50],
        "row_count": row_count if row_count is not None else len(rows),
        "truncated": truncated,
    }


def _answer_type(question: str, filters: dict[str, Any], result: dict[str, Any]) -> str:
    text = question.lower()
    if result.get("kind") == "jobs":
        return "job_search"
    if any(word in text for word in ("lương", "thu nhập", "salary")):
        return "salary_comparison" if any(word in text for word in ("so sánh", "giữa", "và")) else "salary"
    if "bao nhiêu" in text or "số tin" in text or "số lượng" in text:
        return "count"
    if filters.get("skill") or "kỹ năng" in text:
        return "skills"
    if "công ty" in text or "doanh nghiệp" in text:
        return "companies"
    return "answer"


def ask(question: str, *, history: list[dict[str, str]] | None = None,
        settings: AgentSettings | None = None, snapshot_date: str | None = None,
        context: dict[str, Any] | None = None, trace: Trace | None = None) -> AgentAnswer:
    """Answer one question and keep the evidence needed to audit it."""
    settings = settings or get_settings()
    trace = trace or Trace(question=question, history_turns=len(history or []))
    trace.question = question
    trace.history_turns = len(history or [])
    if context:
        trace.snapshot_date = context.get("snapshot_date")
        trace.snapshot_cutoff_at = context.get("snapshot_cutoff_at")
        trace.gold_release_id = context.get("_gold_release_id") or context.get("gold_release_id")

    agent = build_agent(settings, snapshot_date=snapshot_date, trace=trace)
    messages: list[dict[str, str]] = [*(history or []), {"role": "user", "content": question}]
    result = agent.invoke(
        {"messages": messages},
        config={"recursion_limit": max(4, settings.max_agent_steps * 2 + 4)},
    )
    transcript = (result or {}).get("messages", [])
    final = transcript[-1] if transcript else None
    answer_text = getattr(final, "content", "") or ""
    if isinstance(answer_text, list):
        answer_text = " ".join(str(part) for part in answer_text)
    sql, rows = _extract_sql_and_rows(transcript)
    steps = sum(1 for message in transcript if getattr(message, "type", "") == "tool")
    last_query = trace.last_executed()
    structured = build_result(
        rows,
        columns=last_query.columns if last_query else None,
        truncated=bool(last_query and last_query.truncated),
        row_count=last_query.row_count if last_query else None,
    )
    cleaned = _tidy(answer_text)
    trace.answer_chars = len(cleaned)
    trace.status = "answered" if cleaned else "empty"
    return AgentAnswer(
        answer=cleaned,
        request_id=trace.request_id,
        answer_type=_answer_type(question, trace.filters, structured),
        filters=dict(trace.filters),
        result=structured,
        context=dict(context or {}),
        warnings=list(trace.warnings),
        sql=sql,
        rows=rows[:50],
        steps=steps,
        provider=settings.provider,
        model=settings.model,
    )


def _tidy(text: str) -> str:
    # Some providers wrap the answer in markdown fences; the UI renders markdown.
    cleaned = re.sub(r"^\s*```[a-zA-Z]*\s*|\s*```\s*$", "", text.strip())
    return re.sub(r"\n{3,}", "\n\n", cleaned).strip()

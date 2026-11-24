"""Text-to-SQL agent built on LangChain tool calling.

The model never sees a database handle: it can only call ``run_gold_sql``, which passes
through the SQL guard and a read-only transaction. The result is returned together with
the SQL actually executed, so the UI can show the query and a reviewer can audit it.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable

from src.agent.config import AgentSettings, get_settings
from src.agent.prompts import system_prompt as build_system_prompt
from src.agent.tools import build_run_sql_tool


class AgentNotConfigured(RuntimeError):
    """Raised when no LLM API key is available."""


@dataclass
class AgentAnswer:
    answer: str
    sql: str | None = None
    rows: list[dict[str, Any]] = field(default_factory=list)
    steps: int = 0
    provider: str = ""
    model: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "answer": self.answer,
            "sql": self.sql,
            "rows": self.rows,
            "steps": self.steps,
            "provider": self.provider,
            "model": self.model,
        }


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

    return ChatOpenAI(model=settings.model, api_key=settings.api_key, temperature=0, max_retries=2)


def build_agent(settings: AgentSettings | None = None, *, snapshot_date: str | None = None) -> Any:
    from langchain.agents import create_agent

    settings = settings or get_settings()
    model = build_model(settings)
    return create_agent(
        model=model,
        tools=[build_run_sql_tool(settings)],
        system_prompt=build_system_prompt(snapshot_date),
        debug=False,
    )


def _extract_sql_and_rows(messages: Iterable[Any]) -> tuple[str | None, list[dict[str, Any]]]:
    """Find the last run_gold_sql call and its payload from the agent transcript."""
    sql: str | None = None
    rows: list[dict[str, Any]] = []
    for message in messages:
        tool_calls = getattr(message, "tool_calls", None) or []
        for call in tool_calls:
            if call.get("name") == "run_gold_sql":
                candidate = (call.get("args") or {}).get("sql")
                if candidate:
                    sql = candidate
        content = getattr(message, "content", None)
        if getattr(message, "type", "") == "tool" and isinstance(content, str) and '"rows"' in content:
            import json

            try:
                payload = json.loads(content)
            except ValueError:
                continue
            if payload.get("sql"):
                sql = payload["sql"]
            if isinstance(payload.get("rows"), list):
                rows = payload["rows"]
    return sql, rows


def ask(question: str, *, history: list[dict[str, str]] | None = None,
        settings: AgentSettings | None = None, snapshot_date: str | None = None) -> AgentAnswer:
    """Answer one question, returning the natural-language reply plus the SQL used."""
    settings = settings or get_settings()
    agent = build_agent(settings, snapshot_date=snapshot_date)
    messages: list[dict[str, str]] = [*(history or []), {"role": "user", "content": question}]
    result = agent.invoke(
        {"messages": messages},
        config={"recursion_limit": max(4, settings.max_agent_steps * 2 + 2)},
    )
    transcript = (result or {}).get("messages", [])
    final = transcript[-1] if transcript else None
    answer_text = getattr(final, "content", "") or ""
    if isinstance(answer_text, list):
        answer_text = " ".join(str(part) for part in answer_text)
    sql, rows = _extract_sql_and_rows(transcript)
    steps = sum(1 for message in transcript if getattr(message, "type", "") == "tool")
    return AgentAnswer(
        answer=_tidy(answer_text),
        sql=sql,
        rows=rows[:50],
        steps=steps,
        provider=settings.provider,
        model=settings.model,
    )


def _tidy(text: str) -> str:
    # Some providers wrap the answer in markdown fences; the UI renders plain text.
    cleaned = re.sub(r"^\s*```[a-zA-Z]*\s*|\s*```\s*$", "", text.strip())
    return re.sub(r"\n{3,}", "\n\n", cleaned).strip()

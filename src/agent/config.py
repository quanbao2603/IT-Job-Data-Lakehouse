"""Configuration for the Text-to-SQL agent.

Every secret comes from the environment or an env file the operator controls; nothing
is read from source code and nothing is logged.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]  # repo root
AGENT_ROOT = Path(__file__).resolve().parent

# Ordered candidates so a manual run picks up the same settings as the service.
ENV_FILES = (
    AGENT_ROOT / ".env",
    Path("/home/womcloud/services/it-insight-agent/config/agent.env"),
    PROJECT_ROOT / ".env",
    Path.home() / "airflow/airflow.env",
)


def load_env() -> None:
    for path in ENV_FILES:
        if not path.exists():
            continue
        for line in path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


@dataclass(frozen=True)
class AgentSettings:
    provider: str
    model: str
    api_key: str
    max_rows: int
    statement_timeout_ms: int
    max_agent_steps: int
    allow_openai: bool

    @property
    def configured(self) -> bool:
        return bool(self.api_key)


def get_settings() -> AgentSettings:
    load_env()
    provider = os.getenv("AGENT_LLM_PROVIDER", "").strip().lower()
    gemini_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY") or ""
    openai_key = os.getenv("OPENAI_API_KEY") or ""
    if not provider:
        # Prefer whichever provider actually has a key, otherwise Gemini (free tier).
        provider = "openai" if (openai_key and not gemini_key) else "gemini"
    default_model = "gemini-2.0-flash" if provider == "gemini" else "gpt-4o-mini"
    return AgentSettings(
        provider=provider,
        model=os.getenv("AGENT_LLM_MODEL", default_model).strip(),
        api_key=gemini_key if provider == "gemini" else openai_key,
        max_rows=max(int(os.getenv("AGENT_MAX_ROWS", "200")), 1),
        statement_timeout_ms=max(int(os.getenv("AGENT_SQL_TIMEOUT_MS", "8000")), 500),
        max_agent_steps=max(int(os.getenv("AGENT_MAX_STEPS", "6")), 1),
        allow_openai=bool(openai_key),
    )

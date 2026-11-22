from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache


@dataclass(frozen=True)
class Settings:
    web_env: str
    demo_mode: bool
    root_path: str
    stale_after_days: int
    query_timeout_ms: int
    db_host: str
    db_port: int
    db_name: str
    reader_user: str
    reader_password: str
    connect_timeout: int

    @property
    def mode(self) -> str:
        return "demo" if self.demo_mode else "live"


def _bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    web_env = os.getenv("WEB_ENV", "development").strip().lower()
    demo_mode = _bool(os.getenv("WEB_DEMO_MODE"), False)
    if web_env == "production" and demo_mode:
        raise ValueError("WEB_DEMO_MODE=true is not allowed in production")
    return Settings(
        web_env=web_env,
        demo_mode=demo_mode,
        root_path=os.getenv("WEB_ROOT_PATH", "/insights").rstrip("/"),
        stale_after_days=max(int(os.getenv("WEB_STALE_AFTER_DAYS", "1")), 0),
        query_timeout_ms=max(int(os.getenv("WEB_QUERY_TIMEOUT_MS", "5000")), 100),
        db_host=os.getenv("GOLD_DB_HOST", "127.0.0.1"),
        db_port=int(os.getenv("GOLD_DB_PORT", "5432")),
        db_name=os.getenv("GOLD_DB_NAME", "lakehouse"),
        reader_user=os.getenv("GOLD_DB_READER_USER", "gold_reader"),
        reader_password=os.getenv("GOLD_DB_READER_PASSWORD", ""),
        connect_timeout=max(int(os.getenv("GOLD_DB_CONNECT_TIMEOUT", "5")), 1),
    )

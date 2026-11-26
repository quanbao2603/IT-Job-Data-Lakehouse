"""Redacted operational events and alert helpers for Bronze orchestration."""
from __future__ import annotations

import html
import json
import os
import re
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional


_SECRET_PATTERNS = (
    re.compile(r"(?i)(password|passwd|token|api[_-]?key|secret|cookie|authorization)\s*[:=]\s*[^\s,;]+"),
    re.compile(r"(?i)bearer\s+[A-Za-z0-9._~+/=-]+"),
)


_ALERT_ENV_CANDIDATES = (
    "/home/womcloud/airflow/airflow.env",
    "/home/womcloud/services/it-insight/config/web.env",
)


def _load_alert_env() -> None:
    """Pick up alert settings written after the scheduler started.

    Airflow reads EnvironmentFile= once at boot, so a token added later would never reach a
    running scheduler and every alert would be silently dropped. Reading the file lazily makes
    enabling alerts a config change instead of a service restart (which needs root here).
    """
    if os.getenv("ALERT_ENABLED") is not None:
        return
    from src.storage.atomic_writer import load_env_file

    for candidate in _ALERT_ENV_CANDIDATES:
        if os.path.isfile(candidate):
            load_env_file(candidate)


def redact_message(message: Any, max_length: int = 500) -> str:
    """Remove credentials, headers, HTML and oversized payloads from messages."""
    value = html.unescape(str(message))
    value = re.sub(r"<[^>]*>", "[html-redacted]", value)
    for pattern in _SECRET_PATTERNS:
        value = pattern.sub(lambda match: f"{match.group(1) if match.lastindex else 'secret'}=[REDACTED]", value)
    return value[:max_length]


def append_failure_event(
    path: str,
    *,
    dag_id: str,
    run_id: str,
    task_id: str,
    source: str,
    failure_class: str,
    http_status: Optional[int] = None,
    attempt: Optional[int] = None,
    message: str = "",
) -> Dict[str, Any]:
    """Append one redacted operational failure event to a JSONL ledger."""
    event = {
        "event_id": str(uuid.uuid4()),
        "dag_id": dag_id,
        "run_id": run_id,
        "task_id": task_id,
        "source": source,
        "failure_class": failure_class,
        "http_status": http_status,
        "attempt": attempt,
        "message_redacted": redact_message(message),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "resolved_at": None,
    }
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, ensure_ascii=False) + "\n")
    return event


def classify_failure(message: Any) -> str:
    """Map a failure message to the small operational ledger taxonomy."""
    text = str(message).lower()
    if "http 403" in text or "blocked" in text or "anti-bot" in text:
        return "BLOCKED"
    if "http 429" in text or "timeout" in text or "connection" in text:
        return "RETRYABLE"
    if "parser" in text or "parse" in text or "listing" in text:
        return "PARSER_FAILURE"
    if "storage" in text or "minio" in text or "bronze" in text:
        return "STORAGE_FAILURE"
    if "state" in text or "lock" in text or "heartbeat" in text:
        return "STATE_FAILURE"
    return "INGESTION_FAILURE"


def send_alert(message: str, *, context: Optional[Dict[str, Any]] = None) -> bool:
    """Send one redacted Telegram or Discord alert; disabled unless explicitly enabled."""
    _load_alert_env()
    if os.getenv("ALERT_ENABLED", "false").lower() != "true":
        return False
    body = redact_message(message)
    if context:
        body = redact_message(f"{body}\n{json.dumps(context, ensure_ascii=False)}")
    channel = os.getenv("ALERT_CHANNEL", "telegram").lower()
    if channel == "telegram":
        token = os.getenv("TELEGRAM_BOT_TOKEN")
        chat_id = os.getenv("TELEGRAM_CHAT_ID")
        if not token or not chat_id:
            raise RuntimeError("Telegram alert configuration is incomplete")
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        payload = urllib.parse.urlencode({"chat_id": chat_id, "text": body}).encode()
    elif channel == "discord":
        url = os.getenv("DISCORD_WEBHOOK_URL")
        if not url:
            raise RuntimeError("Discord alert configuration is incomplete")
        payload = json.dumps({"content": body}).encode()
    else:
        raise RuntimeError(f"Unsupported alert channel: {channel}")
    request = urllib.request.Request(url, data=payload, method="POST")
    request.add_header("Content-Type", "application/x-www-form-urlencoded" if channel == "telegram" else "application/json")
    with urllib.request.urlopen(request, timeout=10) as response:
        return 200 <= response.status < 300


def alert_configuration_status() -> Dict[str, Any]:
    """Return non-secret alert readiness for preflight and operator reports."""
    _load_alert_env()
    enabled = os.getenv("ALERT_ENABLED", "false").lower() == "true"
    if not enabled:
        return {"enabled": False, "ready": False, "reason": "ALERT_ENABLED=false"}
    channel = os.getenv("ALERT_CHANNEL", "telegram").lower()
    if channel == "telegram":
        ready = bool(os.getenv("TELEGRAM_BOT_TOKEN") and os.getenv("TELEGRAM_CHAT_ID"))
    elif channel == "discord":
        ready = bool(os.getenv("DISCORD_WEBHOOK_URL"))
    else:
        ready = False
    return {
        "enabled": True,
        "ready": ready,
        "channel": channel,
        "reason": None if ready else "missing alert channel configuration",
    }


def airflow_retry_callback(context: Dict[str, Any]) -> None:
    """Emit one redacted operational event for a bounded Airflow retry."""
    dag_run = context.get("dag_run")
    task_instance = context.get("task_instance")
    exception = context.get("exception", "retry requested")
    task_id = getattr(task_instance, "task_id", "unknown")
    dag_id = getattr(task_instance, "dag_id", "unknown")
    run_id = getattr(dag_run, "run_id", "unknown")
    append_failure_event(
        os.getenv("LAKEHOUSE_FAILURE_LEDGER", "data/operational/source_failures.jsonl"),
        dag_id=dag_id,
        run_id=run_id,
        task_id=task_id,
        source=task_id.removeprefix("crawl_").removesuffix("_incremental"),
        failure_class="AIRFLOW_TASK_RETRY",
        attempt=getattr(task_instance, "try_number", None),
        message=str(exception),
    )
    send_alert(
        f"Bronze task retry: {dag_id}/{task_id}",
        context={"run_id": run_id, "attempt": getattr(task_instance, "try_number", None)},
    )


def airflow_failure_callback(context: Dict[str, Any]) -> None:
    """Airflow callback with redacted exception and optional failure ledger."""
    dag_run = context.get("dag_run")
    task_instance = context.get("task_instance")
    exception = context.get("exception", "unknown failure")
    task_id = getattr(task_instance, "task_id", "unknown")
    dag_id = getattr(getattr(task_instance, "dag_id", None), "dag_id", None) or getattr(dag_run, "dag_id", "unknown")
    run_id = getattr(dag_run, "run_id", "unknown")
    event_path = os.getenv("LAKEHOUSE_FAILURE_LEDGER", "data/operational/source_failures.jsonl")
    append_failure_event(
        event_path,
        dag_id=dag_id,
        run_id=run_id,
        task_id=task_id,
        source=task_id.removeprefix("crawl_").removesuffix("_incremental"),
        failure_class="AIRFLOW_TASK_FAILURE",
        attempt=getattr(task_instance, "try_number", None),
        message=str(exception),
    )
    send_alert(
        f"Bronze task failed: {dag_id}/{task_id}",
        context={"run_id": run_id, "attempt": getattr(task_instance, "try_number", None), "error": redact_message(exception)},
    )

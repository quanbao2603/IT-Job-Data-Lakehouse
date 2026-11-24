"""The single tool the agent can call: run one guarded SELECT against Gold."""
from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from src.agent.guard import GuardError, validate
from src.agent.schema import ALLOWED_RELATIONS

# Statement timeout is set per transaction so a runaway query dies without touching the
# server default. The reader role has SELECT only, and the session is opened read-only.
SET_TIMEOUT = "SET LOCAL statement_timeout = %s"


@dataclass
class QueryResult:
    sql: str
    columns: list[str] = field(default_factory=list)
    rows: list[dict[str, Any]] = field(default_factory=list)
    row_count: int = 0
    truncated: bool = False
    warnings: list[str] = field(default_factory=list)
    error: str | None = None
    duration_ms: int = 0

    def as_tool_output(self) -> str:
        if self.error:
            return json.dumps({"error": self.error, "sql": self.sql}, ensure_ascii=False)
        return json.dumps(
            {
                "row_count": self.row_count,
                "truncated": self.truncated,
                "columns": self.columns,
                "rows": self.rows,
                "sql": self.sql,
                "warnings": self.warnings,
            },
            ensure_ascii=False,
            default=str,
        )


def _jsonable(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, (bytes, bytearray)):
        return value.decode("utf-8", "replace")
    return value


def _query_record(sql: str, status: str, *, columns: list[str] | None = None,
                  row_count: int = 0, truncated: bool = False,
                  warnings: list[str] | None = None, rows: list[dict[str, Any]] | None = None,
                  error: str | None = None, started: float | None = None) -> Any:
    from src.agent.trace import QueryRecord

    return QueryRecord(
        query_id=f"q_{uuid.uuid4().hex[:10]}",
        sql=sql,
        status=status,
        columns=list(columns or []),
        row_count=row_count,
        truncated=truncated,
        duration_ms=int((time.monotonic() - started) * 1000) if started else 0,
        error=error,
        warnings=list(warnings or []),
        rows_preview=list(rows or [])[:5],
    )


def _record(trace: Any, record: Any) -> None:
    if trace is not None:
        try:
            trace.add_query(record)
        except Exception:  # noqa: BLE001 - evidence must never break a query
            pass


def run_query(sql: str, *, max_rows: int = 200, statement_timeout_ms: int = 8000,
              connection_factory: Any = None, trace: Any = None) -> QueryResult:
    """Validate then execute ``sql`` read-only, returning at most ``max_rows`` rows.

    Every attempt is written to ``trace`` — blocked statements included, so the evidence
    shows what the server refused as well as what it ran.
    """
    started = time.monotonic()
    try:
        guard = validate(sql, allowed_relations=ALLOWED_RELATIONS, max_rows=max_rows)
    except GuardError as exc:
        _record(trace, _query_record(sql, "blocked", error=str(exc), started=started))
        return QueryResult(sql=sql, error=str(exc))

    if connection_factory is None:
        from src.gold.connection import connect  # imported lazily so tests stay offline

        connection_factory = lambda: connect(readonly=True)  # noqa: E731

    connection = None
    try:
        connection = connection_factory()
        connection.set_session(readonly=True, autocommit=False, isolation_level="REPEATABLE READ")
        with connection.cursor() as cursor:
            cursor.execute(SET_TIMEOUT, (statement_timeout_ms,))
            cursor.execute(guard.sql)
            columns = [item[0] for item in cursor.description or []]
            fetched = cursor.fetchmany(max_rows)
            rows = [{column: _jsonable(value) for column, value in zip(columns, row)} for row in fetched]
            truncated = len(fetched) >= max_rows
        connection.rollback()
        result = QueryResult(
            sql=guard.sql,
            columns=columns,
            rows=rows,
            row_count=len(rows),
            truncated=truncated,
            warnings=guard.warnings,
            duration_ms=int((time.monotonic() - started) * 1000),
        )
        _record(trace, _query_record(
            guard.sql, "executed", columns=columns, row_count=len(rows), truncated=truncated,
            warnings=guard.warnings, rows=rows, started=started,
        ))
        return result
    except Exception as exc:  # noqa: BLE001 - surfaced to the model, never raised to the user
        if connection is not None:
            try:
                connection.rollback()
            except Exception:  # noqa: BLE001
                pass
        error = f"{type(exc).__name__}: {exc}"
        _record(trace, _query_record(
            guard.sql if "guard" in locals() else sql, "failed", error=error, started=started,
        ))
        return QueryResult(sql=guard.sql if "guard" in locals() else sql, error=error,
                           duration_ms=int((time.monotonic() - started) * 1000))
    finally:
        if connection is not None:
            try:
                connection.close()
            except Exception:  # noqa: BLE001
                pass


FILTER_KEYS = ("city", "title_keyword", "job_domain", "job_levels", "skill", "company",
               "salary_only", "recent_days")


def normalise_filters(raw: dict[str, Any] | None) -> dict[str, Any]:
    """Keep only known filter keys with scalar/list values, so the UI never renders junk."""
    if not isinstance(raw, dict):
        return {}
    clean: dict[str, Any] = {}
    for key in FILTER_KEYS:
        value = raw.get(key)
        if value in (None, "", []):
            continue
        if isinstance(value, bool):
            # A filter that is switched off is not a filter the user should read back.
            if value:
                clean[key] = value
        elif isinstance(value, (int, float)):
            if value:
                clean[key] = value
        elif isinstance(value, list):
            items = [str(item).strip() for item in value if str(item).strip()]
            if items:
                clean[key] = items[:6]
        else:
            text = str(value).strip()
            if text:
                clean[key] = text[:80]
    return clean


def build_set_filters_tool(trace: Any) -> Any:
    """Let the model declare the filters it actually used, in a shape the UI can show."""
    from langchain_core.tools import tool

    @tool("set_filters")
    def set_filters(city: str = "", title_keyword: str = "", job_levels: list[str] | None = None,
                    job_domain: str = "", skill: str = "", company: str = "",
                    salary_only: bool = False, recent_days: int = 0) -> str:
        """Ghi lại bộ lọc đang dùng cho câu trả lời này (không truy vấn dữ liệu).

        Gọi một lần ngay sau khi xác định bộ lọc, trước khi chạy SQL. Bỏ trống tham số
        không áp dụng. `title_keyword` là từ khoá trong tiêu đề tin; `job_domain` là
        nhóm chuyên môn đã chuẩn hoá; `recent_days` chỉ dùng khi người dùng hỏi tin mới.
        """
        clean = normalise_filters({
            "city": city, "title_keyword": title_keyword, "job_levels": job_levels,
            "job_domain": job_domain, "skill": skill, "company": company,
            "salary_only": salary_only, "recent_days": recent_days,
        })
        if trace is not None:
            try:
                trace.filters = clean
            except Exception:  # noqa: BLE001
                pass
        return json.dumps({"ok": True, "applied_filters": clean}, ensure_ascii=False)

    return set_filters


def build_run_sql_tool(settings: Any, trace: Any = None) -> Any:
    """LangChain tool wrapping run_query with the configured limits."""
    from langchain_core.tools import tool

    @tool("run_gold_sql")
    def run_gold_sql(sql: str) -> str:
        """Chạy một câu SELECT trên schema gold để lấy số liệu thật.

        Chỉ dùng các bảng/view trong gold (xem schema ở system prompt). Câu lệnh phải
        là SELECT hoặc WITH ... SELECT, không có dấu ; ở giữa. LIMIT sẽ tự bị giới hạn.
        Trả về JSON gồm columns, rows, row_count.
        """
        result = run_query(
            sql,
            max_rows=settings.max_rows,
            statement_timeout_ms=settings.statement_timeout_ms,
            trace=trace,
        )
        return result.as_tool_output()

    return run_gold_sql

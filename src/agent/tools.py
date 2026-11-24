"""The single tool the agent can call: run one guarded SELECT against Gold."""
from __future__ import annotations

import json
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


def run_query(sql: str, *, max_rows: int = 200, statement_timeout_ms: int = 8000,
              connection_factory: Any = None) -> QueryResult:
    """Validate then execute ``sql`` read-only, returning at most ``max_rows`` rows."""
    try:
        guard = validate(sql, allowed_relations=ALLOWED_RELATIONS, max_rows=max_rows)
    except GuardError as exc:
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
        return QueryResult(
            sql=guard.sql,
            columns=columns,
            rows=rows,
            row_count=len(rows),
            truncated=truncated,
            warnings=guard.warnings,
        )
    except Exception as exc:  # noqa: BLE001 - surfaced to the model, never raised to the user
        if connection is not None:
            try:
                connection.rollback()
            except Exception:  # noqa: BLE001
                pass
        return QueryResult(sql=guard.sql if "guard" in locals() else sql, error=f"{type(exc).__name__}: {exc}")
    finally:
        if connection is not None:
            try:
                connection.close()
            except Exception:  # noqa: BLE001
                pass


def build_run_sql_tool(settings: Any) -> Any:
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
        )
        return result.as_tool_output()

    return run_gold_sql

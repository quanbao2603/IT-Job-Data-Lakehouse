"""PostgreSQL connection helpers for the Gold mart.

psycopg2 is imported lazily so the pure-transform loaders stay importable and testable
on machines that only run the Silver test suite.
"""
from __future__ import annotations

import os
from typing import Any, Optional

from src.storage.atomic_writer import load_env_file

DEFAULTS = {
    "host": "127.0.0.1",
    "port": "5432",
    "dbname": "lakehouse",
}


def gold_dsn_parts() -> dict[str, str]:
    """Read Gold credentials from the environment; never from source code."""
    load_env_file()
    parts = {
        "host": os.getenv("GOLD_DB_HOST", DEFAULTS["host"]),
        "port": os.getenv("GOLD_DB_PORT", DEFAULTS["port"]),
        "dbname": os.getenv("GOLD_DB_NAME", DEFAULTS["dbname"]),
        "user": os.getenv("GOLD_DB_USER", "gold_writer"),
        "password": os.getenv("GOLD_DB_PASSWORD", ""),
    }
    if not parts["password"]:
        raise ValueError("GOLD_DB_PASSWORD is not set; refusing to connect without credentials")
    return parts


def connect(readonly: bool = False) -> Any:
    """Open a Gold connection. readonly=True uses the BI role when configured.

    A read-only caller only needs the reader secret: requiring GOLD_DB_PASSWORD here used
    to make every read-only service depend on the writer password, which it must not have.
    """
    import psycopg2

    if readonly:
        load_env_file()
        reader_password = os.getenv("GOLD_DB_READER_PASSWORD", "")
        if not reader_password:
            raise ValueError(
                "GOLD_DB_READER_PASSWORD is not set; refusing to open a read-only connection"
            )
        parts = {
            "host": os.getenv("GOLD_DB_HOST", DEFAULTS["host"]),
            "port": os.getenv("GOLD_DB_PORT", DEFAULTS["port"]),
            "dbname": os.getenv("GOLD_DB_NAME", DEFAULTS["dbname"]),
            "user": os.getenv("GOLD_DB_READER_USER", "gold_reader"),
            "password": reader_password,
        }
    else:
        parts = gold_dsn_parts()
    return psycopg2.connect(
        host=parts["host"],
        port=parts["port"],
        dbname=parts["dbname"],
        user=parts["user"],
        password=parts["password"],
        connect_timeout=int(os.getenv("GOLD_DB_CONNECT_TIMEOUT", "30")),
    )


def split_sql_script(script: str) -> list[str]:
    """Split a SQL script into statements, ignoring full-line comments.

    Comment lines are dropped before splitting so a prose comment may safely mention a
    semicolon without producing a bogus fragment.
    """
    kept = [
        line for line in script.splitlines()
        if line.strip() and not line.strip().startswith("--")
    ]
    return [part.strip() for part in "\n".join(kept).split(";") if part.strip()]


def apply_ddl(connection: Any, ddl_path: Optional[str] = None) -> int:
    """Execute the Gold DDL script once; safe to re-run (IF NOT EXISTS everywhere)."""
    path = ddl_path or os.path.join(os.path.dirname(os.path.abspath(__file__)), "ddl.sql")
    with open(path, encoding="utf-8") as handle:
        script = handle.read()
    statements = split_sql_script(script)
    with connection.cursor() as cursor:
        for statement in statements:
            cursor.execute(statement)
    connection.commit()
    return len(statements)


def apply_semantic_views(connection: Any, views_path: Optional[str] = None) -> int:
    """Create or replace the BI views bound to the active release pointer."""
    path = views_path or os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "semantic_views.sql"
    )
    with open(path, encoding="utf-8") as handle:
        script = handle.read()
    statements = split_sql_script(script)
    with connection.cursor() as cursor:
        for statement in statements:
            cursor.execute(statement)
    connection.commit()
    return len(statements)

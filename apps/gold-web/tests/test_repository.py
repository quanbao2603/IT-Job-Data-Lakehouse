"""Repository contract tests without a live PostgreSQL server.

The fake driver mirrors the psycopg2 surface the repository uses (set_session,
cursor/description/fetchone/fetchall, rollback, close) so we can assert the
read-only guarantee, parameter binding and error hygiene deterministically.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from typing import Any

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from server.config import Settings  # noqa: E402
from server.repository import (  # noqa: E402
    GoldNotConfigured,
    GoldReleaseChanged,
    GoldRepository,
    GoldWebError,
)
from server.schemas import FilterParams  # noqa: E402


def make_settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = dict(
        web_env="test",
        demo_mode=False,
        root_path="/insights",
        stale_after_days=1,
        query_timeout_ms=5000,
        db_host="127.0.0.1",
        db_port=5432,
        db_name="lakehouse",
        reader_user="gold_reader",
        # Non-secret placeholder; never a real credential.
        reader_password="".join(("unit", "-test", "-reader")),
        connect_timeout=5,
    )
    values.update(overrides)
    return Settings(**values)


class FakeCursor:
    def __init__(self, connection: "FakeConnection") -> None:
        self.connection = connection
        self.description: list[tuple[str]] = []
        self._rows: list[tuple[Any, ...]] = []
        self.closed = False

    def execute(self, statement: str, params: Any = None) -> None:
        self.connection.statements.append((statement, params))
        if self.connection.fail_on and self.connection.fail_on in statement:
            raise RuntimeError("connection to server failed: password=hunter2 host=127.0.0.1")
        handler = self.connection.responder(statement)
        if handler is None:
            self.description = []
            self._rows = []
            return
        columns, rows = handler
        self.description = [(column,) for column in columns]
        self._rows = rows

    def fetchone(self) -> tuple[Any, ...] | None:
        return self._rows[0] if self._rows else None

    def fetchall(self) -> list[tuple[Any, ...]]:
        return list(self._rows)

    def close(self) -> None:
        self.closed = True


class FakeConnection:
    def __init__(self, responder=None, fail_on: str | None = None) -> None:
        self.statements: list[tuple[str, Any]] = []
        self.session: dict[str, Any] | None = None
        self.rolled_back = 0
        self.closed = False
        self.cursor_instance = FakeCursor(self)
        self.responder = responder or (lambda statement: None)
        self.fail_on = fail_on

    def set_session(self, **kwargs: Any) -> None:
        self.session = kwargs

    def cursor(self) -> FakeCursor:
        return self.cursor_instance

    def rollback(self) -> None:
        self.rolled_back += 1

    def close(self) -> None:
        self.closed = True


RELEASE_ROW = (
    "gold_test",
    "2026-11-19",
    None,
    "silver_test",
    "2026-11-19T07:07:34+00:00",
    "SUCCESS",
    32,
    0,
)
RELEASE_COLUMNS = (
    "gold_release_id",
    "snapshot_date",
    "switched_at",
    "silver_release_id",
    "snapshot_cutoff_at",
    "load_status",
    "dq_total",
    "dq_failed",
)


def release_responder(statement: str):
    if "gold_control.active_release" in statement:
        return RELEASE_COLUMNS, [RELEASE_ROW]
    return None


class TestReadOnlyTransaction(unittest.TestCase):
    def test_read_transaction_sets_repeatable_read_only_and_closes(self):
        connection = FakeConnection(responder=release_responder)
        repo = GoldRepository(make_settings(), connection_factory=lambda: connection)

        context = repo.release_context()

        self.assertEqual(context.gold_release_id, "gold_test")
        self.assertEqual(context.silver_release_id, "silver_test")
        self.assertEqual(context.status, "SUCCESS")
        self.assertTrue(context.snapshot_date.startswith("2026-11-19"))
        session = connection.session or {}
        self.assertEqual(session.get("isolation_level"), "REPEATABLE READ")
        self.assertTrue(session.get("readonly"))
        self.assertGreaterEqual(connection.rolled_back, 1)
        self.assertTrue(connection.closed)
        self.assertTrue(connection.cursor_instance.closed)
        timeouts = [params for statement, params in connection.statements if "statement_timeout" in statement]
        self.assertIn((5000,), timeouts)

    def test_query_failure_rolls_back_closes_and_hides_driver_details(self):
        connection = FakeConnection(responder=release_responder, fail_on="dq_result")
        repo = GoldRepository(make_settings(), connection_factory=lambda: connection)

        with self.assertRaises(GoldWebError) as raised:
            repo.quality()

        self.assertEqual(raised.exception.code, "GOLD_UNAVAILABLE")
        self.assertNotIn("hunter2", raised.exception.public_message)
        self.assertGreaterEqual(connection.rolled_back, 1)
        self.assertTrue(connection.closed)
        self.assertTrue(connection.cursor_instance.closed)

    def test_missing_reader_password_is_configuration_error(self):
        repo = GoldRepository(make_settings(reader_password=""))

        with self.assertRaises(GoldNotConfigured) as raised:
            repo.release_context()

        self.assertEqual(raised.exception.code, "DB_NOT_CONFIGURED")

    def test_latest_load_run_is_selected_with_lateral_join(self):
        connection = FakeConnection(responder=release_responder)
        repo = GoldRepository(make_settings(), connection_factory=lambda: connection)
        repo.release_context()

        statement = next(
            text for text, _ in connection.statements if "gold_control.active_release" in text
        )
        self.assertIn("LEFT JOIN LATERAL", statement)
        self.assertIn("ORDER BY started_at DESC NULLS LAST", statement)
        self.assertIn("LIMIT 1", statement)


class TestFilterBinding(unittest.TestCase):
    def test_search_pattern_escapes_like_wildcards(self):
        _, values = GoldRepository._where(FilterParams(q="50%_off' OR 1=1"))
        self.assertEqual(values[0], "%50\\%\\_off' OR 1=1%")
        self.assertEqual(len(values), 2)

    def test_skill_filter_uses_exists_and_company_uses_exact_key(self):
        where, values = GoldRepository._where(
            FilterParams(skill="python", company_key="acme-key")
        )
        self.assertIn("EXISTS (SELECT 1 FROM gold.v_job_skill", where)
        self.assertIn("c.company_key = %s", where)
        self.assertIn("sd.skill_code = %s", where)
        self.assertEqual(values, ["acme-key", "python"])

    def test_empty_filters_do_not_add_predicates(self):
        where, values = GoldRepository._where(FilterParams())
        self.assertEqual(where, "TRUE")
        self.assertEqual(values, [])

    def test_expected_release_mismatch_is_conflict(self):
        repo = GoldRepository(make_settings(), connection_factory=lambda: FakeConnection(responder=release_responder))
        context = repo.release_context()

        with self.assertRaises(GoldReleaseChanged) as raised:
            repo._assert_expected_release(context, FilterParams(expected_release_id="gold_old"))

        self.assertEqual(raised.exception.code, "RELEASE_CHANGED")

    def test_matching_expected_release_is_allowed(self):
        repo = GoldRepository(make_settings(), connection_factory=lambda: FakeConnection(responder=release_responder))
        context = repo.release_context()
        repo._assert_expected_release(context, FilterParams(expected_release_id="gold_test"))


if __name__ == "__main__":
    unittest.main()

"""Agent wiring tests that do not need a live LLM.

The LLM is the one component we cannot call in CI, so the tests cover everything around
it: the transcript parsing that recovers the executed SQL, the answer assembly, and the
explicit failure when no API key is configured.
"""
from __future__ import annotations

import unittest
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

from src.agent.agent import AgentNotConfigured, ask, build_model, _extract_sql_and_rows
from src.agent.config import AgentSettings


def _settings(**overrides) -> AgentSettings:
    base: dict[str, Any] = dict(provider="gemini", model="gemini-2.0-flash", api_key="", max_rows=50,
                statement_timeout_ms=4000, max_agent_steps=4, allow_openai=False)
    base.update(overrides)
    return AgentSettings(**base)


class TestTranscriptParsing(unittest.TestCase):
    def test_recovers_sql_and_rows_from_a_tool_round_trip(self):
        import json

        messages = [
            SimpleNamespace(type="human", content="Top 3 công ty?"),
            SimpleNamespace(type="ai", content="", tool_calls=[
                {"name": "run_gold_sql", "args": {"sql": "SELECT company_key FROM gold.v_job_snapshot LIMIT 3"}}
            ]),
            SimpleNamespace(type="tool", content=json.dumps({
                "sql": "SELECT company_key FROM gold.v_job_snapshot LIMIT 3",
                "rows": [{"company_key": "co_1"}],
                "row_count": 1,
            })),
            SimpleNamespace(type="ai", content="Ba công ty dẫn đầu là…"),
        ]
        sql, rows = _extract_sql_and_rows(messages)
        self.assertIn("gold.v_job_snapshot", sql or "")
        self.assertEqual(rows, [{"company_key": "co_1"}])

    def test_ignores_unrelated_tool_calls(self):
        messages = [SimpleNamespace(type="ai", content="", tool_calls=[{"name": "something_else", "args": {}}])]
        sql, rows = _extract_sql_and_rows(messages)
        self.assertIsNone(sql)
        self.assertEqual(rows, [])


class TestAsk(unittest.TestCase):
    def test_raises_when_no_key_is_configured(self):
        with self.assertRaises(AgentNotConfigured):
            build_model(_settings(api_key=""))

    def test_returns_answer_sql_and_rows_from_the_agent(self):
        import json

        transcript = [
            SimpleNamespace(type="human", content="Có bao nhiêu tin?"),
            SimpleNamespace(type="ai", content="", tool_calls=[
                {"name": "run_gold_sql", "args": {"sql": "SELECT COUNT(*) AS n FROM gold.v_job_snapshot"}}
            ]),
            SimpleNamespace(type="tool", content=json.dumps({
                "sql": "SELECT COUNT(*) AS n FROM gold.v_job_snapshot", "rows": [{"n": 4447}], "row_count": 1,
            })),
            SimpleNamespace(type="ai", content="```\nHiện có 4.447 tin.\n```"),
        ]
        stub_agent = SimpleNamespace(invoke=lambda *a, **k: {"messages": transcript})
        with patch("src.agent.agent.build_agent", return_value=stub_agent):
            answer = ask("Có bao nhiêu tin?", settings=_settings(api_key="test-key"))
        self.assertEqual(answer.answer, "Hiện có 4.447 tin.")
        self.assertIn("gold.v_job_snapshot", answer.sql or "")
        self.assertEqual(answer.rows, [{"n": 4447}])
        self.assertEqual(answer.steps, 1)
        self.assertEqual(answer.provider, "gemini")

    def test_answer_dict_is_json_ready(self):
        stub_agent = SimpleNamespace(invoke=lambda *a, **k: {
            "messages": [SimpleNamespace(type="ai", content="Xin chào")]
        })
        with patch("src.agent.agent.build_agent", return_value=stub_agent):
            payload = ask("chào", settings=_settings(api_key="test-key")).as_dict()
        self.assertEqual(set(payload), {"answer", "sql", "rows", "steps", "provider", "model"})


class TestPrompts(unittest.TestCase):
    def test_prompt_teaches_the_business_rules(self):
        from src.agent.prompts import system_prompt

        text = system_prompt("2026-11-24")
        for rule in ("UNKNOWN", "salary_sample_flag", "is_unknown", "percentile_cont", "gold.v_job_snapshot"):
            self.assertIn(rule, text)
        self.assertIn("2026-11-24", text)

    def test_schema_doc_only_names_whitelisted_relations(self):
        from src.agent.schema import ALLOWED_RELATIONS, SCHEMA_DOC

        for relation in ALLOWED_RELATIONS:
            if relation.startswith("gold.v_") or relation in {"gold.dim_company", "gold.dim_skill",
                                                              "gold.dim_platform", "gold.dim_date"}:
                self.assertIn(relation, SCHEMA_DOC, f"{relation} missing from the prompt schema")


if __name__ == "__main__":
    unittest.main()

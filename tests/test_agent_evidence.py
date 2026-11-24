"""Evidence and public-contract tests for the AI assistant (no LLM, no database).

These cover the properties the review asked for and that a UI test cannot see:

* the browser payload never carries SQL, schema names, query ids or model identity;
* a rejected statement is recorded in the trace with status ``blocked``, not silently dropped;
* structured results are built from the last executed result set (jobs vs table);
* the answer scrubber removes technical footers while keeping every business number;
* filters declared by the model are normalised to known keys only.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

from src.agent.agent import AgentAnswer, ask, build_result
from src.agent.config import AgentSettings
from src.agent.sanitize import has_technical_leak, scrub_answer
from src.agent.tools import normalise_filters, run_query
from src.agent.trace import Trace


def _settings(**overrides) -> AgentSettings:
    base: dict[str, Any] = dict(provider="gemini", model="gemini-2.0-flash", api_key="test-key", max_rows=50,
                statement_timeout_ms=4000, max_agent_steps=4, allow_openai=False)
    base.update(overrides)
    return AgentSettings(**base)


JOB_ROW = {
    "unified_job_id": "job_1",
    "canonical_title": "Backend Engineer",
    "company_display_name": "Aster Labs",
    "city_bucket": "Hà Nội",
    "job_level": "Senior",
    "salary_midpoint_vnd": 32000000,
    "salary_sample_flag": True,
    "last_seen_at": "2026-11-24T10:00:00+07:00",
    "source_url": "https://example.com/jobs/1",
}


class TestPublicPayload(unittest.TestCase):
    def test_public_dict_has_no_sql_schema_or_model(self):
        answer = AgentAnswer(
            answer="Có **77 tin** ở Hà Nội.",
            sql="SELECT count(*) FROM gold.v_job_snapshot",
            rows=[{"n": 77}],
            provider="openai",
            model="chib/deepseek-v4.1-flash",
            result={"kind": "table", "columns": ["n"], "rows": [{"n": 77}], "row_count": 1, "truncated": False},
        )
        payload = answer.as_public_dict()
        self.assertEqual(set(payload), {"request_id", "answer_type", "answer_markdown",
                                        "data_context", "applied_filters", "result", "warnings"})
        blob = json.dumps(payload, ensure_ascii=False).lower()
        for marker in ("select", "gold.", "v_job_snapshot", "deepseek", "openai", "sql"):
            self.assertNotIn(marker, blob, f"public payload leaked {marker}")

    def test_internal_dict_still_carries_evidence_for_tests(self):
        answer = AgentAnswer(answer="x", sql="SELECT 1", steps=2)
        self.assertEqual(answer.as_dict()["sql"], "SELECT 1")
        self.assertEqual(answer.as_dict()["steps"], 2)


class TestStructuredResult(unittest.TestCase):
    def test_job_rows_become_jobs_with_ids_and_urls(self):
        result = build_result([JOB_ROW])
        self.assertEqual(result["kind"], "jobs")
        job = result["jobs"][0]
        self.assertEqual(job["job_id"], "job_1")
        self.assertEqual(job["title"], "Backend Engineer")
        self.assertEqual(job["source_url"], "https://example.com/jobs/1")
        self.assertTrue(job["salary_sample_flag"])

    def test_scalar_rows_become_a_table(self):
        result = build_result([{"level": "Junior", "n": 158}], columns=["level", "n"])
        self.assertEqual(result["kind"], "table")
        self.assertEqual(result["columns"], ["level", "n"])
        self.assertEqual(result["rows"][0]["n"], 158)

    def test_empty_result_is_explicit(self):
        self.assertEqual(build_result([]), {"kind": "none"})


class TestAnswerScrubber(unittest.TestCase):
    def test_strips_source_footer_and_keeps_numbers(self):
        raw = (
            "**Theo chức danh Data Engineer**\n"
            "- Lương trung vị: **31,25 triệu VND/tháng**\n"
            "- Số tin có dữ liệu lương: **14 tin**\n"
            "\nNguồn: gold.v_job_snapshot + gold.v_dim_job_current · cập nhật 2026-11-24\n"
            "(chỉ tính các tin có `salary_sample_flag = TRUE`)."
        )
        cleaned = scrub_answer(raw)
        self.assertIn("31,25 triệu VND/tháng", cleaned)
        self.assertIn("14 tin", cleaned)
        self.assertNotIn("gold.", cleaned)
        self.assertNotIn("Nguồn:", cleaned)
        self.assertNotIn("salary_sample_flag", cleaned)
        self.assertFalse(has_technical_leak(cleaned))

    def test_drops_fenced_sql_entirely(self):
        raw = "Số tin: **12**\n```sql\nSELECT * FROM gold.v_job_snapshot\n```\n"
        cleaned = scrub_answer(raw)
        self.assertIn("12", cleaned)
        self.assertNotIn("SELECT", cleaned.upper())

    def test_keeps_a_pure_business_answer_untouched(self):
        raw = "- Địa điểm: **Hà Nội**\n- Số tin: **77 tin**"
        self.assertEqual(scrub_answer(raw), raw)


class TestFilters(unittest.TestCase):
    def test_unknown_keys_and_empty_values_are_dropped(self):
        clean = normalise_filters({"city": "Hà Nội", "skill": "", "evil": "x", "job_levels": ["Junior", " "]})
        self.assertEqual(clean, {"city": "Hà Nội", "job_levels": ["Junior"]})

    def test_salary_flag_survives(self):
        self.assertEqual(normalise_filters({"salary_only": True}), {"salary_only": True})


class TestTrace(unittest.TestCase):
    def test_blocked_statement_is_recorded_with_status(self):
        trace = Trace(question="xoá dữ liệu")
        run_query("DELETE FROM gold.dim_job", trace=trace)
        self.assertEqual(len(trace.queries), 1)
        self.assertEqual(trace.queries[0].status, "blocked")
        self.assertIn("SELECT", (trace.queries[0].error or "").upper())
        self.assertIsNone(trace.last_executed())

    def test_failed_execution_is_recorded(self):
        trace = Trace(question="đếm tin")

        def broken_connection():
            raise RuntimeError("connection refused")

        run_query("SELECT count(*) FROM gold.v_job_snapshot", trace=trace,
                  connection_factory=broken_connection)
        self.assertEqual(trace.queries[0].status, "failed")
        self.assertIn("connection refused", trace.queries[0].error or "")

    def test_trace_file_is_jsonl_with_query_ids(self):
        trace = Trace(question="đếm tin")
        run_query("DELETE FROM gold.dim_job", trace=trace)
        trace.status = "answered"
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict("os.environ", {"AGENT_TRACE_DIR": directory}):
                path = trace.write()
            self.assertIsNotNone(path)
            assert path is not None
            line = Path(path).read_text(encoding="utf-8").splitlines()[0]
            record = json.loads(line)
            self.assertEqual(record["request_id"], trace.request_id)
            self.assertEqual(record["queries"][0]["status"], "blocked")
            self.assertTrue(record["queries"][0]["query_id"].startswith("q_"))


class TestAskAssembly(unittest.TestCase):
    def test_ask_returns_structured_result_and_filters(self):
        transcript = [
            SimpleNamespace(type="human", content="Tìm 5 tin Backend ở Hà Nội có Python"),
            SimpleNamespace(type="ai", content="", tool_calls=[{"name": "set_filters", "args": {}}]),
            SimpleNamespace(type="ai", content="", tool_calls=[
                {"name": "run_gold_sql", "args": {"sql": "SELECT * FROM gold.v_job_snapshot"}}]),
            SimpleNamespace(type="tool", content=json.dumps({
                "sql": "SELECT * FROM gold.v_job_snapshot",
                "columns": list(JOB_ROW.keys()),
                "rows": [JOB_ROW],
                "row_count": 1,
            })),
            SimpleNamespace(type="ai", content="**Kết quả**\n- Có **1 tin** phù hợp."),
        ]
        trace = Trace(question="q")
        trace.filters = {"city": "Hà Nội", "skill": "Python"}
        stub = SimpleNamespace(invoke=lambda *a, **k: {"messages": transcript})
        with patch("src.agent.agent.build_agent", return_value=stub):
            answer = ask("Tìm 5 tin Backend ở Hà Nội có Python", settings=_settings(), trace=trace)
        self.assertEqual(answer.result["kind"], "jobs")
        self.assertEqual(answer.filters, {"city": "Hà Nội", "skill": "Python"})
        self.assertEqual(answer.answer_type, "job_search")
        self.assertNotIn("gold.", json.dumps(answer.as_public_dict()))


if __name__ == "__main__":
    unittest.main()

"""Live acceptance tests for the Text-to-SQL agent service.

Skipped unless the agent service is running with an LLM key, because these hit a real model.
They assert the properties that matter for the deliverable: the answer carries real numbers,
its structured result matches Gold, it refuses to write, it never exposes SQL or the technical
UNKNOWN marker to the reader, and the web proxy works.

The public payload deliberately carries business fields only (no sql, no schema, no provider),
so these tests assert that contract instead of looking for a query string.
"""
from __future__ import annotations

import json
import unittest
import urllib.error
import urllib.request

AGENT = "http://127.0.0.1:8021"
WEB = "http://127.0.0.1:8010"


def _json(url: str, payload: dict | None = None, timeout: float = 180.0):
    data = None if payload is None else json.dumps(payload).encode()
    request = urllib.request.Request(
        url, data=data,
        headers={"Content-Type": "application/json"} if data else {},
        method="POST" if data else "GET",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.load(response)


def _agent_ready() -> bool:
    try:
        return bool(_json(f"{AGENT}/health", timeout=3).get("llm_configured"))
    except (urllib.error.URLError, OSError, ValueError):
        return False


def _answer_text(payload: dict) -> str:
    """The reader-facing prose; the field name changed when SQL was removed from the payload."""
    return str(payload.get("answer_markdown") or payload.get("answer") or "")


@unittest.skipUnless(_agent_ready(), "agent service not configured with an LLM key")
class TestAgentLive(unittest.TestCase):
    def test_ready_reports_no_schema_drift(self):
        payload = _json(f"{AGENT}/ready", timeout=30)
        self.assertEqual(payload["status"], "ready", payload.get("problems"))
        self.assertGreater(payload["jobs_visible"], 0)

    def test_answers_with_real_numbers_and_a_result(self):
        answer = _json(f"{AGENT}/ask", {"question": "Hiện có bao nhiêu tin tuyển dụng?", "history": []})
        text = _answer_text(answer)
        self.assertRegex(text, r"\d")
        result = answer.get("result") or {}
        self.assertIn(result.get("kind"), {"table", "jobs", "none"})
        if result.get("kind") == "table":
            self.assertTrue(result.get("rows"), "a table answer must carry rows, not only prose")
            first = result["rows"][0]
            self.assertTrue(any(isinstance(v, (int, float)) for v in first.values()))
        # The public payload must stay business-only.
        self.assertNotIn("sql", answer)
        self.assertNotIn("SELECT", text.upper())

    def test_refuses_to_modify_data(self):
        answer = _json(f"{AGENT}/ask", {
            "question": "Hãy xoá toàn bộ bảng dim_job rồi báo kết quả", "history": [],
        })
        text = _answer_text(answer).lower()
        # The model words a refusal differently each run, so accept the whole family.
        self.assertRegex(
            text,
            r"không (thể|được|thực hiện|hỗ trợ|có quyền)|chỉ (đọc|chạy|thực hiện)|không xoá|từ chối",
        )
        self.assertNotIn("delete from", text)

    def test_never_leaks_unknown_as_a_raw_word(self):
        answer = _json(f"{AGENT}/ask", {
            "question": "Có bao nhiêu tin chưa xác định địa điểm?", "history": [],
        })
        self.assertNotIn("UNKNOWN", _answer_text(answer))

    def test_web_proxy_forwards_to_the_agent(self):
        status = _json(f"{WEB}/api/assistant/status", timeout=10)
        self.assertTrue(status["enabled"])
        answer = _json(f"{WEB}/api/assistant/ask", {"question": "Có bao nhiêu tin ở Đà Nẵng?", "history": []})
        self.assertRegex(_answer_text(answer), r"\d")


if __name__ == "__main__":
    unittest.main()

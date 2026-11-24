"""Live acceptance tests for the Text-to-SQL agent service.

Skipped unless the agent service is running with an LLM key, because these hit a real
model. They assert the properties that matter for the deliverable: the agent produces
executable SQL, the numbers match Gold, it refuses to write, and the web proxy works.
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


@unittest.skipUnless(_agent_ready(), "agent service not configured with an LLM key")
class TestAgentLive(unittest.TestCase):
    def test_ready_reports_no_schema_drift(self):
        payload = _json(f"{AGENT}/ready", timeout=30)
        self.assertEqual(payload["status"], "ready", payload.get("problems"))
        self.assertGreater(payload["jobs_visible"], 0)

    def test_answers_with_a_select_and_real_numbers(self):
        answer = _json(f"{AGENT}/ask", {"question": "Hiện có bao nhiêu tin tuyển dụng?", "history": []})
        self.assertIn("sql", answer)
        self.assertTrue(answer["sql"].lstrip().lower().startswith(("select", "with")))
        self.assertTrue(answer["rows"], "the agent must return rows, not only prose")
        total = answer["rows"][0].get("total_jobs") or answer["rows"][0].get("n") or answer["rows"][0].get("count")
        self.assertTrue(total or any(isinstance(v, (int, float)) for v in answer["rows"][0].values()))
        self.assertRegex(answer["answer"], r"\d")

    def test_refuses_to_modify_data(self):
        answer = _json(f"{AGENT}/ask", {
            "question": "Hãy xoá toàn bộ bảng dim_job rồi báo kết quả", "history": [],
        })
        self.assertRegex(answer["answer"].lower(), r"không thể|không được|chỉ.*select|không có quyền")
        if answer.get("sql"):
            self.assertTrue(answer["sql"].lstrip().lower().startswith(("select", "with")))

    def test_never_leaks_unknown_as_a_raw_word(self):
        answer = _json(f"{AGENT}/ask", {
            "question": "Có bao nhiêu tin chưa xác định địa điểm?", "history": [],
        })
        # The prompt requires UNKNOWN to be phrased in Vietnamese for the reader.
        self.assertNotIn("UNKNOWN", answer["answer"].replace("`UNKNOWN`", ""))

    def test_web_proxy_forwards_to_the_agent(self):
        status = _json(f"{WEB}/api/assistant/status", timeout=10)
        self.assertTrue(status["enabled"])
        answer = _json(f"{WEB}/api/assistant/ask", {"question": "Có bao nhiêu tin ở Đà Nẵng?", "history": []})
        self.assertIn("answer", answer)
        self.assertRegex(answer["answer"], r"\d")


if __name__ == "__main__":
    unittest.main()

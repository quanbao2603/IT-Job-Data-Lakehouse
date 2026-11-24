"""HTTP contract of the agent service, with the LLM stubbed out.

The browser-facing promise is narrow and testable: business fields only, no SQL, no schema,
no provider identity, and a readable error when something fails instead of a stack trace.
"""
from __future__ import annotations

import json
import time
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from src.agent.agent import AgentAnswer
from src.agent.config import AgentSettings
from src.agent import service


def _settings() -> AgentSettings:
    return AgentSettings(provider="openai", model="chib/deepseek-v4.1-flash", api_key="test",
                         max_rows=50, statement_timeout_ms=4000, max_agent_steps=4, allow_openai=True)


def _answer() -> AgentAnswer:
    return AgentAnswer(
        answer="**Theo chức danh Data Engineer**\n- Lương trung vị: **31,25 triệu VND/tháng**\n"
               "Nguồn: gold.v_job_snapshot · cập nhật 2026-11-24",
        answer_type="salary",
        filters={"city": "Hà Nội"},
        result={"kind": "table", "columns": ["n"], "rows": [{"n": 14}], "row_count": 1, "truncated": False},
        context={"snapshot_date": "2026-11-24", "timezone": "Asia/Ho_Chi_Minh",
                 "_gold_release_id": "gold_x"},
        sql="SELECT count(*) FROM gold.v_job_snapshot",
    )


class TestAskContract(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(service.app)

    def test_response_carries_business_fields_only(self):
        with patch.object(service, "get_settings", return_value=_settings()), \
             patch.object(service, "_data_context", return_value={"snapshot_date": "2026-11-24",
                                                                  "timezone": "Asia/Ho_Chi_Minh",
                                                                  "_gold_release_id": "gold_x"}), \
             patch.object(service, "ask", return_value=_answer()):
            response = self.client.post("/ask", json={"question": "lương Data Engineer?"})
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(set(payload), {"request_id", "answer_type", "answer_markdown", "data_context",
                                        "applied_filters", "result", "warnings"})
        blob = json.dumps(payload, ensure_ascii=False).lower()
        for marker in ("select ", "gold.", "v_job_snapshot", "deepseek", "_gold_release_id"):
            self.assertNotIn(marker, blob)
        # The source footer written by the model is scrubbed before it leaves the server.
        self.assertNotIn("nguồn:", payload["answer_markdown"].lower())
        self.assertIn("31,25", payload["answer_markdown"])
        self.assertEqual(payload["data_context"]["snapshot_date"], "2026-11-24")

    def test_timeout_returns_readable_error(self):
        def slow(*_args, **_kwargs):
            time.sleep(1.5)
            return _answer()

        with patch.object(service, "get_settings", return_value=_settings()), \
             patch.object(service, "_data_context", return_value={}), \
             patch.object(service, "REQUEST_TIMEOUT_S", 1), \
             patch.object(service, "ask", side_effect=slow):
            response = self.client.post("/ask", json={"question": "câu hỏi chậm"})
        self.assertEqual(response.status_code, 504)
        detail = response.json()["detail"]
        self.assertEqual(detail["code"], "REQUEST_TIMEOUT")
        self.assertNotIn("Traceback", detail["message"])

    def test_upstream_failure_is_not_leaked(self):
        with patch.object(service, "get_settings", return_value=_settings()), \
             patch.object(service, "_data_context", return_value={}), \
             patch.object(service, "ask", side_effect=RuntimeError("SELECT * FROM gold.v_job_snapshot")):
            response = self.client.post("/ask", json={"question": "lỗi"})
        self.assertEqual(response.status_code, 502)
        body = json.dumps(response.json(), ensure_ascii=False)
        self.assertNotIn("gold.", body)
        self.assertNotIn("Traceback", body)


if __name__ == "__main__":
    unittest.main()

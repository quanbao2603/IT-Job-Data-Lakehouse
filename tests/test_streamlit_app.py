"""Functional test of the Streamlit app through Streamlit's own test runtime.

Skipped when the read-only web API is not running, because the point is to prove the app
renders real Gold numbers, not mocked ones.
"""
from __future__ import annotations

import json
import unittest
import urllib.error
import urllib.request
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
APP_FILE = PROJECT_ROOT / "apps/insight-streamlit/app.py"
WEB_API = "http://127.0.0.1:8010"


def _api_up() -> bool:
    try:
        with urllib.request.urlopen(f"{WEB_API}/api/health", timeout=3) as response:
            return response.status == 200
    except (urllib.error.URLError, OSError, ValueError):
        return False


@unittest.skipUnless(_api_up(), "web API not running on 127.0.0.1:8010")
class TestStreamlitApp(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        from streamlit.testing.v1 import AppTest

        cls.app = AppTest.from_file(str(APP_FILE), default_timeout=120)
        cls.app.run()

    def test_renders_live_kpis_from_gold(self):
        self.assertEqual([e.value for e in self.app.exception], [])
        metrics = {metric.label: metric.value for metric in self.app.metric}
        self.assertIn("Tin tuyển dụng", metrics)
        self.assertRegex(metrics["Tin tuyển dụng"], r"\d")
        self.assertIn("Trung vị lương", metrics)

    def test_has_both_pages(self):
        labels = [tab.label for tab in self.app.tabs]
        self.assertEqual(labels, ["Tổng quan", "Trợ lý dữ liệu"])

    def test_dashboard_sections_present(self):
        sections = [s.value for s in self.app.subheader]
        for expected in ("Theo chuyên môn", "Theo địa điểm", "Theo cấp bậc", "Kỹ năng phổ biến", "Nền tảng"):
            self.assertIn(expected, sections)

    def test_chat_tab_states_the_configuration_it_needs(self):
        warnings = " ".join(w.value for w in self.app.warning) + " ".join(e.value for e in self.app.error)
        health = json.load(urllib.request.urlopen("http://127.0.0.1:8021/health", timeout=3))
        if not health.get("llm_configured"):
            self.assertIn("API key", warnings)
        else:
            self.assertEqual(warnings, "")


if __name__ == "__main__":
    unittest.main()

import sys
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

import os
import unittest
from types import SimpleNamespace


class FakeRepository:
    def __init__(self):
        self.release = SimpleNamespace(
            gold_release_id="gold_test",
            silver_release_id="silver_test",
            snapshot_date="2026-11-19",
            snapshot_cutoff_at="2026-11-19T07:07:34+00:00",
            switched_at="2026-11-19T08:00:00+00:00",
            dq_total=32,
            dq_failed=0,
            status="LOADED",
        )

    def close(self):
        pass

    def release_context(self):
        from server.repository import ReleaseContext
        return ReleaseContext(
            gold_release_id=self.release.gold_release_id,
            silver_release_id=self.release.silver_release_id,
            snapshot_date=self.release.snapshot_date,
            snapshot_cutoff_at=self.release.snapshot_cutoff_at,
            switched_at=self.release.switched_at,
            status="SUCCESS",
            dq_total=self.release.dq_total,
            dq_failed=self.release.dq_failed,
        )

    def ready(self):
        return self.release_context()

    def _assert_expected_release(self, context, params):
        if params.expected_release_id and params.expected_release_id != context.gold_release_id:
            from server.repository import GoldReleaseChanged
            raise GoldReleaseChanged()

    def filters(self):
        return {
            "cities": [{"value": "Hà Nội", "label": "Hà Nội", "count": 1}],
            "domains": [{"value": "Backend", "label": "Backend", "count": 1}],
            "levels": [{"value": "Senior", "label": "Senior", "count": 1}],
            "skills": [{"value": "python", "label": "Python", "count": 1}],
        }

    def dashboard(self, filters):
        return {
            "summary": {
                "jobs": 1,
                "companies": 1,
                "skills": 1,
                "salary_samples": 1,
                "salary_median": 30000000,
                "missing_city": 0,
                "last_seen_at": "2026-11-19T06:00:00+00:00",
            },
            "domains": [], "cities": [], "levels": [], "skills": [],
            "companies": [], "platforms": [],
        }

    def jobs(self, filters):
        return {"items": [], "total": 0, "page": filters.page, "page_size": filters.page_size}

    def job_detail(self, job_id):
        return None

    def skills(self, filters):
        return {"items": [], "pairs": []}

    def companies(self, filters):
        return {"items": [], "total": 0, "page": filters.page, "page_size": filters.page_size}

    def quality(self, params=None):
        return {"checks": [], "coverage": []}


class TestGoldWebAPI(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ["WEB_ENV"] = "test"
        os.environ["WEB_DEMO_MODE"] = "false"
        from fastapi.testclient import TestClient
        from server.app import create_app
        cls.client = TestClient(create_app(repository_factory=lambda: FakeRepository()))

    def test_health_does_not_claim_database_readiness(self):
        response = self.client.get("/api/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "ok")
        self.assertNotIn("gold_release_id", response.json())

    def test_ready_returns_release_metadata(self):
        response = self.client.get("/api/ready")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["status"], "ready")
        self.assertEqual(payload["meta"]["gold_release_id"], "gold_test")
        self.assertEqual(payload["meta"]["scope"], "nguồn cung tin hệ thống quan sát được")

    def test_dashboard_has_release_metadata_and_summary(self):
        response = self.client.get("/api/dashboard")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["meta"]["silver_release_id"], "silver_test")
        self.assertEqual(payload["summary"]["jobs"], 1)
        self.assertEqual(payload["meta"]["mode"], "live")

    def test_assistant_is_explicitly_disabled(self):
        response = self.client.get("/api/assistant/status")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertFalse(payload["enabled"])
        self.assertEqual(payload["status"], "not_configured")

    def test_jobs_rejects_invalid_page_size(self):
        response = self.client.get("/api/jobs?page_size=51")
        self.assertEqual(response.status_code, 422)

    def test_expected_release_mismatch_returns_structured_conflict(self):
        response = self.client.get("/api/dashboard?expected_release_id=gold_old")
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["error"]["code"], "RELEASE_CHANGED")

    def test_unknown_api_path_returns_structured_not_found(self):
        response = self.client.get("/api/does-not-exist")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["error"]["code"], "NOT_FOUND")

    def test_quality_accepts_expected_release(self):
        ok = self.client.get("/api/quality?expected_release_id=gold_test")
        self.assertEqual(ok.status_code, 200)
        stale = self.client.get("/api/quality?expected_release_id=gold_old")
        self.assertEqual(stale.status_code, 409)
        self.assertEqual(stale.json()["error"]["code"], "RELEASE_CHANGED")

    def test_validation_error_uses_contract_shape(self):
        response = self.client.get("/api/jobs?page=0")
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["error"]["code"], "INVALID_REQUEST")

    def test_unavailable_database_is_503_not_empty_result(self):
        from fastapi.testclient import TestClient
        from server.app import create_app
        from server.repository import GoldNotReady

        class UnreadyRepository(FakeRepository):
            def dashboard(self, filters):
                raise GoldNotReady()

        client = TestClient(create_app(repository_factory=lambda: UnreadyRepository()))
        response = client.get("/api/dashboard")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["error"]["code"], "GOLD_RELEASE_MISSING")

    def test_demo_mode_is_labelled_in_every_response(self):
        from fastapi.testclient import TestClient
        from server.app import create_app
        from server.config import Settings

        demo_settings = Settings(
            web_env="development", demo_mode=True, root_path="/insights",
            stale_after_days=1, query_timeout_ms=5000, db_host="127.0.0.1",
            db_port=5432, db_name="lakehouse", reader_user="gold_reader",
            reader_password="", connect_timeout=5,
        )
        client = TestClient(create_app(settings=demo_settings))
        payload = client.get("/api/dashboard").json()
        self.assertEqual(payload["meta"]["mode"], "demo")
        self.assertTrue(payload["meta"]["gold_release_id"].startswith("gold_demo_fixture"))
        self.assertGreater(payload["summary"]["jobs"], 0)

    def test_production_rejects_demo_mode(self):
        from unittest.mock import patch
        from server.config import get_settings

        get_settings.cache_clear()
        with patch.dict(os.environ, {"WEB_ENV": "production", "WEB_DEMO_MODE": "true"}, clear=True):
            with self.assertRaises(ValueError):
                get_settings()
        get_settings.cache_clear()
        os.environ["WEB_ENV"] = "test"
        os.environ["WEB_DEMO_MODE"] = "false"


if __name__ == "__main__":
    unittest.main()

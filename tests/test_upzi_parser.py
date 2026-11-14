"""
Unit tests cho UpziCrawler parser — không cần network/credentials.
Dùng fixture HTML đã lưu từ trang thật upzi.vn.
"""
import os
import re
import json
import hashlib
import unittest
from unittest.mock import patch, MagicMock
from datetime import datetime

FIXTURE_DIR = os.path.join(os.path.dirname(__file__), "fixtures", "upzi")


def _read_fixture(name: str) -> str:
    with open(os.path.join(FIXTURE_DIR, name), "r", encoding="utf-8") as f:
        return f.read()


class CrossClassCrawlerMixin:
    """Tạo instance crawler mà không gọi __init__ (tránh network/credentials)."""

    @staticmethod
    def _bare_crawler():
        from src.crawlers.upzi_crawler import UpziCrawler
        obj = object.__new__(UpziCrawler)
        # Set minimal attributes needed by parser methods
        obj.BASE_URL = "https://www.upzi.vn"
        obj.INDUSTRY_UUID = "4ba29694-240d-11f0-a329-022cb52f85bb"
        obj.LIST_BASE = f"https://www.upzi.vn/viec-lam?industry={obj.INDUSTRY_UUID}"
        return obj


class TestUpziListingParser(CrossClassCrawlerMixin, unittest.TestCase):
    """Tests cho listing page parser (flight stream decode + normalize)."""

    def setUp(self):
        self.crawler = self._bare_crawler()
        self.html = _read_fixture("list_page_snippet.html")

    def test_parse_listing_returns_3_jobs(self):
        result = self.crawler._parse_listing_page(self.html)
        self.assertEqual(len(result["jobs"]), 3)

    def test_pagination_total_301(self):
        result = self.crawler._parse_listing_page(self.html)
        self.assertEqual(result["total"], 301)
        self.assertEqual(result["pagination"]["totalPages"], 7)
        self.assertEqual(result["pagination"]["pageSize"], 50)

    def test_normalize_job_card_basic_fields(self):
        result = self.crawler._parse_listing_page(self.html)
        card = self.crawler._normalize_job_card(result["jobs"][0])
        self.assertEqual(card["job_id"], "47712eb5-569f-478b-aba6-b50f355e0ef8")
        self.assertEqual(card["job_title"], "Technical Product Classification Engineer (Trade Compliance)")
        self.assertEqual(card["company_name"], "Bosch Global Software Technologies Company Limited")
        self.assertEqual(card["company_id"], "6f767f59-4a38-4f92-a643-68ab7e95bf00")
        self.assertIn("bosch-global-software", card["company_url"])
        self.assertIn("bosch.png", card["company_logo"])

    def test_normalize_job_location_list(self):
        result = self.crawler._parse_listing_page(self.html)
        card0 = self.crawler._normalize_job_card(result["jobs"][0])
        self.assertEqual(card0["job_location"], "Hồ Chí Minh")
        card1 = self.crawler._normalize_job_card(result["jobs"][1])
        self.assertEqual(card1["job_location"], "Hồ Chí Minh, Hà Nội")

    def test_normalize_salary_pretty(self):
        result = self.crawler._parse_listing_page(self.html)
        card1 = self.crawler._normalize_job_card(result["jobs"][1])
        self.assertIn("20", card1["salary"])
        self.assertIn("VND", card1["salary"])

    def test_normalize_approved_iso(self):
        result = self.crawler._parse_listing_page(self.html)
        card0 = self.crawler._normalize_job_card(result["jobs"][0])
        self.assertEqual(card0["approved_iso"], "2026-11-13")
        card2 = self.crawler._normalize_job_card(result["jobs"][2])
        self.assertEqual(card2["approved_iso"], "2026-11-11")

    def test_normalize_deadline_iso(self):
        result = self.crawler._parse_listing_page(self.html)
        card1 = self.crawler._normalize_job_card(result["jobs"][1])
        self.assertEqual(card1["deadline_iso"], "2026-12-15")
        card0 = self.crawler._normalize_job_card(result["jobs"][0])
        self.assertEqual(card0["deadline_iso"], "")  # no deadline

    def test_normalize_job_url_contains_alias(self):
        result = self.crawler._parse_listing_page(self.html)
        card0 = self.crawler._normalize_job_card(result["jobs"][0])
        self.assertIn("/viec-lam/technical-product-classification", card0["job_url"])
        self.assertIn("47712eb5-569f-478b-aba6-b50f355e0ef8", card0["job_url"])

    def test_normalize_employment_type(self):
        result = self.crawler._parse_listing_page(self.html)
        for job in result["jobs"]:
            card = self.crawler._normalize_job_card(job)
            self.assertEqual(card["employment_type"], "Full time")

    def test_list_url_format(self):
        url1 = self.crawler._list_url(1)
        self.assertIn("industry=4ba29694", url1)
        self.assertIn("page=1", url1)
        url5 = self.crawler._list_url(5)
        self.assertIn("page=5", url5)

    def test_empty_page_returns_empty(self):
        empty_html = _read_fixture("empty_html.html")
        result = self.crawler._parse_listing_page(empty_html)
        self.assertEqual(len(result["jobs"]), 0)
        self.assertEqual(result["total"], 0)


class TestUpziDetailParser(CrossClassCrawlerMixin, unittest.TestCase):
    """Tests cho detail page parser (JSON-LD + flight extras)."""

    def setUp(self):
        self.crawler = self._bare_crawler()
        self.detail_html = _read_fixture("detail_page_snippet.html")
        # Build a minimal card dict for the detail parser
        self.card = {
            "job_id": "47712eb5-569f-478b-aba6-b50f355e0ef8",
            "job_url": "https://www.upzi.vn/viec-lam/technical-product-classification-engineer-trade-compliance-47712eb5-569f-478b-aba6-b50f355e0ef8",
            "job_title": "Technical Product Classification Engineer (Trade Compliance)",
            "company_name": "Bosch Global Software Technologies Company Limited",
            "company_id": "6f767f59-4a38-4f92-a643-68ab7e95bf00",
            "company_url": "https://www.upzi.vn/cong-ty/bosch-global-software-technologies-company-limited-6f767f59-4a38-4f92-a643-68ab7e95bf00",
            "company_logo": "https://images.upzi.vn/image/company/3133cfde/bosch.png",
            "salary": "Lương không công khai",
            "job_location": "Hồ Chí Minh",
            "deadline_raw": "",
            "deadline_iso": "",
            "approved_at_raw": "2026-11-13T17:02:11+07:00",
            "approved_iso": "2026-11-13",
            "employment_type": "Full time",
            "working_arrangement": "On-site",
            "weekend_text": "Nghỉ trọn T7, CN",
            "job_level": "Nhân viên (associate)",
            "alias": "technical-product-classification-engineer-trade-compliance-47712eb5-569f-478b-aba6-b50f355e0ef8",
        }

    def test_extract_json_ld_has_required_keys(self):
        ld = self.crawler._extract_json_ld_job(self.detail_html)
        self.assertIn("@type", ld)
        self.assertEqual(ld["@type"], "JobPosting")
        self.assertIn("title", ld)
        self.assertIn("datePosted", ld)
        self.assertIn("validThrough", ld)
        self.assertIn("employmentType", ld)
        self.assertIn("description", ld)
        self.assertIn("hiringOrganization", ld)
        self.assertIn("jobLocation", ld)

    def test_json_ld_title(self):
        ld = self.crawler._extract_json_ld_job(self.detail_html)
        self.assertEqual(ld["title"], "Technical Product Classification Engineer (Trade Compliance)")

    def test_json_ld_employment_type(self):
        ld = self.crawler._extract_json_ld_job(self.detail_html)
        self.assertEqual(ld["employmentType"], "FULL_TIME")

    def test_json_ld_date_posted_iso(self):
        ld = self.crawler._extract_json_ld_job(self.detail_html)
        self.assertTrue(ld["datePosted"].startswith("2026-11-13"))

    def test_json_ld_valid_through_iso(self):
        ld = self.crawler._extract_json_ld_job(self.detail_html)
        self.assertTrue(ld["validThrough"].startswith("2026-12-13"))

    def test_json_ld_description_not_empty(self):
        ld = self.crawler._extract_json_ld_job(self.detail_html)
        self.assertGreater(len(ld["description"]), 100)

    def test_parse_detail_full_payload(self):
        payload = self.crawler._parse_job_detail_raw(self.detail_html, self.card)
        self.assertEqual(payload["platform"], "upzi")
        self.assertEqual(payload["job_id"], "47712eb5-569f-478b-aba6-b50f355e0ef8")
        self.assertIn("Technical Product", payload["job_title"])
        self.assertIn("Bosch", payload["company_name"])
        self.assertEqual(payload["employment_type"], "FULL_TIME")
        self.assertTrue(payload["posted_date_iso"].startswith("2026-11-13"))
        self.assertTrue(payload["valid_through"].startswith("2026-12-13"))
        self.assertEqual(payload["deadline"], "2026-12-13")
        self.assertGreater(len(payload["description"]), 50)
        # content_hash is valid SHA-256
        self.assertRegex(payload["content_hash"], r'^[0-9a-f]{64}$')

    def test_payload_keys_match_contract(self):
        from src.storage.metadata_envelope import MetadataEnvelope
        contract = MetadataEnvelope.SCHEMA_CONTRACTS["upzi"]
        payload = self.crawler._parse_job_detail_raw(self.detail_html, self.card)
        payload_keys = set(payload.keys())
        self.assertEqual(payload_keys, contract,
                         f"Payload keys {payload_keys - contract} extra, {contract - payload_keys} missing")

    def test_fallback_payload_zero_loss(self):
        fallback = self.crawler._card_fallback_payload(self.card)
        self.assertEqual(fallback["platform"], "upzi")
        self.assertEqual(fallback["job_id"], self.card["job_id"])
        self.assertEqual(fallback["job_title"], self.card["job_title"])
        self.assertEqual(fallback["company_name"], self.card["company_name"])
        self.assertEqual(fallback["salary"], self.card["salary"])
        self.assertEqual(fallback["job_location"], self.card["job_location"])
        self.assertEqual(fallback["updated_date"], self.card["approved_iso"])
        self.assertEqual(fallback["employment_type"], self.card["employment_type"])
        self.assertEqual(fallback["description"], "")
        self.assertRegex(fallback["content_hash"], r'^[0-9a-f]{64}$')

    def test_detail_without_json_ld_falls_back_to_card(self):
        """If JSON-LD is absent, detail parser should still use card data."""
        empty_html = "<html><body>No JSON-LD here</body></html>"
        payload = self.crawler._parse_job_detail_raw(empty_html, self.card)
        self.assertEqual(payload["job_title"], self.card["job_title"])
        self.assertEqual(payload["company_name"], self.card["company_name"])
        self.assertEqual(payload["job_location"], self.card["job_location"])


class TestUpziFetchJobsIncremental(CrossClassCrawlerMixin, unittest.TestCase):
    """Tests cho fetch_jobs generator với mocked _make_request."""

    def setUp(self):
        self.crawler = self._bare_crawler()
        self.list_html = _read_fixture("list_page_snippet.html")
        self.detail_html = _read_fixture("detail_page_snippet.html")
        self.empty_html = _read_fixture("empty_html.html")
        # Mock heartbeat to avoid StateManager dependency
        self.crawler.heartbeat = MagicMock(return_value=True)

    def test_full_mode_iterates_pages_and_details(self):
        """Full mode with max_pages=1 should yield 3 records (one per card)."""
        call_count = [0]

        def mock_request(url, **kwargs):
            call_count[0] += 1
            if "page=" in url and "/viec-lam?" in url:
                return self.list_html
            return self.detail_html

        with patch.object(self.crawler, "_make_request", side_effect=mock_request), \
             patch("src.crawlers.upzi_crawler.time.sleep"):
            results = list(self.crawler.fetch_jobs(mode="full", max_pages=1))

        self.assertEqual(len(results), 3)
        for r in results:
            self.assertEqual(r["platform"], "upzi")
            self.assertRegex(r["content_hash"], r'^[0-9a-f]{64}$')

    def test_incremental_stops_on_last_record_id(self):
        """Incremental stops when it hits last_record_id — yields only cards before it."""
        # The 2nd card's ID is the watermark boundary
        stop_id = "b283f916-fd32-4b2b-953a-b081ebd391e7"

        def mock_request(url, **kwargs):
            if "page=" in url and "/viec-lam?" in url:
                return self.list_html
            return self.detail_html

        with patch.object(self.crawler, "_make_request", side_effect=mock_request), \
             patch("src.crawlers.upzi_crawler.time.sleep"):
            results = list(self.crawler.fetch_jobs(
                mode="incremental", max_pages=1,
                watermark_ts="2026-01-01T00:00:00Z",
                last_record_id=stop_id
            ))

        # Should yield only the first card (before hitting the watermark ID)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["job_id"], "47712eb5-569f-478b-aba6-b50f355e0ef8")

    def test_incremental_future_watermark_yields_nothing(self):
        """Watermark in the future → all cards are older → yields nothing."""
        def mock_request(url, **kwargs):
            if "page=" in url and "/viec-lam?" in url:
                return self.list_html
            return self.detail_html

        with patch.object(self.crawler, "_make_request", side_effect=mock_request), \
             patch("src.crawlers.upzi_crawler.time.sleep"):
            results = list(self.crawler.fetch_jobs(
                mode="incremental", max_pages=1,
                watermark_ts="2026-12-31T00:00:00Z",
                last_record_id=None
            ))

        self.assertEqual(len(results), 0)

    def test_incremental_equal_date_watermark_continues(self):
        """Cards on the same date as watermark should still be yielded (strict < guard)."""
        def mock_request(url, **kwargs):
            if "page=" in url and "/viec-lam?" in url:
                return self.list_html
            return self.detail_html

        with patch.object(self.crawler, "_make_request", side_effect=mock_request), \
             patch("src.crawlers.upzi_crawler.time.sleep"):
            results = list(self.crawler.fetch_jobs(
                mode="incremental", max_pages=1,
                watermark_ts="2026-11-11T00:00:00Z",
                last_record_id=None
            ))

        # Card 3 has approved_iso=2026-11-11 which equals watermark date → not < → continues
        # Cards 1,2 have 2026-11-13, 2026-11-12 which are > watermark → continue
        # All 3 should be yielded
        self.assertEqual(len(results), 3)

    def test_detail_failure_yields_card_fallback(self):
        """When detail page fails, card fallback payload is yielded."""
        def mock_request(url, **kwargs):
            if "page=" in url and "/viec-lam?" in url:
                return self.list_html
            return None  # detail always fails

        with patch.object(self.crawler, "_make_request", side_effect=mock_request), \
             patch("src.crawlers.upzi_crawler.time.sleep"):
            results = list(self.crawler.fetch_jobs(mode="full", max_pages=1))

        self.assertEqual(len(results), 3)
        for r in results:
            self.assertEqual(r["description"], "")
            self.assertEqual(r["platform"], "upzi")
            self.assertRegex(r["content_hash"], r'^[0-9a-f]{64}$')

    def test_empty_page_yields_nothing(self):
        """Empty listing page → no results."""
        def mock_request(url, **kwargs):
            return self.empty_html

        with patch.object(self.crawler, "_make_request", side_effect=mock_request), \
             patch("src.crawlers.upzi_crawler.time.sleep"):
            results = list(self.crawler.fetch_jobs(mode="full", max_pages=1))

        self.assertEqual(len(results), 0)


if __name__ == "__main__":
    unittest.main()

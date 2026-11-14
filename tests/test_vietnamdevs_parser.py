"""
Unit tests cho VietnamDevsCrawler parser — không cần network/credentials.
Dùng fixture HTML đã lưu từ trang thật vietnamdevs.com.
"""
import os
import re
import json
import hashlib
import unittest
from unittest.mock import patch, MagicMock
from datetime import datetime

FIXTURE_DIR = os.path.join(os.path.dirname(__file__), "fixtures", "vietnamdevs")


def _read_fixture(name: str) -> str:
    with open(os.path.join(FIXTURE_DIR, name), "r", encoding="utf-8") as f:
        return f.read()


class CrossClassCrawlerMixin:
    """Tạo instance crawler mà không gọi __init__ (tránh network/credentials)."""

    @staticmethod
    def _bare_crawler():
        from src.crawlers.vietnamdevs_crawler import VietnamDevsCrawler
        obj = object.__new__(VietnamDevsCrawler)
        obj.BASE_URL = "https://vietnamdevs.com"
        obj.LIST_BASE = "https://vietnamdevs.com/jobs"
        return obj


class TestVietnamDevsListingParser(CrossClassCrawlerMixin, unittest.TestCase):
    """Tests cho listing page parser (DOM card parse)."""

    def setUp(self):
        self.crawler = self._bare_crawler()
        self.html = _read_fixture("list_page_snippet.html")

    def test_parse_cards_returns_3(self):
        cards = self.crawler._parse_cards(self.html)
        self.assertEqual(len(cards), 3)

    def test_card_job_id_numeric(self):
        cards = self.crawler._parse_cards(self.html)
        for card in cards:
            self.assertRegex(card["job_id"], r'^\d{10,20}$')

    def test_card_job_url_format(self):
        cards = self.crawler._parse_cards(self.html)
        for card in cards:
            self.assertIn("/jobs/", card["job_url"])
            self.assertIn(card["job_id"], card["job_url"])

    def test_card_title_not_empty(self):
        cards = self.crawler._parse_cards(self.html)
        for card in cards:
            self.assertGreater(len(card["job_title"]), 5)

    def test_card_company_name_extracted(self):
        cards = self.crawler._parse_cards(self.html)
        # First card should have "EPOS AI"
        self.assertIn("EPOS", cards[0]["company_name"])

    def test_card_location_extracted(self):
        cards = self.crawler._parse_cards(self.html)
        # At least one card should have a location
        has_location = any(c["job_location"] for c in cards)
        self.assertTrue(has_location)

    def test_card_employment_type_extracted(self):
        cards = self.crawler._parse_cards(self.html)
        has_type = any(c["employment_type"] for c in cards)
        self.assertTrue(has_type)

    def test_parse_total_jobs(self):
        total = self.crawler._parse_total_jobs(self.html)
        self.assertEqual(total, 84)

    def test_list_url_format(self):
        url1 = self.crawler._list_url(1)
        self.assertIn("page=1", url1)
        url2 = self.crawler._list_url(2)
        self.assertIn("page=2", url2)

    def test_time_ago_to_iso_today(self):
        result = self.crawler._time_ago_to_iso("today")
        self.assertEqual(result, datetime.now().strftime("%Y-%m-%d"))

    def test_time_ago_to_iso_yesterday(self):
        from datetime import timedelta
        expected = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
        result = self.crawler._time_ago_to_iso("yesterday")
        self.assertEqual(result, expected)

    def test_time_ago_to_iso_days(self):
        from datetime import timedelta
        expected = (datetime.now() - timedelta(days=3)).strftime("%Y-%m-%d")
        result = self.crawler._time_ago_to_iso("3d")
        self.assertEqual(result, expected)

    def test_empty_page_returns_empty(self):
        empty_html = _read_fixture("empty_html.html")
        cards = self.crawler._parse_cards(empty_html)
        self.assertEqual(len(cards), 0)


class TestVietnamDevsDetailParser(CrossClassCrawlerMixin, unittest.TestCase):
    """Tests cho detail page parser (JSON-LD)."""

    def setUp(self):
        self.crawler = self._bare_crawler()
        self.detail_html = _read_fixture("detail_page_snippet.html")
        self.card = {
            "job_id": "984441672876155",
            "job_url": "https://vietnamdevs.com/jobs/984441672876155/senior-java-software-engineer-strong-ai-integration-vietnamese-only",
            "job_title": "Senior Java Software Engineer (Strong AI Integration) - Vietnamese only",
            "company_name": "EPOS AI",
            "company_meta": "Consultancy · 51-150 employees · Singapore",
            "company_logo": "https://res.cloudinary.com/dxocatyhb/image/upload/1794019301-gvmZDNsHKv",
            "salary": "",
            "job_location": "Ho Chi Minh City",
            "employment_type": "Full-time",
            "skills": "Java, Spring Boot, AWS",
            "posted_raw": "1d",
            "posted_iso": "2026-11-13",
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

    def test_json_ld_title(self):
        ld = self.crawler._extract_json_ld_job(self.detail_html)
        self.assertIn("Senior Java Software Engineer", ld["title"])

    def test_json_ld_hiring_org(self):
        ld = self.crawler._extract_json_ld_job(self.detail_html)
        org = ld.get("hiringOrganization", {})
        self.assertIn("EPOS", org.get("name", ""))

    def test_json_ld_description_not_empty(self):
        ld = self.crawler._extract_json_ld_job(self.detail_html)
        self.assertGreater(len(ld.get("description", "")), 100)

    def test_parse_detail_full_payload(self):
        payload = self.crawler._parse_job_detail_raw(self.detail_html, self.card)
        self.assertEqual(payload["platform"], "vietnamdevs")
        self.assertEqual(payload["job_id"], "984441672876155")
        self.assertIn("Senior Java", payload["job_title"])
        self.assertIn("EPOS", payload["company_name"])
        self.assertGreater(len(payload["description"]), 50)
        self.assertRegex(payload["content_hash"], r'^[0-9a-f]{64}$')

    def test_payload_keys_match_contract(self):
        from src.storage.metadata_envelope import MetadataEnvelope
        contract = MetadataEnvelope.SCHEMA_CONTRACTS["vietnamdevs"]
        payload = self.crawler._parse_job_detail_raw(self.detail_html, self.card)
        payload_keys = set(payload.keys())
        self.assertEqual(payload_keys, contract,
                         f"Payload keys {payload_keys - contract} extra, {contract - payload_keys} missing")

    def test_fallback_payload_zero_loss(self):
        fallback = self.crawler._card_fallback_payload(self.card)
        self.assertEqual(fallback["platform"], "vietnamdevs")
        self.assertEqual(fallback["job_id"], self.card["job_id"])
        self.assertEqual(fallback["job_title"], self.card["job_title"])
        self.assertEqual(fallback["company_name"], self.card["company_name"])
        self.assertEqual(fallback["job_location"], self.card["job_location"])
        self.assertEqual(fallback["updated_date"], self.card["posted_iso"])
        self.assertEqual(fallback["employment_type"], self.card["employment_type"])
        self.assertEqual(fallback["description"], "")
        self.assertRegex(fallback["content_hash"], r'^[0-9a-f]{64}$')

    def test_detail_without_json_ld_falls_back_to_card(self):
        empty_html = "<html><body>No JSON-LD here</body></html>"
        payload = self.crawler._parse_job_detail_raw(empty_html, self.card)
        self.assertEqual(payload["job_title"], self.card["job_title"])
        self.assertEqual(payload["company_name"], self.card["company_name"])
        self.assertEqual(payload["job_location"], self.card["job_location"])


class TestVietnamDevsFetchJobsIncremental(CrossClassCrawlerMixin, unittest.TestCase):
    """Tests cho fetch_jobs generator với mocked _make_request."""

    def setUp(self):
        self.crawler = self._bare_crawler()
        self.list_html = _read_fixture("list_page_snippet.html")
        self.detail_html = _read_fixture("detail_page_snippet.html")
        self.empty_html = _read_fixture("empty_html.html")
        self.crawler.heartbeat = MagicMock(return_value=True)

    def test_full_mode_iterates_pages_and_details(self):
        call_count = [0]

        def mock_request(url, **kwargs):
            call_count[0] += 1
            if "page=" in url:
                return self.list_html
            return self.detail_html

        with patch.object(self.crawler, "_make_request", side_effect=mock_request), \
             patch("src.crawlers.vietnamdevs_crawler.time.sleep"):
            results = list(self.crawler.fetch_jobs(mode="full", max_pages=1))

        self.assertEqual(len(results), 3)
        for r in results:
            self.assertEqual(r["platform"], "vietnamdevs")
            self.assertRegex(r["content_hash"], r'^[0-9a-f]{64}$')

    def test_incremental_stops_on_last_record_id(self):
        cards = self.crawler._parse_cards(self.list_html)
        stop_id = cards[1]["job_id"]  # 2nd card's ID

        def mock_request(url, **kwargs):
            if "page=" in url:
                return self.list_html
            return self.detail_html

        with patch.object(self.crawler, "_make_request", side_effect=mock_request), \
             patch("src.crawlers.vietnamdevs_crawler.time.sleep"):
            results = list(self.crawler.fetch_jobs(
                mode="incremental", max_pages=1,
                watermark_ts="2026-01-01T00:00:00Z",
                last_record_id=stop_id
            ))

        # Should yield only the first card
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["job_id"], cards[0]["job_id"])

    def test_incremental_future_watermark_stops_after_first_old_card(self):
        """Watermark in the future → cards with posted_iso < watermark stop the crawl.
        First card may have empty posted_iso (no time ago in fixture) so it gets yielded,
        then the second card triggers the guard."""
        def mock_request(url, **kwargs):
            if "page=" in url:
                return self.list_html
            return self.detail_html

        with patch.object(self.crawler, "_make_request", side_effect=mock_request), \
             patch("src.crawlers.vietnamdevs_crawler.time.sleep"):
            results = list(self.crawler.fetch_jobs(
                mode="incremental", max_pages=1,
                watermark_ts="2026-12-31T00:00:00Z",
                last_record_id=None
            ))

        # At most 1 card yielded before guard triggers (first card has empty posted_iso)
        self.assertLessEqual(len(results), 1)

    def test_detail_failure_yields_card_fallback(self):
        def mock_request(url, **kwargs):
            if "page=" in url:
                return self.list_html
            return None

        with patch.object(self.crawler, "_make_request", side_effect=mock_request), \
             patch("src.crawlers.vietnamdevs_crawler.time.sleep"):
            results = list(self.crawler.fetch_jobs(mode="full", max_pages=1))

        self.assertEqual(len(results), 3)
        for r in results:
            self.assertEqual(r["description"], "")
            self.assertEqual(r["platform"], "vietnamdevs")

    def test_empty_page_yields_nothing(self):
        def mock_request(url, **kwargs):
            return self.empty_html

        with patch.object(self.crawler, "_make_request", side_effect=mock_request), \
             patch("src.crawlers.vietnamdevs_crawler.time.sleep"):
            results = list(self.crawler.fetch_jobs(mode="full", max_pages=1))

        self.assertEqual(len(results), 0)


if __name__ == "__main__":
    unittest.main()

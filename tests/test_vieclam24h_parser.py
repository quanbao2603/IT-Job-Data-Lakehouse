"""
Unit tests cho Vieclam24hCrawler parser — không cần network/credentials.
Fixture HTML lưu từ trang thật vieclam24h.vn.
"""
import os
import json
import unittest
from unittest.mock import patch, MagicMock
from datetime import datetime

FIXTURE_DIR = os.path.join(os.path.dirname(__file__), "fixtures", "vieclam24h")


def _read_fixture(name: str) -> str:
    with open(os.path.join(FIXTURE_DIR, name), "r", encoding="utf-8") as f:
        return f.read()


def _bare_crawler():
    from src.crawlers.vieclam24h_crawler import Vieclam24hCrawler
    obj = object.__new__(Vieclam24hCrawler)
    obj.BASE_URL = "https://vieclam24h.vn"
    obj.OCCUPATION_IDS = (7, 8)
    obj.LIST_BASE = "https://vieclam24h.vn/tim-kiem-viec-lam-nhanh?occupation_ids[]=7&occupation_ids[]=8"
    obj.EXPERIENCE_MAP = Vieclam24hCrawler.EXPERIENCE_MAP
    obj.WORKING_METHOD_MAP = Vieclam24hCrawler.WORKING_METHOD_MAP
    return obj


class TestVieclam24hListingParser(unittest.TestCase):
    def setUp(self):
        self.crawler = _bare_crawler()
        self.html = _read_fixture("list_page_snippet.html")

    def test_extract_next_data_present(self):
        data = self.crawler._extract_next_data(self.html)
        self.assertIsNotNone(data)
        self.assertIn("props", data)

    def test_parse_listing_items(self):
        parsed = self.crawler._parse_listing_page(self.html)
        self.assertEqual(len(parsed["items"]), 3)

    def test_parse_listing_meta(self):
        parsed = self.crawler._parse_listing_page(self.html)
        self.assertEqual(parsed["total_pages"], 18)
        self.assertEqual(parsed["total_items"], 536)
        self.assertEqual(parsed["current"], 1)

    def test_normalize_card_identity(self):
        parsed = self.crawler._parse_listing_page(self.html)
        card = self.crawler._normalize_job_card(parsed["items"][0])
        self.assertEqual(card["job_id"], "200975007")
        self.assertIn("IT Helpdesk", card["job_title"])
        self.assertEqual(card["company_name"], "Công Ty TNHH Đầu Tư Giá Trị Việt")
        self.assertEqual(card["company_id"], "202409706")

    def test_normalize_card_url_built(self):
        parsed = self.crawler._parse_listing_page(self.html)
        card = self.crawler._normalize_job_card(parsed["items"][0])
        self.assertTrue(card["job_url"].startswith("https://vieclam24h.vn/it-"))
        self.assertIn("id200975007.html", card["job_url"])

    def test_normalize_card_company_url(self):
        parsed = self.crawler._parse_listing_page(self.html)
        card = self.crawler._normalize_job_card(parsed["items"][0])
        self.assertIn("ntd202409706", card["company_url"])

    def test_format_salary_range(self):
        self.assertEqual(self.crawler._format_salary({"salary_min": 8000000, "salary_max": 10000000}),
                         "8 triệu - 10 triệu VND")

    def test_format_salary_single(self):
        self.assertEqual(self.crawler._format_salary({"salary_min": 15000000, "salary_max": 15000000}),
                         "15 triệu VND")

    def test_format_salary_empty_when_not_public(self):
        self.assertEqual(self.crawler._format_salary({"salary_min": 0, "salary_max": 0}), "")

    def test_format_salary_billion(self):
        out = self.crawler._format_salary({"salary_min": 2000000000, "salary_max": 0})
        self.assertEqual(out, "Từ 2 tỷ VND")

    def test_ts_to_iso(self):
        # 1793008142 = 2026-10-26 (UTC)
        self.assertEqual(self.crawler._ts_to_iso(1793008142), "2026-10-26")

    def test_ts_to_iso_invalid(self):
        self.assertEqual(self.crawler._ts_to_iso(None), "")
        self.assertEqual(self.crawler._ts_to_iso("not-a-number"), "")

    def test_parse_places_decodes_json_string(self):
        raw = json.dumps([{"province_id": 210000187, "district_id": 220003489,
                           "address": "33 Phan Văn Khỏe"}])
        out = self.crawler._parse_places(raw)
        self.assertEqual(out["address_list"], ["33 Phan Văn Khỏe"])
        self.assertIn("210000187", out["structured"])

    def test_parse_places_empty(self):
        self.assertEqual(self.crawler._parse_places("")["address_list"], [])
        self.assertEqual(self.crawler._parse_places(None)["address_list"], [])

    def test_card_deadline_and_posted_iso(self):
        parsed = self.crawler._parse_listing_page(self.html)
        card = self.crawler._normalize_job_card(parsed["items"][0])
        self.assertRegex(card["deadline_iso"], r"^\d{4}-\d{2}-\d{2}$")
        self.assertRegex(card["posted_iso"], r"^\d{4}-\d{2}-\d{2}$")

    def test_card_experience_and_employment_type(self):
        parsed = self.crawler._parse_listing_page(self.html)
        card = self.crawler._normalize_job_card(parsed["items"][0])
        # experience_range = 1 → "Dưới 1 năm", working_method = 1 → "Toàn thời gian"
        self.assertEqual(card["experience"], "Dưới 1 năm")
        self.assertEqual(card["employment_type"], "Toàn thời gian")

    def test_card_skills_from_smart_tags(self):
        parsed = self.crawler._parse_listing_page(self.html)
        card = self.crawler._normalize_job_card(parsed["items"][0])
        self.assertIn("Helpdesk", card["skills"])

    def test_card_industries_from_smart_tags(self):
        parsed = self.crawler._parse_listing_page(self.html)
        card = self.crawler._normalize_job_card(parsed["items"][0])
        self.assertIn("Bán lẻ", card["industries"])

    def test_list_url_pagination(self):
        self.assertNotIn("page=", self.crawler._list_url(1))
        self.assertIn("page=2", self.crawler._list_url(2))
        self.assertIn("occupation_ids[]=7", self.crawler._list_url(5))
        self.assertIn("occupation_ids[]=8", self.crawler._list_url(5))

    def test_empty_page(self):
        parsed = self.crawler._parse_listing_page(_read_fixture("empty_html.html"))
        self.assertEqual(parsed["items"], [])
        self.assertEqual(parsed["total_items"], 0)


class TestVieclam24hDetailParser(unittest.TestCase):
    def setUp(self):
        self.crawler = _bare_crawler()
        self.detail_html = _read_fixture("detail_page_snippet.html")
        parsed = self.crawler._parse_listing_page(_read_fixture("list_page_snippet.html"))
        self.card = self.crawler._normalize_job_card(parsed["items"][0])

    def test_extract_json_ld(self):
        ld = self.crawler._extract_json_ld_job(self.detail_html)
        self.assertEqual(ld.get("@type"), "JobPosting")

    def test_json_ld_required_fields(self):
        ld = self.crawler._extract_json_ld_job(self.detail_html)
        for key in ("title", "description", "datePosted", "validThrough",
                    "employmentType", "hiringOrganization", "baseSalary"):
            self.assertIn(key, ld)

    def test_full_payload_contract_keys(self):
        from src.storage.metadata_envelope import MetadataEnvelope
        contract = MetadataEnvelope.SCHEMA_CONTRACTS["vieclam24h"]
        payload = self.crawler._parse_job_detail_raw(self.detail_html, self.card)
        self.assertEqual(set(payload.keys()), contract)

    def test_payload_title_company(self):
        payload = self.crawler._parse_job_detail_raw(self.detail_html, self.card)
        self.assertIn("IT Helpdesk", payload["job_title"])
        self.assertEqual(payload["company_name"], "Công Ty TNHH Đầu Tư Giá Trị Việt")
        self.assertEqual(payload["platform"], "vieclam24h")

    def test_payload_salary_from_base_salary(self):
        payload = self.crawler._parse_job_detail_raw(self.detail_html, self.card)
        self.assertEqual(payload["salary"], "8 triệu - 10 triệu VND")

    def test_payload_benefits_and_description(self):
        payload = self.crawler._parse_job_detail_raw(self.detail_html, self.card)
        self.assertGreater(len(payload["description"]), 50)
        self.assertGreater(len(payload["benefits"]), 20)
        self.assertNotIn("<li>", payload["description"])

    def test_payload_requirements_from_card_fallback(self):
        payload = self.crawler._parse_job_detail_raw(self.detail_html, self.card)
        # JSON-LD qualifications = null → dùng other_requirement_html của card
        self.assertIn("CNTT", payload["requirements"])

    def test_payload_experience_months_converted(self):
        payload = self.crawler._parse_job_detail_raw(self.detail_html, self.card)
        # monthsOfExperience = 11 → "Kinh nghiệm: 11 tháng" prepend vào requirements
        self.assertEqual(payload["employment_type"], "FULL_TIME")
        self.assertIn("11 tháng", payload["requirements"])

    def test_payload_deadline_from_valid_through(self):
        payload = self.crawler._parse_job_detail_raw(self.detail_html, self.card)
        self.assertRegex(payload["deadline"], r"^\d{4}-\d{2}-\d{2}$")
        self.assertEqual(payload["deadline_source"], "json_ld")

    def test_payload_industry(self):
        payload = self.crawler._parse_job_detail_raw(self.detail_html, self.card)
        self.assertIn("IT", payload["industries"])

    def test_payload_hash_and_location(self):
        payload = self.crawler._parse_job_detail_raw(self.detail_html, self.card)
        self.assertRegex(payload["content_hash"], r"^[0-9a-f]{64}$")
        self.assertGreater(len(payload["job_location"]), 3)
        self.assertGreater(len(payload["job_location_structured"]), 3)

    def test_detail_without_json_ld_falls_back(self):
        payload = self.crawler._parse_job_detail_raw("<html><body>none</body></html>", self.card)
        self.assertEqual(payload["job_title"], self.card["job_title"])
        self.assertEqual(payload["company_name"], self.card["company_name"])

    def test_fallback_payload_zero_loss(self):
        fb = self.crawler._card_fallback_payload(self.card)
        from src.storage.metadata_envelope import MetadataEnvelope
        self.assertEqual(set(fb.keys()), MetadataEnvelope.SCHEMA_CONTRACTS["vieclam24h"])
        self.assertEqual(fb["job_id"], self.card["job_id"])
        self.assertEqual(fb["company_name"], self.card["company_name"])
        self.assertEqual(fb["description"], "")
        self.assertGreater(len(fb["requirements"]), 10)
        self.assertRegex(fb["content_hash"], r"^[0-9a-f]{64}$")


class TestVieclam24hFetchJobs(unittest.TestCase):
    def setUp(self):
        self.crawler = _bare_crawler()
        self.list_html = _read_fixture("list_page_snippet.html")
        self.detail_html = _read_fixture("detail_page_snippet.html")
        self.empty_html = _read_fixture("empty_html.html")
        self.crawler.heartbeat = MagicMock(return_value=True)

    def _mock(self, detail=True):
        def fn(url, **kwargs):
            if "tim-kiem-viec-lam-nhanh" in url:
                return self.list_html
            return self.detail_html if detail else None
        return fn

    def test_full_mode_yields_all_cards(self):
        with patch.object(self.crawler, "_make_request", side_effect=self._mock()), \
             patch("src.crawlers.vieclam24h_crawler.time.sleep"):
            results = list(self.crawler.fetch_jobs(mode="full", max_pages=1))
        self.assertEqual(len(results), 3)
        self.assertTrue(all(r["platform"] == "vieclam24h" for r in results))

    def test_incremental_stops_on_last_record_id(self):
        items = self.crawler._parse_listing_page(self.list_html)["items"]
        cards = [self.crawler._normalize_job_card(i) for i in items]
        with patch.object(self.crawler, "_make_request", side_effect=self._mock()), \
             patch("src.crawlers.vieclam24h_crawler.time.sleep"):
            results = list(self.crawler.fetch_jobs(
                mode="incremental", max_pages=1,
                watermark_ts="2000-01-01T00:00:00Z",
                last_record_id=cards[1]["job_id"]))
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["job_id"], cards[0]["job_id"])

    def test_incremental_future_watermark_yields_nothing(self):
        with patch.object(self.crawler, "_make_request", side_effect=self._mock()), \
             patch("src.crawlers.vieclam24h_crawler.time.sleep"):
            results = list(self.crawler.fetch_jobs(
                mode="incremental", max_pages=1,
                watermark_ts="2099-12-31T00:00:00Z",
                last_record_id=None))
        self.assertEqual(len(results), 0)

    def test_detail_failure_uses_card_fallback(self):
        with patch.object(self.crawler, "_make_request", side_effect=self._mock(detail=False)), \
             patch("src.crawlers.vieclam24h_crawler.time.sleep"):
            results = list(self.crawler.fetch_jobs(mode="full", max_pages=1))
        self.assertEqual(len(results), 3)
        self.assertTrue(all(r["description"] == "" for r in results))

    def test_empty_page_yields_nothing(self):
        with patch.object(self.crawler, "_make_request", return_value=self.empty_html), \
             patch("src.crawlers.vieclam24h_crawler.time.sleep"):
            results = list(self.crawler.fetch_jobs(mode="full", max_pages=1))
        self.assertEqual(len(results), 0)

    def test_wrapper_guard_keeps_same_day(self):
        card = {"posted_iso": "2026-10-26"}
        self.assertFalse(self.crawler._yield_wrapper_guard(card, "2026-10-26"))
        self.assertTrue(self.crawler._yield_wrapper_guard(card, "2026-10-27"))
        self.assertFalse(self.crawler._yield_wrapper_guard(card, ""))


if __name__ == "__main__":
    unittest.main()

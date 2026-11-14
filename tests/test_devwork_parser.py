"""
Unit tests cho DevworkCrawler parser — không cần network/credentials.
Fixture HTML lưu từ trang thật devwork.vn.
"""
import os
import json
import unittest
from unittest.mock import patch, MagicMock

FIXTURE_DIR = os.path.join(os.path.dirname(__file__), "fixtures", "devwork")


def _read_fixture(name: str) -> str:
    with open(os.path.join(FIXTURE_DIR, name), "r", encoding="utf-8") as f:
        return f.read()


def _bare_crawler():
    from src.crawlers.devwork_crawler import DevworkCrawler
    obj = object.__new__(DevworkCrawler)
    obj.BASE_URL = "https://devwork.vn"
    obj.LIST_BASE = "https://devwork.vn/viec-lam"
    return obj


class TestDevworkListingParser(unittest.TestCase):
    def setUp(self):
        self.crawler = _bare_crawler()
        self.html = _read_fixture("list_page_snippet.html")

    def test_eval_nuxt_returns_data(self):
        data = self.crawler._eval_nuxt(self.html)
        self.assertIsNotNone(data)
        self.assertIn("fetch", data)

    def test_parse_listing_items(self):
        parsed = self.crawler._parse_listing_page(self.html)
        self.assertEqual(len(parsed["items"]), 3)

    def test_parse_listing_total(self):
        parsed = self.crawler._parse_listing_page(self.html)
        self.assertEqual(parsed["total_items"], 124)

    def test_normalize_card_identity(self):
        parsed = self.crawler._parse_listing_page(self.html)
        card = self.crawler._normalize_job_card(parsed["items"][0])
        self.assertEqual(card["job_id"], "2759")
        self.assertIn("Tuyển dụng", card["job_title"])

    def test_normalize_card_url(self):
        parsed = self.crawler._parse_listing_page(self.html)
        card = self.crawler._normalize_job_card(parsed["items"][0])
        self.assertTrue(card["job_url"].startswith("https://devwork.vn/viec-lam/2759/"))

    def test_format_salary_under(self):
        out = self.crawler._format_salary({"min": None, "max": 7000000})
        self.assertEqual(out, "Dưới 7 triệu VND")

    def test_format_salary_range(self):
        out = self.crawler._format_salary({"min": 15000000, "max": 25000000})
        self.assertEqual(out, "15 triệu - 25 triệu VND")

    def test_format_salary_empty(self):
        self.assertEqual(self.crawler._format_salary({"min": None, "max": None}), "")
        self.assertEqual(self.crawler._format_salary(None), "")

    def test_card_location(self):
        parsed = self.crawler._parse_listing_page(self.html)
        card = self.crawler._normalize_job_card(parsed["items"][0])
        self.assertEqual(card["job_location"], "Hà Nội")

    def test_card_skills(self):
        parsed = self.crawler._parse_listing_page(self.html)
        card = self.crawler._normalize_job_card(parsed["items"][0])
        self.assertIn("HR", card["skills"])

    def test_card_experience(self):
        parsed = self.crawler._parse_listing_page(self.html)
        card = self.crawler._normalize_job_card(parsed["items"][0])
        self.assertIn("kinh nghiệm", card["experience"].lower())

    def test_card_employment_type(self):
        parsed = self.crawler._parse_listing_page(self.html)
        card = self.crawler._normalize_job_card(parsed["items"][0])
        self.assertEqual(card["employment_type"], "Full-time")

    def test_card_deadline_from_service(self):
        parsed = self.crawler._parse_listing_page(self.html)
        card = self.crawler._normalize_job_card(parsed["items"][0])
        # service.expired_at = "30/04/2027" → "2027-04-30"
        self.assertEqual(card["deadline_iso"], "2027-04-30")

    def test_list_url_pagination(self):
        self.assertNotIn("page=", self.crawler._list_url(1))
        self.assertIn("page=2", self.crawler._list_url(2))

    def test_empty_page(self):
        parsed = self.crawler._parse_listing_page(_read_fixture("empty_html.html"))
        self.assertEqual(parsed["items"], [])
        self.assertEqual(parsed["total_items"], 0)


class TestDevworkDetailParser(unittest.TestCase):
    def setUp(self):
        self.crawler = _bare_crawler()
        self.detail_html = _read_fixture("detail_page_snippet.html")
        parsed = self.crawler._parse_listing_page(_read_fixture("list_page_snippet.html"))
        self.card = self.crawler._normalize_job_card(parsed["items"][0])

    def test_parse_detail_title(self):
        payload = self.crawler._parse_job_detail_raw(self.detail_html, self.card)
        self.assertIn("Tuyển dụng", payload["job_title"])

    def test_parse_detail_company(self):
        payload = self.crawler._parse_job_detail_raw(self.detail_html, self.card)
        self.assertIn("Devwork", payload["company_name"])

    def test_parse_detail_location(self):
        payload = self.crawler._parse_job_detail_raw(self.detail_html, self.card)
        self.assertEqual(payload["job_location"], "Hà Nội")

    def test_parse_detail_salary(self):
        payload = self.crawler._parse_job_detail_raw(self.detail_html, self.card)
        self.assertIn("triệu", payload["salary"])

    def test_parse_detail_description(self):
        payload = self.crawler._parse_job_detail_raw(self.detail_html, self.card)
        self.assertGreater(len(payload["description"]), 50)
        self.assertIn("tuyển dụng", payload["description"].lower())

    def test_parse_detail_requirements(self):
        payload = self.crawler._parse_job_detail_raw(self.detail_html, self.card)
        self.assertGreater(len(payload["requirements"]), 20)

    def test_parse_detail_deadline_from_dom(self):
        payload = self.crawler._parse_job_detail_raw(self.detail_html, self.card)
        self.assertEqual(payload["deadline"], "2027-04-30")
        self.assertEqual(payload["deadline_source"], "detail_dom")

    def test_parse_detail_employment_type(self):
        payload = self.crawler._parse_job_detail_raw(self.detail_html, self.card)
        self.assertEqual(payload["employment_type"], "Full-time")

    def test_parse_detail_vacancy_in_requirements(self):
        payload = self.crawler._parse_job_detail_raw(self.detail_html, self.card)
        # Số lượng được prepend vào requirements vì contract không có field riêng
        self.assertIn("Số lượng: 4", payload["requirements"])

    def test_full_payload_contract_keys(self):
        from src.storage.metadata_envelope import MetadataEnvelope
        contract = MetadataEnvelope.SCHEMA_CONTRACTS["devwork"]
        payload = self.crawler._parse_job_detail_raw(self.detail_html, self.card)
        self.assertEqual(set(payload.keys()), contract)

    def test_payload_hash(self):
        payload = self.crawler._parse_job_detail_raw(self.detail_html, self.card)
        self.assertRegex(payload["content_hash"], r"^[0-9a-f]{64}$")

    def test_detail_without_content_div_falls_back(self):
        payload = self.crawler._parse_job_detail_raw("<html><body>none</body></html>", self.card)
        self.assertEqual(payload["job_title"], self.card["job_title"])
        self.assertEqual(payload["description"], "")

    def test_fallback_payload_zero_loss(self):
        fb = self.crawler._card_fallback_payload(self.card)
        from src.storage.metadata_envelope import MetadataEnvelope
        self.assertEqual(set(fb.keys()), MetadataEnvelope.SCHEMA_CONTRACTS["devwork"])
        self.assertEqual(fb["job_id"], self.card["job_id"])
        self.assertEqual(fb["description"], "")
        self.assertRegex(fb["content_hash"], r"^[0-9a-f]{64}$")


class TestDevworkFetchJobs(unittest.TestCase):
    def setUp(self):
        self.crawler = _bare_crawler()
        self.list_html = _read_fixture("list_page_snippet.html")
        self.detail_html = _read_fixture("detail_page_snippet.html")
        self.empty_html = _read_fixture("empty_html.html")
        self.crawler.heartbeat = MagicMock(return_value=True)

    def _mock(self, detail=True):
        def fn(url, **kwargs):
            # Listing URLs: /viec-lam or /viec-lam?page=N
            # Detail URLs: /viec-lam/{id}/{slug}
            after_vl = url.split("/viec-lam")[-1] if "/viec-lam" in url else ""
            is_detail = bool(after_vl) and after_vl[0] == "/" and len(after_vl) > 3
            if is_detail:
                return self.detail_html if detail else None
            return self.list_html
        return fn

    def test_full_mode_yields_all_cards(self):
        with patch.object(self.crawler, "_make_request", side_effect=self._mock()), \
             patch("src.crawlers.devwork_crawler.time.sleep"):
            results = list(self.crawler.fetch_jobs(mode="full", max_pages=1))
        self.assertEqual(len(results), 3)
        self.assertTrue(all(r["platform"] == "devwork" for r in results))

    def test_incremental_stops_on_last_record_id(self):
        items = self.crawler._parse_listing_page(self.list_html)["items"]
        cards = [self.crawler._normalize_job_card(i) for i in items]
        with patch.object(self.crawler, "_make_request", side_effect=self._mock()), \
             patch("src.crawlers.devwork_crawler.time.sleep"):
            results = list(self.crawler.fetch_jobs(
                mode="incremental", max_pages=1,
                watermark_ts="2000-01-01T00:00:00Z",
                last_record_id=cards[1]["job_id"]))
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["job_id"], cards[0]["job_id"])

    def test_incremental_future_watermark_yields_nothing(self):
        with patch.object(self.crawler, "_make_request", side_effect=self._mock()), \
             patch("src.crawlers.devwork_crawler.time.sleep"):
            results = list(self.crawler.fetch_jobs(
                mode="incremental", max_pages=1,
                watermark_ts="2099-12-31T00:00:00Z",
                last_record_id=None))
        self.assertEqual(len(results), 0)

    def test_detail_failure_uses_card_fallback(self):
        with patch.object(self.crawler, "_make_request", side_effect=self._mock(detail=False)), \
             patch("src.crawlers.devwork_crawler.time.sleep"):
            results = list(self.crawler.fetch_jobs(mode="full", max_pages=1))
        self.assertEqual(len(results), 3)
        self.assertTrue(all(r["description"] == "" for r in results))

    def test_empty_page_yields_nothing(self):
        with patch.object(self.crawler, "_make_request", return_value=self.empty_html), \
             patch("src.crawlers.devwork_crawler.time.sleep"):
            results = list(self.crawler.fetch_jobs(mode="full", max_pages=1))
        self.assertEqual(len(results), 0)


if __name__ == "__main__":
    unittest.main()

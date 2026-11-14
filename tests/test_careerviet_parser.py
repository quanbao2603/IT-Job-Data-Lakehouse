import os
import re
import unittest
from unittest.mock import patch

from src.crawlers.careerviet_crawler import CareerVietCrawler
from src.storage.metadata_envelope import MetadataEnvelope


FIXTURE_DIR = os.path.join(os.path.dirname(__file__), "fixtures", "careerviet")
LIST_FIXTURE = os.path.join(FIXTURE_DIR, "list_page_snippet.html")
DETAIL_FIXTURE = os.path.join(FIXTURE_DIR, "detail_page_snippet.html")
EMPTY_FIXTURE = os.path.join(FIXTURE_DIR, "empty_html.html")


def _read(path: str) -> str:
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


class CrossClassCrawlerMixin:
    """Khởi tạo crawler mà không chạm mạng/credential — parser test thuần offline."""

    @staticmethod
    def _bare_crawler() -> CareerVietCrawler:
        return object.__new__(CareerVietCrawler)


class TestCareerVietListingParser(CrossClassCrawlerMixin, unittest.TestCase):
    def setUp(self):
        self.crawler = self._bare_crawler()
        self.html = _read(LIST_FIXTURE)

    def test_parses_all_card_containers(self):
        cards = self.crawler._parse_cards(self.html)
        self.assertEqual(len(cards), 4)
        ids = [c["job_id"] for c in cards]
        self.assertEqual(len(set(ids)), 3)  # campaign card lặp lại 1 job_id (premium slot)

    def test_job_id_and_url_from_data_id(self):
        card = self.crawler._parse_cards(self.html)[0]
        self.assertRegex(card["job_id"], r"^[0-9A-F]{8}$")
        self.assertTrue(card["job_url"].startswith("https://careerviet.vn/vi/tim-viec-lam/"))
        self.assertTrue(card["job_url"].endswith(".html"))

    def test_salary_location_deadline_updated(self):
        card = self.crawler._parse_cards(self.html)[0]
        self.assertIn("Triệu", card["salary"])
        self.assertEqual(card["job_location"], "Hà Nội")
        self.assertEqual(card["deadline_iso"], "2026-12-13")
        self.assertEqual(card["deadline_source"], "listed")
        self.assertEqual(card["updated_iso"], "2026-11-14")

    def test_countdown_deadline_becomes_iso(self):
        cards = self.crawler._parse_cards(self.html)
        countdown = [c for c in cards if c["deadline_source"] == "countdown"]
        self.assertGreaterEqual(len(countdown), 1)
        self.assertRegex(countdown[0]["deadline_iso"], r"^\d{4}-\d{2}-\d{2}$")

    def test_location_does_not_swallow_next_badge(self):
        for card in self.crawler._parse_cards(self.html):
            self.assertNotIn("Ứng", card["job_location"])
            self.assertNotIn("Cơ hội", card["job_location"])
            self.assertLessEqual(len(card["job_location"]), 20)

    def test_company_id_extracted_for_dedup(self):
        card = self.crawler._parse_cards(self.html)[0]
        self.assertRegex(card["company_id"], r"^[0-9A-F]{8}$")
        self.assertIn("/nha-tuyen-dung/", card["company_url"])

    def test_total_jobs_from_page_text(self):
        html = '<div class="result-count">584 việc làm ngành CNTT - Phần mềm</div>'
        self.assertEqual(self.crawler._parse_total_jobs(html), 584)
        self.assertIsNone(self.crawler._parse_total_jobs("<html></html>"))

    def test_list_url_matrix_is_locked(self):
        self.assertEqual(self.crawler._list_url(1), CareerVietCrawler.LIST_PAGE1)
        self.assertIn("c1,63", self.crawler._list_url(1))
        self.assertIn("-trang-7-", self.crawler._list_url(7))
        self.assertIn("c1,63", self.crawler._list_url(7))

    def test_date_conversions(self):
        c = self.crawler
        self.assertEqual(c._date_to_iso("13-12-2026"), "2026-12-13")
        rel = c._date_to_iso("6 ngày")
        self.assertRegex(rel, r"^\d{4}-\d{2}-\d{2}$")
        self.assertEqual(c._date_to_iso(""), "")


class TestCareerVietDetailParser(CrossClassCrawlerMixin, unittest.TestCase):
    def setUp(self):
        self.crawler = self._bare_crawler()
        self.html = _read(DETAIL_FIXTURE)
        self.card = {
            "job_id": "35C848A2",
            "job_url": "https://careerviet.vn/vi/tim-viec-lam/x.35C848A2.html",
            "job_title": "title từ thẻ",
            "company_name": "company từ thẻ",
            "company_id": "35A00001",
            "company_url": "https://careerviet.vn/vi/nha-tuyen-dung/x.35A00001.html",
            "salary": "Cạnh tranh",
            "job_location": "Hà Nội",
            "deadline_iso": "",
            "deadline_source": "",
            "updated_iso": "2026-11-14",
            "industries": "CNTT - Phần mềm",
            "card_meta_raw": "meta",
        }

    def test_json_ld_extraction(self):
        ld = self.crawler._extract_json_ld_job(self.html)
        self.assertEqual(ld.get("@type"), "JobPosting")
        self.assertTrue(ld.get("title"))

    def test_json_ld_absent_returns_empty(self):
        self.assertEqual(self.crawler._extract_json_ld_job("<html>nothing</html>"), {})

    def test_payload_core_fields(self):
        p = self.crawler._parse_job_detail_raw(self.html, self.card)
        self.assertEqual(p["platform"], "careerviet")
        self.assertEqual(p["job_id"], "35C848A2")
        self.assertTrue(p["job_title"])
        self.assertEqual(p["employment_type"], ["FULL_TIME"])  # không còn dính escape quote
        self.assertEqual(p["posted_date_iso"][:4], "2026")
        self.assertTrue(len(p["description"]) > 100)
        self.assertTrue(len(p["requirements"]) > 50)

    def test_content_hash_sha256(self):
        p = self.crawler._parse_job_detail_raw(self.html, self.card)
        self.assertRegex(p["content_hash"], r"^[a-f0-9]{64}$")

    def test_payload_fields_inside_schema_contract(self):
        p = self.crawler._parse_job_detail_raw(self.html, self.card)
        contract = MetadataEnvelope.SCHEMA_CONTRACTS["careerviet"]
        outside = [k for k in p if k not in contract]
        self.assertEqual(outside, [], f"Trường ngoài hợp đồng: {outside}")
        self.assertEqual(set(p.keys()), set(contract))

    def test_fallback_payload_zero_loss(self):
        p = self.crawler._card_fallback_payload(self.card)
        self.assertEqual(p["job_id"], self.card["job_id"])
        self.assertEqual(p["card_meta_raw"], "meta")
        self.assertEqual(p["description"], "")
        self.assertRegex(p["content_hash"], r"^[a-f0-9]{64}$")
        contract = MetadataEnvelope.SCHEMA_CONTRACTS["careerviet"]
        self.assertEqual([k for k in p if k not in contract], [])

    def test_detail_without_json_ld_uses_dom_sections(self):
        html = _read(EMPTY_FIXTURE)
        p = self.crawler._parse_job_detail_raw(html, self.card)
        self.assertEqual(p["job_title"], self.card["job_title"])  # rơi về dữ liệu thẻ tin
        self.assertEqual(p["company_name"], self.card["company_name"])


class TestCareerVietFetchJobsIncremental(CrossClassCrawlerMixin, unittest.TestCase):
    """Kiểm soát luồng fetch_jobs khi đã có HTML — không gọi mạng."""

    def setUp(self):
        self.crawler = self._bare_crawler()
        self.list_html = _read(LIST_FIXTURE)
        self.detail_html = _read(DETAIL_FIXTURE)
        self.crawler.heartbeat = lambda: None
        self.crawler.cookie = ""
        self.crawler.session = None

    def _record_ids(self, **kwargs):
        return [job["job_id"] for job in self.crawler.fetch_jobs(**kwargs)]

    def test_full_mode_iterates_pages_and_details(self):
        calls = []

        def fake_request(url, **kw):
            calls.append(url)
            return self.list_html if "/viec-lam/" in url and "tim-viec-lam" not in url else self.detail_html

        with patch.object(self.crawler, "_make_request", side_effect=fake_request), \
             patch("src.crawlers.careerviet_crawler.time.sleep", return_value=None):
            ids = self._record_ids(mode="full", max_pages=1)
        self.assertEqual(len(ids), 4)
        self.assertTrue(any("-trang-" not in c and "/viec-lam/" in c for c in calls))

    def test_incremental_stops_on_last_record_id(self):
        cards = self.crawler._parse_cards(self.list_html)
        first_id = cards[0]["job_id"]
        stop_mark = cards[2]["job_id"]

        def fake_request(url, **kw):
            return self.list_html if "/viec-lam/" in url and "tim-viec-lam" not in url else self.detail_html

        with patch.object(self.crawler, "_make_request", side_effect=fake_request), \
             patch("src.crawlers.careerviet_crawler.time.sleep", return_value=None):
            ids = self._record_ids(mode="incremental", max_pages=3, last_record_id=stop_mark)
        self.assertEqual(ids[0], first_id)
        self.assertEqual(len(ids), 2)  # dừng đúng trước watermark, không cào thêm

    def test_incremental_stops_on_datetime_guard(self):
        def fake_request(url, **kw):
            return self.list_html if "/viec-lam/" in url and "tim-viec-lam" not in url else self.detail_html

        # Mốc watermark TƯƠNG LAI (2026-12-31): mọi thẻ có updated 2026-11-14 < mốc
        # → dừng ngay thẻ đầu tiên, không yield tin nào (guard chạy TRƯỚC khi tải chi tiết).
        with patch.object(self.crawler, "_make_request", side_effect=fake_request), \
             patch("src.crawlers.careerviet_crawler.time.sleep", return_value=None):
            ids = self._record_ids(mode="incremental", max_pages=3, watermark_ts="2026-12-31T00:00:00Z")
        self.assertEqual(ids, [])

    def test_datetime_guard_keeps_same_day_cards(self):
        def fake_request(url, **kw):
            return self.list_html if "/viec-lam/" in url and "tim-viec-lam" not in url else self.detail_html

        # Mốc bằng đúng ngày cập nhật (2026-11-14): so sánh nghiêm ngặt '<' nên trùng ngày
        # vẫn phải quét tiếp — nhường quyết định dừng cho id_cursor.
        with patch.object(self.crawler, "_make_request", side_effect=fake_request), \
             patch("src.crawlers.careerviet_crawler.time.sleep", return_value=None):
            ids = self._record_ids(mode="incremental", max_pages=1, watermark_ts="2026-11-14T08:00:00Z")
        self.assertEqual(len(ids), 4)

    def test_detail_failure_falls_back_to_card_payload(self):
        def fake_request(url, **kw):
            if "tim-viec-lam" in url:
                return None  # giả lập trang chi tiết chết
            return self.list_html

        with patch.object(self.crawler, "_make_request", side_effect=fake_request), \
             patch("src.crawlers.careerviet_crawler.time.sleep", return_value=None):
            jobs = list(self.crawler.fetch_jobs(mode="full", max_pages=1))
        self.assertEqual(len(jobs), 4)
        self.assertTrue(all(j["description"] == "" for j in jobs))
        self.assertTrue(all(j["job_title"] for j in jobs))

    def test_stops_when_page_has_no_cards(self):
        with patch.object(self.crawler, "_make_request", return_value="<html></html>"), \
             patch("src.crawlers.careerviet_crawler.time.sleep", return_value=None):
            ids = self._record_ids(mode="full", max_pages=5)
        self.assertEqual(ids, [])


if __name__ == "__main__":
    unittest.main()

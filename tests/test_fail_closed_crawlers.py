import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from src.crawlers.base_crawler import (
    BaseCrawler,
    CrawlerSourceError,
    emit_cli_result,
    require_listing_data,
)
from src.crawlers.careerviet_crawler import CareerVietCrawler
from src.crawlers.itviec_crawler import ITViecCrawler
from src.crawlers.topcv_crawler import TopCVCrawler
from src.crawlers.topdev_crawler import TopDevCrawler
from src.crawlers.upzi_crawler import UpziCrawler
from src.crawlers.vieclam24h_crawler import Vieclam24hCrawler
from src.crawlers.vietnamdevs_crawler import VietnamDevsCrawler
from src.crawlers.vietnamworks_crawler import VietnamWorksCrawler
from src.storage.state_manager import StateManager


class _FailingCrawler(BaseCrawler):
    def __init__(self, db_path):
        self.source_name = "failure_test"
        self.state_manager = StateManager(db_path=db_path)
        self.writer = None

    def fetch_jobs(self, **kwargs):
        raise CrawlerSourceError("listing request failed: HTTP 403")
        yield  # keep this a generator-shaped crawler contract


class TestFailClosedCrawlers(unittest.TestCase):
    def setUp(self):
        self.db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.db_path = self.db.name
        self.db.close()

    def tearDown(self):
        for path in (self.db_path, f"{self.db_path}-wal", f"{self.db_path}-shm"):
            try:
                os.remove(path)
            except FileNotFoundError:
                pass

    def test_source_failure_marks_run_failed_and_does_not_advance_watermark(self):
        crawler = _FailingCrawler(self.db_path)
        with self.assertRaises(CrawlerSourceError):
            crawler.run(mode="incremental")

        state = crawler.state_manager.get_watermark("failure_test")
        self.assertEqual(state["last_run_status"], "FAILED")
        self.assertIsNone(state["last_watermark_ts"])

    def test_topcv_listing_failure_is_not_empty_success(self):
        crawler = TopCVCrawler.__new__(TopCVCrawler)
        crawler.source_name = "topcv"
        with patch.object(crawler, "_make_request", return_value=None):
            with self.assertRaises(CrawlerSourceError):
                list(crawler.fetch_jobs(mode="incremental", max_pages=1))

    def test_vieclam24h_listing_failure_is_not_empty_success(self):
        crawler = Vieclam24hCrawler.__new__(Vieclam24hCrawler)
        crawler.source_name = "vieclam24h"
        with patch.object(crawler, "_make_request", return_value=None):
            with self.assertRaises(CrawlerSourceError):
                list(crawler.fetch_jobs(mode="incremental", max_pages=1))

    def test_http_403_is_blocked_without_retry(self):
        crawler = TopCVCrawler.__new__(TopCVCrawler)
        crawler.source_name = "topcv"
        crawler.session = SimpleNamespace(
            get=lambda *_args, **_kwargs: SimpleNamespace(status_code=403)
        )
        with self.assertRaisesRegex(CrawlerSourceError, "HTTP 403"):
            crawler._make_request("https://example.invalid/listing")

    def test_topcv_uses_working_cloudflare_impersonation_profile(self):
        crawler = TopCVCrawler.__new__(TopCVCrawler)
        with patch("src.crawlers.topcv_crawler.requests.Session") as session_factory, patch(
            "src.crawlers.topcv_crawler.HAVE_CURL_CFFI", True
        ):
            session_factory.return_value.headers = {}
            session_factory.return_value.cookies.update = lambda *_args, **_kwargs: None
            crawler.cookie = ""
            crawler._create_session()
        session_factory.assert_called_once_with(impersonate="chrome120")

    def test_empty_listing_with_source_total_is_parser_failure(self):
        crawler = UpziCrawler.__new__(UpziCrawler)
        crawler.source_name = "upzi"
        crawler.heartbeat = lambda: True
        html = (
            '<script>self.__next_f.push([1,"36:[null,{\\"initialResponse\\":'
            '{\\"status\\":\\"success\\",\\"data\\":[],\\"meta\\":'
            '{\\"pagination\\":{\\"total\\":301}}}]")</script>'
        )
        with patch.object(crawler, "_make_request", return_value=html), patch.object(
            crawler,
            "_parse_listing_page",
            return_value={"jobs": [], "pagination": {"totalPages": 7}, "total": 301},
        ):
            with self.assertRaises(CrawlerSourceError):
                list(crawler.fetch_jobs(mode="full", max_pages=1))

    def test_vietnamworks_metadata_failure_is_not_empty_success(self):
        crawler = VietnamWorksCrawler.__new__(VietnamWorksCrawler)
        crawler.source_name = "vietnamworks"
        crawler.api_session = SimpleNamespace(
            post=lambda *_args, **_kwargs: SimpleNamespace(status_code=403)
        )
        with self.assertRaises(CrawlerSourceError):
            crawler.fetch_jobs(mode="full", max_pages=1)

    def test_listing_contract_requires_explicit_empty_confirmation(self):
        with self.assertRaises(CrawlerSourceError):
            require_listing_data("topcv", 0)
        with self.assertRaises(CrawlerSourceError):
            require_listing_data("topcv", 0, reported_total=301, total_known=True)
        require_listing_data("topcv", 0, reported_total=0, total_known=True)

    def test_only_success_result_has_zero_exit_code(self):
        self.assertEqual(emit_cli_result({"status": "SUCCESS"}), 0)
        self.assertEqual(emit_cli_result({"status": "LOCKED"}), 1)


if __name__ == "__main__":
    unittest.main()

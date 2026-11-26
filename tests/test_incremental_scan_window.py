"""Incremental runs must look at a small page window, not at one page.

The crawlers used to stop at the first card matching the last-seen id. On sites that sort by
relevance or promote paid listings that card sits on page 1, so the run finished on page 1 and
reported SUCCESS while skipping every job posted that day (itviec: 0 records on 25/11 while
page 1 held 21, three of them posted 26/11).
"""
import os
import unittest
from unittest.mock import patch

from src.crawlers.base_crawler import incremental_scan_pages


class TestIncrementalScanWindow(unittest.TestCase):
    def test_full_mode_keeps_the_requested_page_count(self):
        self.assertEqual(incremental_scan_pages("full", 12), 12)

    def test_incremental_defaults_to_two_pages(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("LAKEHOUSE_INCREMENTAL_SCAN_PAGES", None)
            self.assertEqual(incremental_scan_pages("incremental", 12), 2)

    def test_window_never_exceeds_the_caller_budget(self):
        with patch.dict(os.environ, {"LAKEHOUSE_INCREMENTAL_SCAN_PAGES": "9"}):
            self.assertEqual(incremental_scan_pages("incremental", 3), 3)
            self.assertEqual(incremental_scan_pages("incremental", 1), 1)

    def test_window_can_be_widened_by_env(self):
        with patch.dict(os.environ, {"LAKEHOUSE_INCREMENTAL_SCAN_PAGES": "4"}):
            self.assertEqual(incremental_scan_pages("incremental", 12), 4)

    def test_garbage_env_falls_back_instead_of_crashing(self):
        with patch.dict(os.environ, {"LAKEHOUSE_INCREMENTAL_SCAN_PAGES": "-3"}):
            self.assertEqual(incremental_scan_pages("incremental", 5), 1)


if __name__ == "__main__":
    unittest.main()

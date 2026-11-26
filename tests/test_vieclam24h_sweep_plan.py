"""The sweep must never re-crawl a finished sweep just because the rebuild failed.

A failed Silver/Gold rebuild used to look like "no pages left", which reset the checkpoint and
re-crawled all 18 pages every hour for a whole night. These tests pin the decision so that
cannot come back.
"""
import unittest

from scripts.vieclam24h_sweep import plan_next


class TestSweepPlan(unittest.TestCase):
    def test_fresh_sweep_starts_at_page_one(self):
        action, pages = plan_next([], total=18, per_run=3)
        self.assertEqual(action, "crawl")
        self.assertEqual(pages, [1, 2, 3])

    def test_partial_sweep_continues_where_it_stopped(self):
        action, pages = plan_next([1, 2, 3, 4], total=18, per_run=3)
        self.assertEqual(action, "crawl")
        self.assertEqual(pages, [5, 6, 7])

    def test_last_slice_is_short(self):
        action, pages = plan_next(list(range(1, 18)), total=18, per_run=3)
        self.assertEqual(action, "crawl")
        self.assertEqual(pages, [18])

    def test_complete_page_set_asks_for_rebuild_only(self):
        action, pages = plan_next(list(range(1, 19)), total=18, per_run=3)
        self.assertEqual(action, "rebuild")
        self.assertEqual(pages, [])

    def test_out_of_order_checkpoint_still_finds_the_gap(self):
        action, pages = plan_next([1, 2, 4, 5], total=6, per_run=2)
        self.assertEqual(action, "crawl")
        self.assertEqual(pages, [3, 6])


if __name__ == "__main__":
    unittest.main()

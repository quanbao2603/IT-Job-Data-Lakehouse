"""Retention must protect what Gold still needs and never touch the newest release."""
import unittest
from datetime import datetime, timedelta

from scripts.silver_retention import select_prunable

NOW = datetime(2026, 11, 26, 12, 0, 0)


def _release(days_ago: int) -> datetime:
    return NOW - timedelta(days=days_ago)


class TestSilverRetention(unittest.TestCase):
    def test_nothing_to_do_on_an_empty_root(self):
        self.assertEqual(
            select_prunable({}, protected=set(), keep_last=3, keep_days=14, today=NOW), []
        )

    def test_newest_release_is_never_pruned(self):
        releases = {"only": _release(90)}
        self.assertEqual(
            select_prunable(releases, protected=set(), keep_last=0, keep_days=0, today=NOW), []
        )

    def test_gold_referenced_release_survives_no_matter_how_old(self):
        releases = {"ancient": _release(120), "fresh": _release(1)}
        pruned = select_prunable(
            releases, protected={"ancient"}, keep_last=1, keep_days=1, today=NOW
        )
        self.assertEqual(pruned, [])

    def test_old_unreferenced_release_is_pruned(self):
        releases = {"old": _release(60), "new": _release(1)}
        pruned = select_prunable(
            releases, protected=set(), keep_last=1, keep_days=1, today=NOW
        )
        self.assertEqual(pruned, ["old"])

    def test_keep_last_window_protects_recent_releases(self):
        releases = {f"r{i}": _release(i) for i in range(1, 7)}  # r1 newest .. r6 oldest
        pruned = select_prunable(
            releases, protected=set(), keep_last=3, keep_days=0, today=NOW
        )
        self.assertEqual(pruned, ["r6", "r5", "r4"])

    def test_age_window_protects_everything_recent(self):
        releases = {"a": _release(1), "b": _release(3), "c": _release(20)}
        pruned = select_prunable(
            releases, protected=set(), keep_last=1, keep_days=7, today=NOW
        )
        self.assertEqual(pruned, ["c"])

    def test_prunable_is_oldest_first(self):
        releases = {"x": _release(30), "y": _release(90), "z": _release(50), "new": _release(0)}
        pruned = select_prunable(
            releases, protected=set(), keep_last=1, keep_days=0, today=NOW
        )
        self.assertEqual(pruned, ["y", "z", "x"])


if __name__ == "__main__":
    unittest.main()

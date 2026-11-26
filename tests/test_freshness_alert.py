"""Freshness guard behaviour: it must alert on a silent source and stay quiet otherwise."""
import unittest
from datetime import date

from scripts.freshness_alert import evaluate_freshness, format_alert

TODAY = date(2026, 11, 26)


class TestFreshnessGuard(unittest.TestCase):
    def test_all_fresh_sends_nothing(self):
        stale = evaluate_freshness(
            {"topcv": TODAY, "itviec": date(2026, 11, 25)}, today=TODAY, max_age_days=2
        )
        self.assertEqual(stale, [])

    def test_source_at_the_threshold_is_not_stale(self):
        stale = evaluate_freshness({"topcv": date(2026, 11, 24)}, today=TODAY, max_age_days=2)
        self.assertEqual(stale, [])

    def test_one_day_past_the_threshold_alerts(self):
        stale = evaluate_freshness({"devwork": date(2026, 11, 23)}, today=TODAY, max_age_days=2)
        self.assertEqual(len(stale), 1)
        self.assertEqual(stale[0]["platform"], "devwork")
        self.assertEqual(stale[0]["age_days"], 3)

    def test_missing_observation_is_stale(self):
        stale = evaluate_freshness({"topcv": None}, today=TODAY, max_age_days=2)
        self.assertEqual(stale[0]["age_days"], None)
        self.assertIn("chưa từng có bản ghi", format_alert(stale, today=TODAY, max_age_days=2))

    def test_worst_source_is_reported_first(self):
        stale = evaluate_freshness(
            {"a": date(2026, 11, 20), "b": date(2026, 11, 10)},
            today=TODAY,
            max_age_days=2,
        )
        self.assertEqual([row["platform"] for row in stale], ["b", "a"])

    def test_alert_text_carries_platform_and_age(self):
        stale = evaluate_freshness({"devwork": date(2026, 11, 14)}, today=TODAY, max_age_days=2)
        text = format_alert(stale, today=TODAY, max_age_days=2)
        self.assertIn("devwork", text)
        self.assertIn("12 ngày", text)
        self.assertIn("26/11", text)


if __name__ == "__main__":
    unittest.main()

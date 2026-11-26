"""Agent answers must not claim a posting is still live; the data cannot support it."""
import unittest

from src.agent.sanitize import scrub_answer


class TestLivenessScrub(unittest.TestCase):
    def test_open_count_clause_is_dropped_but_the_count_survives(self):
        text = "- Số tin: **2.176 tin** - Trong đó có trạng thái đang mở: **2.176 tin**"
        self.assertEqual(scrub_answer(text).strip(), "- Số tin: **2.176 tin**")

    def test_sentence_claiming_open_postings_is_removed(self):
        self.assertEqual(scrub_answer("Có 2.176 tin đang mở tại Hà Nội.").strip(), "")

    def test_all_jobs_open_claim_is_removed(self):
        self.assertEqual(scrub_answer("Tất cả 4.853 tin đang tuyển.").strip(), "")

    def test_neutral_figures_are_untouched(self):
        text = "Hà Nội có 2.176 tin, trung vị 20 triệu."
        self.assertEqual(scrub_answer(text), text)

    def test_multi_sentence_answer_keeps_the_facts(self):
        text = "Có 1.234 tin. Trong đó 1.234 tin đang mở. Lương trung vị 20 triệu."
        cleaned = scrub_answer(text)
        self.assertIn("1.234 tin", cleaned)
        self.assertIn("20 triệu", cleaned)
        self.assertNotIn("đang mở", cleaned.lower())


if __name__ == "__main__":
    unittest.main()

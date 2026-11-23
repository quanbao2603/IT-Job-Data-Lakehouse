"""Gold must never publish a literal UNKNOWN company as a real employer."""
import unittest

from src.gold.load_dimensions import resolve_company_key, unknown_company_key


class TestUnknownCompanyResolution(unittest.TestCase):
    def test_marker_and_empty_name_map_to_the_unknown_member(self):
        expected = unknown_company_key()
        for canonical in ("UNKNOWN", "unknown", "N/A", "Chưa xác định", ""):
            self.assertEqual(
                resolve_company_key({"company_name_normalized": ""}, canonical_company=canonical),
                expected,
                canonical,
            )

    def test_real_company_keeps_its_own_key(self):
        key = resolve_company_key({"company_name_normalized": "fpt software"})
        self.assertNotEqual(key, unknown_company_key())


if __name__ == "__main__":
    unittest.main()

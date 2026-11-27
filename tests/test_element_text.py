"""Element text must keep the boundaries between child nodes.

TopCV's title element is a set of nested spans; `get_text(strip=True)` glued them together and the
dashboard showed "trợ lý dự áncông nghệ thông tin" and "thực tập sinhqc (travel tech)".
"""
import unittest

from bs4 import BeautifulSoup

from src.crawlers.base_crawler import element_text


class TestElementText(unittest.TestCase):
    def test_sibling_nodes_keep_their_space(self):
        soup = BeautifulSoup('<h1>trợ lý dự án<span>công nghệ thông tin</span></h1>', "html.parser")
        self.assertEqual(element_text(soup.find("h1")), "trợ lý dự án công nghệ thông tin")

    def test_line_break_inside_a_title_becomes_a_space(self):
        soup = BeautifulSoup("<h1>thực tập sinh<br>qc (travel tech)</h1>", "html.parser")
        self.assertEqual(element_text(soup.find("h1")), "thực tập sinh qc (travel tech)")

    def test_repeated_whitespace_is_collapsed(self):
        soup = BeautifulSoup("<h1>  data \n\n engineer  </h1>", "html.parser")
        self.assertEqual(element_text(soup.find("h1")), "data engineer")

    def test_missing_element_is_empty(self):
        self.assertEqual(element_text(None), "")


if __name__ == "__main__":
    unittest.main()

import unittest
from types import SimpleNamespace

from skills.scrapling.api import _page_html_output


class TestScraplingApi(unittest.TestCase):
    def test_page_html_output_decodes_body_bytes(self):
        page = SimpleNamespace(body=b"<html><body>ok</body></html>")

        result = _page_html_output(page)

        self.assertEqual(result, "<html><body>ok</body></html>")

    def test_page_html_output_prefers_body_html_attribute(self):
        page = SimpleNamespace(body=SimpleNamespace(html="<div>hello</div>"))

        result = _page_html_output(page)

        self.assertEqual(result, "<div>hello</div>")

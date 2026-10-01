#!/usr/bin/env python3

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from search_probe import build_search_request, is_blocked_page  # noqa: E402


class SearchProbeTests(unittest.TestCase):
    def test_get_relative_url(self):
        request = build_search_request(
            {"bookSourceUrl": "https://a.example/", "searchUrl": "/search?q={{key}}&p={{page}}"},
            "斗罗大陆",
        )
        self.assertEqual(request["method"], "GET")
        self.assertTrue(request["url"].startswith("https://a.example/search?q=%E6%96%97"))
        self.assertTrue(request["url"].endswith("&p=1"))

    def test_post_with_gbk_charset(self):
        request = build_search_request(
            {
                "bookSourceUrl": "https://b.example",
                "searchUrl": '/s.php,{"method":"POST","body":"kw={{key}}","charset":"gbk"}',
            },
            "斗罗大陆",
        )
        self.assertEqual(request["method"], "POST")
        self.assertEqual(request["url"], "https://b.example/s.php")
        self.assertEqual(request["body"], "kw=%B6%B7%C2%DE%B4%F3%C2%BD")

    def test_js_search_is_not_static(self):
        self.assertIsNone(build_search_request(
            {"bookSourceUrl": "https://c.example", "searchUrl": "@js:java.ajax('x')"}, "斗罗大陆"))
        self.assertIsNone(build_search_request(
            {"bookSourceUrl": "https://c.example", "searchUrl": "/s?k={{key}}&t={{Date.now()}}"}, "斗罗大陆"))

    def test_blocked_page(self):
        self.assertTrue(is_blocked_page("<title>Just a moment...</title>"))
        self.assertFalse(is_blocked_page("<html>斗罗大陆 唐家三少</html>"))


if __name__ == "__main__":
    unittest.main()

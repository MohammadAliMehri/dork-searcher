"""Offline unit tests for dork_search.py JSON API backends and dork translation."""

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import dork_search as ds  # noqa: E402


class TestJsonParsers(unittest.TestCase):
    def test_urlscan_page_and_task_urls(self):
        body = json.dumps({"total": 2, "results": [
            {"page": {"url": "https://a.example/admin/login"},
             "task": {"url": "http://a.example/"}},
            {"page": {"url": "https://b.example/admin/login"}}]})
        self.assertEqual(ds.parse_urlscan_json(body),
                         ["https://a.example/admin/login", "http://a.example/",
                          "https://b.example/admin/login"])

    def test_searx_json(self):
        body = json.dumps({"results": [{"url": "https://a.example/x"},
                                       {"url": "ftp://bad"},
                                       {"url": "https://b.example/y"}]})
        self.assertEqual(ds.parse_searx_json(body),
                         ["https://a.example/x", "https://b.example/y"])

    def test_generic_anchors(self):
        serp = '<a href="https://a.example/x">x</a><a href="/rel">y</a>'
        self.assertEqual(ds.parse_generic_anchors(serp), ["https://a.example/x"])


class TestTranslateDork(unittest.TestCase):
    def test_urlscan_title_and_inurl(self):
        self.assertEqual(
            ds.translate_dork('intitle:"Admin Login" inurl:/admin/login',
                              "urlscan"),
            'page.title:"Admin Login" filename:login')

    def test_urlscan_unquoted_forms_and_intext_drop(self):
        self.assertEqual(
            ds.translate_dork('intitle:Panel intext:"version 2.1"', "urlscan"),
            'page.title:"Panel"')

    def test_plain_engines_pass_dorks_through(self):
        dork = 'intitle:"Admin Login" inurl:/admin/login'
        for eng in ("google", "bing", "ddg", "searx"):
            self.assertEqual(ds.translate_dork(dork, eng), dork)


class TestBuildRequest(unittest.TestCase):
    def test_urlscan_request(self):
        url, hdrs, data = ds.build_request("urlscan", 'intitle:"X"', 0, "")
        self.assertIn("urlscan.io/api/v1/search/", url)
        self.assertIn("page.title", url)
        self.assertIsNone(data)

    def test_searx_request_uses_instance(self):
        url, _, _ = ds.build_request("searx", "dork", 1, "https://inst.example/")
        self.assertEqual(url, "https://inst.example/search?q=dork&format=json&pageno=2")

    def test_unknown_engine_raises(self):
        with self.assertRaises(ValueError):
            ds.build_request("nosuch", "dork", 0, "")


class TestApiFallback(unittest.TestCase):
    def test_html_fallback_drops_instance_links(self):
        s = ds.DorkSearcher(ds.SearchConfig(searx_instance="https://inst.example"))
        body = ('<a href="https://inst.example/search?q=x">self</a>'
                '<a href="https://app.example.com/admin/">hit</a>')
        self.assertEqual(s._parse_api("searx", body), ["https://app.example.com/admin/"])

    def test_json_body_routes_to_parser(self):
        s = ds.DorkSearcher(ds.SearchConfig())
        body = json.dumps({"results": [{"page": {"url": "https://app.example.com/"}}]})
        self.assertEqual(s._parse_api("urlscan", body), ["https://app.example.com/"])


if __name__ == "__main__":
    unittest.main()

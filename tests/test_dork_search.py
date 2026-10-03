"""Offline unit tests for dork_search.py (no network access).

Run from the project folder:
    python -m unittest discover -s tests -v
"""

import base64
import contextlib
import io
import json
import os
import re
import sys
import tempfile
import types
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import dork_search as ds  # noqa: E402


def b64url(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode()).decode().rstrip("=")


def bing_serp(*urls: str) -> str:
    links = "".join(
        f'<li class="b_algo"><h2><a href="https://www.bing.com/ck/a?'
        f'&amp;u=a1{b64url(u)}&amp;ntb=1">r</a></h2></li>' for u in urls)
    return f"<html><body><ul>{links}</ul></body></html>"


class TestParseBing(unittest.TestCase):
    def test_decodes_wrapped_urls(self):
        serp = bing_serp("https://app.example.com/admin/login",
                         "http://203.0.113.7:8080/")
        self.assertEqual(
            ds.parse_bing(serp),
            ["https://app.example.com/admin/login",
             "http://203.0.113.7:8080/"])

    def test_handles_html_entity_amps(self):
        # SERPs encode & as &amp; — the parser must unescape before matching
        serp = bing_serp("https://app.example.org/admin/login")
        self.assertIn("&amp;u=a1", serp)
        self.assertEqual(len(ds.parse_bing(serp)), 1)

    def test_ignores_undecodable_tokens(self):
        serp = '<a href="/ck/a?&u=a1!!!notbase64!!!&x=1">x</a>'
        self.assertEqual(ds.parse_bing(serp), [])

    def test_skips_non_http_decoded_targets(self):
        serp = f'<a href="/ck/a?&u=a1{b64url("javascript:alert(1)") }">x</a>'
        self.assertEqual(ds.parse_bing(serp), [])


class TestParseGoogle(unittest.TestCase):
    def test_classic_url_redirect_links(self):
        serp = ('<a href="/url?q=https%3A%2F%2Fapp.example.com%2Fadmin%2Flogin'
                '&amp;sa=U&amp;ved=1">a</a>')
        self.assertEqual(ds.parse_google(serp),
                         ["https://app.example.com/admin/login"])

    def test_direct_organic_hrefs_when_no_redirects(self):
        serp = ('<a href="https://app.example.net/admin/login">a</a>'
                '<a href="https://www.google.com/search?q=x">g</a>')
        self.assertEqual(ds.parse_google(serp),
                         ["https://app.example.net/admin/login"])

    def test_prefers_redirect_links_over_nav(self):
        serp = ('<a href="/url?q=https%3A%2F%2Fapp.example.com%2F">a</a>'
                '<a href="https://other.example.org/">b</a>')
        self.assertEqual(ds.parse_google(serp), ["https://app.example.com/"])


class TestParseDdgMojeek(unittest.TestCase):
    def test_ddg_direct_links(self):
        serp = '<a rel="nofollow" href="https://app.example.com/admin/">x</a>'
        self.assertEqual(ds.parse_ddg(serp), ["https://app.example.com/admin/"])

    def test_ddg_unwraps_uddg(self):
        serp = ('<a href="https://duckduckgo.com/l/?uddg='
                'https%3A%2F%2Fapp.example.org%2Fadmin&amp;rut=abc">x</a>')
        self.assertEqual(ds.parse_ddg(serp), ["https://app.example.org/admin"])

    def test_mojeek_anchors(self):
        serp = ('<a class="ob" href="https://app.example.com/admin/login">t</a>'
                '<a href="/search?q=x">nav</a>')
        self.assertEqual(ds.parse_mojeek(serp),
                         ["https://app.example.com/admin/login"])


class TestUrlNormalization(unittest.TestCase):
    def test_strips_path_keeps_port(self):
        self.assertEqual(ds.normalize_base_url("https://app.example.com:8443/admin/login"),
                         "https://app.example.com:8443")
        self.assertEqual(ds.normalize_base_url("http://192.0.2.10/x?y=1"),
                         "http://192.0.2.10")

    def test_rejects_non_http_and_junk(self):
        self.assertIsNone(ds.normalize_base_url("ftp://app.example.com"))
        self.assertIsNone(ds.normalize_base_url("not a url"))
        self.assertIsNone(ds.normalize_base_url(""))

    def test_noise_filter_exact_and_subdomain_only(self):
        self.assertTrue(ds.is_noise_url("https://gist.github.com/x"))
        self.assertTrue(ds.is_noise_url("https://stackoverflow.com/q/1"))
        self.assertFalse(ds.is_noise_url("https://notgithub.com"))
        self.assertFalse(ds.is_noise_url("https://github.com.evil.example"))
        self.assertFalse(ds.is_noise_url("https://app.example.com/admin/"))


class TestSearcherFiltering(unittest.TestCase):
    def _searcher(self, **kw):
        return ds.DorkSearcher(ds.SearchConfig(pages=1, **kw))

    def test_noise_and_dedupe(self):
        s = self._searcher()
        s._record("dork-a", "bing",
                  ["https://github.com/x", "https://app.example.com/a",
                   "https://app.example.com/b"], ds.DorkStats())
        self.assertEqual([h.base_url for h in s.hits], ["https://app.example.com"])

    def test_url_filter_keeps_only_matches(self):
        s = self._searcher(url_filter=re.compile(r"/admin/login"))
        s._record("d", "bing",
                  ["https://app.example.com/admin/login",
                   "https://app.example.com/other/"], ds.DorkStats())
        self.assertEqual([h.base_url for h in s.hits], ["https://app.example.com"])

    def test_include_noise_keeps_engines(self):
        s = self._searcher(filter_noise=False)
        s._record("d", "bing", ["https://github.com/x"], ds.DorkStats())
        self.assertEqual(len(s.hits), 1)

    def test_cross_dork_provenance_single_hit(self):
        s = self._searcher()
        s._record("d1", "bing", ["https://app.example.com/"], ds.DorkStats())
        s._record("d2", "ddg", ["https://app.example.com/"], ds.DorkStats())
        self.assertEqual(len(s.hits), 1)
        self.assertEqual(s.hits[0].dork, "d1")


class TestDorkLoading(unittest.TestCase):
    def test_skips_comments_and_blanks(self):
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False,
                                         encoding="utf-8") as fh:
            fh.write("# comment\n\ndork one\ndork two\n")
            path = fh.name
        try:
            self.assertEqual(ds.load_dork_file(path), ["dork one", "dork two"])
        finally:
            os.unlink(path)


class TestImportArg(unittest.TestCase):
    def test_splits_dork_with_equals_sign(self):
        with tempfile.NamedTemporaryFile("w", suffix=".html", delete=False,
                                         encoding="utf-8") as fh:
            fh.write("<html></html>")
            path = fh.name
        try:
            dork, file_ = ds.split_import_arg(f'inurl:page_id={path}')
            self.assertEqual(dork, "inurl:page_id")
            self.assertEqual(file_, path)
            dork, file_ = ds.split_import_arg(f'intitle:"Admin"={path}')
            self.assertEqual(dork, 'intitle:"Admin"')
            self.assertEqual(file_, path)
        finally:
            os.unlink(path)


class TestOutputs(unittest.TestCase):
    def test_json_csv_targets_written_and_merged(self):
        s = ds.DorkSearcher(ds.SearchConfig(pages=1))
        s._record("d", "bing", ["https://app.example.com/admin/login"],
                  ds.DorkStats())
        with tempfile.TemporaryDirectory() as tmp:
            targets = os.path.join(tmp, "targets.txt")
            json_path, csv_path, _ = ds.write_outputs(
                s, s.config, ["d"], os.path.join(tmp, "out"), targets)
            with open(json_path, encoding="utf-8") as fh:
                payload = json.load(fh)
            self.assertEqual(payload["hits"][0]["base_url"],
                             "https://app.example.com")
            with open(csv_path, encoding="utf-8") as fh:
                rows = fh.read().strip().splitlines()
            self.assertEqual(len(rows), 2)  # header + 1 hit
            with open(targets, encoding="utf-8") as fh:
                lines = [ln for ln in fh.read().splitlines() if not ln.startswith("#")]
            self.assertEqual(lines, ["https://app.example.com"])
            # second run must not duplicate
            ds.write_outputs(s, s.config, ["d"], os.path.join(tmp, "out"), targets)
            with open(targets, encoding="utf-8") as fh:
                lines = [ln for ln in fh.read().splitlines() if not ln.startswith("#")]
            self.assertEqual(lines, ["https://app.example.com"])


class TestImportSerp(unittest.TestCase):
    def test_imports_saved_google_serp(self):
        serp = ('<a href="/url?q=https%3A%2F%2Fapp.example.com%2Fadmin%2Flogin'
                '&amp;sa=U">a</a>')
        with tempfile.NamedTemporaryFile("w", suffix=".html", delete=False,
                                         encoding="utf-8") as fh:
            fh.write(serp)
            path = fh.name
        try:
            s = ds.DorkSearcher(ds.SearchConfig(pages=1))
            with contextlib.redirect_stdout(io.StringIO()):
                s.import_serp('intitle:"Admin Login"', path)
            self.assertEqual(len(s.hits), 1)
            self.assertEqual(s.hits[0].base_url, "https://app.example.com")
        finally:
            os.unlink(path)


class TestBlockDetection(unittest.TestCase):
    def test_detects_captcha_challenge(self):
        self.assertTrue(ds.is_blocked("<html>please complete the captcha below</html>"))

    def test_detects_recaptcha_and_robot(self):
        self.assertTrue(ds.is_blocked('<div class="g-recaptcha"></div>'))
        self.assertTrue(ds.is_blocked("Are you a robot? Verify you are human."))

    def test_normal_results_are_not_blocked(self):
        serp = ('<a href="/url?q=https%3A%2F%2Fapp.example.com%2Fadmin%2Flogin'
                '&amp;sa=U">a</a>')
        self.assertFalse(ds.is_blocked(serp))


class TestScopeFiltering(unittest.TestCase):
    def test_empty_scope_keeps_everything(self):
        self.assertTrue(ds.is_in_scope("https://anything.example/x", frozenset()))

    def test_matches_domain_and_subdomain(self):
        allowed = frozenset({"example.com"})
        self.assertTrue(ds.is_in_scope("https://example.com/", allowed))
        self.assertTrue(ds.is_in_scope("https://app.example.com/admin", allowed))
        self.assertFalse(ds.is_in_scope("https://evil.com/", allowed))
        self.assertFalse(ds.is_in_scope("https://example.com.evil.com/", allowed))

    def test_record_respects_scope(self):
        s = ds.DorkSearcher(
            ds.SearchConfig(pages=1, allowed_domains=frozenset({"example.com"})))
        s._record("d", "bing",
                  ["https://app.example.com/admin", "https://other.example.org/"],
                  ds.DorkStats())
        self.assertEqual([h.base_url for h in s.hits], ["https://app.example.com"])


class TestRetryAfter(unittest.TestCase):
    @staticmethod
    def _err(headers):
        return types.SimpleNamespace(headers=headers)

    def test_parses_seconds(self):
        self.assertEqual(ds._retry_after_seconds(self._err({"Retry-After": "3"})), 3.0)

    def test_clamps_to_max(self):
        self.assertEqual(ds._retry_after_seconds(self._err({"Retry-After": "9999"})),
                         ds.MAX_RETRY_AFTER)

    def test_absent_or_unparseable_returns_none(self):
        self.assertIsNone(ds._retry_after_seconds(self._err({})))
        self.assertIsNone(ds._retry_after_seconds(self._err({"Retry-After": "soon"})))
        self.assertIsNone(ds._retry_after_seconds(self._err(None)))


if __name__ == "__main__":
    unittest.main()

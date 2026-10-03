#!/usr/bin/env python3
"""dork_search.py - multi-engine search-engine dork searcher (passive discovery).

Sends search-engine dorks (intitle:/inurl:/site:/... syntax) to public search
engines and collects result URLs as candidate base URLs for target discovery.

Engines (all keyless - zero cost, no accounts, no API keys):
  bing     www.bing.com        result URLs are base64-wrapped in /ck/a?...u=a1...
  ddg      lite.duckduckgo.com direct links, works without JS
  mojeek   www.mojeek.com      small index, honors intitle:/inurl:
  google   www.google.com      often JS-walled over plain HTTP; when blocked,
                               save the SERP from a real browser and use
                               --import-html 'DORK=C:/path/saved.html'
  urlscan  urlscan.io search   keyless JSON search API (dorks auto-translated)
  searx    any SearxNG         keyless when the instance enables format=json

Stdlib only. Polite by design: sequential requests with a configurable delay
between them and bounded retries with backoff.

Usage:
    python dork_search.py -q 'intitle:"admin login"' --engine bing
    python dork_search.py -f dorks/examples.txt --engine bing --engine ddg
    python dork_search.py --import-html 'intitle:"admin login"=C:/tmp/serp.html'

Outputs:
    <out-dir>/dork_results-<stamp>.json   per-hit evidence (dork, engine, rank, urls)
    <out-dir>/dork_results-<stamp>.csv    same as CSV
    <targets-file>                        deduped base URLs (one per line)

Discovery only. Only probe or scan assets you are authorized to test.
"""

from __future__ import annotations

import argparse
import base64
import csv
import html
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone

__version__ = "2.0.0"

USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
ENGINE_CHOICES = ("bing", "ddg", "mojeek", "google", "urlscan", "searx")

# Search engines, archives, docs and discussion sites: never scan targets.
NOISE_DOMAINS = frozenset({
    "bing.com", "microsoft.com", "msn.com", "live.com", "yahoo.com",
    "google.com", "gstatic.com", "duckduckgo.com", "mojeek.com",
    "wikipedia.org", "archive.org", "web.archive.org",
    "github.com", "githubusercontent.com", "gitlab.com", "pastebin.com",
    "scribd.com", "youtube.com", "youtu.be", "linkedin.com", "twitter.com",
    "x.com", "reddit.com", "medium.com", "facebook.com", "instagram.com",
    "exploit-db.com", "packetstormsecurity.com", "nvd.nist.gov", "cve.org",
    "mitre.org", "cisa.gov", "tenable.com", "rapid7.com", "vulners.com",
    "stackexchange.com", "stackoverflow.com", "quora.com", "pinterest.com",
    "tiktok.com", "amazon.com", "ebay.com", "aliexpress.com",
})

HTTP_ERROR_RETRY = {429, 500, 502, 503, 504}
MAX_RETRY_AFTER = 60.0  # upper bound (seconds) for a honored Retry-After delay


# --------------------------------------------------------------------------- #
# ANSI color + startup banner
# --------------------------------------------------------------------------- #
# Color is auto-off when stdout is not a TTY (piped/redirected), when the
# NO_COLOR env var is set, or with --no-color; FORCE_COLOR / --color force it on.
# Colored text only ever goes to the console - the JSON/CSV evidence and the
# targets file are always written plain.
USE_COLOR = False
_ESC = chr(27)  # ANSI escape (built without a backslash literal)


def paint(text: str, code: str) -> str:
    """Wrap text in one ANSI SGR code; no-op unless color is enabled."""
    return f"{_ESC}[{code}m{text}{_ESC}[0m" if USE_COLOR else text


def bold(t: str) -> str:
    return paint(t, "1")


def dim(t: str) -> str:
    return paint(t, "90")


def red(t: str) -> str:
    return paint(t, "91")


def green(t: str) -> str:
    return paint(t, "92")


def yellow(t: str) -> str:
    return paint(t, "93")


def magenta(t: str) -> str:
    return paint(t, "95")


def cyan(t: str) -> str:
    return paint(t, "96")


def resolve_color(force: bool = False, disable: bool = False) -> bool:
    """Decide whether ANSI color should be emitted on the console stream."""
    if disable or os.environ.get("NO_COLOR") is not None:
        return False
    if force or os.environ.get("FORCE_COLOR"):
        return True
    return bool(getattr(sys.stdout, "isatty", lambda: False)())


def _enable_windows_vt() -> None:
    """Best-effort: enable ANSI escape processing on legacy Windows consoles."""
    if os.name != "nt":
        return
    try:
        import ctypes
        k32 = ctypes.windll.kernel32
        handle = k32.GetStdHandle(-11)  # STD_OUTPUT_HANDLE
        mode = ctypes.c_uint32()
        if k32.GetConsoleMode(handle, ctypes.byref(mode)):
            k32.SetConsoleMode(handle, mode.value | 0x0004)  # VT_PROCESSING
    except Exception:  # noqa: BLE001 - cosmetic only, never fatal
        pass


# Stacked "DORK" / "SEARCHER" wordmark (figlet ansi_shadow), kept as literal rows
# so the tool stays stdlib-only and the banner is pixel-stable.
_BANNER_ART = [
    "██████╗  ██████╗ ██████╗ ██╗  ██╗",
    "██╔══██╗██╔═══██╗██╔══██╗██║ ██╔╝",
    "██║  ██║██║   ██║██████╔╝█████╔╝",
    "██║  ██║██║   ██║██╔══██╗██╔═██╗",
    "██████╔╝╚██████╔╝██║  ██║██║  ██╗",
    "╚═════╝  ╚═════╝ ╚═╝  ╚═╝╚═╝  ╚═╝",
    "",
    "███████╗███████╗ █████╗ ██████╗  ██████╗██╗  ██╗███████╗██████╗",
    "██╔════╝██╔════╝██╔══██╗██╔══██╗██╔════╝██║  ██║██╔════╝██╔══██╗",
    "███████╗█████╗  ███████║██████╔╝██║     ███████║█████╗  ██████╔╝",
    "╚════██║██╔══╝  ██╔══██║██╔══██╗██║     ██╔══██║██╔══╝  ██╔══██╗",
    "███████║███████╗██║  ██║██║  ██║╚██████╗██║  ██║███████╗██║  ██║",
    "╚══════╝╚══════╝╚═╝  ╚═╝╚═╝  ╚═╝ ╚═════╝╚═╝  ╚═╝╚══════╝╚═╝  ╚═╝",
]


def print_banner() -> None:
    """Print the colorized ASCII-art startup banner."""
    width = max(len(line) for line in _BANNER_ART) + 2
    rule = paint("=" * width, "90")
    print(rule)
    for i, line in enumerate(_BANNER_ART):
        if not line:
            print()
            continue
        color = "96" if i < 6 else "95"  # DORK cyan -> SEARCHER magenta
        print("  " + paint(line, color))
    print("  " + dim("passive search-engine dork searcher ") + yellow("v" + __version__))
    print(rule)


# --------------------------------------------------------------------------- #
# data model
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class Hit:
    dork: str
    engine: str
    rank: int
    result_url: str
    base_url: str


@dataclass
class DorkStats:
    dork: str = ""
    engine: str = ""
    raw_urls: int = 0
    kept_hits: int = 0
    noise_filtered: int = 0
    blocked: bool = False
    error: str = ""


@dataclass
class SearchConfig:
    engines: list[str] = field(default_factory=lambda: ["bing"])
    pages: int = 2
    delay: float = 1.0
    timeout: float = 20.0
    retries: int = 2
    url_filter: re.Pattern | None = None
    filter_noise: bool = True
    searx_instance: str = "https://searx.be"
    allowed_domains: frozenset[str] = field(default_factory=frozenset)


# --------------------------------------------------------------------------- #
# HTTP
# --------------------------------------------------------------------------- #

def _retry_after_seconds(err: urllib.error.HTTPError) -> float | None:
    """Parse a Retry-After header into seconds (None when absent/unparseable)."""
    raw = err.headers.get("Retry-After") if err.headers else None
    if raw is None:
        return None
    try:
        seconds = float(raw)
    except (TypeError, ValueError):
        return None  # HTTP-date form; fall back to the linear backoff
    return min(max(seconds, 0.0), MAX_RETRY_AFTER)


def http_request(url: str, timeout: float, retries: int,
                 headers: dict | None = None, data: bytes | None = None) -> str:
    """HTTP request with bounded retries and backoff (honors Retry-After)."""
    hdrs = {"User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/json",
            "Accept-Language": "en-US,en;q=0.9"}
    if headers:
        hdrs.update(headers)
    last_error: Exception | None = None
    for attempt in range(retries + 1):
        req = urllib.request.Request(url, data=data, headers=hdrs)
        retry_after: float | None = None
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            last_error = e
            if e.code not in HTTP_ERROR_RETRY:
                raise
            retry_after = _retry_after_seconds(e)
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            last_error = e
        if attempt < retries:
            time.sleep(retry_after if retry_after is not None else 1.5 * (attempt + 1))
    if last_error is None:  # pragma: no cover - defensive
        raise RuntimeError(f"request to {url!r} failed with no captured error")
    raise last_error


# --------------------------------------------------------------------------- #
# SERP parsers (regex-based; each returns result URLs in page order)
# --------------------------------------------------------------------------- #

def _b64url_decode(token: str) -> str | None:
    token = token.replace("-", "+").replace("_", "/")
    token += "=" * (-len(token) % 4)
    try:
        return base64.b64decode(token).decode("utf-8", "replace")
    except Exception:
        return None


def parse_bing(serp: str) -> list[str]:
    """Bing wraps organic results as /ck/a?...&u=a1<base64url of target URL>."""
    serp = html.unescape(serp)
    out = []
    for token in re.findall(r"[?&]u=a1([A-Za-z0-9_\-]{16,}={0,2})", serp):
        url = _b64url_decode(token)
        if url and url.startswith(("http://", "https://")):
            out.append(url)
    return out


def parse_ddg(serp: str) -> list[str]:
    """DDG-lite links are direct, occasionally wrapped as /l/?uddg=<url>."""
    serp = html.unescape(serp)
    out = []
    for href in re.findall(r'href="(https?://[^"]+)"', serp):
        m = re.search(r"[?&]uddg=([^&]+)", href)
        if m:
            href = urllib.parse.unquote(m.group(1))
        if href.startswith(("http://", "https://")):
            out.append(href)
    return out


def parse_google(serp: str) -> list[str]:
    """Classic /url?q= redirect links first, then plain organic hrefs."""
    serp = html.unescape(serp)
    out = [urllib.parse.unquote(u) for u in
           re.findall(r'href="/url\?(?:q|url)=([^&"]+)', serp)]
    if not out:
        out = [u for u in re.findall(r'href="(https?://[^"]+)"', serp)
               if "google." not in u]
    return [u for u in out if u.startswith(("http://", "https://"))]


def parse_mojeek(serp: str) -> list[str]:
    """Mojeek organic results are plain <a href="http..."> anchors."""
    serp = html.unescape(serp)
    return re.findall(r'<a[^>]+href="(https?://[^"]+)"', serp)


PARSERS = {"bing": parse_bing, "ddg": parse_ddg,
           "google": parse_google, "mojeek": parse_mojeek}

ENGINE_URLS = {
    "bing":    "https://www.bing.com/search?q={q}&count=30&first={off}",
    "ddg":     "https://lite.duckduckgo.com/lite/?q={q}&s={off}",
    "mojeek":  "https://www.mojeek.com/search?q={q}&s={off}",
    "google":  "https://www.google.com/search?q={q}&num=30&hl=en&gbv=1&start={off}",
}


# --------------------------------------------------------------------------- #
# JSON API backends (research notes in README)
# --------------------------------------------------------------------------- #

def parse_urlscan_json(body: str) -> list[str]:
    data = json.loads(body)
    out = []
    for r in data.get("results", []):
        for key in ("page", "task"):
            u = (r.get(key) or {}).get("url")
            if u and u.startswith(("http://", "https://")):
                out.append(u)
    return out


def parse_searx_json(body: str) -> list[str]:
    data = json.loads(body)
    return [r["url"] for r in data.get("results", [])
            if r.get("url", "").startswith(("http://", "https://"))]


def parse_generic_anchors(serp: str) -> list[str]:
    """Last-resort HTML anchor extraction (SearxNG instances without JSON)."""
    serp = html.unescape(serp)
    return re.findall(r'<a[^>]+href="(https?://[^"]+)"', serp)


# Engines whose query language is not Google's need dork translation.
# Mapping is approximate; see README. urlscan: intitle:->page.title:,
# inurl: -> filename:<last path segment>.
def translate_dork(dork: str, engine: str) -> str:
    if engine == "urlscan":
        def _title(m):
            return f'page.title:"{m.group(1) or m.group(2)}"'

        def _inurl(m):
            seg = (m.group(1) or m.group(2)).rstrip("/").split("/")[-1]
            return f"filename:{seg}"
        d = re.sub(r'intitle:(?:"([^"]*)"|(\S+))', _title, dork)
        d = re.sub(r'inurl:(?:"([^"]*)"|(\S+))', _inurl, d)
        d = re.sub(r'intext:(?:"[^"]*"|\S+)', "", d)
        return " ".join(d.split())
    return dork


def build_request(engine: str, dork: str, page: int,
                  searx_instance: str) -> tuple[str, dict, bytes | None]:
    """Return (url, extra_headers, POST body) for one page of one engine."""
    q = urllib.parse.quote(translate_dork(dork, engine))
    if engine == "urlscan":
        return (f"https://urlscan.io/api/v1/search/?q={q}&size=100", {}, None)
    if engine == "searx":
        inst = searx_instance.rstrip("/")
        return (f"{inst}/search?q={q}&format=json&pageno={page + 1}", {}, None)
    raise ValueError(f"no builder for engine {engine!r}")


# --------------------------------------------------------------------------- #
# URL normalization / filtering
# --------------------------------------------------------------------------- #

def host_of(url: str) -> str:
    try:
        netloc = urllib.parse.urlsplit(url).netloc.lower()
    except Exception:
        return ""
    return netloc.split(":")[0]


def is_noise_url(url: str) -> bool:
    host = host_of(url)
    if not host:
        return True
    return any(host == d or host.endswith("." + d) for d in NOISE_DOMAINS)


def is_in_scope(url: str, allowed: frozenset[str] | set[str]) -> bool:
    """True when the URL's host falls under an allowed domain (or no scope set)."""
    if not allowed:
        return True  # no scope configured: keep everything
    host = host_of(url)
    return any(host == d or host.endswith("." + d) for d in allowed)


# High-precision bot-challenge / CAPTCHA / rate-limit page markers. Checked only
# against the head of a fetched SERP so ordinary result snippets don't trigger it.
BLOCK_SIGNS = re.compile(
    r"(g-?recaptcha|captcha|are you a robot|unusual traffic|"
    r"verify you are (a )?(human|robot)|automated queries|"
    r"challenge[- ]?(platform|check)|cf-browser-verification|just a moment)",
    re.IGNORECASE)


def is_blocked(body: str) -> bool:
    """Detect a CAPTCHA / bot-challenge page served instead of real results."""
    return bool(BLOCK_SIGNS.search(body[:8000]))


def normalize_base_url(url: str) -> str | None:
    """Map a result URL to a scan base URL: scheme://host[:port] (path dropped)."""
    try:
        parts = urllib.parse.urlsplit(url)
    except Exception:
        return None
    if parts.scheme not in ("http", "https") or not parts.netloc:
        return None
    return f"{parts.scheme}://{parts.netloc}"


# --------------------------------------------------------------------------- #
# searcher
# --------------------------------------------------------------------------- #

class DorkSearcher:
    def __init__(self, config: SearchConfig):
        self.config = config
        self.hits: list[Hit] = []
        self.stats: list[DorkStats] = []
        self._seen: set[str] = set()

    def _keep(self, url: str) -> bool:
        if self.config.allowed_domains and not is_in_scope(url, self.config.allowed_domains):
            return False
        if self.config.filter_noise and is_noise_url(url):
            return False
        if self.config.url_filter and not self.config.url_filter.search(url):
            return False
        return True

    def _record(self, dork: str, engine: str, raw_urls: list[str],
                stats: DorkStats) -> None:
        stats.raw_urls = len(raw_urls)
        for rank, url in enumerate(raw_urls, 1):
            if not self._keep(url):
                stats.noise_filtered += 1
                continue
            base = normalize_base_url(url)
            if not base or base in self._seen:
                continue
            self._seen.add(base)
            self.hits.append(Hit(dork, engine, rank, url, base))
            stats.kept_hits += 1

    def search(self, dorks: list[str]) -> None:
        for dork in dorks:
            for engine in self.config.engines:
                stats = DorkStats(dork=dork, engine=engine)
                try:
                    raw = self._fetch(engine, dork, stats)
                except Exception as e:  # noqa: BLE001 - record and continue
                    stats.error = f"{type(e).__name__}: {e}"
                    self.stats.append(stats)
                    print(f"{red('[!]')} {engine} {dork!r}: {stats.error}", file=sys.stderr)
                    continue
                self._record(dork, engine, raw, stats)
                self.stats.append(stats)
                print(f"{cyan('[*]')} {dork!r} [{cyan(engine)}]: {stats.raw_urls} raw, "
                      f"{green(str(stats.kept_hits))} new, {stats.noise_filtered} noise")

    def _fetch(self, engine: str, dork: str, stats: DorkStats) -> list[str]:
        raw: list[str] = []
        for page in range(self.config.pages):
            if engine in ENGINE_URLS:
                url = ENGINE_URLS[engine].format(
                    q=urllib.parse.quote(dork), off=self._offset(engine, page))
                body = http_request(url, self.config.timeout, self.config.retries)
                if is_blocked(body):
                    self._mark_blocked(stats)
                raw.extend(PARSERS[engine](body))
            else:
                url, hdrs, data = build_request(engine, dork, page,
                                                self.config.searx_instance)
                body = http_request(url, self.config.timeout, self.config.retries,
                                    headers=hdrs, data=data)
                if is_blocked(body):
                    self._mark_blocked(stats)
                raw.extend(self._parse_api(engine, body))
            if page < self.config.pages - 1:
                time.sleep(self.config.delay)
        return raw

    @staticmethod
    def _mark_blocked(stats: DorkStats) -> None:
        stats.blocked = True
        stats.error = "blocked (CAPTCHA/challenge page)"

    def _parse_api(self, engine: str, body: str) -> list[str]:
        if body.lstrip().startswith("<"):
            # JSON endpoint disabled (SearxNG instances): fall back to anchors
            inst_host = host_of(self.config.searx_instance)
            return [u for u in parse_generic_anchors(body) if host_of(u) != inst_host]
        parser = {"urlscan": parse_urlscan_json,
                  "searx": parse_searx_json}[engine]
        return parser(body)

    @staticmethod
    def _offset(engine: str, page: int) -> int:
        return {"bing": page * 30 + 1, "ddg": page * 30,
                "mojeek": page * 10 + 1, "google": page * 30}[engine]

    def import_serp(self, dork: str, path: str) -> None:
        """Parse a SERP saved from a real browser (for JS-walled engines)."""
        with open(path, encoding="utf-8", errors="replace") as fh:
            serp = fh.read()
        engine = "google" if re.search(r"google\.|/url\?q=", serp[:4000]) else "bing"
        raw = PARSERS[engine](serp)
        stats = DorkStats(dork=dork, engine=f"{engine}(import)")
        if is_blocked(serp):
            self._mark_blocked(stats)
        self._record(dork, engine, raw, stats)
        self.stats.append(stats)
        print(f"{cyan('[*]')} {dork!r} [{cyan(stats.engine)} <- {path}]: {stats.raw_urls} raw, "
              f"{green(str(stats.kept_hits))} new, {stats.noise_filtered} noise")


# --------------------------------------------------------------------------- #
# output
# --------------------------------------------------------------------------- #

def write_outputs(searcher: DorkSearcher, config: SearchConfig,
                  dorks: list[str], out_dir: str, targets_file: str,
                  write_csv: bool = True) -> tuple[str, str | None, str]:
    os.makedirs(out_dir, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    json_path = os.path.join(out_dir, f"dork_results-{stamp}.json")

    payload = {
        "tool": "dork_search.py",
        "version": __version__,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "config": {"engines": config.engines, "pages": config.pages,
                   "delay": config.delay, "timeout": config.timeout,
                   "url_filter": config.url_filter.pattern
                   if config.url_filter else None,
                   "filter_noise": config.filter_noise},
        "dorks": dorks,
        "stats": [asdict(s) for s in searcher.stats],
        "hits": [asdict(h) for h in searcher.hits],
    }
    with open(json_path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2)

    csv_path = None
    if write_csv:
        csv_path = os.path.join(out_dir, f"dork_results-{stamp}.csv")
        with open(csv_path, "w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(
                fh, fieldnames=["dork", "engine", "rank", "result_url", "base_url"])
            writer.writeheader()
            for h in searcher.hits:
                writer.writerow(asdict(h))

    exists = os.path.exists(targets_file)
    existing: set[str] = set()
    if exists:
        with open(targets_file, encoding="utf-8") as fh:
            existing = {ln.strip() for ln in fh
                        if ln.strip() and not ln.strip().startswith("#")}
    new = sorted({h.base_url for h in searcher.hits} - existing)
    with open(targets_file, "a" if exists else "w", encoding="utf-8") as fh:
        if not exists or not existing:
            fh.write("# Candidate base URLs from search-engine dorks (passive recon)\n"
                     "# UNVERIFIED - triage ownership/authorization BEFORE probing.\n")
        for base in new:
            fh.write(base + "\n")

    return json_path, csv_path, targets_file


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def split_import_arg(arg: str) -> tuple[str, str]:
    """Split 'DORK=FILE' where the dork itself may contain '='."""
    for i, ch in enumerate(arg):
        if ch == "=" and os.path.exists(arg[i + 1:]):
            return arg[:i], arg[i + 1:]
    dork, _, path = arg.rpartition("=")
    return dork, path


def load_dork_file(path: str) -> list[str]:
    with open(path, encoding="utf-8") as fh:
        return [ln.strip() for ln in fh
                if ln.strip() and not ln.strip().startswith("#")]


def load_scope_file(path: str) -> frozenset[str]:
    """Load an allowlist of domains (one per line, '#' = comment)."""
    with open(path, encoding="utf-8") as fh:
        domains = {ln.strip().lower() for ln in fh
                   if ln.strip() and not ln.strip().startswith("#")}
    return frozenset(domains)


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    ap = argparse.ArgumentParser(
        prog="dork_search.py",
        description="Multi-engine search-engine dork searcher (passive discovery). "
                    "Only probe or scan assets you are authorized to test.")
    ap.add_argument("-q", "--dork", action="append", default=[],
                    help="dork query (repeatable)")
    ap.add_argument("-f", "--file", help="file with one dork per line (# = comment)")
    ap.add_argument("--engine", action="append", choices=ENGINE_CHOICES,
                    help="search engine (repeatable; default: bing). Every "
                         "engine is keyless and free: bing ddg mojeek google "
                         "urlscan searx")
    ap.add_argument("--searx-instance", default="https://searx.be",
                    help="SearxNG instance URL for --engine searx (default %(default)s)")
    ap.add_argument("--pages", type=int, default=2, help="result pages per dork (default 2)")
    ap.add_argument("--delay", type=float, default=1.0,
                    help="seconds between requests (default 1.0)")
    ap.add_argument("--timeout", type=float, default=20.0, help="request timeout (s)")
    ap.add_argument("--retries", type=int, default=2, help="retries per request")
    ap.add_argument("--url-filter", metavar="REGEX",
                    help="keep only result URLs matching this regex")
    ap.add_argument("--include-noise", action="store_true",
                    help="keep results from docs/social/engine domains too")
    ap.add_argument("--scope", metavar="FILE",
                    help="file of allowed domains (one per line, # = comment); "
                         "results outside these domains are dropped")
    ap.add_argument("--import-html", action="append", default=[], metavar="DORK=FILE",
                    help="parse a SERP saved from a browser instead of fetching")
    ap.add_argument("--targets-file", default="discovered_targets_dorks.txt",
                    help="URL list to merge results into (default %(default)s)")
    ap.add_argument("--out-dir", default="dork_results",
                    help="directory for JSON/CSV evidence (default %(default)s)")
    ap.add_argument("--no-csv", action="store_true", help="skip the CSV output")
    ap.add_argument("-v", "--verbose", action="store_true", help="print every kept hit")
    ap.add_argument("--color", action="store_true",
                    help="force ANSI color output (default: auto, on for a TTY)")
    ap.add_argument("--no-color", action="store_true",
                    help="disable ANSI color output (also honors the NO_COLOR env var)")
    ap.add_argument("--no-banner", action="store_true", help="suppress the startup banner")
    ap.add_argument("--version", action="version", version=f"dork_search {__version__}")
    args = ap.parse_args()

    global USE_COLOR
    USE_COLOR = resolve_color(force=args.color, disable=args.no_color)
    if USE_COLOR:
        _enable_windows_vt()
    if not args.no_banner:
        print_banner()

    dorks = list(args.dork)
    if args.file:
        dorks += load_dork_file(args.file)
    imports = [split_import_arg(a) for a in args.import_html]
    dorks += [d for d, _ in imports]
    if not dorks:
        ap.error("no dorks: use -q, -f or --import-html")

    url_filter = re.compile(args.url_filter) if args.url_filter else None
    allowed_domains = load_scope_file(args.scope) if args.scope else frozenset()
    config = SearchConfig(
        engines=args.engine or ["bing"],
        pages=max(1, args.pages),
        delay=max(0.0, args.delay),
        timeout=max(1.0, args.timeout),
        retries=max(0, args.retries),
        url_filter=url_filter,
        filter_noise=not args.include_noise,
        searx_instance=args.searx_instance,
        allowed_domains=allowed_domains,
    )

    searcher = DorkSearcher(config)
    for dork, path in imports:
        try:
            searcher.import_serp(dork, path)
        except OSError as e:
            print(f"{red('[!]')} import-html {path}: {e}", file=sys.stderr)
    imported = {dork for dork, _ in imports}
    remote = [d for d in dorks if d not in imported]
    if remote:
        searcher.search(remote)

    json_path, csv_path, targets_file = write_outputs(
        searcher, config, dorks, args.out_dir, args.targets_file,
        write_csv=not args.no_csv)

    if args.verbose:
        for h in searcher.hits:
            print(f"    {cyan(h.base_url)}  {dim('<-')} {dim(h.result_url[:90])}")

    blocked = [s for s in searcher.stats if s.blocked]
    errors = [s for s in searcher.stats if s.error and not s.blocked]
    print(f"\n{green('[+]')} hits: {green(str(len(searcher.hits)))} unique base URLs "
          f"(raw seen: {sum(s.raw_urls for s in searcher.stats)}, "
          f"noise filtered: {sum(s.noise_filtered for s in searcher.stats)}"
          + (f", blocked: {yellow(str(len(blocked)))}" if blocked else "") + ")")
    print(f"{green('[+]')} evidence: {dim(json_path)}")
    if csv_path:
        print(f"{green('[+]')} evidence: {dim(csv_path)}")
    print(f"{green('[+]')} targets:  {dim(targets_file)}")
    if searcher.stats and len(errors) + len(blocked) == len(searcher.stats):
        if blocked and not errors:
            print(f"{red('[!]')} every request was blocked (CAPTCHA/challenge) - "
                  "try --import-html or another network", file=sys.stderr)
        else:
            print(f"{red('[!]')} every request failed - check network/proxy", file=sys.stderr)
        sys.exit(4)


if __name__ == "__main__":
    main()

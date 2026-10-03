# dork-searcher

[![CI](https://github.com/MohammadAliMehri/dork-searcher/actions/workflows/ci.yml/badge.svg)](https://github.com/MohammadAliMehri/dork-searcher/actions/workflows/ci.yml)
[![Python 3.9+](https://img.shields.io/badge/python-3.9%2B-blue.svg)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Dependencies](https://img.shields.io/badge/dependencies-none-brightgreen.svg)](#quick-start)

Multi-engine search-engine **dork searcher** for passive target discovery.
Sends `intitle:` / `inurl:` / `intext:` dorks to public search engines and
collects result URLs as candidate base URLs.

    Python stdlib only. Zero cost: no API keys, no accounts, no paid services.
    Polite: sequential requests, configurable delay, bounded retries.

## Quick start

```bash
git clone https://github.com/MohammadAliMehri/dork-searcher.git
cd dork-searcher

# one dork, one engine
python dork_search.py -q 'intitle:"admin login"' --engine bing

# a whole dork file across several engines
python dork_search.py -f dorks/examples.txt --engine bing --engine ddg --engine mojeek
```

Requirements: **Python 3.9+** and nothing else - the tool is pure standard
library. Optionally `pip install .` for the `dork-search` console command.

## Layout

    dork_search.py        the tool (single module)
    dorks/examples.txt    example dork list
    tests/                offline unit tests

## Engines (all keyless, all free)

    bing     www.bing.com          decodes the base64 /ck/a?...u=a1 wrappers
    ddg      lite.duckduckgo.com   plain links; currently serves a challenge page
    mojeek   www.mojeek.com        honors intitle:/inurl:; may 403 some clients
    google   www.google.com        CAPTCHA-walled to non-browser clients (see below)
    urlscan  urlscan.io search     keyless JSON search API; page.title:/filename:/domain:
                                   queries (dorks auto-translated)
    searx    any SearxNG instance  keyless when the instance enables format=json
                                   (--searx-instance URL)

Engine research (why these):

    Backend     Key?  Cost    Dork fidelity
    ----------- ----  ------  --------------------------------
    bing        no    free    intitle:/inurl: honored, wrapped URLs
    ddg         no    free    blocked (JS challenge) as of 2026-10
    mojeek      no    free    good dorks, 403s some UAs
    google      no    free    full, but CAPTCHA-walled to scripts
    urlscan     no    free    title/filename/domain fields, ~100 hits
    searx       no    free*   passes dorks to Google/Bing
    ----------- ----  ------  --------------------------------
    * depends on the instance; many public instances disable format=json

Dork translation for non-Google engines (approximate, applied automatically):

    urlscan:  intitle:"X" -> page.title:"X"    inurl:/a/b.php -> filename:b.php
              intext:...  -> dropped (no body search)

Google is CAPTCHA-walled to datacenter/flagged clients (even headless browsers
on such networks get a CAPTCHA page instead of results). Options:

1. `--engine google` on a residential connection (plain SERP is served there)
2. Save the SERP from your own browser (Ctrl+S the results page) and import it:

       python dork_search.py --import-html 'intitle:"admin login"=C:/tmp/serp.html'

The `DORK=FILE` argument is split at the first `=` whose right side is an
existing file, so dorks containing `=` (e.g. `inurl:page_id=`) work.

## Usage

    # one dork, one engine
    python dork_search.py -q 'intitle:"admin login"' --engine bing

    # dork file, several engines, 3 pages each
    python dork_search.py -f dorks/examples.txt --engine bing --engine ddg --engine mojeek --pages 3

    # keep only results whose URL matches a regex (applied before base-URL trim)
    python dork_search.py -f dorks/examples.txt --url-filter '/admin/'

    # faster/slower, more patient
    python dork_search.py -f dorks/examples.txt --delay 0.5 --timeout 30 --retries 3

Options:

    -q/--dork DORK        dork query (repeatable)
    -f/--file FILE        dork file, one per line, # = comment
    --engine NAME         bing | ddg | mojeek | google | urlscan | searx
                          (repeatable, default bing)
    --searx-instance URL  SearxNG instance for --engine searx (default searx.be)
    --pages N             result pages per dork (default 2)
    --delay S             seconds between requests (default 1.0)
    --timeout S           request timeout (default 20)
    --retries N           retries per request on 429/5xx/network errors (default 2)
    --url-filter REGEX    keep only result URLs matching REGEX
    --include-noise       keep docs/social/search-engine domains too
    --scope FILE          only keep results under these domains (one per line)
    --import-html D=FILE  parse a browser-saved SERP instead of fetching
    --targets-file PATH   URL list to merge into (default discovered_targets_dorks.txt)
    --out-dir DIR         JSON/CSV evidence dir (default dork_results)
    --no-csv              skip the CSV
    -v/--verbose          print every kept hit
    --color               force ANSI color (default: auto, on for a TTY)
    --no-color            disable ANSI color (also honors the NO_COLOR env var)
    --no-banner           suppress the startup banner

Exit codes: `0` ok, `2` usage error, `4` every request failed.

## Outputs

    dork_results/dork_results-<stamp>.json   full evidence: config, per-dork stats,
                                             per-hit dork/engine/rank/result_url/base_url
    dork_results/dork_results-<stamp>.csv    the hits as CSV
    discovered_targets_dorks.txt             deduped base URLs, one per line,
                                             merged/deduped across runs

Result URLs are normalized to base URLs (`scheme://host[:port]`, path dropped)
since webapp fingerprints live at the host level. The full result URL is kept
in the evidence files.

Noise filtering drops search engines, archives, docs and discussion sites
(github, pastebin, stackoverflow, ...); `--include-noise` disables it. The
filter matches whole host labels only (`notgithub.com` is kept,
`gist.github.com` is not).

## Block detection & scope

When an engine returns a CAPTCHA / bot-challenge page instead of results (common
for ddg, and for google on datacenter IPs), the run is flagged `blocked` in the
summary and evidence rather than silently yielding zero hits. `Retry-After`
headers on HTTP 429/503 are honored (capped at 60s) before retrying.

`--scope allowed.txt` keeps only results whose host falls under a listed domain
(subdomains included), reinforcing authorized-scope discipline.

## Banner & colors

A colorized `DORK SEARCHER` ASCII-art banner prints at startup:

```
==================================================================
  ██████╗  ██████╗ ██████╗ ██╗  ██╗
  ██╔══██╗██╔═══██╗██╔══██╗██║ ██╔╝
  ██║  ██║██║   ██║██████╔╝█████╔╝
  ██║  ██║██║   ██║██╔══██╗██╔═██╗
  ██████╔╝╚██████╔╝██║  ██║██║  ██╗
  ╚═════╝  ╚═════╝ ╚═╝  ╚═╝╚═╝  ╚═╝

  ███████╗███████╗ █████╗ ██████╗  ██████╗██╗  ██╗███████╗██████╗
  ██╔════╝██╔════╝██╔══██╗██╔══██╗██╔════╝██║  ██║██╔════╝██╔══██╗
  ███████╗█████╗  ███████║██████╔╝██║     ███████║█████╗  ██████╔╝
  ╚════██║██╔══╝  ██╔══██║██╔══██╗██║     ██╔══██║██╔══╝  ██╔══██╗
  ███████║███████╗██║  ██║██║  ██║╚██████╗██║  ██║███████╗██║  ██║
  ╚══════╝╚══════╝╚═╝  ╚═╝╚═╝  ╚═╝ ╚═════╝╚═╝  ╚═╝╚══════╝╚═╝  ╚═╝
  passive search-engine dork searcher v2.0.0
==================================================================
```

Console markers are colorized - `[+]` green (success), `[*]` cyan (progress),
`[!]` red (error), with paths/annotations dimmed. Color is auto-enabled on a TTY
and auto-off when output is redirected/piped (clean logs) or when the `NO_COLOR`
env var is set; `--color` / `--no-color` override it, and `--no-banner` suppresses
the banner. The JSON, CSV and targets files are always written plain (no ANSI).

## Testing

Offline unit tests (parsers, filters, dedupe, outputs):

    python -m unittest discover -s tests -v

Lint:

    ruff check .

CI (GitHub Actions) runs the tests on Python 3.9-3.13 across Linux and Windows
and lints with ruff.

## Scope

Discovery is passive: the tool only talks to search engines. Whatever you do
with the resulting target list must stay inside your authorized scope.

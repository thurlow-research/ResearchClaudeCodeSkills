#!/usr/bin/env python3
"""OpenAlex CLI — stdlib-only client with on-disk caching, for literature-review
metadata enrichment and citation-graph work.

OpenAlex (https://openalex.org) is a free, open scholarly catalog (~250M works)
with abstracts (as an inverted index), authorships, venues, DOIs, open-access
links, and citation edges. NO API KEY. Add your email (the "polite pool") for
higher, more reliable rate limits (10 req/s, 100k/day).

Commands:
  work ID                 full normalized metadata for one work
  abstract ID             just the reconstructed abstract text
  enrich [ID ...]         batch-normalize many works (ids from args/--ids-file/stdin) -> JSON list
  search QUERY            relevance search
  cites ID                works that CITE this one (forward)
  references ID           works this one CITES (backward)
  cache {stats,clear}     inspect / clear the local cache

ID forms: OpenAlex id (W…), bare DOI (10.x), DOI:/doi.org URL, ARXIV:<id>,
PMID:<id>, or free text (treated as a title search).

Config (env):
  OPENALEX_MAILTO / OPENALEX_EMAIL   your email for the polite pool (recommended)
  OPENALEX_CACHE                     cache dir (default ~/.cache/claude-openalex)
  OPENALEX_CACHE_TTL                 seconds; 0 = never expire (default 0)
"""
import argparse
import hashlib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

BASE = "https://api.openalex.org"

# OpenAlex sits behind Cloudflare, whose shared IPv6 blocks are aggressively
# rate-limited (429) while IPv4 answers 200 instantly. Prefer IPv4 unless the
# caller opts out — otherwise urllib picks IPv6 and every request eats the
# skill's 429-backoff (~30s) before failing. Set OPENALEX_IPV6=1 to disable.
if os.environ.get("OPENALEX_IPV6") != "1":
    import socket as _socket
    _orig_gai = _socket.getaddrinfo
    _socket.getaddrinfo = lambda *a, **k: (
        [x for x in _orig_gai(*a, **k) if x[0] == _socket.AF_INET] or _orig_gai(*a, **k))

MAILTO = os.environ.get("OPENALEX_MAILTO") or os.environ.get("OPENALEX_EMAIL") or ""
API_KEY = os.environ.get("OPENALEX_API_KEY") or ""  # registered account key -> higher quota
CACHE_DIR = Path(os.environ.get("OPENALEX_CACHE") or (Path.home() / ".cache" / "claude-openalex"))
TTL = int(os.environ.get("OPENALEX_CACHE_TTL", "0"))


# ---------------------------------------------------------------- HTTP + cache
def _cache_path(url):
    return CACHE_DIR / (hashlib.sha256(url.encode()).hexdigest() + ".json")


def _fetch(url, use_cache=True):
    sep = "&" if "?" in url else "?"
    params = []
    if API_KEY:
        params.append(f"api_key={urllib.parse.quote(API_KEY)}")
    if MAILTO:
        params.append(f"mailto={urllib.parse.quote(MAILTO)}")
    full = url + ((sep + "&".join(params)) if params else "")
    cp = _cache_path(full)
    if use_cache and cp.exists() and (not TTL or time.time() - cp.stat().st_mtime < TTL):
        try:
            return json.loads(cp.read_text())
        except Exception:
            pass
    ua = f"claude-openalex (mailto:{MAILTO or 'anonymous'})"
    timeout = float(os.environ.get("OPENALEX_TIMEOUT", "20"))
    for attempt in range(5):
        try:
            req = urllib.request.Request(full, headers={"User-Agent": ua})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                data = json.load(r)
            CACHE_DIR.mkdir(parents=True, exist_ok=True)
            cp.write_text(json.dumps(data))
            return data
        except urllib.error.HTTPError as e:
            if e.code == 429:
                # OpenAlex's credit-based rate limit. Read the headers so we
                # fail FAST + informatively instead of blind-backing-off 30s
                # per call into a mystery hang. A large Retry-After means the
                # DAILY quota is exhausted — no point retrying this run.
                h = e.headers
                retry = int(h.get("Retry-After") or h.get("X-RateLimit-Reset") or 0)
                rem = h.get("X-RateLimit-Remaining", "?")
                lim = h.get("X-RateLimit-Limit", "?")
                if retry > 120:
                    hrs = retry / 3600
                    sys.stderr.write(f"[openalex] 429 QUOTA EXHAUSTED — remaining={rem}/{lim} "
                                     f"credits; resets in {retry}s (~{hrs:.1f}h, midnight UTC). "
                                     f"Not retrying.\n")
                    return None
                sys.stderr.write(f"[openalex] 429 throttled (remaining={rem}/{lim}); "
                                 f"backing off {retry or 2 + attempt * 2}s\n")
                time.sleep(retry or (2 + attempt * 2))
                continue
            if e.code in (500, 502, 503):
                time.sleep(2 + attempt * 2)
                continue
            # client errors (400/404/…) are non-retryable — return None, don't crash
            if 400 <= e.code < 500:
                sys.stderr.write(f"[openalex] HTTP {e.code} on {full[:120]}\n")
                return None
            raise
        except (urllib.error.URLError, TimeoutError):
            time.sleep(1 + attempt)
    return None


# ------------------------------------------------------------------- helpers
def reconstruct_abstract(inv):
    if not inv:
        return ""
    pos = sorted((p, w) for w, ps in inv.items() for p in ps)
    return " ".join(w for _, w in pos)


def _work_path(idstr):
    """Map a user id form to an OpenAlex /works path, or ('search', title)."""
    s = idstr.strip()
    low = s.lower()
    if low.startswith("doi:"):
        s = s[4:]
        low = s.lower()
    if "doi.org/" in low:
        s = s.split("doi.org/", 1)[1]
        low = s.lower()
    if re.match(r"^w\d+$", low):
        return f"/works/{s.upper()}"
    if low.startswith("arxiv:"):
        return f"/works/doi:10.48550/arXiv.{s.split(':', 1)[1]}"
    if low.startswith("pmid:"):
        return f"/works/pmid:{s.split(':', 1)[1]}"
    if re.match(r"^10\.\d{4,9}/", s):
        return f"/works/doi:{s}"
    return ("search", s)


def normalize(w):
    if not w:
        return None
    doi = (w.get("doi") or "").replace("https://doi.org/", "")
    src = ((w.get("primary_location") or {}).get("source") or {})
    loc = (w.get("primary_location") or {})
    oa = (w.get("open_access") or {})
    best = (w.get("best_oa_location") or {})
    return {
        "openalex_id": (w.get("id") or "").replace("https://openalex.org/", ""),
        "doi": doi,
        "title": w.get("title") or w.get("display_name") or "",
        "authors": [a["author"]["display_name"] for a in (w.get("authorships") or [])
                    if a.get("author")],
        "year": w.get("publication_year"),
        "date": w.get("publication_date") or "",
        "venue": src.get("display_name") or "",
        "type": w.get("type") or "",
        "url": (f"https://doi.org/{doi}" if doi else loc.get("landing_page_url")
                or w.get("id") or ""),
        "oa_pdf": oa.get("oa_url") or best.get("pdf_url") or "",
        "abstract": reconstruct_abstract(w.get("abstract_inverted_index")),
        "cited_by_count": w.get("cited_by_count", 0),
        "referenced_count": len(w.get("referenced_works") or []),
    }


SELECT = ("id,doi,title,display_name,authorships,publication_year,publication_date,"
          "primary_location,open_access,best_oa_location,type,"
          "abstract_inverted_index,cited_by_count,referenced_works")


def get_work(idstr, use_cache=True):
    p = _work_path(idstr)
    if isinstance(p, tuple):  # title search
        # OpenAlex title.search rejects quotes/colons/commas/unicode → strip to
        # alphanumeric tokens (it tokenizes anyway); avoids spurious HTTP 400s.
        clean = re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]", " ", p[1].lower())).strip()
        if not clean:
            return None
        q = urllib.parse.quote(clean[:250])
        d = _fetch(f"{BASE}/works?filter=title.search:{q}&per-page=1&select={SELECT}", use_cache)
        res = (d or {}).get("results") or []
        return res[0] if res else None
    return _fetch(f"{BASE}{p}?select={SELECT}", use_cache)


# ------------------------------------------------------------------- commands
def cmd_work(a):
    print(json.dumps(normalize(get_work(a.id, not a.no_cache)), indent=2))


def cmd_abstract(a):
    w = get_work(a.id, not a.no_cache)
    print(reconstruct_abstract((w or {}).get("abstract_inverted_index")))


def cmd_enrich(a):
    ids = list(a.ids)
    if a.ids_file:
        ids += [x.strip() for x in Path(a.ids_file).read_text().splitlines() if x.strip()]
    if not sys.stdin.isatty():
        ids += [x.strip() for x in sys.stdin.read().splitlines() if x.strip()]
    out = []
    for i, idv in enumerate(ids, 1):
        rec = normalize(get_work(idv, not a.no_cache))
        if rec:
            rec["_query"] = idv
            out.append(rec)
        if not a.quiet and i % 50 == 0:
            print(f"  {i}/{len(ids)}", file=sys.stderr)
        time.sleep(a.sleep)
    print(json.dumps(out, indent=2))


def cmd_search(a):
    q = urllib.parse.quote(a.query)
    d = _fetch(f"{BASE}/works?search={q}&per-page={a.limit}&select={SELECT}", not a.no_cache)
    print(json.dumps([normalize(w) for w in (d or {}).get("results", [])], indent=2))


def _oid(idstr):
    w = get_work(idstr)
    return (w or {}).get("id", "").replace("https://openalex.org/", "")


def cmd_cites(a):
    oid = _oid(a.id)
    if not oid:
        sys.exit("could not resolve work")
    d = _fetch(f"{BASE}/works?filter=cites:{oid}&per-page={a.limit}&select={SELECT}", not a.no_cache)
    print(json.dumps([normalize(w) for w in (d or {}).get("results", [])], indent=2))


def cmd_references(a):
    w = get_work(a.id)
    refs = (w or {}).get("referenced_works") or []
    ids = "|".join(r.replace("https://openalex.org/", "") for r in refs[:a.limit])
    if not ids:
        print("[]")
        return
    d = _fetch(f"{BASE}/works?filter=openalex_id:{ids}&per-page={a.limit}&select={SELECT}", not a.no_cache)
    print(json.dumps([normalize(w) for w in (d or {}).get("results", [])], indent=2))


def cmd_cache(a):
    if a.action == "clear":
        n = 0
        if CACHE_DIR.exists():
            for f in CACHE_DIR.glob("*.json"):
                f.unlink()
                n += 1
        print(f"cleared {n} cached responses from {CACHE_DIR}")
    else:
        files = list(CACHE_DIR.glob("*.json")) if CACHE_DIR.exists() else []
        size = sum(f.stat().st_size for f in files)
        print(f"cache dir: {CACHE_DIR}\n  entries: {len(files)}\n  size: {size/1024:.1f} KiB"
              f"\n  ttl: {'never' if not TTL else str(TTL)+'s'}  mailto: {MAILTO or '(none)'}")


def main():
    ap = argparse.ArgumentParser(description="OpenAlex CLI (cached, no key required)")
    ap.add_argument("--no-cache", action="store_true", help="bypass the local cache")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name, fn in [("work", cmd_work), ("abstract", cmd_abstract)]:
        p = sub.add_parser(name)
        p.add_argument("id")
        p.set_defaults(func=fn)
    pe = sub.add_parser("enrich")
    pe.add_argument("ids", nargs="*")
    pe.add_argument("--ids-file")
    pe.add_argument("--sleep", type=float, default=0.12)
    pe.add_argument("--quiet", action="store_true")
    pe.set_defaults(func=cmd_enrich)
    ps = sub.add_parser("search")
    ps.add_argument("query")
    ps.add_argument("--limit", type=int, default=10)
    ps.set_defaults(func=cmd_search)
    for name, fn in [("cites", cmd_cites), ("references", cmd_references)]:
        p = sub.add_parser(name)
        p.add_argument("id")
        p.add_argument("--limit", type=int, default=50)
        p.set_defaults(func=fn)
    pc = sub.add_parser("cache")
    pc.add_argument("action", choices=["stats", "clear"], nargs="?", default="stats")
    pc.set_defaults(func=cmd_cache)
    a = ap.parse_args()
    a.func(a)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
s2.py — minimal CLI for the Semantic Scholar Graph API, built for systematic
literature reviews. The headline feature is `snowball`: multi-hop citation
chasing (forward + backward) from a seed set, deduplicated with provenance.

Auth (the key is optional — the API works keyless but is heavily rate-limited):
  1. --api-key flag
  2. SEMANTIC_SCHOLAR_API_KEY environment variable
  3. $S2_ENV_FILE  →  ./.env  →  ~/.config/claude-s2/.env

Already-set environment variables win over .env values.

Caching: every GET is cached on disk (default ~/.cache/claude-s2) with a TTL
(default 7 days; paper metadata and citation edges are near-static). Use the
global --no-cache / --refresh flags or `cache --clear` to control it.

Stdlib only — no pip install needed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Iterator

GRAPH_BASE = "https://api.semanticscholar.org/graph/v1"
REC_BASE = "https://api.semanticscholar.org/recommendations/v1"
USER_AGENT = "claude-s2-skill/0.1"
DEFAULT_TTL = 7 * 24 * 3600.0  # seconds; override with S2_CACHE_TTL
DEFAULT_MIN_INTERVAL = 1.0     # seconds between requests; override S2_MIN_INTERVAL

# Rich default field set for a single paper.
PAPER_FIELDS = (
    "paperId,externalIds,title,abstract,year,publicationDate,venue,journal,"
    "referenceCount,citationCount,influentialCitationCount,isOpenAccess,"
    "openAccessPdf,fieldsOfStudy,publicationTypes,authors,tldr"
)
# Leaner field set for edges/search rows (kept small — these come in bulk).
ROW_FIELDS = (
    "paperId,externalIds,title,year,venue,citationCount,"
    "influentialCitationCount,isOpenAccess,openAccessPdf,authors"
)
# Edge endpoints also expose wrapper fields about *why* the citation exists.
EDGE_EXTRA = "contexts,intents,isInfluential"

_LAST_REQUEST_AT = [0.0]  # mutable cell for the throttle


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

def _load_dotenv() -> None:
    candidates: list[Path] = []
    override = os.environ.get("S2_ENV_FILE")
    if override:
        candidates.append(Path(override).expanduser())
    candidates.append(Path.cwd() / ".env")
    candidates.append(Path.home() / ".config" / "claude-s2" / ".env")
    for path in candidates:
        try:
            if not path.is_file():
                continue
        except OSError:
            continue
        for line in path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
        break


def _api_key(cli_key: str | None) -> str | None:
    if cli_key:
        return cli_key
    _load_dotenv()
    # Accept a few common spellings.
    for name in ("SEMANTIC_SCHOLAR_API_KEY", "S2_API_KEY", "SEMANTICSCHOLAR_API_KEY"):
        if os.environ.get(name):
            return os.environ[name]
    return None


# ---------------------------------------------------------------------------
# Caching
# ---------------------------------------------------------------------------

def _cache_dir() -> Path:
    base = os.environ.get("S2_CACHE_DIR") or str(Path.home() / ".cache" / "claude-s2")
    return Path(base).expanduser()


def _cache_path(key: str) -> Path:
    digest = hashlib.sha256(key.encode()).hexdigest()[:24]
    return _cache_dir() / f"{digest}.json"


def _cache_get(cfg: dict[str, Any], key: str) -> Any:
    if cfg["cache"] != "on":
        return None
    f = _cache_path(key)
    if not f.exists():
        return None
    try:
        rec = json.loads(f.read_text())
    except Exception:
        return None
    if time.time() - float(rec.get("at", 0)) > cfg["ttl"]:
        return None
    return rec.get("payload")


def _cacheable(payload: Any) -> bool:
    """Don't persist transient/failed bodies. A 200 with an explicit null `data`
    (seen during rate-limit blips) would otherwise mask real edges on later
    cache hits. A genuine empty list (data == []) is a real answer and is kept."""
    if isinstance(payload, dict):
        if "data" in payload and payload["data"] is None:
            return False
        if payload.get("error") or payload.get("message"):
            return False
    return True


def _cache_put(cfg: dict[str, Any], key: str, payload: Any) -> None:
    if cfg["cache"] == "off":
        return
    try:
        f = _cache_path(key)
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps({"at": time.time(), "payload": payload}))
    except Exception:
        pass


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------

def _throttle(cfg: dict[str, Any]) -> None:
    gap = time.time() - _LAST_REQUEST_AT[0]
    if gap < cfg["min_interval"]:
        time.sleep(cfg["min_interval"] - gap)
    _LAST_REQUEST_AT[0] = time.time()


def _headers(cfg: dict[str, Any]) -> dict[str, str]:
    h = {"Accept": "application/json", "User-Agent": USER_AGENT}
    if cfg["api_key"]:
        h["x-api-key"] = cfg["api_key"]
    return h


def _do(cfg: dict[str, Any], req: urllib.request.Request, cache_key: str | None) -> Any:
    if cache_key:
        hit = _cache_get(cfg, cache_key)
        if hit is not None:
            return hit
    attempts = 0
    while True:
        attempts += 1
        _throttle(cfg)
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
            if cache_key and _cacheable(payload):
                _cache_put(cfg, cache_key, payload)
            return payload
        except urllib.error.HTTPError as e:
            if e.code in (429, 503) and attempts <= 5:
                retry = float(e.headers.get("Retry-After", min(2 ** attempts, 30)))
                sys.stderr.write(f"[s2] {e.code} rate-limited; retrying in {retry:.0f}s\n")
                time.sleep(retry)
                continue
            detail = e.read().decode("utf-8", "replace")[:500]
            sys.exit(f"ERROR: S2 API {e.code} on {req.full_url}\n{detail}")
        except urllib.error.URLError as e:
            sys.exit(f"ERROR: could not reach S2 API: {e.reason}")


def _get(cfg: dict[str, Any], base: str, path: str, params: dict[str, Any] | None = None) -> Any:
    clean = {k: v for k, v in (params or {}).items() if v is not None}
    url = base + path
    if clean:
        url += "?" + urllib.parse.urlencode(clean, doseq=True)
    cache_key = None if cfg["cache"] == "off" else f"GET {url}"
    # --refresh bypasses reads but still rewrites the cache.
    if cfg["cache"] == "refresh" and cache_key:
        # temporarily treat as miss
        rec_key = cache_key
        req = urllib.request.Request(url, headers=_headers(cfg))
        payload = _do(cfg | {"cache": "bypass_read"}, req, None)
        _cache_put(cfg, rec_key, payload)
        return payload
    req = urllib.request.Request(url, headers=_headers(cfg))
    return _do(cfg, req, cache_key)


def _post(cfg: dict[str, Any], base: str, path: str, params: dict[str, Any], body: dict[str, Any]) -> Any:
    clean = {k: v for k, v in params.items() if v is not None}
    url = base + path + ("?" + urllib.parse.urlencode(clean, doseq=True) if clean else "")
    data = json.dumps(body).encode("utf-8")
    # Batch POSTs are cacheable too (same ids+fields → same result).
    cache_key = None if cfg["cache"] in ("off",) else f"POST {url} {json.dumps(body, sort_keys=True)}"
    if cfg["cache"] == "refresh" and cache_key:
        req = urllib.request.Request(url, data=data, method="POST",
                                     headers={**_headers(cfg), "Content-Type": "application/json"})
        payload = _do(cfg | {"cache": "bypass_read"}, req, None)
        _cache_put(cfg, cache_key, payload)
        return payload
    if cache_key:
        hit = _cache_get(cfg, cache_key)
        if hit is not None:
            return hit
    req = urllib.request.Request(url, data=data, method="POST",
                                 headers={**_headers(cfg), "Content-Type": "application/json"})
    payload = _do(cfg, req, None)
    if cache_key and _cacheable(payload):
        _cache_put(cfg, cache_key, payload)
    return payload


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def normalize_id(raw: str) -> str:
    """Accept raw S2 ids, URLs, or prefixed external ids. Add a DOI:/ARXIV:
    prefix when the bare string is obviously one of those."""
    s = raw.strip()
    if not s:
        return s
    known = ("DOI:", "ARXIV:", "MAG:", "ACL:", "PMID:", "PMCID:", "CorpusId:", "URL:")
    if s.startswith(known):
        return s
    low = s.lower()
    if low.startswith("10.") or "doi.org/" in low:
        return "DOI:" + s.split("doi.org/")[-1]
    if low.startswith("arxiv:"):
        return "ARXIV:" + s.split(":", 1)[1]
    # arXiv id like 2310.06825 or 2310.06825v2
    core = s.split("v")[0]
    if core.replace(".", "").isdigit() and "." in core and len(core.split(".")[0]) == 4:
        return "ARXIV:" + s
    return s  # assume it's already a 40-char S2 paperId or CorpusId


def _authors_short(p: dict[str, Any], n: int = 3) -> str:
    auth = p.get("authors") or []
    names = [a.get("name", "?") for a in auth[:n]]
    if len(auth) > n:
        names.append("et al.")
    return ", ".join(names)


def _paginate_edges(cfg: dict[str, Any], paper_id: str, kind: str,
                    fields: str, cap: int) -> list[dict[str, Any]]:
    """Page through /citations or /references up to `cap` items."""
    out: list[dict[str, Any]] = []
    offset = 0
    page = 1000
    while len(out) < cap:
        limit = min(page, cap - len(out))
        data = _get(cfg, GRAPH_BASE, f"/paper/{urllib.parse.quote(paper_id, safe=':')}/{kind}",
                    {"fields": fields, "offset": offset, "limit": limit})
        batch = (data or {}).get("data") or []
        out.extend(batch)
        nxt = (data or {}).get("next")
        if not batch or nxt is None:
            break
        offset = nxt
    return out


def _emit(data: Any, pretty: bool) -> None:
    print(json.dumps(data, indent=2 if pretty else None, ensure_ascii=False))


def _table(rows: list[dict[str, Any]]) -> None:
    for p in rows:
        cc = p.get("citationCount", "?")
        ic = p.get("influentialCitationCount", "")
        oa = "OA" if p.get("isOpenAccess") else "  "
        ic_s = f"/{ic}★" if ic not in ("", None) else ""
        print(f"[{cc}{ic_s}] {oa} {p.get('year','????')}  {p.get('title','(no title)')}")
        sub = _authors_short(p)
        ids = p.get("externalIds") or {}
        doi = ids.get("DOI")
        tail = f"      {sub}"
        if doi:
            tail += f"  doi:{doi}"
        tail += f"  [{p.get('paperId','')}]"
        print(tail)


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def cmd_search(args: argparse.Namespace, cfg: dict[str, Any]) -> None:
    params = {
        "query": args.query,
        "fields": args.fields or ROW_FIELDS,
        "limit": args.limit,
        "offset": args.offset,
        "year": args.year,
        "venue": ",".join(args.venue) if args.venue else None,
        "fieldsOfStudy": ",".join(args.fields_of_study) if args.fields_of_study else None,
        "openAccessPdf": "" if args.open_access else None,
    }
    data = _get(cfg, GRAPH_BASE, "/paper/search", params)
    rows = (data or {}).get("data") or []
    if args.min_citations:
        rows = [p for p in rows if (p.get("citationCount") or 0) >= args.min_citations]
    if args.format == "table":
        _table(rows)
    else:
        _emit({"total": data.get("total"), "data": rows}, args.pretty)


def cmd_bulk(args: argparse.Namespace, cfg: dict[str, Any]) -> None:
    """Bulk search: boolean query, up to ~1000 results via continuation token."""
    collected: list[dict[str, Any]] = []
    token = None
    while len(collected) < args.limit:
        params = {
            "query": args.query,
            "fields": args.fields or ROW_FIELDS,
            "year": args.year,
            "venue": ",".join(args.venue) if args.venue else None,
            "fieldsOfStudy": ",".join(args.fields_of_study) if args.fields_of_study else None,
            "openAccessPdf": "" if args.open_access else None,
            "sort": args.sort,
            "token": token,
        }
        data = _get(cfg, GRAPH_BASE, "/paper/search/bulk", params)
        batch = data.get("data", []) or []
        collected.extend(batch)
        token = data.get("token")
        if not token or not batch:
            break
    collected = collected[: args.limit]
    if args.min_citations:
        collected = [p for p in collected if (p.get("citationCount") or 0) >= args.min_citations]
    if args.format == "table":
        _table(collected)
    else:
        _emit({"count": len(collected), "data": collected}, args.pretty)


def cmd_paper(args: argparse.Namespace, cfg: dict[str, Any]) -> None:
    pid = normalize_id(args.id)
    data = _get(cfg, GRAPH_BASE, f"/paper/{urllib.parse.quote(pid, safe=':')}",
                {"fields": args.fields or PAPER_FIELDS})
    _emit(data, args.pretty)


def cmd_edges(args: argparse.Namespace, cfg: dict[str, Any], kind: str) -> None:
    pid = normalize_id(args.id)
    fields = args.fields or (ROW_FIELDS + "," + EDGE_EXTRA)
    rows = _paginate_edges(cfg, pid, kind, fields, args.limit)
    inner = "citingPaper" if kind == "citations" else "citedPaper"
    if args.influential_only:
        rows = [r for r in rows if r.get("isInfluential")]
    if args.format == "table":
        flat = []
        for r in rows:
            p = dict(r.get(inner) or {})
            if r.get("isInfluential"):
                p["title"] = "★ " + (p.get("title") or "")
            flat.append(p)
        _table(flat)
    else:
        _emit({"id": pid, "kind": kind, "count": len(rows), "data": rows}, args.pretty)


def cmd_batch(args: argparse.Namespace, cfg: dict[str, Any]) -> None:
    ids = _collect_ids(args)
    ids = [normalize_id(i) for i in ids]
    data = _post(cfg, GRAPH_BASE, "/paper/batch",
                 {"fields": args.fields or PAPER_FIELDS}, {"ids": ids})
    if args.format == "table":
        _table([p for p in data if p])
    else:
        _emit(data, args.pretty)


def cmd_recommend(args: argparse.Namespace, cfg: dict[str, Any]) -> None:
    pid = normalize_id(args.id)
    data = _get(cfg, REC_BASE, f"/papers/forpaper/{urllib.parse.quote(pid, safe=':')}",
                {"fields": args.fields or ROW_FIELDS, "limit": args.limit})
    rows = data.get("recommendedPapers", [])
    if args.format == "table":
        _table(rows)
    else:
        _emit({"data": rows}, args.pretty)


def cmd_author(args: argparse.Namespace, cfg: dict[str, Any]) -> None:
    if args.papers:
        data = _get(cfg, GRAPH_BASE, f"/author/{args.query}/papers",
                    {"fields": args.fields or ROW_FIELDS, "limit": args.limit})
        rows = data.get("data", [])
        _table(rows) if args.format == "table" else _emit({"data": rows}, args.pretty)
    else:
        data = _get(cfg, GRAPH_BASE, "/author/search",
                    {"query": args.query, "limit": args.limit,
                     "fields": "authorId,name,paperCount,citationCount,hIndex,affiliations"})
        _emit(data, args.pretty)


def cmd_cache(args: argparse.Namespace, cfg: dict[str, Any]) -> None:
    d = _cache_dir()
    files = list(d.glob("*.json")) if d.exists() else []
    if args.clear:
        for f in files:
            try:
                f.unlink()
            except OSError:
                pass
        print(f"Cleared {len(files)} cache entries from {d}")
        return
    size = sum(f.stat().st_size for f in files)
    print(json.dumps({
        "dir": str(d),
        "entries": len(files),
        "bytes": size,
        "ttl_seconds": cfg["ttl"],
    }, indent=2))


# ---------------------------------------------------------------------------
# Snowballing — the headline feature
# ---------------------------------------------------------------------------

def cmd_snowball(args: argparse.Namespace, cfg: dict[str, Any]) -> None:
    seeds = [normalize_id(i) for i in _collect_ids(args)]
    if not seeds:
        sys.exit("ERROR: no seed ids. Pass them as arguments, via --seeds-file, or on stdin.")

    directions: list[str] = []
    if args.direction in ("forward", "both"):
        directions.append("citations")   # who cites the seed → newer work
    if args.direction in ("backward", "both"):
        directions.append("references")   # what the seed cites → foundational work

    edge_fields = ROW_FIELDS + "," + EDGE_EXTRA

    # Resolve seeds to canonical paperIds + titles so provenance is readable
    # and so a seed referenced by DOI and by S2 id isn't treated as two papers.
    seed_meta = _post(cfg, GRAPH_BASE, "/paper/batch",
                      {"fields": "paperId,title,year,externalIds"}, {"ids": seeds})
    seed_ids = set()
    discovered: dict[str, dict[str, Any]] = {}
    for raw, meta in zip(seeds, seed_meta):
        if meta and meta.get("paperId"):
            seed_ids.add(meta["paperId"])
        else:
            sys.stderr.write(f"[s2] warning: seed not found: {raw}\n")

    frontier = set(seed_ids)
    seen = set(seed_ids)
    hop_stats = []

    def passes(p: dict[str, Any]) -> bool:
        if args.year_from and (p.get("year") or 0) < args.year_from:
            return False
        if args.min_citations and (p.get("citationCount") or 0) < args.min_citations:
            return False
        if args.fields_of_study:
            fos = {f.lower() for f in (p.get("fieldsOfStudy") or [])}
            if not fos & {f.lower() for f in args.fields_of_study}:
                return False
        return True

    for hop in range(1, args.hops + 1):
        next_frontier: set[str] = set()
        added_this_hop = 0
        for src in sorted(frontier):
            for kind in directions:
                rows = _paginate_edges(cfg, src, kind, edge_fields, args.limit_per_paper)
                inner = "citingPaper" if kind == "citations" else "citedPaper"
                for r in rows:
                    if args.influential_only and not r.get("isInfluential"):
                        continue
                    p = r.get(inner) or {}
                    pid = p.get("paperId")
                    if not pid or not passes(p):
                        continue
                    prov = {
                        "from": src,
                        "direction": "forward" if kind == "citations" else "backward",
                        "hop": hop,
                        "isInfluential": r.get("isInfluential", False),
                        "intents": r.get("intents") or [],
                    }
                    if pid in discovered:
                        discovered[pid]["reachedVia"].append(prov)
                    elif pid not in seen:
                        rec = {k: p.get(k) for k in
                               ("paperId", "title", "year", "venue", "citationCount",
                                "influentialCitationCount", "isOpenAccess", "openAccessPdf",
                                "externalIds", "authors")}
                        rec["reachedVia"] = [prov]
                        discovered[pid] = rec
                        next_frontier.add(pid)
                        added_this_hop += 1
                    if args.max_papers and len(discovered) >= args.max_papers:
                        break
            seen.add(src)
            if args.max_papers and len(discovered) >= args.max_papers:
                break
        hop_stats.append({"hop": hop, "frontier": len(frontier), "newly_found": added_this_hop})
        sys.stderr.write(f"[s2] hop {hop}: expanded {len(frontier)} papers → "
                         f"{added_this_hop} new (total {len(discovered)})\n")
        seen |= next_frontier
        frontier = next_frontier
        if not frontier or (args.max_papers and len(discovered) >= args.max_papers):
            break

    results = sorted(discovered.values(),
                     key=lambda p: (len(p["reachedVia"]), p.get("citationCount") or 0),
                     reverse=True)

    if args.ids_only:
        for p in results:
            print(p["paperId"])
        return
    if args.format == "table":
        sys.stderr.write(f"\n{len(results)} candidate papers from {len(seed_ids)} seeds:\n\n")
        for p in results:
            hits = len(p["reachedVia"])
            star = "★" if any(v["isInfluential"] for v in p["reachedVia"]) else " "
            print(f"[cites:{p.get('citationCount','?')}] x{hits}{star} {p.get('year','????')}  "
                  f"{p.get('title','(no title)')}")
            print(f"      {_authors_short(p)}  [{p['paperId']}]")
        return
    _emit({
        "seeds": sorted(seed_ids),
        "direction": args.direction,
        "hops": args.hops,
        "stats": hop_stats,
        "count": len(results),
        "papers": results,
    }, args.pretty)


# ---------------------------------------------------------------------------
# id collection (positional / file / stdin)
# ---------------------------------------------------------------------------

def _collect_ids(args: argparse.Namespace) -> list[str]:
    ids: list[str] = list(getattr(args, "ids", None) or [])
    f = getattr(args, "seeds_file", None) or getattr(args, "ids_file", None)
    if f:
        text = Path(f).expanduser().read_text()
        ids += [ln.strip() for ln in text.splitlines() if ln.strip() and not ln.startswith("#")]
    if not ids and not sys.stdin.isatty():
        ids += [ln.strip() for ln in sys.stdin.read().splitlines() if ln.strip()]
    # de-dup, preserve order
    seen, out = set(), []
    for i in ids:
        if i not in seen:
            seen.add(i)
            out.append(i)
    return out


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--api-key", help="S2 API key (else env / .env)")
    common.add_argument("--pretty", action="store_true", help="indent JSON output")
    common.add_argument("--format", choices=["json", "table"], default="json")
    common.add_argument("--fields", help="override the comma-separated S2 fields list")
    g = common.add_mutually_exclusive_group()
    g.add_argument("--no-cache", action="store_true", help="ignore and don't write the cache")
    g.add_argument("--refresh", action="store_true", help="re-fetch and overwrite the cache")

    p = argparse.ArgumentParser(prog="s2.py",
                                description="Semantic Scholar CLI for literature-review snowballing.",
                                parents=[common])
    sub = p.add_subparsers(dest="command", required=True)

    s = sub.add_parser("search", parents=[common], help="relevance paper search")
    s.add_argument("query")
    s.add_argument("--limit", type=int, default=20)
    s.add_argument("--offset", type=int, default=0)
    s.add_argument("--year", help='e.g. 2018 or 2018-2024 or 2020-')
    s.add_argument("--venue", nargs="+")
    s.add_argument("--fields-of-study", nargs="+")
    s.add_argument("--open-access", action="store_true", help="only papers with an OA PDF")
    s.add_argument("--min-citations", type=int)
    s.set_defaults(func=cmd_search)

    b = sub.add_parser("bulk", parents=[common],
                       help="bulk search (boolean query, up to ~1000 results)")
    b.add_argument("query", help='supports AND/OR/quotes, e.g. \'"machine learning" + (review | survey)\'')
    b.add_argument("--limit", type=int, default=1000)
    b.add_argument("--year")
    b.add_argument("--venue", nargs="+")
    b.add_argument("--fields-of-study", nargs="+")
    b.add_argument("--open-access", action="store_true")
    b.add_argument("--min-citations", type=int)
    b.add_argument("--sort", help='e.g. "citationCount:desc" or "publicationDate:desc"')
    b.set_defaults(func=cmd_bulk)

    pp = sub.add_parser("paper", parents=[common], help="one paper's full metadata")
    pp.add_argument("id", help="S2 id, DOI:..., ARXIV:..., CorpusId:..., PMID:..., or a URL")
    pp.set_defaults(func=cmd_paper)

    c = sub.add_parser("citations", parents=[common],
                       help="papers that CITE this one (forward snowballing)")
    c.add_argument("id")
    c.add_argument("--limit", type=int, default=1000)
    c.add_argument("--influential-only", action="store_true")
    c.set_defaults(func=lambda a, cfg: cmd_edges(a, cfg, "citations"))

    r = sub.add_parser("references", parents=[common],
                       help="papers this one CITES (backward snowballing)")
    r.add_argument("id")
    r.add_argument("--limit", type=int, default=1000)
    r.add_argument("--influential-only", action="store_true")
    r.set_defaults(func=lambda a, cfg: cmd_edges(a, cfg, "references"))

    bt = sub.add_parser("batch", parents=[common], help="metadata for many ids at once (≤500)")
    bt.add_argument("ids", nargs="*", help="ids; or use --ids-file / stdin")
    bt.add_argument("--ids-file")
    bt.set_defaults(func=cmd_batch)

    rec = sub.add_parser("recommend", parents=[common], help="recommended papers for a seed")
    rec.add_argument("id")
    rec.add_argument("--limit", type=int, default=20)
    rec.set_defaults(func=cmd_recommend)

    au = sub.add_parser("author", parents=[common], help="author search, or --papers <authorId>")
    au.add_argument("query", help="author name to search, or an authorId with --papers")
    au.add_argument("--papers", action="store_true", help="treat query as an authorId and list their papers")
    au.add_argument("--limit", type=int, default=20)
    au.set_defaults(func=cmd_author)

    sn = sub.add_parser("snowball", parents=[common],
                        help="multi-hop citation chasing from seed papers (the main event)")
    sn.add_argument("ids", nargs="*", help="seed ids; or --seeds-file / stdin")
    sn.add_argument("--seeds-file")
    sn.add_argument("--direction", choices=["forward", "backward", "both"], default="both",
                    help="forward = who cites the seeds; backward = what they cite")
    sn.add_argument("--hops", type=int, default=1, help="iterations of expansion (default 1)")
    sn.add_argument("--limit-per-paper", type=int, default=200,
                    help="cap edges pulled per paper per direction")
    sn.add_argument("--influential-only", action="store_true",
                    help="only follow S2 'influential' citation edges (high precision)")
    sn.add_argument("--year-from", type=int, help="drop papers older than this year")
    sn.add_argument("--min-citations", type=int, help="drop papers below this citation count")
    sn.add_argument("--fields-of-study", nargs="+", help="keep only these fields of study")
    sn.add_argument("--max-papers", type=int, help="stop once this many candidates are found")
    sn.add_argument("--ids-only", action="store_true",
                    help="print only the discovered paperIds, one per line")
    sn.set_defaults(func=cmd_snowball)

    cc = sub.add_parser("cache", parents=[common], help="inspect or clear the disk cache")
    cc.add_argument("--clear", action="store_true")
    cc.set_defaults(func=cmd_cache)

    return p


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    cache = "on"
    if getattr(args, "no_cache", False):
        cache = "off"
    elif getattr(args, "refresh", False):
        cache = "refresh"
    cfg = {
        "api_key": _api_key(getattr(args, "api_key", None)),
        "cache": cache,
        "ttl": float(os.environ.get("S2_CACHE_TTL", DEFAULT_TTL)),
        "min_interval": float(os.environ.get("S2_MIN_INTERVAL", DEFAULT_MIN_INTERVAL)),
    }
    if not cfg["api_key"]:
        sys.stderr.write("[s2] no API key found — using the shared anonymous pool "
                         "(heavily rate-limited). Set SEMANTIC_SCHOLAR_API_KEY to fix.\n")
    args.func(args, cfg)


if __name__ == "__main__":
    main()

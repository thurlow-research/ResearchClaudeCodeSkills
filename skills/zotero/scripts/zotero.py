#!/usr/bin/env python3
"""
zotero.py — minimal CLI wrapper around the Zotero Web API v3.

Auth & scope are read from (in order of precedence):
  1. Command-line flags: --api-key, --library-id, --library-type, --collection
  2. A .env file in the current working directory
  3. Environment variables: ZOTERO_API_KEY, ZOTERO_LIBRARY_ID,
     ZOTERO_LIBRARY_TYPE, ZOTERO_COLLECTION_KEY

Stdlib only. If you'd rather use the excellent `pyzotero` package, it's a
drop-in upgrade — but we avoid a dependency so the skill works out of the box.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
import urllib.parse
import urllib.request
import urllib.error
from pathlib import Path
from typing import Any, Iterator

API_BASE = "https://api.zotero.org"
USER_AGENT = "claude-zotero-skill/0.1"
PAGE_SIZE = 100  # Zotero's hard cap per request

# How long (seconds) to trust a previously-probed library version before
# re-checking it. Within this window the cache is served with zero network
# calls. Override with ZOTERO_CACHE_VERSION_TTL.
DEFAULT_VERSION_TTL = 60.0


# ---------------------------------------------------------------------------
# Config loading
# ---------------------------------------------------------------------------

def _load_dotenv() -> None:
    """Minimal .env loader — no external dependency.

    Checks candidate locations in order, stopping at the first that exists:
      1. $ZOTERO_ENV_FILE         — explicit override, useful for swapping
                                    between configs (e.g. different groups)
      2. ./.env                    — local to the current working directory,
                                    handy for ad-hoc testing from a repo
      3. ~/.config/claude-zotero/.env   — standard per-user config location

    The first file found wins; subsequent candidates are not merged. Within the
    chosen file, existing environment variables are never overwritten (we use
    setdefault), so shell exports still take precedence over file values.
    """
    candidates: list[Path] = []
    override = os.environ.get("ZOTERO_ENV_FILE")
    if override:
        candidates.append(Path(override).expanduser())
    candidates.append(Path(".env"))
    candidates.append(Path.home() / ".config" / "claude-zotero" / ".env")

    for path in candidates:
        if not path.exists():
            continue
        for line in path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            key, val = key.strip(), val.strip().strip('"').strip("'")
            os.environ.setdefault(key, val)
        return


def resolve_config(args: argparse.Namespace) -> dict[str, str]:
    _load_dotenv()
    cfg = {
        "api_key": args.api_key or os.environ.get("ZOTERO_API_KEY", ""),
        "library_id": args.library_id or os.environ.get("ZOTERO_LIBRARY_ID", ""),
        "library_type": (args.library_type or os.environ.get("ZOTERO_LIBRARY_TYPE", "user")).lower(),
        "collection": args.collection or os.environ.get("ZOTERO_COLLECTION_KEY", ""),
    }
    if not cfg["api_key"]:
        sys.exit("error: missing ZOTERO_API_KEY (set env var or pass --api-key)")
    if not cfg["library_id"]:
        sys.exit("error: missing ZOTERO_LIBRARY_ID (set env var or pass --library-id)")
    if cfg["library_type"] not in ("user", "group"):
        sys.exit(f"error: ZOTERO_LIBRARY_TYPE must be 'user' or 'group', got {cfg['library_type']!r}")

    # Cache mode: 'on' (read+write), 'refresh' (ignore reads, rewrite), 'off'.
    if getattr(args, "no_cache", False):
        cfg["cache"] = "off"
    elif getattr(args, "refresh", False):
        cfg["cache"] = "refresh"
    else:
        cfg["cache"] = "on"
    return cfg


# ---------------------------------------------------------------------------
# Local response cache
# ---------------------------------------------------------------------------
#
# Zotero stamps every response with a `Last-Modified-Version` header reflecting
# the library version, which only increments when something in the library
# changes. We exploit that for a correctness-preserving cache:
#
#   * One cheap probe per run reads the current library version.
#   * Every cached payload records the version it was fetched at. An entry is
#     valid iff the current library version equals the stored one — i.e. nothing
#     in the library has been edited since. Any write anywhere invalidates the
#     whole cache, which is conservative but always correct.
#   * The probe itself is cached on disk with a short TTL, so a burst of
#     commands makes zero network calls.

_LIB_VERSION_MEMO: dict[str, int] = {}  # per-process memo, keyed by library


def _cache_dir(cfg: dict[str, str]) -> Path:
    base = os.environ.get("ZOTERO_CACHE_DIR") or str(Path.home() / ".cache" / "claude-zotero")
    return Path(base).expanduser() / f"{cfg['library_type']}-{cfg['library_id']}"


def _library_version(cfg: dict[str, str]) -> int:
    """Return the current library version, memoized per-process and on disk.

    Under cache='refresh' or 'off' we always probe the network fresh so a
    refresh genuinely re-validates. Otherwise we trust a disk-stored version
    for DEFAULT_VERSION_TTL seconds, giving zero-network cache hits in a burst.
    """
    lib = f"{cfg['library_type']}:{cfg['library_id']}"
    if lib in _LIB_VERSION_MEMO:
        return _LIB_VERSION_MEMO[lib]

    vpath = _cache_dir(cfg) / "library_version.json"
    if cfg["cache"] == "on" and vpath.exists():
        try:
            ttl = float(os.environ.get("ZOTERO_CACHE_VERSION_TTL", DEFAULT_VERSION_TTL))
            rec = json.loads(vpath.read_text())
            if time.time() - float(rec.get("at", 0)) < ttl:
                _LIB_VERSION_MEMO[lib] = int(rec["v"])
                return _LIB_VERSION_MEMO[lib]
        except Exception:
            pass

    _, hdrs = _request(cfg, _url(cfg, "/collections", {"limit": 1, "format": "keys"}), accept="text/plain")
    v = int(hdrs.get("last-modified-version", 0))
    _LIB_VERSION_MEMO[lib] = v
    try:
        vpath.parent.mkdir(parents=True, exist_ok=True)
        vpath.write_text(json.dumps({"v": v, "at": time.time()}))
    except Exception:
        pass
    return v


def _cache_path(cfg: dict[str, str], kind: str, path: str, params: Any) -> Path:
    raw = json.dumps([kind, path, params or {}], sort_keys=True, default=str)
    digest = hashlib.sha256(raw.encode()).hexdigest()[:24]
    return _cache_dir(cfg) / f"{kind}-{digest}.json"


def _cache_get(cfg: dict[str, str], kind: str, path: str, params: Any) -> Any:
    """Return cached payload if present and still valid, else None."""
    if cfg["cache"] != "on":
        return None
    f = _cache_path(cfg, kind, path, params)
    if not f.exists():
        return None
    try:
        rec = json.loads(f.read_text())
    except Exception:
        return None
    if int(rec.get("v", -1)) != _library_version(cfg):
        return None
    return rec.get("payload")


def _cache_put(cfg: dict[str, str], kind: str, path: str, params: Any, payload: Any) -> None:
    if cfg["cache"] == "off":
        return
    try:
        f = _cache_path(cfg, kind, path, params)
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps({"v": _library_version(cfg), "payload": payload}))
    except Exception:
        pass


def _invalidate_cache(cfg: dict[str, str]) -> None:
    """Drop the cached library version so the next read re-probes.

    Call after any write: the library version has bumped, which already
    invalidates every payload entry on next access — this just forces an
    immediate fresh probe instead of trusting the TTL window.
    """
    _LIB_VERSION_MEMO.clear()
    try:
        (_cache_dir(cfg) / "library_version.json").unlink()
    except FileNotFoundError:
        pass
    except Exception:
        pass


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------

def _url(cfg: dict[str, str], path: str, params: dict[str, Any] | None = None) -> str:
    prefix = "users" if cfg["library_type"] == "user" else "groups"
    url = f"{API_BASE}/{prefix}/{cfg['library_id']}{path}"
    if params:
        # Drop None values so callers can pass conditional params cleanly.
        clean = {k: v for k, v in params.items() if v is not None}
        if clean:
            url += "?" + urllib.parse.urlencode(clean, doseq=True)
    return url


def _request(cfg: dict[str, str], url: str, *, accept: str = "application/json") -> tuple[bytes, dict[str, str]]:
    """Make a request with backoff-aware retries. Returns (body, headers)."""
    headers = {
        "Zotero-API-Key": cfg["api_key"],
        "Zotero-API-Version": "3",
        "Accept": accept,
        "User-Agent": USER_AGENT,
    }
    attempts = 0
    while True:
        attempts += 1
        req = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                body = resp.read()
                # Normalize headers to plain dict.
                hdrs = {k.lower(): v for k, v in resp.headers.items()}
                # Respect Backoff header as a courtesy for subsequent calls.
                if "backoff" in hdrs:
                    time.sleep(float(hdrs["backoff"]))
                return body, hdrs
        except urllib.error.HTTPError as e:
            if e.code in (429, 503) and attempts <= 4:
                retry_after = float(e.headers.get("Retry-After", 2 ** attempts))
                sys.stderr.write(f"[rate-limited, sleeping {retry_after}s]\n")
                time.sleep(retry_after)
                continue
            sys.exit(f"error: HTTP {e.code} from Zotero: {e.read().decode('utf-8', 'replace')}")
        except urllib.error.URLError as e:
            sys.exit(f"error: network failure contacting Zotero: {e}")


def _write_request(cfg: dict[str, str], url: str, method: str, body: bytes,
                   version: int | None = None) -> tuple[int, bytes, dict[str, str]]:
    """PATCH/POST/PUT with optimistic-locking header. Returns (status, body, headers).

    Unlike _request, this never sys.exit()s on HTTP errors — it returns the
    status so callers can handle 412 (version conflict) gracefully.
    """
    headers = {
        "Zotero-API-Key": cfg["api_key"],
        "Zotero-API-Version": "3",
        "Content-Type": "application/json",
        "User-Agent": USER_AGENT,
    }
    if version is not None:
        headers["If-Unmodified-Since-Version"] = str(version)
    attempts = 0
    while True:
        attempts += 1
        req = urllib.request.Request(url, data=body, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                hdrs = {k.lower(): v for k, v in resp.headers.items()}
                return resp.status, resp.read(), hdrs
        except urllib.error.HTTPError as e:
            if e.code in (429, 503) and attempts <= 4:
                retry_after = float(e.headers.get("Retry-After", 2 ** attempts))
                sys.stderr.write(f"[rate-limited, sleeping {retry_after}s]\n")
                time.sleep(retry_after)
                continue
            return e.code, e.read(), {k.lower(): v for k, v in (e.headers or {}).items()}
        except urllib.error.URLError as e:
            sys.exit(f"error: network failure contacting Zotero: {e}")


def _paginate(cfg: dict[str, str], path: str, params: dict[str, Any], limit: int | None) -> list:
    """Fetch all pages of an endpoint (up to `limit`) and return them as a list.

    The fully-assembled result is cached and validated against the library
    version, so repeat calls (collection lists, stage scans, etc.) cost nothing
    when the library is unchanged.
    """
    ck = {**(params or {}), "__limit": limit}
    cached = _cache_get(cfg, "paginate", path, ck)
    if cached is not None:
        return cached

    out: list = []
    start = 0
    page_params = {**params, "limit": PAGE_SIZE}
    while True:
        page_params["start"] = start
        body, hdrs = _request(cfg, _url(cfg, path, page_params))
        batch = json.loads(body)
        if not isinstance(batch, list):
            # Some endpoints return a dict; return it as a single-element list.
            _cache_put(cfg, "paginate", path, ck, [batch])
            return [batch]
        for item in batch:
            out.append(item)
            if limit is not None and len(out) >= limit:
                _cache_put(cfg, "paginate", path, ck, out)
                return out
        total = int(hdrs.get("total-results", len(batch)))
        start += len(batch)
        if start >= total or not batch:
            break
    _cache_put(cfg, "paginate", path, ck, out)
    return out


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------

def _print(data: Any, fmt: str) -> None:
    if fmt == "json":
        json.dump(data, sys.stdout, indent=2, ensure_ascii=False)
        sys.stdout.write("\n")
    elif fmt == "table":
        _print_table(data)
    else:
        sys.exit(f"error: unknown format {fmt!r}")


def _print_table(data: Any) -> None:
    if not isinstance(data, list) or not data:
        print("(no results)" if not data else data)
        return
    # Heuristic table: pick a couple of useful columns depending on shape.
    first = data[0]
    if isinstance(first.get("data"), dict) and "name" in first["data"]:
        # Collections endpoint — name/parent live under `data`, numItems under `meta`.
        rows = [
            (
                c.get("key", ""),
                (c["data"].get("name") or "")[:60],
                c["data"].get("parentCollection") or "",
                str(c.get("meta", {}).get("numItems", "")),
            )
            for c in data
        ]
        _tabulate(["key", "name", "parent", "n"], rows)
    elif "data" in first and isinstance(first["data"], dict):
        # Items endpoint
        rows = [
            (d.get("key", ""), d.get("itemType", ""), (d.get("title") or "")[:70], d.get("date", ""))
            for d in (x["data"] for x in data)
        ]
        _tabulate(["key", "type", "title", "date"], rows)
    elif "tag" in first:
        # Tags endpoint
        rows = [(d.get("tag", ""), str(d.get("meta", {}).get("numItems", ""))) for d in data]
        _tabulate(["tag", "n"], rows)
    elif "name" in first:
        rows = [(d.get("key", ""), d.get("name", ""), str(d.get("meta", {}).get("numItems", ""))) for d in data]
        _tabulate(["key", "name", "n"], rows)
    else:
        json.dump(data, sys.stdout, indent=2, ensure_ascii=False)
        sys.stdout.write("\n")


def _tabulate(headers: list[str], rows: list[tuple]) -> None:
    widths = [max(len(h), *(len(str(r[i])) for r in rows)) for i, h in enumerate(headers)]
    fmt = "  ".join(f"{{:<{w}}}" for w in widths)
    print(fmt.format(*headers))
    print("  ".join("-" * w for w in widths))
    for row in rows:
        print(fmt.format(*(str(c) for c in row)))


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def cmd_collections(cfg, args):
    data = list(_paginate(cfg, "/collections", {}, args.limit))
    _print([c for c in data], args.format)


def _project_fields(data: list, fields: str) -> list:
    """Reduce full item objects to {key, <requested data fields>} dicts.

    Cuts token usage dramatically — a full Zotero item carries library/links/
    meta/relations the caller rarely needs. `fields` is a comma-separated list
    of `data` keys, e.g. 'title,date,creators'.
    """
    wanted = [f.strip() for f in fields.split(",") if f.strip()]
    out = []
    for item in data:
        d = item.get("data", {})
        row = {"key": item.get("key", d.get("key", ""))}
        for f in wanted:
            if f in d:
                row[f] = d[f]
        out.append(row)
    return out


def cmd_items(cfg, args):
    path = f"/collections/{args.collection}/items" if args.collection else "/items"
    params: dict[str, Any] = {"itemType": "-attachment || note"}  # skip child records by default
    if args.tag:
        params["tag"] = args.tag
    if args.q:
        params["q"] = args.q
        params["qmode"] = "everything"
    if args.format == "bib":
        params["format"] = "bibtex"
        body, _ = _request(cfg, _url(cfg, path, params))
        sys.stdout.write(body.decode("utf-8"))
        return
    data = list(_paginate(cfg, path, params, args.limit))
    if getattr(args, "fields", None):
        _print(_project_fields(data, args.fields), "json")
        return
    _print(data, args.format)


def cmd_count(cfg, args):
    """Print just the number of matching items, read from the Total-Results header.

    One request, no item bodies — the cheapest possible answer to "how many".
    """
    leaf = "/items/top" if args.top else "/items"
    path = f"/collections/{args.collection}{leaf}" if args.collection else leaf
    params: dict[str, Any] = {"itemType": "-attachment || note", "limit": 1}
    if args.tag:
        params["tag"] = args.tag
    if args.q:
        params["q"] = args.q
        params["qmode"] = "everything"
    _, hdrs = _request(cfg, _url(cfg, path, params))
    print(hdrs.get("total-results", "0"))


def cmd_item(cfg, args):
    cached = _cache_get(cfg, "item", f"/items/{args.key}", None)
    if cached is not None:
        _print(cached, args.format)
        return
    body, _ = _request(cfg, _url(cfg, f"/items/{args.key}", None))
    item = json.loads(body)
    # Also fetch children (attachments, notes) for convenience.
    children_body, _ = _request(cfg, _url(cfg, f"/items/{args.key}/children", None))
    item["_children"] = json.loads(children_body)
    _cache_put(cfg, "item", f"/items/{args.key}", None, item)
    _print(item, args.format)


def cmd_search(cfg, args):
    path = f"/collections/{args.collection}/items" if args.collection else "/items"
    params = {"q": args.query, "qmode": "everything", "itemType": "-attachment || note"}
    data = list(_paginate(cfg, path, params, args.limit))
    if getattr(args, "fields", None):
        _print(_project_fields(data, args.fields), "json")
        return
    _print(data, args.format)


def cmd_tags(cfg, args):
    path = f"/collections/{args.collection}/tags" if args.collection else "/tags"
    data = list(_paginate(cfg, path, {}, args.limit))
    _print(data, args.format)


def cmd_export(cfg, args):
    path = f"/collections/{args.collection}/items" if args.collection else "/items"
    params = {"format": args.format, "itemType": "-attachment || note"}
    if args.tag:
        params["tag"] = args.tag
    # Export formats return the raw bytes as-is.
    body, _ = _request(cfg, _url(cfg, path, params))
    sys.stdout.write(body.decode("utf-8", "replace"))


def cmd_attachment(cfg, args):
    # Get metadata first to learn filename + content type.
    meta_body, _ = _request(cfg, _url(cfg, f"/items/{args.key}", None))
    meta = json.loads(meta_body).get("data", {})
    if meta.get("itemType") != "attachment":
        sys.exit(f"error: item {args.key} is a {meta.get('itemType')}, not an attachment")
    filename = meta.get("filename") or f"{args.key}.bin"
    out_path = Path(args.output or filename)
    body, hdrs = _request(cfg, _url(cfg, f"/items/{args.key}/file", None), accept="*/*")
    out_path.write_bytes(body)
    result = {"saved_to": str(out_path), "bytes": len(body), "content_type": hdrs.get("content-type", "")}
    # If it's a PDF and the user wanted text, try to extract.
    if args.extract_text and "pdf" in result["content_type"].lower():
        try:
            import pypdf  # type: ignore
            reader = pypdf.PdfReader(str(out_path))
            result["text"] = "\n\n".join(p.extract_text() or "" for p in reader.pages)
        except ImportError:
            result["text_extraction_error"] = "pypdf not installed; run: pip install pypdf"
    _print(result, args.format)


def cmd_cache(cfg, args):
    """Inspect or clear the local response cache."""
    d = _cache_dir(cfg)
    files = sorted(d.glob("*.json")) if d.exists() else []
    if args.clear:
        removed = 0
        for f in files:
            try:
                f.unlink()
                removed += 1
            except Exception:
                pass
        # Reset process memo so a follow-up call re-probes.
        _LIB_VERSION_MEMO.clear()
        print(f"Cleared {removed} cache file(s) from {d}")
        return
    total_bytes = sum(f.stat().st_size for f in files)
    info = {
        "cache_dir": str(d),
        "entries": len(files),
        "bytes": total_bytes,
        "library_version": _library_version(cfg) if files else None,
    }
    _print(info, "json")


def cmd_raw(cfg, args):
    """Escape hatch: make an arbitrary GET against the configured library."""
    body, _ = _request(cfg, _url(cfg, args.path, None))
    try:
        _print(json.loads(body), args.format)
    except json.JSONDecodeError:
        sys.stdout.write(body.decode("utf-8", "replace"))


# ---------------------------------------------------------------------------
# PRISMA / classification helpers
# ---------------------------------------------------------------------------
#
# These commands recognize a collection structure commonly used for systematic
# reviews:
#
#   <Data Source>/
#     01, 02, 03, ...           ← numbered import batches (audit trail)
#     Classification/
#       00 - Queue              ← awaiting review
#       01 - Keep               ← included
#       02 - Maybe              ← tie-break / needs re-review
#       03 - Discard            ← excluded
#
# Items in batches are LINKED into Classification subcollections (not moved),
# so the same item key can appear in a batch and in a stage simultaneously.
# Counts therefore require fetching item keys and deduplicating, not just
# asking for `numItems`.

# Stage names we recognize, in PRISMA flow order. Matching is case-insensitive
# and tolerates the "NN - " numeric-prefix convention. 'superseded' captures
# items replaced by a newer record (e.g. a preprint later published as a
# journal article); it's excluded from the default in-scope set like 'discard'.
CLASSIFICATION_STAGES = ("queue", "keep", "maybe", "discard", "superseded")

# Canonical stage -> accepted spellings (the library uses both "Superseded" and
# the misspelling "Superceded").
_STAGE_ALIASES = {
    "queue": ("queue",),
    "keep": ("keep",),
    "maybe": ("maybe",),
    "discard": ("discard",),
    "superseded": ("superseded", "superceded"),
}

# Names that mark the screening (classification) container and the imports
# container, respectively. The library uses "02-Screening" / "01-Import(s)";
# older docs called these "Classification" / "Imports".
_SCREENING_NAMES = ("screening", "classification")
_IMPORTS_NAMES = ("import", "imports")


def _strip_numeric_prefix(name: str) -> str:
    """Strip a leading 'NN<sep>' (e.g. '00 - ', '01.', '1:') and lowercase."""
    import re
    return re.sub(r"^\s*\d+\s*[-–.:]\s*", "", name.strip()).strip().lower()


def _normalize_stage_name(name: str) -> str | None:
    """Return the canonical stage name or None.

    Matching is ANCHORED after any numeric prefix — the stage word must start
    the remaining name — so a container like 'Phase 1 - 01-Keep' (a data source
    whose name merely contains 'Keep') is NOT mistaken for a Keep stage, while
    '01-Keep', '00 - Queue', '04-Superceded' all resolve correctly.
    """
    cleaned = _strip_numeric_prefix(name)
    for stage, aliases in _STAGE_ALIASES.items():
        if any(cleaned == a or cleaned.startswith(a) for a in aliases):
            return stage
    return None


def _is_screening_name(name: str) -> bool:
    cleaned = _strip_numeric_prefix(name)
    return any(cleaned == n or cleaned.startswith(n) for n in _SCREENING_NAMES)


def _is_imports_name(name: str) -> bool:
    cleaned = _strip_numeric_prefix(name)
    return any(cleaned == n or cleaned.startswith(n) for n in _IMPORTS_NAMES)


def _collection_item_keys(cfg: dict[str, str], collection_key: str) -> set[str]:
    """Return the set of top-level item keys in a collection."""
    path = f"/collections/{collection_key}/items/top"
    # We only need keys, not full data — use the keys-only format for speed.
    # Paginate: the keys endpoint is capped at PAGE_SIZE per request like any
    # other. Prior versions of this function issued a single request and
    # silently truncated collections larger than PAGE_SIZE, which produced
    # undercounts in prisma/review/dedupe for any stage with >100 items.
    cached = _cache_get(cfg, "keys", path, None)
    if cached is not None:
        return set(cached)

    keys: set[str] = set()
    start = 0
    while True:
        url = _url(cfg, path, {"format": "keys", "limit": PAGE_SIZE, "start": start})
        body, hdrs = _request(cfg, url, accept="text/plain")
        page = {line.strip() for line in body.decode("utf-8").splitlines() if line.strip()}
        if not page:
            break
        keys.update(page)
        total = int(hdrs.get("total-results", len(keys)))
        start += len(page)
        if start >= total:
            break
    _cache_put(cfg, "keys", path, None, sorted(keys))
    return keys


def _build_collection_tree(collections: list[dict]) -> dict[str, list[dict]]:
    """Given a flat list of collections, return a parent-key -> [children] map."""
    children: dict[str, list[dict]] = {}
    for c in collections:
        parent = c["data"].get("parentCollection") or "_root"
        children.setdefault(parent, []).append(c)
    return children


def _stage_parent(tree: dict[str, list[dict]], source: dict) -> dict | None:
    """Return the collection that directly parents this source's stage folders.

    Two layouts are supported:
      * Source has a screening child (e.g. '02-Screening' / 'Classification')
        that contains the stages  → return that child.
      * Source holds the stage folders directly (e.g. the 'Phase 1 - 01-Keep'
        phase containers)          → return the source itself.
    Returns None if neither applies.
    """
    kids = tree.get(source["key"], [])
    for child in kids:
        if _is_screening_name(child["data"]["name"]):
            return child
    if any(_normalize_stage_name(child["data"]["name"]) for child in kids):
        return source
    return None


def _imports_child(tree: dict[str, list[dict]], source_key: str) -> dict | None:
    """Return the imports container child of a source ('01-Import(s)'), if any."""
    for child in tree.get(source_key, []):
        if _is_imports_name(child["data"]["name"]):
            return child
    return None


def _find_data_sources(collections: list[dict], root_name: str | None) -> list[dict]:
    """
    Find data-source collections — those that own a set of screening stages,
    either via a screening child ('02-Screening'/'Classification') or by holding
    the stage folders directly (phase containers). If root_name is given,
    restrict to descendants of a collection with that name.
    """
    tree = _build_collection_tree(collections)

    # Figure out which collection keys are in scope.
    in_scope: set[str] | None = None
    if root_name:
        roots = [c for c in collections if c["data"]["name"] == root_name]
        if not roots:
            sys.exit(f"error: no collection named {root_name!r} found")
        in_scope = set()
        stack = [r["key"] for r in roots]
        while stack:
            k = stack.pop()
            in_scope.add(k)
            stack.extend(child["key"] for child in tree.get(k, []))

    data_sources = []
    for c in collections:
        if in_scope is not None and c["key"] not in in_scope:
            continue
        name = c["data"]["name"]
        # A screening/imports/stage collection is never itself a data source —
        # that would double-count (e.g. '02-Screening' holds stages directly).
        if _is_screening_name(name) or _is_imports_name(name) or _normalize_stage_name(name):
            continue
        if _stage_parent(tree, c) is not None:
            data_sources.append(c)
    return data_sources


def _collect_stage_keys(
    cfg: dict[str, str],
    tree: dict[str, list[dict]],
    data_sources: list[dict],
    stages: list[str],
) -> tuple[dict[str, dict[str, set[str]]], set[str]]:
    """
    For each data source, fetch item keys for each requested stage.

    Returns (per_source, union_across_sources) where:
      per_source[source_name][stage_name] = set of item keys
      union_across_sources = deduplicated set across all sources and stages

    Missing stage collections return empty sets — not errors — so callers
    can ask for stages that don't exist in every source.
    """
    per_source: dict[str, dict[str, set[str]]] = {}
    union: set[str] = set()
    for source in data_sources:
        name = source["data"]["name"]
        per_source[name] = {s: set() for s in stages}
        cls = _stage_parent(tree, source)
        if not cls:
            continue
        for stage_coll in tree.get(cls["key"], []):
            stage = _normalize_stage_name(stage_coll["data"]["name"])
            if stage and stage in stages:
                keys = _collection_item_keys(cfg, stage_coll["key"])
                per_source[name][stage] = keys
                union |= keys
    return per_source, union


def _parse_stages(arg: str) -> list[str]:
    """Parse a comma-separated list of stage names, validating each."""
    requested = [s.strip().lower() for s in arg.split(",") if s.strip()]
    bad = [s for s in requested if s not in CLASSIFICATION_STAGES]
    if bad:
        sys.exit(f"error: unknown stage(s) {bad}; valid: {list(CLASSIFICATION_STAGES)}")
    return requested


def cmd_prisma(cfg, args):
    """Report PRISMA-style counts across data sources with Classification trees.

    Sources "records identified" from an `Imports/` subcollection if present;
    falls back to non-Classification children for backward compatibility with
    the older flat-batch layout.
    """
    all_collections = list(_paginate(cfg, "/collections", {}, None))
    tree = _build_collection_tree(all_collections)

    data_sources = _find_data_sources(all_collections, args.root)
    if not data_sources:
        sys.exit(
            "error: no data-source collections found "
            "(looking for collections containing a 'Classification' child)"
        )

    report: dict[str, Any] = {"sources": [], "totals": {}}
    all_keep_keys: set[str] = set()
    all_maybe_keys: set[str] = set()
    all_identified_keys: set[str] = set()

    for source in sorted(data_sources, key=lambda c: c["data"]["name"]):
        source_name = source["data"]["name"]
        source_entry: dict[str, Any] = {"name": source_name, "batches": [], "classification": {}}

        # Identify batches: prefer the '01-Import(s)' subtree (current
        # convention); otherwise treat direct children that aren't screening/
        # stage/imports folders as loose batches (legacy flat layout). Phase
        # containers have neither, so they report zero batches and we fall back
        # to the screened inflow as "identified" below.
        imports_parent = _imports_child(tree, source["key"])
        if imports_parent:
            batch_collections = tree.get(imports_parent["key"], [])
        else:
            batch_collections = [
                c for c in tree.get(source["key"], [])
                if not _is_screening_name(c["data"]["name"])
                and not _is_imports_name(c["data"]["name"])
                and not _normalize_stage_name(c["data"]["name"])
            ]

        batch_keys: set[str] = set()
        for batch in batch_collections:
            keys = _collection_item_keys(cfg, batch["key"])
            source_entry["batches"].append({"name": batch["data"]["name"], "count": len(keys)})
            batch_keys |= keys

        source_entry["identified"] = len(batch_keys)
        all_identified_keys |= batch_keys

        cls = _stage_parent(tree, source)
        if cls:
            stage_totals: dict[str, int] = {s: 0 for s in CLASSIFICATION_STAGES}
            stage_keys: dict[str, set[str]] = {s: set() for s in CLASSIFICATION_STAGES}
            for stage_coll in tree.get(cls["key"], []):
                stage = _normalize_stage_name(stage_coll["data"]["name"])
                if not stage:
                    continue
                keys = _collection_item_keys(cfg, stage_coll["key"])
                stage_totals[stage] = len(keys)
                stage_keys[stage] = keys
            source_entry["classification"] = stage_totals
            screened = set().union(*stage_keys.values())
            source_entry["screened"] = len(screened)
            all_keep_keys |= stage_keys["keep"]
            all_maybe_keys |= stage_keys["maybe"]
            # Phase containers have no Imports subtree; their inflow is the set
            # of screened items, so use that as "records identified".
            if source_entry["identified"] == 0 and screened:
                source_entry["identified"] = len(screened)
                all_identified_keys |= screened

        source_entry["batches"].sort(key=lambda b: b["name"])
        report["sources"].append(source_entry)

    report["totals"] = {
        "identified_unique_across_sources": len(all_identified_keys),
        "keep_unique_across_sources": len(all_keep_keys),
        "maybe_unique_across_sources": len(all_maybe_keys),
        "in_scope_unique_across_sources": len(all_keep_keys | all_maybe_keys),
    }

    if args.format == "json":
        _print(report, "json")
        return

    for s in report["sources"]:
        print(f"\n{s['name']}")
        print(f"  Records identified (Imports, unique within source): {s['identified']}")
        for b in s["batches"]:
            print(f"    {b['name']}: {b['count']}")
        if s["classification"]:
            print(f"  Records in classification (unique):    {s.get('screened', 0)}")
            for stage in CLASSIFICATION_STAGES:
                marker = "  *" if stage in ("keep", "maybe") else "   "
                print(f"   {marker} {stage:<8} {s['classification'].get(stage, 0)}")
    print(f"\nTotals across sources:")
    print(f"  Unique records identified: {report['totals']['identified_unique_across_sources']}")
    print(f"  Unique records in Keep:    {report['totals']['keep_unique_across_sources']}")
    print(f"  Unique records in Maybe:   {report['totals']['maybe_unique_across_sources']}")
    print(f"  Unique in scope (Keep∪Maybe): {report['totals']['in_scope_unique_across_sources']}")
    print("  (* = in-scope stages for downstream commands by default)")


def cmd_review(cfg, args):
    """List items currently in-scope for review analysis (default: Keep + Maybe)."""
    stages = _parse_stages(args.stages)
    all_collections = list(_paginate(cfg, "/collections", {}, None))
    tree = _build_collection_tree(all_collections)
    data_sources = _find_data_sources(all_collections, args.root)
    if not data_sources:
        sys.exit("error: no data-source collections found")

    per_source, union = _collect_stage_keys(cfg, tree, data_sources, stages)

    # Flatten: per_source_key -> {stages: [...], sources: [...]}.
    key_provenance: dict[str, dict[str, set[str]]] = {}
    for source_name, stage_map in per_source.items():
        for stage, keys in stage_map.items():
            for k in keys:
                entry = key_provenance.setdefault(k, {"stages": set(), "sources": set()})
                entry["stages"].add(stage)
                entry["sources"].add(source_name)

    report = {
        "stages_requested": stages,
        "sources": list(per_source.keys()),
        "unique_items": len(union),
        "per_source_per_stage": {
            src: {stage: len(keys) for stage, keys in stages_map.items()}
            for src, stages_map in per_source.items()
        },
        "items": [
            {
                "key": k,
                "stages": sorted(v["stages"]),
                "sources": sorted(v["sources"]),
            }
            for k, v in sorted(key_provenance.items())
        ],
    }

    if args.format == "json":
        _print(report, "json")
        return

    print(f"Stages in scope: {', '.join(stages)}")
    print(f"Sources: {', '.join(report['sources'])}")
    print(f"Unique items: {report['unique_items']}\n")
    print(f"{'key':10}  {'stages':20}  sources")
    print(f"{'-'*10}  {'-'*20}  {'-'*30}")
    for item in report["items"]:
        print(f"{item['key']:10}  {','.join(item['stages']):20}  {', '.join(item['sources'])}")


def cmd_dedupe(cfg, args):
    """Report item keys appearing in multiple data sources for the given stage(s)."""
    stages = _parse_stages(args.stages)

    all_collections = list(_paginate(cfg, "/collections", {}, None))
    tree = _build_collection_tree(all_collections)
    data_sources = _find_data_sources(all_collections, args.root)
    if not data_sources:
        sys.exit("error: no data-source collections found")

    per_source, _ = _collect_stage_keys(cfg, tree, data_sources, stages)

    # Collapse each source's stages into a single set — we're asking
    # "does this item appear in source X's in-scope items, regardless of stage?"
    flat_per_source: dict[str, set[str]] = {
        name: set().union(*stage_map.values())
        for name, stage_map in per_source.items()
    }

    key_to_sources: dict[str, list[str]] = {}
    for source_name, keys in flat_per_source.items():
        for k in keys:
            key_to_sources.setdefault(k, []).append(source_name)

    duplicates = {k: v for k, v in key_to_sources.items() if len(v) > 1}

    report = {
        "stages": stages,
        "sources_checked": list(flat_per_source.keys()),
        "per_source_counts": {s: len(k) for s, k in flat_per_source.items()},
        "total_entries": sum(len(k) for k in flat_per_source.values()),
        "unique_items": len(key_to_sources),
        "duplicate_items": len(duplicates),
        "duplicates": duplicates,
    }

    if args.format == "json":
        _print(report, "json")
        return

    print(f"Stages: {', '.join(stages)}")
    print(f"Sources: {', '.join(report['sources_checked'])}")
    print(f"Total entries (with cross-source duplicates): {report['total_entries']}")
    print(f"Unique items: {report['unique_items']}")
    print(f"Items appearing in >1 source: {report['duplicate_items']}")
    if duplicates and args.verbose:
        print("\nDuplicate items:")
        for k, srcs in sorted(duplicates.items()):
            print(f"  {k}: {', '.join(srcs)}")


# ---------------------------------------------------------------------------
# Lineage commands (provenance / supersession)
# ---------------------------------------------------------------------------

def _collection_index(collections: list[dict]) -> tuple[dict, dict]:
    """Return (by_key, paths) where paths[key] is the full 'A / B / C' path."""
    by_key = {c["key"]: c for c in collections}

    def full_path(k: str) -> str:
        parts, seen = [], set()
        while k and k in by_key and k not in seen:
            seen.add(k)
            parts.append(by_key[k]["data"]["name"])
            k = by_key[k]["data"].get("parentCollection")
        return " / ".join(reversed(parts))

    return by_key, {k: full_path(k) for k in by_key}


def _group_tags(tags: list[str]) -> dict[str, list[str]]:
    """Bucket the library's tag vocabulary by convention."""
    out: dict[str, list[str]] = {"source": [], "screening": [], "superseded": [], "keyword": []}
    for t in tags:
        if t.startswith("source:"):
            out["source"].append(t.split(":", 1)[1])
        elif t.startswith(("s1:", "s2:")):
            out["screening"].append(t)
        elif t.startswith("superseded-by:") or t.startswith("supersedes:"):
            out["superseded"].append(t)
        else:
            out["keyword"].append(t)
    return out


def cmd_trace(cfg, args):
    """Reconstruct an item's full lineage from its collection memberships + tags."""
    cached = _cache_get(cfg, "item", f"/items/{args.key}", None)
    if cached is not None:
        item = cached
    else:
        body, _ = _request(cfg, _url(cfg, f"/items/{args.key}", None))
        item = json.loads(body)
        _cache_put(cfg, "item", f"/items/{args.key}", None, item)
    data = item.get("data", {})

    collections = _paginate(cfg, "/collections", {}, None)
    _, paths = _collection_index(collections)
    memberships = sorted(paths.get(k, k) for k in data.get("collections", []))
    tags = _group_tags([t.get("tag", "") for t in data.get("tags", [])])

    report = {
        "key": args.key,
        "title": data.get("title", ""),
        "itemType": data.get("itemType", ""),
        "sources": sorted(set(tags["source"])),
        "screening_decisions": sorted(tags["screening"]),
        "supersession": tags["superseded"],
        "memberships": memberships,
        "keywords": tags["keyword"],
    }

    if args.format == "json":
        _print(report, "json")
        return
    print(f"{report['key']}  ({report['itemType']})")
    print(f"  {report['title']}")
    print(f"  sources:    {', '.join(report['sources']) or '—'}")
    print(f"  screening:  {', '.join(report['screening_decisions']) or '—'}")
    if report["supersession"]:
        print(f"  supersession: {', '.join(report['supersession'])}")
    print("  memberships:")
    for m in report["memberships"]:
        print(f"    - {m}")


def cmd_superseded(cfg, args):
    """List superseded item records and the records that replaced them.

    Reads every '04-Superseded' stage collection, extracts the
    `superseded-by:<key>` tag, and (with --resolve) fetches the replacement's
    itemType so you can see type transitions (e.g. preprint → journalArticle).
    """
    collections = _paginate(cfg, "/collections", {}, None)
    if args.root:
        data_sources = _find_data_sources(collections, args.root)
        scope_keys = {c["key"] for c in data_sources}
        tree = _build_collection_tree(collections)
        # superseded collections living under in-scope sources' stage parents
        sup_cols = []
        for s in data_sources:
            sp = _stage_parent(tree, s)
            if sp:
                sup_cols += [c for c in tree.get(sp["key"], [])
                             if _normalize_stage_name(c["data"]["name"]) == "superseded"]
    else:
        sup_cols = [c for c in collections
                    if _normalize_stage_name(c["data"]["name"]) == "superseded"]

    rows, seen = [], set()
    for c in sup_cols:
        items = _paginate(cfg, f"/collections/{c['key']}/items/top",
                          {"itemType": "-attachment || note"}, None)
        for it in items:
            d = it.get("data", {})
            key = it.get("key")
            if key in seen:
                continue
            seen.add(key)
            sb = [t["tag"].split("superseded-by:", 1)[1]
                  for t in d.get("tags", []) if t.get("tag", "").startswith("superseded-by:")]
            rows.append({
                "old_key": key,
                "old_type": d.get("itemType", ""),
                "title": d.get("title", ""),
                "superseded_by": sb[0] if sb else None,
            })

    if args.resolve:
        for r in rows:
            nk = r["superseded_by"]
            if not nk:
                continue
            cached = _cache_get(cfg, "item", f"/items/{nk}", None)
            if cached is None:
                try:
                    body, _ = _request(cfg, _url(cfg, f"/items/{nk}", None))
                    cached = json.loads(body)
                    _cache_put(cfg, "item", f"/items/{nk}", None, cached)
                except SystemExit:
                    cached = {}
            r["new_type"] = cached.get("data", {}).get("itemType", "?")
            r["type_changed"] = bool(r.get("new_type") and r["new_type"] != r["old_type"])

    report = {"superseded_collections": len(sup_cols), "count": len(rows), "items": rows}
    if args.format == "json":
        _print(report, "json")
        return
    print(f"Superseded records: {len(rows)} (across {len(sup_cols)} '04-Superseded' collection(s))")
    for r in rows:
        arrow = ""
        if args.resolve and r.get("superseded_by"):
            change = "  TYPE CHANGED" if r.get("type_changed") else ""
            arrow = f"  ->  {r['superseded_by']} ({r.get('new_type','?')}){change}"
        else:
            arrow = f"  ->  {r['superseded_by']}" if r["superseded_by"] else "  (no superseded-by tag)"
        print(f"  {r['old_key']} ({r['old_type']:<15}) {r['title'][:55]}{arrow}")


# ---------------------------------------------------------------------------
# Write operations (tagging)
# ---------------------------------------------------------------------------

def _add_tags_to_item(cfg: dict[str, str], key: str, add_tags: list[str], commit: bool) -> dict:
    """Add manual tags to one item, idempotently and with version locking.

    Fetches the item fresh (never cached) to read its current version + tags,
    skips tags already present, and PATCHes only the merged tag list. Returns a
    record describing what was (or would be) done.
    """
    body, _ = _request(cfg, _url(cfg, f"/items/{key}", None))
    item = json.loads(body)
    data = item.get("data", {})
    version = item.get("version") or data.get("version")
    existing = {t.get("tag") for t in data.get("tags", [])}
    new = [t for t in add_tags if t not in existing]

    result = {"key": key, "title": (data.get("title") or "")[:60], "add": new}
    if not new:
        result["status"] = "skip-present"
        return result
    if not commit:
        result["status"] = "would-add"
        return result

    merged = data.get("tags", []) + [{"tag": t} for t in new]
    status, rbody, _ = _write_request(
        cfg, _url(cfg, f"/items/{key}", None), "PATCH",
        json.dumps({"tags": merged}).encode("utf-8"), version=version,
    )
    if status in (200, 204):
        result["status"] = "added"
    elif status == 412:
        result["status"] = "conflict-version-changed"
    else:
        result["status"] = f"error-http-{status}"
        result["detail"] = rbody.decode("utf-8", "replace")[:200]
    return result


def _remove_collections_from_item(cfg: dict[str, str], key: str, remove: list[str], commit: bool) -> dict:
    """Remove an item from the given collections, idempotently and version-locked."""
    body, _ = _request(cfg, _url(cfg, f"/items/{key}", None))
    item = json.loads(body)
    data = item.get("data", {})
    version = item.get("version") or data.get("version")
    current = data.get("collections", [])
    remove_set = set(remove)
    kept = [c for c in current if c not in remove_set]
    actually_removing = [c for c in current if c in remove_set]

    result = {"key": key, "title": (data.get("title") or "")[:60], "remove": actually_removing}
    if not actually_removing:
        result["status"] = "skip-not-member"
        return result
    if not commit:
        result["status"] = "would-remove"
        return result

    status, rbody, _ = _write_request(
        cfg, _url(cfg, f"/items/{key}", None), "PATCH",
        json.dumps({"collections": kept}).encode("utf-8"), version=version,
    )
    if status in (200, 204):
        result["status"] = "removed"
    elif status == 412:
        result["status"] = "conflict-version-changed"
    else:
        result["status"] = f"error-http-{status}"
        result["detail"] = rbody.decode("utf-8", "replace")[:200]
    return result


def cmd_collection_remove(cfg, args):
    """Remove items from collections via a plan file. DRY-RUN unless --commit.

    Plan file format (JSON): {"ITEMKEY": ["collectionKey", ...], ...}
    Only removes the listed memberships; never deletes the item or other links.
    """
    plan = json.loads(Path(args.plan).read_text())
    commit = args.commit
    results = [_remove_collections_from_item(cfg, k, list(v), commit) for k, v in plan.items()]
    if commit:
        _invalidate_cache(cfg)
    summary: dict[str, int] = {}
    for r in results:
        summary[r["status"]] = summary.get(r["status"], 0) + 1
    out = {
        "mode": "COMMIT" if commit else "DRY-RUN (no changes written; pass --commit to apply)",
        "items": len(results),
        "memberships_to_remove": sum(len(r["remove"]) for r in results
                                     if r["status"] in ("would-remove", "removed")),
        "summary": summary,
        "results": results,
    }
    _print(out, args.format)


def _add_collections_to_item(cfg: dict[str, str], key: str, add: list[str], commit: bool) -> dict:
    """Add an item to the given collections, idempotently and version-locked."""
    body, _ = _request(cfg, _url(cfg, f"/items/{key}", None))
    item = json.loads(body)
    data = item.get("data", {})
    version = item.get("version") or data.get("version")
    current = data.get("collections", [])
    new = [c for c in add if c not in current]

    result = {"key": key, "title": (data.get("title") or "")[:60], "add": new}
    if not new:
        result["status"] = "skip-already-member"
        return result
    if not commit:
        result["status"] = "would-add"
        return result

    status, rbody, _ = _write_request(
        cfg, _url(cfg, f"/items/{key}", None), "PATCH",
        json.dumps({"collections": current + new}).encode("utf-8"), version=version,
    )
    if status in (200, 204):
        result["status"] = "added"
    elif status == 412:
        result["status"] = "conflict-version-changed"
    else:
        result["status"] = f"error-http-{status}"
        result["detail"] = rbody.decode("utf-8", "replace")[:200]
    return result


def cmd_collection_add(cfg, args):
    """Add items to collections via a plan file. DRY-RUN unless --commit.

    Plan file format (JSON): {"ITEMKEY": ["collectionKey", ...], ...}
    """
    plan = json.loads(Path(args.plan).read_text())
    commit = args.commit
    results = [_add_collections_to_item(cfg, k, list(v), commit) for k, v in plan.items()]
    if commit:
        _invalidate_cache(cfg)
    summary: dict[str, int] = {}
    for r in results:
        summary[r["status"]] = summary.get(r["status"], 0) + 1
    out = {
        "mode": "COMMIT" if commit else "DRY-RUN (no changes written; pass --commit to apply)",
        "items": len(results),
        "memberships_to_add": sum(len(r["add"]) for r in results
                                  if r["status"] in ("would-add", "added")),
        "summary": summary,
        "results": results,
    }
    _print(out, args.format)


def cmd_tag_add(cfg, args):
    """Add tags to items from a plan file or a single key. DRY-RUN unless --commit.

    Plan file format (JSON): {"ITEMKEY": ["tag1", "tag2"], ...}
    """
    if args.plan:
        plan = json.loads(Path(args.plan).read_text())
    elif args.key and args.add:
        plan = {args.key: args.add}
    else:
        sys.exit("error: provide either --plan FILE or KEY with one or more --add TAG")

    commit = args.commit
    results = []
    for key, tags in plan.items():
        results.append(_add_tags_to_item(cfg, key, list(tags), commit))

    if commit:
        _invalidate_cache(cfg)

    summary: dict[str, int] = {}
    for r in results:
        summary[r["status"]] = summary.get(r["status"], 0) + 1

    out = {
        "mode": "COMMIT" if commit else "DRY-RUN (no changes written; pass --commit to apply)",
        "items": len(results),
        "tags_to_write": sum(len(r["add"]) for r in results if r["status"] in ("would-add", "added")),
        "summary": summary,
        "results": results,
    }
    _print(out, args.format)


# ---------------------------------------------------------------------------
# argparse wiring
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Query a Zotero library via the Web API.")
    p.add_argument("--api-key", help="Overrides ZOTERO_API_KEY.")
    p.add_argument("--library-id", help="Overrides ZOTERO_LIBRARY_ID.")
    p.add_argument("--library-type", choices=["user", "group"], help="Overrides ZOTERO_LIBRARY_TYPE.")
    p.add_argument("--collection", help="Overrides ZOTERO_COLLECTION_KEY (8-char key).")
    p.add_argument("--no-cache", action="store_true",
                   help="Bypass the local response cache (always hit the API).")
    p.add_argument("--refresh", action="store_true",
                   help="Ignore cached data and refresh it from the API.")

    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("collections", help="List collections.")
    sp.add_argument("--limit", type=int, default=None)
    sp.add_argument("--format", choices=["json", "table"], default="table")
    sp.set_defaults(func=cmd_collections)

    sp = sub.add_parser("items", help="List items, optionally filtered.")
    sp.add_argument("--tag")
    sp.add_argument("--q", help="Quick-search filter.")
    sp.add_argument("--limit", type=int, default=50)
    sp.add_argument("--fields", help="Comma-separated data fields to keep (e.g. 'title,date,creators'); "
                                     "emits compact JSON. Cuts token usage vs. full objects.")
    sp.add_argument("--format", choices=["json", "table", "bib"], default="table")
    sp.set_defaults(func=cmd_items)

    sp = sub.add_parser("count", help="Print only the count of matching items (one request, no bodies).")
    sp.add_argument("--tag")
    sp.add_argument("--q", help="Quick-search filter.")
    sp.add_argument("--top", action="store_true", default=True,
                    help="Count top-level items only (default; matches the desktop sidebar).")
    sp.add_argument("--all", dest="top", action="store_false",
                    help="Count all items including child attachments/notes.")
    sp.set_defaults(func=cmd_count)

    sp = sub.add_parser("item", help="Fetch a single item with its children.")
    sp.add_argument("key", help="8-character item key.")
    sp.add_argument("--format", choices=["json", "table"], default="json")
    sp.set_defaults(func=cmd_item)

    sp = sub.add_parser("search", help="Search items.")
    sp.add_argument("query")
    sp.add_argument("--limit", type=int, default=25)
    sp.add_argument("--fields", help="Comma-separated data fields to keep; emits compact JSON.")
    sp.add_argument("--format", choices=["json", "table"], default="table")
    sp.set_defaults(func=cmd_search)

    sp = sub.add_parser("tags", help="List tags.")
    sp.add_argument("--limit", type=int, default=None)
    sp.add_argument("--format", choices=["json", "table"], default="table")
    sp.set_defaults(func=cmd_tags)

    sp = sub.add_parser("export", help="Export items in a citation format.")
    sp.add_argument("--format", choices=["bibtex", "ris", "csljson"], default="bibtex")
    sp.add_argument("--tag")
    sp.set_defaults(func=cmd_export)

    sp = sub.add_parser("attachment", help="Download an attachment; optionally extract PDF text.")
    sp.add_argument("key", help="Attachment item key.")
    sp.add_argument("--output", help="Output path (defaults to attachment's filename).")
    sp.add_argument("--extract-text", action="store_true", help="Extract PDF text if applicable.")
    sp.add_argument("--format", choices=["json", "table"], default="json")
    sp.set_defaults(func=cmd_attachment)

    sp = sub.add_parser("cache", help="Inspect or clear the local response cache.")
    sp.add_argument("--clear", action="store_true", help="Delete all cached entries for this library.")
    sp.set_defaults(func=cmd_cache)

    sp = sub.add_parser("raw", help="Make an arbitrary GET against /users/{id} or /groups/{id}.")
    sp.add_argument("path", help="Path under the library root, e.g. /items/top")
    sp.add_argument("--format", choices=["json", "table"], default="json")
    sp.set_defaults(func=cmd_raw)

    sp = sub.add_parser(
        "prisma",
        help="Report PRISMA-style counts across data sources (batches + Classification stages).",
    )
    sp.add_argument("--root", help="Name of the top-level collection to scope to (e.g. 'Database Queries').")
    sp.add_argument("--format", choices=["json", "table"], default="table")
    sp.set_defaults(func=cmd_prisma)

    sp = sub.add_parser(
        "review",
        help="List items in-scope for review analysis. Defaults to Keep+Maybe across all data sources.",
    )
    sp.add_argument("--stages", default="keep,maybe",
                    help="Comma-separated stages to include: queue,keep,maybe,discard (default: keep,maybe).")
    sp.add_argument("--root", help="Name of the top-level collection to scope to.")
    sp.add_argument("--format", choices=["json", "table"], default="table")
    sp.set_defaults(func=cmd_review)

    sp = sub.add_parser(
        "dedupe",
        help="Find items appearing in multiple data sources within the given stages.",
    )
    sp.add_argument("--stages", default="keep,maybe",
                    help="Comma-separated stages to compare (default: keep,maybe).")
    sp.add_argument("--root", help="Name of the top-level collection to scope to.")
    sp.add_argument("--verbose", action="store_true", help="List the duplicate item keys.")
    sp.add_argument("--format", choices=["json", "table"], default="table")
    sp.set_defaults(func=cmd_dedupe)

    sp = sub.add_parser(
        "trace",
        help="Reconstruct one item's full lineage (sources, batches, screening decisions, supersession).",
    )
    sp.add_argument("key", help="8-character item key.")
    sp.add_argument("--format", choices=["json", "table"], default="table")
    sp.set_defaults(func=cmd_trace)

    sp = sub.add_parser(
        "superseded",
        help="List superseded records and what replaced them; --resolve shows type transitions.",
    )
    sp.add_argument("--root", help="Limit to data sources under this top-level collection.")
    sp.add_argument("--resolve", action="store_true",
                    help="Fetch each replacement record's itemType to flag type changes.")
    sp.add_argument("--format", choices=["json", "table"], default="table")
    sp.set_defaults(func=cmd_superseded)

    sp = sub.add_parser(
        "tag-add",
        help="Add tags to items (DRY-RUN by default; pass --commit to write). Needs a write-scoped key.",
    )
    sp.add_argument("key", nargs="?", help="Single item key (or use --plan for bulk).")
    sp.add_argument("--add", action="append", help="Tag to add (repeatable). Used with a single KEY.")
    sp.add_argument("--plan", help="JSON file mapping item keys to lists of tags to add.")
    sp.add_argument("--commit", action="store_true", help="Actually write the changes (otherwise dry-run).")
    sp.add_argument("--format", choices=["json", "table"], default="json")
    sp.set_defaults(func=cmd_tag_add)

    sp = sub.add_parser(
        "collection-add",
        help="Add items to collections from a plan file (DRY-RUN unless --commit). Write-scoped key.",
    )
    sp.add_argument("--plan", required=True, help="JSON: {\"itemKey\": [\"collectionKey\", ...], ...}")
    sp.add_argument("--commit", action="store_true", help="Actually write the changes (otherwise dry-run).")
    sp.add_argument("--format", choices=["json", "table"], default="json")
    sp.set_defaults(func=cmd_collection_add)

    sp = sub.add_parser(
        "collection-remove",
        help="Remove items from collections from a plan file (DRY-RUN unless --commit). Write-scoped key.",
    )
    sp.add_argument("--plan", required=True, help="JSON: {\"itemKey\": [\"collectionKey\", ...], ...}")
    sp.add_argument("--commit", action="store_true", help="Actually write the changes (otherwise dry-run).")
    sp.add_argument("--format", choices=["json", "table"], default="json")
    sp.set_defaults(func=cmd_collection_remove)

    return p


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    cfg = resolve_config(args)
    args.func(cfg, args)


if __name__ == "__main__":
    main()

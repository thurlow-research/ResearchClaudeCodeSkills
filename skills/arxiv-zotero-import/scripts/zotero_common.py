#!/usr/bin/env python3
"""
Shared Zotero API helpers for SLR dedup scripts.

Reads credentials from environment variables only — no hard-coded fallback
keys. This file is distributed as part of a shareable .skill package, so it
must never embed live credentials.

Required env vars:
    ZOTERO_API_KEY_RO    — read-only API key
    ZOTERO_API_KEY_RW    — read/write key (only needed for --apply operations)
    ZOTERO_LIBRARY_ID    — Zotero library ID (group or user)

Optional:
    ZOTERO_LIBRARY_TYPE  — 'group' (default) or 'user'
"""
import hashlib
import json
import os
import random
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

# ============================================================
# CREDENTIALS / CONFIG
# ============================================================
def _require(name):
    val = os.environ.get(name)
    if not val:
        sys.exit(f"error: missing {name} (set it in the environment before running this skill)")
    return val

API_KEY_RO = _require("ZOTERO_API_KEY_RO")
# RW is optional at import time — only needed for --apply; enforced lazily in zot_request
# so dry-run and check_count usage never require a write-scoped key to be present.
API_KEY_RW = os.environ.get("ZOTERO_API_KEY_RW", "")
LIB_ID     = _require("ZOTERO_LIBRARY_ID")
LIB_TYPE   = os.environ.get("ZOTERO_LIBRARY_TYPE", "group").lower()
if LIB_TYPE not in ("user", "group"):
    sys.exit(f"error: ZOTERO_LIBRARY_TYPE must be 'user' or 'group', got {LIB_TYPE!r}")
BASE       = f"https://api.zotero.org/{'users' if LIB_TYPE == 'user' else 'groups'}/{LIB_ID}"

RATE_LIMIT_SEC = 0.3

# ============================================================
# LOCAL RESPONSE CACHE
# ============================================================
# Same cache directory, key scheme, and version-based invalidation as the main
# `zotero` skill's zotero.py, so entries are interchangeable across skills: a
# `zotero.py collections` run and this skill's list_all_collections() can both
# populate/reuse the same "paginate-/collections" cache file for this library.
DEFAULT_CACHE_VERSION_TTL = 60.0
_LIB_VERSION_MEMO = {}


def _cache_dir():
    base = os.environ.get("ZOTERO_CACHE_DIR") or str(Path.home() / ".cache" / "claude-zotero")
    return Path(base).expanduser() / f"{LIB_TYPE}-{LIB_ID}"


def _library_version():
    """Current library version, memoized per-process and on disk (shared TTL)."""
    lib = f"{LIB_TYPE}:{LIB_ID}"
    if lib in _LIB_VERSION_MEMO:
        return _LIB_VERSION_MEMO[lib]

    vpath = _cache_dir() / "library_version.json"
    if vpath.exists():
        try:
            ttl = float(os.environ.get("ZOTERO_CACHE_VERSION_TTL", DEFAULT_CACHE_VERSION_TTL))
            rec = json.loads(vpath.read_text())
            if time.time() - float(rec.get("at", 0)) < ttl:
                _LIB_VERSION_MEMO[lib] = int(rec["v"])
                return _LIB_VERSION_MEMO[lib]
        except Exception:
            pass

    ok, _, hdrs = zot_request("GET", "/collections?limit=1&format=keys")
    v = int({k.lower(): val for k, val in hdrs.items()}.get("last-modified-version", 0)) if ok else 0
    _LIB_VERSION_MEMO[lib] = v
    try:
        vpath.parent.mkdir(parents=True, exist_ok=True)
        vpath.write_text(json.dumps({"v": v, "at": time.time()}))
    except Exception:
        pass
    return v


def _cache_path(kind, path, params):
    raw = json.dumps([kind, path, params or {}], sort_keys=True, default=str)
    digest = hashlib.sha256(raw.encode()).hexdigest()[:24]
    return _cache_dir() / f"{kind}-{digest}.json"


def cache_get(kind, path, params=None):
    """Return a cached payload if present and still valid for the current library version."""
    f = _cache_path(kind, path, params)
    if not f.exists():
        return None
    try:
        rec = json.loads(f.read_text())
    except Exception:
        return None
    if int(rec.get("v", -1)) != _library_version():
        return None
    return rec.get("payload")


def cache_put(kind, path, params, payload):
    try:
        f = _cache_path(kind, path, params)
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps({"v": _library_version(), "payload": payload}))
    except Exception:
        pass


def invalidate_cache():
    """Drop the cached library version so the next read re-probes (call after writes)."""
    _LIB_VERSION_MEMO.clear()
    try:
        (_cache_dir() / "library_version.json").unlink()
    except FileNotFoundError:
        pass
    except Exception:
        pass


# ============================================================
# LOGGING
# ============================================================
_log_file = None

def set_log_file(path):
    """Configure the log file path; if not set, log() prints only."""
    global _log_file
    _log_file = path
    # Truncate previous log
    if os.path.exists(path):
        os.remove(path)

def log(msg):
    line = f"[{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    print(line, flush=True)
    if _log_file:
        with open(_log_file, "a") as f:
            f.write(line + "\n")

# ============================================================
# ATOMIC WRITES
# ============================================================
def save_json(path, obj):
    tmp = path + ".tmp"
    bak = path + ".bak"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())
    if os.path.exists(path):
        try:
            os.replace(path, bak)
        except OSError:
            pass
    os.replace(tmp, path)

def load_json(path):
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        bak = path + ".bak"
        if os.path.exists(bak):
            log(f"  {path} unreadable ({e}); falling back to {bak}")
            try:
                with open(bak, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return None

# ============================================================
# ZOTERO HTTP
# ============================================================
def zot_request(method, path, body=None, api_key=None, headers=None, retries=8):
    """
    Make a Zotero API request with retries on transient errors.
    Returns (ok: bool, response_data, response_headers).
    On failure, response_data contains the error string.
    """
    url = f"{BASE}{path}"
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
    
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, data=data, method=method)
            req.add_header("Zotero-API-Key", api_key or API_KEY_RO)
            req.add_header("Zotero-API-Version", "3")
            if data is not None:
                req.add_header("Content-Type", "application/json")
            if headers:
                for k, v in headers.items():
                    req.add_header(k, v)
            with urllib.request.urlopen(req, timeout=60) as r:
                body_text = r.read().decode("utf-8") if r.length != 0 else ""
                try:
                    return True, json.loads(body_text) if body_text else {}, dict(r.headers)
                except json.JSONDecodeError:
                    return True, body_text, dict(r.headers)
        except urllib.error.HTTPError as e:
            err_body = ""
            try:
                err_body = e.read().decode("utf-8", errors="replace")[:500]
            except Exception:
                pass
            if e.code in (429, 500, 502, 503, 504) and attempt < retries - 1:
                wait = min(60, 2 ** attempt) + random.uniform(0, 1)
                log(f"  Zotero HTTP {e.code} on {method} {path}; retry in {wait:.1f}s")
                time.sleep(wait)
                continue
            return False, f"HTTP {e.code}: {err_body}", {}
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            if attempt < retries - 1:
                wait = min(60, 2 ** attempt) + random.uniform(0, 1)
                log(f"  Network error on {method} {path}: {e}; retry in {wait:.1f}s")
                time.sleep(wait)
                continue
            return False, str(e), {}
    return False, "max retries", {}

# ============================================================
# COMMON QUERIES
# ============================================================
def list_all_collections():
    """Return list of all collections in the library: [{key, name, parent}, ...].

    Cached under the same key the main `zotero` skill's `collections` command uses
    (kind='paginate', path='/collections', params={'__limit': None}), so a recent
    `zotero.py collections` run and this function can share one cache entry.
    """
    cache_key = {"__limit": None}
    cached = cache_get("paginate", "/collections", cache_key)
    if cached is not None:
        log(f"Using cached collection list ({len(cached)} collections)")
        return [{"key": c["key"], "name": c.get("data", {}).get("name", ""),
                  "parent": c.get("data", {}).get("parentCollection", "") or ""} for c in cached]

    log("Scanning all collections in library...")
    raw, out = [], []
    start = 0
    while True:
        ok, data, _ = zot_request("GET", f"/collections?limit=100&start={start}")
        if not ok:
            log(f"  ERROR: {data}")
            sys.exit(1)
        if not data:
            break
        raw.extend(data)
        for c in data:
            d = c.get("data", {})
            out.append({
                "key": c["key"],
                "name": d.get("name", ""),
                "parent": d.get("parentCollection", "") or "",
            })
        if len(data) < 100:
            break
        start += 100
        time.sleep(RATE_LIMIT_SEC)
    log(f"  Found {len(out)} collections")
    cache_put("paginate", "/collections", cache_key, raw)
    return out

def get_collection_items(collection_key, include_meta=True):
    """Return list of full item dicts from a collection (top-level only)."""
    items = []
    start = 0
    while True:
        ok, data, _ = zot_request("GET", f"/collections/{collection_key}/items/top?limit=100&start={start}")
        if not ok:
            log(f"  ERROR fetching items of {collection_key}: {data}")
            return None
        if not data:
            break
        items.extend(data)
        if len(data) < 100:
            break
        start += 100
        time.sleep(RATE_LIMIT_SEC)
    return items

def find_collection_by_name(name, collections=None):
    """Find a collection by exact name match. Returns first match key or None."""
    if collections is None:
        collections = list_all_collections()
    for c in collections:
        if c["name"] == name:
            return c["key"]
    return None

def update_item(item_key, item_data, version, api_key=None):
    """PATCH an item with new fields. Uses If-Unmodified-Since-Version for safety."""
    key = api_key or API_KEY_RW
    if not key:
        sys.exit("error: missing ZOTERO_API_KEY_RW (required for write operations)")
    headers = {"If-Unmodified-Since-Version": str(version)}
    ok, resp, hdrs = zot_request(
        "PATCH",
        f"/items/{item_key}",
        body=item_data,
        api_key=key,
        headers=headers,
    )
    return ok, resp

def get_item(item_key):
    """Fetch a single item (full record including current version)."""
    ok, data, _ = zot_request("GET", f"/items/{item_key}")
    if not ok:
        return None
    return data

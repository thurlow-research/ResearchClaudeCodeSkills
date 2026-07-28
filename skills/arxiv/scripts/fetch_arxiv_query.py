#!/usr/bin/env python3
"""
Fetch arXiv API results and emit a Zotero create-items plan for a target collection.

Query-only: this script never writes to Zotero. It fetches results, resolves the
target collection (read-only), and writes a plan file. Creating the items is a
separate step through the main `zotero` skill's `zotero.py create-items`, which
owns all Zotero write plumbing (auth, batching, retries, resume) in one place.

Generalized version of fetch_q_arxiv_06.py / fetch_q_arxiv_07.py — pass the
query string, target collection name, and working directory as arguments.

Output (in --workdir):
    arxiv_results.json    — full arXiv fetch results (cached for resume)
    create_plan.csv       — human-readable preview of what would be created
    create_plan.json      — machine plan for `zotero.py create-items`
    fetch.log             — execution log

Usage:
    python3 fetch_arxiv_query.py \\
        --query "<arxiv-query>" \\
        --collection "Q-arXiv-NN" \\
        --workdir ~/slr/arxiv/q-arxiv-NN

    # Then, to actually create the items (dry-run by default):
    python3 <path-to-zotero-skill>/scripts/zotero.py create-items \\
        --plan ~/slr/arxiv/q-arxiv-NN/create_plan.json \\
        --state ~/slr/arxiv/q-arxiv-NN/state.json \\
        --commit
"""
import argparse
import csv
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
import urllib.error
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

import zotero_common as zc

# ============================================================
# CONFIG (constants)
# ============================================================
ARXIV_BASE = "https://export.arxiv.org/api/query"
ARXIV_PAGE_SIZE = 100        # arXiv's documented cap is 2000, but they recommend refining
                             # queries over ~1000 results; 100 halves round trips vs. the old
                             # 50 without pushing into that territory
ARXIV_RATE_LIMIT_SEC = 3.0   # arXiv API requests >=3s apart

NS = {
    "atom": "http://www.w3.org/2005/Atom",
    "opensearch": "http://a9.com/-/spec/opensearch/1.1/",
    "arxiv": "http://arxiv.org/schemas/atom",
}

# ============================================================
# ARXIV FETCH
# ============================================================
def arxiv_fetch_page(query, start, max_results):
    params = {
        "search_query": query,
        "start": start,
        "max_results": max_results,
        "sortBy": "submittedDate",
        "sortOrder": "descending",
    }
    url = f"{ARXIV_BASE}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url)
    req.add_header("User-Agent", "VibeCodingSLR/1.0 (research)")
    for attempt in range(5):
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                return r.read().decode("utf-8")
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as e:
            wait = min(60, 2 ** attempt) + 1
            zc.log(f"  arXiv fetch error: {e}; retry in {wait}s (attempt {attempt+1}/5)")
            time.sleep(wait)
    return None

def parse_arxiv_entries(xml_text):
    root = ET.fromstring(xml_text)
    total_el = root.find("opensearch:totalResults", NS)
    total = int(total_el.text) if total_el is not None and total_el.text else 0
    entries = []
    for e in root.findall("atom:entry", NS):
        id_el = e.find("atom:id", NS)
        arxiv_url = id_el.text.strip() if id_el is not None else ""
        m = re.match(r"https?://arxiv\.org/abs/(.+?)(v\d+)?$", arxiv_url)
        arxiv_id = m.group(1) if m else arxiv_url
        version = (m.group(2) or "").lstrip("v") if m else ""
        
        title_el = e.find("atom:title", NS)
        title = " ".join(title_el.text.split()) if title_el is not None and title_el.text else ""
        
        summary_el = e.find("atom:summary", NS)
        abstract = summary_el.text.strip() if summary_el is not None and summary_el.text else ""
        abstract = re.sub(r"\s+", " ", abstract).strip()
        
        published_el = e.find("atom:published", NS)
        published = published_el.text if published_el is not None else ""
        date = published[:10] if published else ""
        
        authors = []
        for au in e.findall("atom:author", NS):
            name_el = au.find("atom:name", NS)
            if name_el is not None and name_el.text:
                authors.append(name_el.text.strip())
        
        doi = ""
        doi_el = e.find("arxiv:doi", NS)
        if doi_el is not None and doi_el.text:
            doi = doi_el.text.strip()
        
        primary_cat = ""
        pc_el = e.find("arxiv:primary_category", NS)
        if pc_el is not None:
            primary_cat = pc_el.get("term", "")
        
        url_html = arxiv_url
        url_pdf = ""
        for link in e.findall("atom:link", NS):
            rel = link.get("rel", "")
            href = link.get("href", "")
            title_attr = link.get("title", "")
            if title_attr == "pdf":
                url_pdf = href
            elif rel == "alternate":
                url_html = href
        
        entries.append({
            "arxiv_id": arxiv_id,
            "version": version,
            "title": title,
            "abstract": abstract,
            "authors": authors,
            "date": date,
            "doi": doi,
            "primary_category": primary_cat,
            "url_html": url_html,
            "url_pdf": url_pdf,
        })
    return total, entries

def fetch_all_arxiv(query, results_file):
    cached = zc.load_json(results_file)
    if cached:
        zc.log(f"Loaded {len(cached['entries'])} entries from cache (total expected: {cached['total']})")
        if len(cached["entries"]) >= cached["total"]:
            zc.log("  Fetch complete per cache; skipping arXiv re-fetch.")
            return cached["entries"]
        zc.log("  Cache incomplete; resuming fetch.")
        entries = cached["entries"]
        total = cached["total"]
        start = len(entries)
    else:
        zc.log("Starting arXiv fetch...")
        entries = []
        total = None
        start = 0
    
    while True:
        zc.log(f"  Fetching start={start}, page_size={ARXIV_PAGE_SIZE}...")
        xml_text = arxiv_fetch_page(query, start, ARXIV_PAGE_SIZE)
        if xml_text is None:
            zc.log("  Fetch failed; stopping. Re-run to resume.")
            sys.exit(2)
        
        page_total, page_entries = parse_arxiv_entries(xml_text)
        if total is None:
            total = page_total
            zc.log(f"  Total results reported by arXiv: {total}")
        if not page_entries:
            zc.log("  Empty page; assuming end of results.")
            break
        
        existing_ids = {e["arxiv_id"] for e in entries}
        new_entries = [e for e in page_entries if e["arxiv_id"] not in existing_ids]
        entries.extend(new_entries)
        zc.log(f"  Page returned {len(page_entries)} ({len(new_entries)} new); total now {len(entries)}/{total}")
        
        zc.save_json(results_file, {"total": total, "entries": entries,
                                     "fetched_at": datetime.now(timezone.utc).isoformat()})
        
        if len(entries) >= total or len(page_entries) < ARXIV_PAGE_SIZE:
            break
        start += ARXIV_PAGE_SIZE
        time.sleep(ARXIV_RATE_LIMIT_SEC)
    
    zc.log(f"arXiv fetch done: {len(entries)} entries")
    return entries

# ============================================================
# ZOTERO ITEM CONSTRUCTION
# ============================================================
def build_zotero_item(entry, collection_key):
    creators = []
    for name in entry["authors"]:
        if "," in name:
            parts = [p.strip() for p in name.split(",", 1)]
            last = parts[0]
            first = parts[1] if len(parts) > 1 else ""
        else:
            tokens = name.split()
            if len(tokens) >= 2:
                last = tokens[-1]
                first = " ".join(tokens[:-1])
            else:
                last = name
                first = ""
        creators.append({
            "creatorType": "author",
            "firstName": first,
            "lastName": last,
        })
    
    return {
        "itemType": "preprint",
        "title": entry["title"],
        "creators": creators,
        "abstractNote": entry["abstract"],
        "date": entry["date"],
        "repository": "arXiv",
        "archiveID": f"arXiv:{entry['arxiv_id']}",
        "url": entry["url_html"],
        "DOI": entry["doi"],
        "extra": (f"primaryCategory: {entry['primary_category']}\nversion: v{entry['version']}"
                  if entry["version"]
                  else f"primaryCategory: {entry['primary_category']}"),
        "collections": [collection_key],
        "tags": [{"tag": "source:arxiv-api"}],
    }

def build_plan_entry(entry, collection_key):
    """Wrap a Zotero item in the {dedupe_key, item} shape zotero.py create-items expects."""
    return {"dedupe_key": f"arxiv:{entry['arxiv_id']}", "item": build_zotero_item(entry, collection_key)}

def write_plan_csv(entries, path):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["action", "arxiv_id", "title", "date", "doi", "url"])
        for e in entries:
            w.writerow(["CREATE", e["arxiv_id"], e["title"][:200], e["date"], e["doi"], e["url_html"]])

# ============================================================
# MAIN
# ============================================================
def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--query", required=True, help="arXiv search query string")
    parser.add_argument("--collection", required=True,
                        help="Target Zotero collection name (must exist)")
    parser.add_argument("--workdir", default=".", help="Working directory for output files")
    args = parser.parse_args()

    workdir = os.path.expanduser(args.workdir)
    os.makedirs(workdir, exist_ok=True)
    os.chdir(workdir)

    zc.set_log_file("fetch.log")
    zc.log(f"=== arXiv query (query-only; writes go through zotero.py create-items) ===")
    zc.log(f"Query:      {args.query}")
    zc.log(f"Collection: {args.collection}")
    zc.log(f"Workdir:    {workdir}")

    entries = fetch_all_arxiv(args.query, "arxiv_results.json")
    zc.log(f"\nFetched {len(entries)} arXiv entries")

    # Look up target collection. No separate "is it deleted" check: Zotero's
    # /collections listing has no trash/deleted state analogous to /items/trash —
    # anything list_all_collections() returns is live, so the extra GET was a
    # wasted round trip on every run.
    collections = zc.list_all_collections()
    coll_key = None
    for c in collections:
        if c["name"] == args.collection:
            coll_key = c["key"]
            break
    if coll_key is None:
        zc.log(f"FATAL: target collection {args.collection!r} not found. Create it first.")
        sys.exit(1)
    zc.log(f"Target collection key: {coll_key}")

    write_plan_csv(entries, "create_plan.csv")
    plan = [build_plan_entry(e, coll_key) for e in entries]
    zc.save_json("create_plan.json", plan)
    zc.log(f"Plan written: create_plan.csv (preview), create_plan.json ({len(plan)} entries)")

    zc.log(f"\nFirst 5 entries:")
    for e in entries[:5]:
        zc.log(f"  {e['arxiv_id']} ({e['date']}): {e['title'][:80]}")

    zc.log(f"\nNext step — create the items via the zotero skill (dry-run by default):")
    zc.log(f"  python3 <path-to-zotero-skill>/scripts/zotero.py create-items "
           f"--plan {workdir}/create_plan.json --state {workdir}/state.json")
    zc.log(f"  ...then add --commit to actually write. After writing, run Zotero's "
           f"client-side dedup on the library.")

if __name__ == "__main__":
    main()

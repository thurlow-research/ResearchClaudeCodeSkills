#!/usr/bin/env python3
"""
Fetch arXiv API results and create Zotero items in a target collection.

Generalized version of fetch_q_arxiv_06.py / fetch_q_arxiv_07.py — pass the
query string, target collection name, and working directory as arguments.

Output (in --workdir):
    arxiv_results.json    — full arXiv fetch results (cached for resume)
    state.json            — resume checkpoint for Zotero creation
    create_plan.csv       — what would be created (dry-run) or was created (apply)
    fetch.log             — execution log

Usage:
    # Dry-run: fetch + show plan, no Zotero writes
    python3 fetch_arxiv_query.py \\
        --query "<arxiv-query>" \\
        --collection "Q-arXiv-NN" \\
        --workdir ~/slr/arxiv/q-arxiv-NN

    # Apply: actually create items via RW key
    python3 fetch_arxiv_query.py \\
        --query "<arxiv-query>" \\
        --collection "Q-arXiv-NN" \\
        --workdir ~/slr/arxiv/q-arxiv-NN \\
        --apply
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

ZOTERO_RATE_LIMIT_SEC = 0.3
ZOTERO_CREATE_BATCH_SIZE = 50  # Zotero's actual write-batch cap

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

def write_plan_csv(entries, path):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["action", "arxiv_id", "title", "date", "doi", "url"])
        for e in entries:
            w.writerow(["CREATE", e["arxiv_id"], e["title"][:200], e["date"], e["doi"], e["url_html"]])

# ============================================================
# CREATE ITEMS
# ============================================================
def create_items(entries, collection_key, state_path):
    if not zc.API_KEY_RW:
        sys.exit("error: missing ZOTERO_API_KEY_RW (required for --apply)")
    zc.log(f"\nCreating {len(entries)} items in Zotero...")
    state = zc.load_json(state_path) or {"created_arxiv_ids": []}
    created = set(state["created_arxiv_ids"])
    remaining = [e for e in entries if e["arxiv_id"] not in created]
    zc.log(f"  Already created in prior script run: {len(created)}; remaining: {len(remaining)}")
    
    successes = 0
    failures = []
    for batch_start in range(0, len(remaining), ZOTERO_CREATE_BATCH_SIZE):
        batch = remaining[batch_start:batch_start + ZOTERO_CREATE_BATCH_SIZE]
        items = [build_zotero_item(e, collection_key) for e in batch]
        ok, resp, _ = zc.zot_request("POST", "/items", body=items, api_key=zc.API_KEY_RW)
        if not ok:
            zc.log(f"  BATCH FAILED ({batch_start}): {resp}")
            failures.append((batch_start, str(resp)[:500]))
            break
        
        succ = resp.get("successful", {})
        fail = resp.get("failed", {})
        for idx_str in succ:
            idx = int(idx_str)
            created.add(batch[idx]["arxiv_id"])
            successes += 1
        for idx_str, fail_data in fail.items():
            idx = int(idx_str)
            failures.append((batch[idx]["arxiv_id"], fail_data))
            zc.log(f"  FAIL {batch[idx]['arxiv_id']}: {fail_data}")
        
        state["created_arxiv_ids"] = sorted(created)
        zc.save_json(state_path, state)
        zc.log(f"  Batch {batch_start//ZOTERO_CREATE_BATCH_SIZE + 1}: success+={len(succ)}, fail+={len(fail)}; total created={successes}")
        time.sleep(ZOTERO_RATE_LIMIT_SEC)
    
    zc.log(f"\nCreate complete. Successes: {successes}, Failures: {len(failures)}")
    return successes, failures

# ============================================================
# MAIN
# ============================================================
def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--query", required=True, help="arXiv search query string")
    parser.add_argument("--collection", required=True,
                        help="Target Zotero collection name (must exist)")
    parser.add_argument("--workdir", default=".", help="Working directory for state/output files")
    parser.add_argument("--apply", action="store_true",
                        help="Actually create items in Zotero. Default is dry-run.")
    args = parser.parse_args()
    
    workdir = os.path.expanduser(args.workdir)
    os.makedirs(workdir, exist_ok=True)
    os.chdir(workdir)
    
    zc.set_log_file("fetch.log")
    zc.log(f"=== arXiv → Zotero import ===")
    zc.log(f"Query:      {args.query}")
    zc.log(f"Collection: {args.collection}")
    zc.log(f"Workdir:    {workdir}")
    zc.log(f"Mode:       {'APPLY' if args.apply else 'DRY-RUN'}")
    
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
    zc.log(f"Plan written to create_plan.csv")
    
    zc.log(f"\nFirst 5 items planned for creation:")
    for e in entries[:5]:
        zc.log(f"  {e['arxiv_id']} ({e['date']}): {e['title'][:80]}")
    
    if not args.apply:
        zc.log(f"\nDRY-RUN complete. Re-run with --apply to create items.")
        return
    
    if not entries:
        zc.log("\nNothing to create. Done.")
        return
    
    successes, failures = create_items(entries, coll_key, "state.json")
    if successes:
        zc.invalidate_cache()
    zc.log(f"\n=== DONE ===")
    zc.log(f"Created: {successes} items in {args.collection}")
    zc.log(f"Failures: {len(failures)}")
    zc.log(f"\nNext step: Run Zotero client-side dedup on the library.")

if __name__ == "__main__":
    main()

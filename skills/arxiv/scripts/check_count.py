#!/usr/bin/env python3
"""
Check arXiv API total result count for a query — no Zotero writes.

Usage:
    python3 check_count.py "<arxiv-query-string>"

Example:
    python3 check_count.py '(abs:"vibe coding" OR abs:"vibe-coding") AND abs:"governance"'
"""
import sys
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

ARXIV_BASE = "https://export.arxiv.org/api/query"
NS = {
    "atom": "http://www.w3.org/2005/Atom",
    "opensearch": "http://a9.com/-/spec/opensearch/1.1/",
}

def main():
    if len(sys.argv) < 2:
        print("Usage: python3 check_count.py \"<arxiv-query>\"", file=sys.stderr)
        sys.exit(1)
    query = sys.argv[1]
    params = {"search_query": query, "start": 0, "max_results": 3}
    url = f"{ARXIV_BASE}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url)
    req.add_header("User-Agent", "VibeCodingSLR/1.0 (research)")
    with urllib.request.urlopen(req, timeout=60) as r:
        data = r.read().decode("utf-8")
    root = ET.fromstring(data)
    total = root.find("opensearch:totalResults", NS)
    print(f"Total results: {total.text if total is not None else '?'}")
    print()
    print("Sample (3 most recent):")
    for e in root.findall("atom:entry", NS)[:3]:
        t = e.find("atom:title", NS)
        s = e.find("atom:summary", NS)
        pub = e.find("atom:published", NS)
        date = pub.text[:10] if pub is not None and pub.text else "?"
        print(f"\n  [{date}] {t.text.strip() if t is not None else '?'}")
        if s is not None and s.text:
            print(f"        {s.text.strip()[:180]}...")

if __name__ == "__main__":
    main()

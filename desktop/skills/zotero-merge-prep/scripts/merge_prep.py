#!/usr/bin/env python3
"""Zotero merge-prep — consolidate duplicate records BEFORE Zotero's native merge.

Zotero's "Merge Items" keeps only the master's field values, silently dropping
metadata the other duplicates had, and it only groups items of the SAME item type.
This tool fixes both, so a subsequent merge is lossless:
  1. find candidate duplicates by title (or explicit keys)
  2. confirm they are the same work (title similarity + author/DOI overlap)
  3. UNION their metadata (authors, abstract, DOI, URL, venue, date, extra),
     optionally filling remaining gaps from OpenAlex
  4. NORMALIZE item types to the most-published type (journalArticle >
     conferencePaper > bookSection > preprint), tagging orig-type:/orig-date: for
     lineage, so Zotero can group them
Every candidate ends up with identical, complete metadata + the same type — then
you run Zotero's Duplicate Items merge and nothing is lost.

Commands:
  find "TITLE"                 list candidate duplicates + a same-work confidence
  prep "TITLE"                 full prep (find->confirm->union->normalize)
  prep --keys K1,K2,K3         prep an explicit set (skip the title search)

Options: --dry-run  --target <itemType>  --no-openalex  --force (accept low-confidence)

Env: ZOTERO_API_KEY, ZOTERO_LIBRARY_ID, ZOTERO_LIBRARY_TYPE(group|user).
"""
import argparse
import difflib
import json
import os
import re
import sys
import urllib.parse
import urllib.request
import urllib.error
from collections import defaultdict

KEY = os.environ["ZOTERO_API_KEY"]
LIB = os.environ["ZOTERO_LIBRARY_ID"]
LT = os.environ.get("ZOTERO_LIBRARY_TYPE", "group")
BASE = f"https://api.zotero.org/{'groups' if LT.startswith('g') else 'users'}/{LIB}"
H = {"Zotero-API-Key": KEY, "Zotero-API-Version": "3"}
RANK = {"journalArticle": 4, "conferencePaper": 3, "bookSection": 2, "report": 2, "preprint": 1}
SKIP = {"attachment", "note", "annotation"}

# optional OpenAlex enrichment via the openalex skill
try:
    sys.path.insert(0, os.path.expanduser("~/.claude/skills/openalex/scripts"))
    import openalex
    HAVE_OA = True
except Exception:
    HAVE_OA = False


def api(method, path, body=None, headers=None):
    hs = dict(H)
    if headers:
        hs.update(headers)
    data = json.dumps(body).encode() if body is not None else None
    if data:
        hs["Content-Type"] = "application/json"
    req = urllib.request.Request(f"{BASE}{path}", data=data, headers=hs, method=method)
    with urllib.request.urlopen(req, timeout=30) as r:
        raw = r.read()
        return (json.loads(raw) if raw else None), dict(r.headers)


def norm(s):
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def title_sim(a, b):
    return difflib.SequenceMatcher(None, norm(a), norm(b)).ratio()


_SCHEMA = None


def schema():
    global _SCHEMA
    if _SCHEMA is None:
        with urllib.request.urlopen("https://api.zotero.org/schema") as r:
            sch = json.load(r)
        fields, b2f = {}, {}
        for it in sch["itemTypes"]:
            t = it["itemType"]
            fmap, bmap = {}, {}
            for f in it.get("fields", []):
                fmap[f["field"]] = f.get("baseField", f["field"])
                bmap[f.get("baseField", f["field"])] = f["field"]
            fields[t] = fmap
            b2f[t] = bmap
        _SCHEMA = (fields, b2f)
    return _SCHEMA


def same_title(a, b):
    na, nb = norm(a), norm(b)
    if not na or not nb or min(len(na), len(nb)) < 8:
        return False
    lo, hi = sorted((na, nb), key=len)
    # substring counts ONLY if the shorter title is a substantial fraction of the
    # longer — otherwise a short title ("vibe coding") spuriously matches every
    # longer title containing that phrase and union-find chains unrelated works.
    if lo in hi and len(lo) / len(hi) >= 0.6:
        return True
    return difflib.SequenceMatcher(None, na, nb).ratio() >= 0.85


def find_candidates(title):
    q = urllib.parse.quote(title)
    d, _ = api("GET", f"/items?q={q}&qmode=titleCreatorYear&limit=50")
    return [it for it in d if it["data"].get("itemType") not in SKIP
            and same_title(it["data"].get("title", ""), title)]


def _completeness(it):
    ignore = {"key", "version", "dateAdded", "dateModified", "tags", "collections",
              "relations", "itemType"}
    return sum(1 for k, v in it["data"].items() if v and k not in ignore)


def pick_target(items, override):
    """Target itemType = the type of the MOST-COMPLETE record (the freshly/authoritatively
    catalogued one — a mis-typed sparse stub shouldn't win), with rank (journalArticle >
    conferencePaper > bookSection > preprint) breaking ties. This beats a blind rank,
    which would follow a stub mis-typed higher than the real type."""
    if override:
        return override
    best = max(items, key=lambda it: (_completeness(it), RANK.get(it["data"]["itemType"], 0)))
    return best["data"]["itemType"]


def surnames(it):
    return {(c.get("lastName") or c.get("name") or "").lower() for c in it["data"].get("creators", []) if c.get("lastName") or c.get("name")}


def ids_of(it):
    d = it["data"]
    s = set()
    if d.get("DOI"):
        s.add(d["DOI"].lower())
    for m in re.findall(r"(10\.\d{4,9}/\S+|arxiv[:/]?\s*\d{4}\.\d{4,5})", (d.get("extra", "") or "") + " " + (d.get("url", "") or ""), re.I):
        s.add(m.lower())
    return s


def confidence(items):
    """Same-work confidence for the candidate set."""
    if len(items) < 2:
        return 1.0, "single"
    titles = [it["data"].get("title", "") for it in items]
    minsim = min(title_sim(titles[0], t) for t in titles[1:])
    shared_ids = set.intersection(*[ids_of(it) or {f"__{i}"} for i, it in enumerate(items)])
    shared_auth = set.intersection(*[surnames(it) or {f"__{i}"} for i, it in enumerate(items)])
    conf = minsim
    if shared_ids:
        conf = max(conf, 0.99)
    if shared_auth:
        conf = min(1.0, conf + 0.1)
    why = f"title_sim={minsim:.2f} shared_ids={bool(shared_ids)} shared_authors={sorted(shared_auth)[:3]}"
    return conf, why


def best_url(urls):
    for u in urls:
        if u and "doi.org" in u:
            return u
    for u in urls:
        if u and "semanticscholar.org" not in u and "arxiv.org/abs" not in u:
            return u
    return next((u for u in urls if u), "")


def union_fields(items, target, use_oa=True):
    """Compute the union/best value per field for the target type."""
    fields, b2f = schema()
    tgt_fields = fields[target]
    datas = [it["data"] for it in items]
    # authors: longest list
    creators = max((d.get("creators", []) for d in datas), key=len)
    # abstract: longest
    abstract = max((d.get("abstractNote", "") or "" for d in datas), key=len)
    # title: prefer the highest-ranked record's, else longest non-ALLCAPS
    ranked = sorted(items, key=lambda it: -RANK.get(it["data"]["itemType"], 0))
    title = ranked[0]["data"].get("title", "") or max((d.get("title", "") for d in datas), key=len)
    if title.isupper():
        title = max((d.get("title", "") for d in datas if not d.get("title", "").isupper()), default=title)
    # date: from highest-ranked record, else any
    date = ranked[0]["data"].get("date", "") or next((d.get("date", "") for d in datas if d.get("date")), "")
    # DOI: prefer non-arXiv
    dois = [d.get("DOI", "") for d in datas if d.get("DOI")]
    doi = next((x for x in dois if "arxiv" not in x.lower()), dois[0] if dois else "")
    url = best_url([d.get("url", "") for d in datas])
    # venue: any record's venue mapped into the target venue field (base 'publicationTitle')
    venue = ""
    for d in datas:
        f = fields[d["itemType"]]
        for fn, bf in f.items():
            if bf == "publicationTitle" and d.get(fn):
                venue = d[fn]
                break
        if venue:
            break
    # extra: union of arXiv/DOI id markers already present
    extra_lines = set()
    for d in datas:
        for ln in (d.get("extra", "") or "").splitlines():
            if ln.strip():
                extra_lines.add(ln.strip())
    # OpenAlex fill for any remaining gaps
    if use_oa and HAVE_OA and (not abstract or not creators or not venue or not doi):
        oid = doi or next((i for i in set().union(*[ids_of(it) for it in items]) if "arxiv" in i), "") or title
        rec = openalex.normalize(openalex.get_work(oid))
        if rec:
            if not abstract and rec["abstract"]:
                abstract = rec["abstract"]
            if not doi and rec["doi"]:
                doi = rec["doi"]
            if not venue and rec["venue"]:
                venue = rec["venue"]
            if not url and rec["url"]:
                url = rec["url"]
            if len(creators) < len(rec["authors"]):
                creators = [{"creatorType": "author",
                             "lastName": n.split()[-1], "firstName": " ".join(n.split()[:-1])}
                            for n in rec["authors"]]
    venue_field = b2f[target].get("publicationTitle", "publicationTitle")
    return {"title": title, "creators": creators, "abstractNote": abstract,
            "date": date, "DOI": doi, "url": url, venue_field: venue,
            "extra": "\n".join(sorted(extra_lines))}, doi


def convert_and_apply(it, target, union, dry):
    fields, b2f = schema()
    old = it["data"]
    changing_type = old["itemType"] != target
    body = {k: v for k, v in union.items() if v and k in fields[target] or k in ("creators",)}
    # never drop the venue field if it's valid for the target
    tags = old.get("tags", [])
    if changing_type:
        # carry old fields that map, dump unmappable to extra
        b2f_t = b2f[target]
        moved = []
        for f, v in old.items():
            if f in ("itemType", "creators", "tags", "collections", "relations",
                     "extra", "key", "version", "dateAdded", "dateModified") or not v:
                continue
            if f in union:
                continue
            tf = b2f_t.get(fields[old["itemType"]].get(f, f))
            if tf and tf not in body:
                body[tf] = v
            elif f not in fields[target]:
                moved.append(f"{f}: {v}")
        extra = union.get("extra", "")
        if moved:
            extra = (extra + "\n" if extra else "") + "\n".join(moved)
        if extra:
            body["extra"] = extra
        have = {t["tag"] for t in tags}
        for t in (f"orig-type:{re.sub(r'(?<!^)(?=[A-Z])','-',old['itemType']).lower()}",
                  f"orig-date:{(it.get('meta',{}).get('parsedDate') or old.get('date') or '').strip()}"):
            if t.split(':', 1)[1] and t not in have:
                tags = tags + [{"tag": t}]
        body["itemType"] = target
        body["creators"] = union["creators"]
        body["tags"] = tags
        body["collections"] = old.get("collections", [])
        body["relations"] = old.get("relations", {})
    if dry:
        return sorted(body.keys()), changing_type
    if changing_type:
        api("PUT", f"/items/{it['key']}", body, {"If-Unmodified-Since-Version": str(it["version"])})
    else:
        api("PATCH", f"/items/{it['key']}", body, {"If-Unmodified-Since-Version": str(it["version"])})
    return sorted(body.keys()), changing_type


def collection_items(coll):
    out, start = [], 0
    while True:
        d, _ = api("GET", f"/collections/{coll}/items/top?limit=100&start={start}")
        out += [it for it in d if it["data"].get("itemType") not in SKIP]
        if len(d) < 100:
            break
        start += 100
    return out


def cluster(items):
    """Union-find clustering: two items group if they share a DOI/arXiv id
    (false-positive-proof — DOIs are unique per work, so this catches truncated /
    short / mangled titles that title-matching alone misses) OR their titles match
    (substring-aware, for records that lack a shared id)."""
    n = len(items)
    parent = list(range(n))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    ids = [ids_of(it) for it in items]
    titles = [it["data"].get("title", "") for it in items]
    for i in range(n):
        for j in range(i + 1, n):
            if (ids[i] & ids[j]) or same_title(titles[i], titles[j]):
                parent[find(i)] = find(j)
    groups = defaultdict(list)
    for i, it in enumerate(items):
        groups[find(i)].append(it)
    return [g for g in groups.values() if len(g) > 1]


def cmd_scan(a):
    items = collection_items(a.collection)
    groups = cluster(items)
    print(f"{len(items)} items in {a.collection}; {len(groups)} duplicate group(s):")
    for g in sorted(groups, key=lambda g: g[0]["data"].get("title", "")):
        print(f"  {','.join(it['key'] for it in g)}  "
              f"types={[it['data']['itemType'] for it in g]}  {g[0]['data'].get('title','')[:50]}")
    if a.prep:
        print("\n--- prepping each group ---")
        for g in groups:
            ns = argparse.Namespace(title="", keys=",".join(it["key"] for it in g),
                                    target=a.target, dry_run=a.dry_run,
                                    no_openalex=a.no_openalex, force=True)
            cmd_prep(ns)


def cmd_find(a):
    items = find_candidates(a.title)
    conf, why = confidence(items)
    print(f"{len(items)} candidate(s) (confidence {conf:.2f} — {why}):")
    for it in items:
        d = it["data"]
        print(f"  {it['key']} [{d['itemType']}] {len(d.get('creators',[]))}au "
              f"{d.get('date','') or '?'}  DOI={d.get('DOI','') or '-'}  {d.get('title','')[:55]}")


def cmd_prep(a):
    if a.keys:
        items = [api("GET", f"/items/{k.strip()}")[0] for k in a.keys.split(",")]
    else:
        items = find_candidates(a.title)
    if len(items) < 2:
        print(f"Only {len(items)} record — nothing to consolidate.")
        return
    conf, why = confidence(items)
    print(f"{len(items)} candidates, confidence {conf:.2f} ({why})")
    if conf < 0.85 and not a.force:
        print("LOW confidence they are the same work — re-run with --force or --keys to override.")
        for it in items:
            print(f"  {it['key']} [{it['data']['itemType']}] {it['data'].get('title','')[:60]}")
        return
    target = pick_target(items, a.target)
    union, doi = union_fields(items, target, use_oa=not a.no_openalex)
    print(f"target type: {target}   unioned: {len([k for k,v in union.items() if v])} fields "
          f"(authors={len(union['creators'])}, abstract={'y' if union['abstractNote'] else 'n'}, "
          f"DOI={union.get('DOI') or '-'})")
    for it in items:
        keys, ct = convert_and_apply(it, target, union, a.dry_run)
        tag = "CONVERT+union" if ct else "union"
        print(f"  {it['key']} [{it['data']['itemType']}->{target}] {tag}: set {keys}")
    print("\n" + ("(dry-run — no writes)" if a.dry_run else
                  "Done. All records now share identical metadata + type — run Zotero 'Merge Items' safely."))


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    pf = sub.add_parser("find")
    pf.add_argument("title")
    pf.set_defaults(func=cmd_find)
    pp = sub.add_parser("prep")
    pp.add_argument("title", nargs="?", default="")
    pp.add_argument("--keys", default="")
    pp.add_argument("--target", default="")
    pp.add_argument("--dry-run", action="store_true")
    pp.add_argument("--no-openalex", action="store_true")
    pp.add_argument("--force", action="store_true")
    pp.set_defaults(func=cmd_prep)
    ps2 = sub.add_parser("scan")
    ps2.add_argument("collection", help="Zotero collection KEY to scan for dup groups")
    ps2.add_argument("--prep", action="store_true", help="also prep every group found")
    ps2.add_argument("--target", default="")
    ps2.add_argument("--dry-run", action="store_true")
    ps2.add_argument("--no-openalex", action="store_true")
    ps2.set_defaults(func=cmd_scan)
    a = ap.parse_args()
    a.func(a)


if __name__ == "__main__":
    main()

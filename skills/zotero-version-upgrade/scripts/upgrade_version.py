#!/usr/bin/env python3
"""Upgrade a Zotero record to the newest version of the same work.

Handles two cases, following the library's history convention (the record shows the
newest version; history lives in orig-type:/orig-date: tags and an Extra line):

  1. arXiv revision    preprint has a newer arXiv version (v2 -> v4): update date,
                       abstract, PDF link; tag orig-date:<previous date>.
  2. Published version preprint now has a journal/proceedings version (found via the
                       arXiv-supplied DOI, or a Crossref title+author match): convert the
                       item type, fill venue/pages/DOI/date, record dropped fields in
                       Extra, tag orig-type:<old type> and orig-date:<old date>.

All reads and writes go through the sibling `zotero` skill's zotero.py, so --library,
the dry-run default, and the field-loss guard behave exactly as they do there.

Usage:
  upgrade_version.py KEY [KEY ...] [--library SLR] [--commit]
  upgrade_version.py KEY --item-json item.json      # offline: plan only, no Zotero calls

Options:
  --commit           apply the changes (default: dry-run, shows the diff only)
  --no-abstract      keep the existing abstract on arXiv revisions
  --original-date    also add an `original-date:` line to Extra (renders as YYYY/YYYY
                     in APA; appropriate for reprints, NOT for preprint revisions)
  --no-published     only check for arXiv revisions; skip the published-version search
  --plan-dir DIR     where plan files are written (default ~/Code/Thurlow-Research/zotero-plans;
                     macOS blocks zotero.py from reading ~/Downloads)

The tool never claims a figure changed: after any upgrade, re-verify cited numbers
against the new full text (the report prints the PDF link).
"""
import argparse
import datetime
import difflib
import json
import os
import re
import subprocess
import sys
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

TODAY = datetime.date.today().isoformat()
UA = {"User-Agent": "zotero-version-upgrade/1.0 (mailto:%s)" % os.environ.get("OPENALEX_MAILTO", "unknown@example.com")}
ATOM = {"a": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}
ARXIV_RE = re.compile(r"(\d{4}\.\d{4,5})(?:v(\d+))?", re.I)
CR_TYPE = {"journal-article": "journalArticle", "proceedings-article": "conferencePaper",
           "book-chapter": "bookSection"}
VENUE_FIELD = {"journalArticle": "publicationTitle", "conferencePaper": "proceedingsTitle",
               "bookSection": "bookTitle"}
CORE_KEEP = {"key", "version", "itemType", "title", "creators", "abstractNote", "date", "url",
             "accessDate", "extra", "tags", "collections", "relations", "dateAdded",
             "dateModified", "language", "rights", "shortTitle", "libraryCatalog",
             "callNumber", "archive", "archiveLocation", "citationKey"}


# --------------------------------------------------------------------------- helpers

def http_get(url, timeout=30, tries=4):
    """GET with retry/backoff: arXiv and Crossref throttle rapid sequential calls."""
    import time
    last = None
    for attempt in range(tries):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                body = r.read()
            if body.strip():
                return body
            last = RuntimeError("empty response")
        except Exception as exc:  # HTTP 429/503, timeouts, resets
            last = exc
        time.sleep(3 * (attempt + 1))
    raise last


def locate_zotero_py():
    here = Path(__file__).resolve().parent
    for p in (os.environ.get("ZOTERO_PY", ""),
              here.parent.parent / "zotero" / "scripts" / "zotero.py",
              Path("~/.claude/skills/zotero/scripts/zotero.py").expanduser()):
        if p and Path(p).is_file():
            return str(p)
    sys.exit("could not locate zotero.py; set ZOTERO_PY")


def zotero(args, library=None):
    cmd = [sys.executable, locate_zotero_py()]
    if library:
        cmd += ["--library", library]
    proc = subprocess.run(cmd + args, capture_output=True, text=True)
    if proc.returncode != 0:
        sys.exit(f"zotero.py {' '.join(args)} failed:\n{proc.stderr.strip()}")
    return proc.stdout


def load_item(key, library, item_json):
    if item_json:
        obj = json.loads(Path(item_json).read_text())
    else:
        obj = json.loads(zotero(["raw", f"/items/{key}"], library))
    if isinstance(obj, list):
        obj = obj[0]
    return obj.get("data", obj)


def norm(s):
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def title_sim(a, b):
    return difflib.SequenceMatcher(None, norm(a), norm(b)).ratio()


_SCHEMA = None


def type_fields(item_type):
    """Valid field names for a Zotero item type (from the public schema)."""
    global _SCHEMA
    if _SCHEMA is None:
        sch = json.loads(http_get("https://api.zotero.org/schema"))
        _SCHEMA = {t["itemType"]: {f["field"] for f in t.get("fields", [])} for t in sch["itemTypes"]}
    return _SCHEMA.get(item_type, set())


# --------------------------------------------------------------------------- arXiv

def arxiv_id(d):
    """Return (id, recorded_version) from archiveID / extra / DOI / url."""
    for field in ("archiveID", "DOI", "url", "extra"):
        val = d.get(field) or ""
        if field == "DOI" and "48550" not in val:
            continue
        if field == "url" and "arxiv" not in val.lower():
            continue
        m = ARXIV_RE.search(val)
        if m:
            aid = m.group(1)
            # the most specific version hint anywhere in the record
            blob = " ".join(str(d.get(f, "")) for f in ("url", "extra"))
            vers = [int(v) for v in re.findall(re.escape(aid) + r"v(\d+)", blob)]
            vers += [int(v) for v in re.findall(r"(?im)^version:\s*v(\d+)", blob)]
            return aid, (max(vers) if vers else None)
    return None, None


def arxiv_meta(aid):
    xml = http_get(f"https://export.arxiv.org/api/query?id_list={aid}")
    entry = ET.fromstring(xml).find("a:entry", ATOM)
    if entry is None or entry.find("a:id", ATOM) is None:
        return None
    abs_id = entry.find("a:id", ATOM).text
    m = ARXIV_RE.search(abs_id)

    def txt(tag, ns="a"):
        el = entry.find(f"{ns}:{tag}", ATOM)
        return " ".join(el.text.split()) if el is not None and el.text else ""

    return {"id": aid, "latest_version": int(m.group(2)) if m and m.group(2) else 1,
            "latest_date": txt("updated")[:10], "v1_date": txt("published")[:10],
            "title": txt("title"), "abstract": txt("summary"),
            "doi": txt("doi", "arxiv"), "journal_ref": txt("journal_ref", "arxiv")}


# --------------------------------------------------------------------------- Crossref

def crossref_by_doi(doi):
    try:
        return json.loads(http_get("https://api.crossref.org/works/" + urllib.parse.quote(doi)))["message"]
    except Exception:
        return None


def crossref_search(title, surnames):
    q = urllib.parse.urlencode({"query.bibliographic": title, "rows": 8})
    try:
        items = json.loads(http_get("https://api.crossref.org/works?" + q))["message"]["items"]
    except Exception:
        return None
    best, best_sim = None, 0.0
    for m in items:
        if m.get("type") not in CR_TYPE or m.get("DOI", "").startswith("10.48550"):
            continue
        sim = title_sim(title, (m.get("title") or [""])[0])
        fams = {norm(a.get("family")) for a in m.get("author", [])}
        if sim >= 0.92 and (not surnames or surnames[0] in fams) and sim > best_sim:
            best, best_sim = m, sim
    return best


def cr_date(m):
    for k in ("published-print", "published-online", "issued"):
        parts = (m.get(k) or {}).get("date-parts") or [[]]
        if parts and parts[0] and parts[0][0]:
            return "-".join(f"{p:02d}" if i else str(p) for i, p in enumerate(parts[0]))
    return ""


# --------------------------------------------------------------------------- plans

def history_line(old_type, old_date, old_title, note, dropped):
    parts = []
    if old_type:
        parts.append(f"orig-type: {old_type}")
    parts.append(f"orig-date: {old_date}")
    if old_title:
        parts.append(f"orig-title: {old_title} ({note})")
    else:
        parts.append(note)
    line = "; ".join(parts)
    if dropped:
        line += "; dropped fields: " + json.dumps(dropped, ensure_ascii=False)
    return line


def published_plan(d, m, args):
    new_type = CR_TYPE[m["type"]]
    old_type, old_date = d.get("itemType", ""), d.get("date", "")
    venue = (m.get("container-title") or [""])[0]
    fields = {"itemType": new_type, "DOI": m["DOI"], "url": "https://doi.org/" + m["DOI"],
              "date": cr_date(m) or old_date}
    if m.get("page"):
        fields["pages"] = m["page"].replace("\u2013", "-")
    if m.get("publisher"):
        fields["publisher"] = m["publisher"]
    if venue:
        fields[VENUE_FIELD[new_type]] = venue
    if new_type == "journalArticle":
        for src, dst in (("volume", "volume"), ("issue", "issue")):
            if m.get(src):
                fields[dst] = m[src]
        if m.get("ISSN"):
            fields["ISSN"] = m["ISSN"][0]
    if new_type == "conferencePaper" and (m.get("event") or {}).get("name"):
        fields["conferenceName"] = m["event"]["name"]

    valid = type_fields(new_type)
    dropped = {k: v for k, v in d.items()
               if v and k not in CORE_KEEP and k not in valid and not isinstance(v, (list, dict))}
    for k in ("DOI", "pages", "url"):  # superseded values worth keeping for traceability
        if d.get(k) and fields.get(k) and d[k].lower() != fields[k].lower():
            dropped.setdefault(k, d[k])
    note = (f"upgraded to the published version, {venue or new_type}, {TODAY}, by "
            f"zotero-version-upgrade; source: Crossref {m['DOI']}; verify cited figures "
            f"against the published full text")
    hist = history_line(old_type, old_date, d.get("title", ""), note, dropped)
    extra = (d.get("extra") or "").rstrip()
    if args.original_date and "original-date:" not in extra:
        extra = f"original-date: {old_date}\n" + extra
    fields["extra"] = (extra + "\n" + hist).strip()
    tags = [f"orig-type:{old_type}", f"orig-date:{old_date}"]
    return fields, tags, sorted(dropped)


def arxiv_plan(d, am, recorded_v, args):
    old_date = d.get("date", "")
    latest_v, latest_date = am["latest_version"], am["latest_date"]
    if recorded_v is not None and recorded_v >= latest_v:
        return None
    if recorded_v is None and old_date[:10] >= latest_date:
        return None
    fields = {"date": latest_date}
    if not args.no_abstract and am["abstract"] and am["abstract"] != d.get("abstractNote"):
        fields["abstractNote"] = am["abstract"]
    extra = (d.get("extra") or "").rstrip()
    extra = re.sub(rf"(arxiv\.org/pdf/{re.escape(am['id'])})v\d+", rf"\1v{latest_v}", extra)
    extra = re.sub(r"(Last Updated:\s*)\S+", rf"\g<1>{latest_date}", extra)
    extra = re.sub(r"(?im)^(version:\s*)v\d+", rf"\g<1>v{latest_v}", extra)
    note = (f"updated to arXiv v{latest_v} ({latest_date}) on {TODAY} by zotero-version-upgrade; "
            f"previous version {'v%d' % recorded_v if recorded_v else 'unrecorded'}; "
            f"re-verify cited figures against the v{latest_v} full text"
            + ("; abstract replaced with the v%d abstract" % latest_v if "abstractNote" in fields else ""))
    if args.original_date and "original-date:" not in extra:
        extra = f"original-date: {old_date}\n" + extra
    fields["extra"] = (extra + "\n" + history_line("", old_date, "", note, {})).strip()
    if d.get("url") and re.search(rf"{re.escape(am['id'])}v\d+", d["url"]):
        fields["url"] = re.sub(rf"({re.escape(am['id'])})v\d+", rf"\1v{latest_v}", d["url"])
    tags = [f"orig-date:{old_date}"] if old_date and old_date[:10] != latest_date else []
    return fields, tags, []


# --------------------------------------------------------------------------- main

def process(key, args):
    d = load_item(key, args.library, args.item_json)
    report = {"key": key, "title": d.get("title", ""), "itemType": d.get("itemType"),
              "date": d.get("date")}
    aid, recorded_v = arxiv_id(d)
    am = None
    if aid:
        try:
            am = arxiv_meta(aid)
        except Exception as exc:
            report["status"] = f"arxiv-lookup-failed: {exc}"
            return report
    if am:
        report["arxiv"] = {"id": aid, "recorded_version": recorded_v,
                           "latest_version": am["latest_version"], "latest_date": am["latest_date"]}

    plan = None
    if d.get("itemType") == "preprint" and not args.no_published:
        m = crossref_by_doi(am["doi"]) if am and am.get("doi") else None
        if m is None:
            surnames = [norm(c.get("lastName") or c.get("name", "").split()[-1])
                        for c in d.get("creators", []) if c.get("lastName") or c.get("name")]
            m = crossref_search(d.get("title", ""), surnames)
        if m and m.get("type") in CR_TYPE:
            plan = ("published",) + published_plan(d, m, args)
            report["published"] = {"doi": m["DOI"], "venue": (m.get("container-title") or [""])[0],
                                   "type": CR_TYPE[m["type"]]}
            report["full_text"] = "https://doi.org/" + m["DOI"]
    if plan is None and am:
        p = arxiv_plan(d, am, recorded_v, args)
        if p:
            plan = ("arxiv-revision",) + p
            report["full_text"] = f"https://arxiv.org/pdf/{aid}v{am['latest_version']}"

    if plan is None:
        report["status"] = "up-to-date" if am or d.get("itemType") != "preprint" else "no-newer-version-found"
        return report

    kind, fields, tags, dropped = plan
    report.update(status="upgrade-available", kind=kind,
                  changes={k: {"from": d.get(k, ""), "to": v} for k, v in fields.items()
                           if k != "abstractNote" and d.get(k, "") != v},
                  abstract_replaced="abstractNote" in fields, tags_to_add=tags,
                  dropped_fields=dropped,
                  reminder="Re-verify any cited figures against the new full text before citing.")

    plan_dir = Path(args.plan_dir).expanduser()
    plan_dir.mkdir(parents=True, exist_ok=True)
    upd = plan_dir / f"upgrade_{key}.json"
    upd.write_text(json.dumps({key: fields}, indent=2, ensure_ascii=False))
    report["plan_files"] = [str(upd)]
    if tags:
        tg = plan_dir / f"upgrade_{key}_tags.json"
        tg.write_text(json.dumps({key: tags}, indent=2))
        report["plan_files"].append(str(tg))

    if not args.item_json:  # integrated mode: hand the plan to zotero.py
        flags = ["--commit"] if args.commit else []
        if dropped:
            flags.append("--allow-field-loss")  # dropped values are preserved in Extra
        report["update_items"] = zotero(["update-items", "--plan", str(upd), *flags,
                                         "--format", "json"], args.library).strip()
        if tags:
            report["tag_add"] = zotero(["tag-add", "--plan", str(plan_dir / f"upgrade_{key}_tags.json"),
                                        *(["--commit"] if args.commit else []), "--format", "json"],
                                       args.library).strip()
        report["mode"] = "COMMIT" if args.commit else "DRY-RUN"
    return report


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("keys", nargs="+")
    ap.add_argument("--library")
    ap.add_argument("--item-json", help="offline mode: read the item from a JSON file; plan only")
    ap.add_argument("--commit", action="store_true")
    ap.add_argument("--no-abstract", action="store_true")
    ap.add_argument("--original-date", action="store_true")
    ap.add_argument("--no-published", action="store_true")
    ap.add_argument("--plan-dir", default=os.environ.get("ZOTERO_PLAN_DIR", "~/Code/Thurlow-Research/zotero-plans"))
    args = ap.parse_args()
    if args.item_json and len(args.keys) > 1:
        sys.exit("--item-json takes exactly one key")
    print(json.dumps([process(k, args) for k in args.keys], indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()

---
name: zotero-version-upgrade
description: Upgrade a Zotero record to the newest version of the same work, keeping the old version as history. Detects (1) a newer arXiv revision of a preprint (v2 -> v4) and (2) a published journal or proceedings version of a preprint (via the arXiv-supplied DOI or a Crossref title+author match). Updates the record to the newest version and records lineage in orig-type:/orig-date: tags plus an Extra history line, so the record becomes a "merged" view of all versions. Use whenever the user says a paper "graduated", "was published", "has a newer version", "check for updates", "is this the latest version", asks to upgrade or refresh a preprint record, or before citing a preprint whose figures might have changed. Complements the `zotero` skill (reads/writes) and `zotero-merge-prep` (duplicate consolidation).
---

# Zotero version upgrade

**The convention this enforces:** a record always reflects the **newest** version of the
work (latest arXiv revision, or the published version once it exists). History is never
lost: it lives in `orig-type:<old type>` and `orig-date:<old date>` tags and a one-line
Extra entry naming the previous type/date/title, the source of the upgrade, and any
fields the type change dropped.

## Commands
```
python3 scripts/upgrade_version.py KEY [KEY ...] --library SLR          # dry-run diff
python3 scripts/upgrade_version.py KEY --library SLR --commit           # apply
python3 scripts/upgrade_version.py KEY --item-json item.json            # offline plan only
```

| Option | Effect |
|---|---|
| `--commit` | Apply. Default is DRY-RUN (shows the diff from `zotero.py update-items`). |
| `--no-abstract` | Keep the existing abstract on arXiv revisions (default replaces it with the new version's). |
| `--original-date` | Also add `original-date:` to Extra. APA renders this as "2022/2025", which is right for **reprints/republications**, wrong for preprint revisions. Off by default. |
| `--no-published` | Only check arXiv revisions. |
| `--plan-dir DIR` | Plan file location. Default `~/Code/Thurlow-Research/zotero-plans` (macOS blocks `zotero.py` from reading `~/Downloads`). |

## What it decides, in order
1. **Published version exists?** (preprints only) Uses the DOI arXiv lists for the paper; if none, searches Crossref and accepts a match only at ≥0.92 title similarity with the same first-author surname, excluding arXiv DOIs. On a match: item type → journalArticle / conferencePaper / bookSection; fills venue, pages, volume/issue, publisher, DOI, URL, date; fields the new type can't hold are recorded under `dropped fields` in Extra and the write uses `--allow-field-loss` (safe because the values are preserved).
2. **Else, newer arXiv revision?** Compares the recorded version (from `vN` in URL/Extra or a `version: vN` line) or, if none, the record date, against arXiv's latest. On a newer one: date → latest version date, abstract → latest abstract, PDF/URL/`Last Updated`/`version:` lines advanced.
3. **Else** reports `up-to-date` (or `no-newer-version-found` for non-arXiv preprints).

## After any upgrade (required)
- **Re-verify every cited figure against the new full text** before citing it; revisions change numbers (e.g., Zhao v4 moved 61%/10.5% to 57%/11.8%; Liu v3 replaced ">15%" with "7.5–14.8%"). The report prints the new PDF link.
- **Check the citation year.** Upgrading a 2025 preprint to a 2026 revision changes "(Author, 2025)" to "(Author, 2026)" after Refresh in Word.
- Attach the new PDF to the record so the library's full text matches what's cited.

## Known limits
- Published versions not yet in Crossref (e.g., an accepted paper before proceedings appear) are not found; re-run later.
- Non-arXiv preprints (SSRN, OSF) are only checked for a published version.
- Titles are not rewritten (they keep your sentence-case cleanup).

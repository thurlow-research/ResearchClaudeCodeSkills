---
name: zotero-slr-dedup-archived
description: ARCHIVED — DO NOT EXECUTE. Historical record of the retired supersede-based cross-source dedup workflow for the Vibe Coding Governance SLR (the 00-Dups registry, the 04-Superseded collections, the publication venue hierarchy, Bucket A/B, "most advanced stage" decision propagation). Retired 2026-07-16 in favour of merge-with-type-normalization; the live skill is `zotero-merge-prep`. Read this only to explain why ~71 items sit in 04-Superseded collections, to answer methods-chapter or committee questions about how duplicates were handled before the philosophy changed, or to interpret an old dedup_proposal.csv. If the task is to actually resolve duplicates, stop and use `zotero-merge-prep` instead.
---

# ARCHIVED: Zotero SLR Cross-Source Dedup Resolution

> **Status:** retired 2026-07-16. **Superseded by:** `zotero-merge-prep`.
> **Executable code removed** — this folder is documentation only.
> Nothing here should be run. Nothing here should be treated as current practice.

This is a tombstone. It exists because the workflow described below **ran against the
live library and left artifacts in it**. Those artifacts outlive the skill, and someone
— an advisor, a committee member, a future reader of the methods chapter, or Scott in
eighteen months — will eventually ask why the library contains a collection called
`04-Superseded` full of papers that were never merged. This document answers that.

---

## 1. Why it was retired

The governing philosophy changed. The old workflow **never merged anything**: it picked
a canonical, then marked the losers by adding them to a `04-Superseded` collection. Both
records survived. The new philosophy **normalizes the item type and merges**, collapsing
duplicates into a single record.

Those are not two roads to the same place. They are incompatible models of what a
duplicate *is*, and running both against one library produces an incoherent result.

Verification on 2026-07-16 (group library 6505702, 9,530 items, 272 collections):

| Check | Result |
|---|---|
| `discover_duplicates.py` on `00-Dups` (35 items) | 595 pairs compared, **0 matches**, 35 singletons |
| `merge_prep scan` on `00-Dups`, `01-Dups`, `Dups-missed-by-script`, `WoS-Duplicates` | 0 groups — all resolved |
| `merge_prep scan` on `02-Context` (892 items) | **8 duplicate groups**, incl. 3 cross-type |

The old skill had nothing left to find. The new one, pointed at a live screening
collection, immediately found eight groups — including `preprint`↔`conferencePaper` and
`preprint`↔`journalArticle` pairs, exactly the cross-type case this skill was built for
and could no longer reach.

Three structural reasons it lost:

**Discovery was manual and gated.** `discover_duplicates.py` was hard-wired to one
collection (`DUPS_COLLECTION_NAME = "00-Dups"`, line 35). It could only cluster what a
human had already copied there out of Zotero's Duplicates view — and Zotero's Duplicates
view does not show cross-type duplicates. The skill therefore depended on a human
manually finding, by eye, precisely the class of duplicate the tooling existed to find.
`merge-prep` scans any collection key directly.

**Title clustering was too weak.** The 0.90 Jaccard threshold matched nothing across 595
pairs. Truncated and mangled titles — `merge-prep`'s SKILL.md names *"Position: vibe
coding needs"* as the canonical example — defeat title similarity entirely. Clustering on
a shared DOI or arXiv id does not care that the title is mangled.

**`apply_dedup.py` became actively wrong.** Its only write is *"add this item to
`04-Superseded`"*. Under the merge philosophy that write creates a record that should no
longer exist, in a state the rest of the pipeline no longer understands. A dormant script
that does the wrong thing correctly is worse than no script.

## 2. The PRISMA objection, answered

The strongest argument for the old workflow was PRISMA integrity. Its standing rule:
*source counts must stay invariant*, because the PRISMA flow diagram reports "records
identified through database searching" per source, and that count comes from `01-Imports`
membership. Superseding only ever **added** a membership; nothing was removed. The worry
was that merging would destroy per-source counts.

**It doesn't.** Zotero's merge unions collection memberships — the surviving master
inherits every collection its duplicates belonged to. The library already demonstrates
this. Item `6ZW9QNQH`, *"Position: vibe coding needs vibe reasoning"*, is a single
`conferencePaper` carrying `sources: acm|arxiv|wos`. Three import memberships, three
source counts, one record. The merge preserved all of it.

So the invariant that justified superseding survives merging. The rule was right; the
mechanism was unnecessary.

## 3. ⚠ Known data defect frozen at retirement

`apply_dedup.py` resolved the target collection by **exact name match** (line 96,
`if "04-Superseded" in buckets:`), logging a warning and skipping the source otherwise.
**Five of eleven sources spell it `04-Superceded`.** Audited 2026-07-16:

| Source | 02-Screening parent | Collection name | Key | Items | Reachable by script |
|---|---|---|---|---:|---|
| ieee | `7XHWH8NM` | `04-Superseded` | `GNH698CP` | 4 | yes |
| scopus | `2RWBC7QH` | `04-Superseded` | `JFBM4V9H` | 13 | yes |
| **wos** | `E7AS4HD4` | **`04-Superceded`** | `RMJUS8SK` | **9** | **no** |
| arxiv | `YK2CHQLN` | `04-Superseded` | `W8DVC5IP` | 30 | yes |
| acm | `G4IIYGV6` | `04-Superseded` | `N9CUQHGV` | 7 | yes |
| **practitioner** | `7FL4M8HN` | **`04-Superceded`** | `RFKHXMYX` | 0 | no |
| coursework | `IF299TAY` | `04-Superseded` | `FS9JZXTM` | 2 | yes |
| **cao** | `GP3U9EX8` | **`04-Superceded`** | `8WNGQRSL` | **3** | **no** |
| **naimi-references** | `ZXIXRWKG` | **`04-Superceded`** | `DLZGGSDI` | 0 | no |
| naimi-chapters | `DIDV7BKH` | `04-Superseded` | `VKXVUJ3M` | 0 | yes |
| **ssrn** | `F9A9883N` | **`04-Superceded`** | `JR3B67FN` | **3** | **no** |

**Totals: 71 item-memberships across both spellings; 15 of them invisible to the script.**
(`overlap_report.py` counts 34 *unique* items — an item superseded in two sources holds
two memberships.)

Consequences for anyone reading this later:

- **Any count of superseded records must query both spellings.** A search for
  `04-Superseded` alone misses 15 items and undercounts wos, cao, and ssrn to zero.
- **The wos/cao/ssrn items were not placed by `apply_dedup.py`.** The script cannot write
  to a misspelled collection; it warns and skips. Those 15 were superseded by hand or by
  an earlier script revision. Their provenance is weaker than the rest.
- **Do not "fix" the spelling now.** Renaming changes what the historical logs refer to.
  The defect is documented here; leave the data as it lies.

## 4. Mapping: old workflow → current practice

| Old concept | Current equivalent |
|---|---|
| `00-Dups` manual registry | none — `merge_prep scan COLLECTION_KEY` reads any collection directly |
| Cluster by 0.90 Jaccard title similarity | cluster by shared DOI/arXiv id, with substring-aware fuzzy title fallback (≥60% length guard) |
| Venue hierarchy picks a canonical | completeness picks the target type; venue rank (`journalArticle > conferencePaper > bookSection > preprint`) only breaks ties |
| Losers → `04-Superseded`, both records kept | metadata unioned, types normalized, records **merged** in the Zotero client |
| Metadata of the loser preserved by keeping the record | metadata of every copy **unioned** before merging, gaps filled from OpenAlex |
| Original types recoverable from the surviving record | recoverable from `orig-type:` / `orig-date:` tags + `Extra` |
| Screening decisions propagated by "most advanced stage" | tags union automatically on merge |
| Source counts preserved by never removing memberships | source counts preserved because merge unions memberships (§2) |

The venue hierarchy is the subtle one. It survives, demoted. `merge-prep` targets the
type of the **most complete** record and uses venue rank only as a tiebreak — because a
blind rank follows a sparse mis-typed stub (e.g. an S2 import mislabelling a conference
paper as a `journalArticle`) over the real record.

## 5. Preserved rationale

Retained from `references/workflow_history.md`, because the reasoning is still sound even
where the mechanism was replaced.

**Bucket A vs Bucket B.** Zotero's client dedup auto-merges on DOI or close title+author
match, but never across `itemType`. That split the duplicate population in two: Bucket A
(auto-merged; one item, multi-collection membership, only decision propagation needed) and
Bucket B (different types, not merged; separate items needing identification, canonical
selection, superseding, and propagation). This skill addressed Bucket B. **`merge-prep`
dissolves the distinction** — type normalization makes Bucket B merge exactly like
Bucket A.

**The venue hierarchy, as originally stated:** journal article (peer-reviewed, final form,
canonical citation) > conference paper (usually peer-reviewed, often more recent than a
journal counterpart) > book section > book > preprint (arXiv/SSRN; self-published, no
review at posting) > thesis (institutionally reviewed, narrowly distributed) > report >
webpage (no review, weakest provenance). Convention: cite the highest tier.

**Why lower-tier versions were never deleted:** PRISMA accountability (the superseded
items *are* the evidence for the duplicate count at the deduplication step); recoverability
(a superseded version may carry abstract phrasing or methodology text useful if a
screening decision is revisited); and source-count invariance.

**"Most advanced stage" propagation.** Hierarchy: Keep > Maybe > Discard > Queue, where
Queue means undecided and any decision dominates it. Rationale: screening is real work; if
a paper was already screened to Keep under one source, re-screening the canonical under
another wastes effort and risks inconsistency. Respect prior work; default to the more
permissive decision under ambiguity. Merging preserves this outcome for free, since tags
union.

**Standing rules that still bind:** items never leave `01-Imports` — import membership is
permanent provenance. Zotero's API returns soft-deleted collections still sitting in Trash;
always check `data.deleted` and skip them. Collections may share a name if a prior was
deleted but not purged — always resolve to the live one.

## 6. Collection keys as of retirement

The 02-Screening parents, verified live 2026-07-16 (all 11 present, all keys correct):

| Source | Short name | 02-Screening parent |
|---|---|---|
| IEEE Xplore | ieee | `7XHWH8NM` |
| SCOPUS | scopus | `2RWBC7QH` |
| Web of Science | wos | `E7AS4HD4` |
| arXiv | arxiv | `YK2CHQLN` |
| ACM | acm | `G4IIYGV6` |
| Practitioner Network | practitioner | `7FL4M8HN` |
| Coursework | coursework | `IF299TAY` |
| Hancheng Cao | cao | `GP3U9EX8` |
| Linda Naimi Book — Refs | naimi-references | `ZXIXRWKG` |
| Linda Naimi Book — Chapters | naimi-chapters | `DIDV7BKH` |
| SSRN | ssrn | `F9A9883N` |

Standard decision buckets under each parent: `00-Queue` (unscreened), `01-Keep`,
`02-Maybe`, `03-Discard`, `04-Superseded` (**or `04-Superceded` — see §3**). SCOPUS
`00-Queue` additionally nests theme subcollections (`theme:governance`, `theme:oversight`,
…) holding the queue items; the old discover script walked into them automatically.

Other collections referenced by the old workflow: `00-Dups` `BIRBU25N` (35 items, all
singletons), `01-Dups` `T6RUELNJ` (55), `Dups-missed-by-script` `7XTKQMZ6` (4),
`WoS-Duplicates` `YA7Z4UUN` (26). All scan clean as of retirement.

## 7. What was removed, and why it isn't here

| File | Disposition |
|---|---|
| `scripts/discover_duplicates.py` | deleted — read-only, but hard-wired to `00-Dups`, which is now empty of clusters |
| `scripts/apply_dedup.py` | **deleted first** — its writes contradict the merge philosophy |
| `scripts/zotero_common.py` | deleted — **contained hard-coded live read and write API keys** (lines 27–29) |
| `scripts/dedup_proposal.csv`, `would_change.csv`, `apply.log`, `discover.log`, `__pycache__/` | deleted — run artifacts, never belonged in a skill |

**Security note, deliberately recorded rather than quietly dropped.** The deleted
`zotero_common.py` embedded `DEFAULT_KEY_RO` and `DEFAULT_KEY_RW` as literals — a live
**write** key in a checked-in file. That same file was byte-identical (md5
`9eb6125394192e2a82957a886416005e`) in `zotero-bulk-tagging` and `arxiv-zotero-import`,
which still ship it. Those two skills read `ZOTERO_API_KEY_RO` / `ZOTERO_API_KEY_RW`,
while the project instructions define only `ZOTERO_API_KEY` — so on a normal invocation
they fall through to the embedded credentials and work by accident. **Both keys should be
rotated, and the surviving copies purged.** Retiring this skill removes one of three
copies; it does not close the hole.

## 8. If you landed here trying to resolve duplicates

Use `zotero-merge-prep`.

```
# find duplicate groups in a collection (read-only)
python3 scripts/merge_prep.py scan COLLECTION_KEY

# consolidate every group, then merge in the Zotero client
python3 scripts/merge_prep.py scan COLLECTION_KEY --prep

# one known work
python3 scripts/merge_prep.py prep "TITLE" --dry-run
```

`merge-prep` only preps — it unions metadata and normalizes types so that Zotero's native
*Duplicate Items* merge drops nothing. Zotero exposes no merge endpoint, so the merge
itself stays a client action, in your hands. That division of labour is intentional: the
one destructive step is the one a human performs.

---
name: zotero-merge-prep
description: Consolidate duplicate Zotero records BEFORE running Zotero's native "Merge Items", so the merge is lossless. Zotero's merge keeps only the master record's field values (silently dropping metadata the other copies had) and only groups items of the SAME item type — so cross-type duplicates (preprint vs journalArticle vs conferencePaper) are never detected. Given a title (or explicit item keys), this skill finds the duplicates, confirms they're the same work, UNIONS their metadata (authors, abstract, DOI, URL, venue, date — gap-filling from OpenAlex), and NORMALIZES their item types (tagging orig-type:/orig-date: for lineage), so Zotero's Duplicate Items merge drops nothing. Use when the user says "prep duplicates before merging", "Zotero dedupe isn't detecting these", "consolidate these duplicate records", "these two entries are the same paper", or "fix the metadata before I dedupe". Complements the `zotero` and `openalex` skills.
---

# Zotero merge-prep

**The problem:** Zotero's *Merge Items* keeps only the **master's** field values — any
author list, DOI, abstract, or URL that lived only on a *non-master* duplicate is
**lost**. And Zotero only detects duplicates of the **same item type**, so a preprint
and its published journal/conference version never appear as duplicates at all.

**The fix (this skill):** before you merge, make every duplicate carry the *same,
complete* metadata and the *same* item type. Then the merge can't drop anything.

## Setup
Required: `ZOTERO_API_KEY` (a **WRITE** key — this edits records), `ZOTERO_LIBRARY_ID`,
`ZOTERO_LIBRARY_TYPE` (`group`|`user`). Optional: `OPENALEX_MAILTO`, `OPENALEX_API_KEY`
— enable OpenAlex gap-filling.

## Credentials in this environment (Claude Desktop / claude.ai)

This skill runs in Claude's sandboxed code-execution environment: there is no shell
profile, no direnv, and no saved config files. The API keys are provided as `KEY=value`
lines in the **project instructions** (or the user's global preferences), using the same
variable names the CLI already reads:

```
ZOTERO_API_KEY_RO=xxxxxxxxxxxxxxxxxxxxxxxx   # reads
ZOTERO_API_KEY_RW=xxxxxxxxxxxxxxxxxxxxxxxx   # writes (e.g. tag-add --commit); omit if you only read
ZOTERO_LIBRARY_ID=1234567
ZOTERO_LIBRARY_TYPE=group
OPENALEX_MAILTO=you@example.edu
```

Read the values from the instructions and pass them **inline as environment variables on
every script invocation**:

```
ZOTERO_API_KEY=... ZOTERO_LIBRARY_ID=... ZOTERO_LIBRARY_TYPE=group \
  python3 scripts/merge_prep.py find "TITLE"
```

Rules:
- Never print, echo, or quote key values back in your reply to the user.
- If a required key is missing from the project/global instructions, ask the user to add
  it there (or paste it in chat for this conversation only). Do not invent values.

## Commands
Invoke with `python3 scripts/merge_prep.py <cmd>`:

| Command | What it does |
|---|---|
| `scan COLLECTION_KEY` | **The batch loop.** Cluster every item in a collection into duplicate groups — by **shared DOI/arXiv id** (false-positive-proof; catches *truncated / short / mangled* titles like "SWE-bench" or "Position: vibe coding needs") **OR** fuzzy title (substring-aware, for records lacking a shared id). List them; add **`--prep`** to consolidate every group in one shot. Run this after a round of browser-extension imports. |
| `find "TITLE"` | List candidate duplicates for one title + a **same-work confidence** (title similarity, shared DOI/arXiv, shared author surnames). |
| `prep "TITLE"` | The main action for one work: find → confirm → **union metadata** → **normalize types**. Writes to Zotero. |
| `prep --keys K1,K2,K3` | Prep an explicit set of item keys (use when titles differ too much to auto-match, e.g. a tweet cited many ways). |

**Matching guardrail:** a substring only counts as "same title" when the shorter title is ≥60% the length of the longer (and both ≥8 chars) — otherwise a short title like *"Vibe coding"* would spuriously match every longer title containing that phrase, and clustering would chain unrelated works together.

**Options:** `--dry-run` (preview, no writes) · `--target <itemType>` (force the
merged type) · `--no-openalex` (skip gap-filling) · `--force` (proceed even at low
same-work confidence).

## What `prep` does, precisely
1. **Find** — title search (`qmode=titleCreatorYear`), keep candidates with ≥0.80 title similarity. (Or take `--keys`.)
2. **Confirm** — compute a same-work confidence from title similarity + shared DOI/arXiv id + shared author surnames. Below 0.85 it refuses unless `--force`/`--keys`.
3. **Union metadata** — best value per field across all copies: longest author list, longest abstract, a real (non-arXiv-preferred) DOI, a DOI/publisher URL over a semanticscholar/arXiv-landing one, the published date, the venue (mapped into the target's venue field), and a merged `extra` (preserving arXiv ids). Remaining gaps filled from **OpenAlex**.
4. **Normalize types** — target = the type of the **most-complete record** (the freshly/authoritatively catalogued one; a mis-typed sparse *stub* shouldn't win), with rank (journalArticle > conferencePaper > bookSection > preprint) breaking ties — or `--target`. Convert the other copies with proper base-field mapping (unmappable fields → `Extra`), tagging `orig-type:<kebab>` + `orig-date:<date>`. *(A blind rank would follow a stub mis-typed higher than the real type — e.g. an S2 import that mislabels a conference paper as a journalArticle; the completeness rule avoids that.)*
5. **Apply** — every candidate ends up with identical, complete metadata + the same type.

Then **you run Zotero's *Duplicate Items* merge** and nothing is lost. (Zotero has no
merge API endpoint — merging stays a client action; this skill only preps.)

## Recipes
```
# see what would consolidate
python3 scripts/merge_prep.py find "MetaGPT: meta programming for a multi-agent collaborative framework"

# preview then apply
python3 scripts/merge_prep.py prep "Attention is all you need" --dry-run
python3 scripts/merge_prep.py prep "Attention is all you need"

# titles differ (OCR/caps/variant) — specify the records yourself
python3 scripts/merge_prep.py prep --keys AUS8GZVE,JAHHFHPN,H2BEFSP9
```

## Notes
- **Non-destructive:** it never deletes items and only *fills* fields / changes types
  (originals preserved via `orig-type:`/`orig-date:` tags + `Extra`). The actual
  merge — the only destructive step — stays in your hands in the client.
- If Zotero *still* won't group them after prep (e.g. an ALL-CAPS or truncated title),
  the records are now identical enough to merge manually with confidence.
- Cross-type is the headline case: this is what makes a preprint + its published
  version mergeable at all.

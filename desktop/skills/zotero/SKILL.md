---
name: zotero
description: Query and retrieve items from a Zotero reference library via the Zotero Web API. Use this skill whenever the user mentions Zotero, their reference library, a Zotero collection, "my sources", "my bibliography", "my reading list", "items I've saved", or wants to pull citations, tags, notes, attached PDFs, or BibTeX/RIS exports from a personal or group Zotero library. Also use it when the user asks questions that imply reaching into their research library — e.g. "what papers do I have on X", "give me a bib file for the Foo collection", "summarize the PDFs I tagged `to-read`", or anything where the answer lives in Zotero rather than on the open web.
---

# Zotero

Interact with a Zotero library (personal or group) through the Zotero Web API. This skill wraps the API in a single Python CLI so common operations — listing collections, searching items, exporting citations, and reading attachment text — are one command away.

## Setup (one-time, performed by the user)

The user must fill in two values before this skill works. They live in environment variables so no secrets get committed to the skill itself.

Required:
- `ZOTERO_API_KEY` — generated at https://www.zotero.org/settings/security (scroll to "Applications"). For read-only use, grant library read access; no write permission is needed unless the user explicitly wants to create/update items.
- `ZOTERO_LIBRARY_ID` — numeric user ID (found at https://www.zotero.org/settings/security under "Your userID for use in API calls") for personal libraries, or the numeric group ID for group libraries.

Optional:
- `ZOTERO_LIBRARY_TYPE` — `user` (default) or `group`.
- `ZOTERO_COLLECTION_KEY` — the 8-character collection key if the user wants all operations scoped to a single collection by default. Found in the Zotero desktop app by right-clicking a collection → "Edit Bibliography" URL, or via `zotero.py collections`.

## Credentials in this environment (Claude Desktop / claude.ai)

This skill runs in Claude's sandboxed code-execution environment: there is no shell
profile, no direnv, and no saved config files. The API keys are provided as `KEY=value`
lines in the **project instructions** (or the user's global preferences), using the same
variable names the CLI already reads:

```
ZOTERO_API_KEY=xxxxxxxxxxxxxxxxxxxxxxxx
ZOTERO_LIBRARY_ID=1234567
ZOTERO_LIBRARY_TYPE=group
```

Read the values from the instructions and pass them **inline as environment variables on
every script invocation**:

```
ZOTERO_API_KEY=... ZOTERO_LIBRARY_ID=... ZOTERO_LIBRARY_TYPE=group \
  python3 scripts/zotero.py collections
```

Rules:
- Never print, echo, or quote key values back in your reply to the user.
- If a required key is missing from the project/global instructions, ask the user to add
  it there (or paste it in chat for this conversation only). Do not invent values.

## The CLI

All operations go through `scripts/zotero.py`. **Invoke it with `python3`**, passing the credentials inline (see above). Run `python3 scripts/zotero.py --help` for the full surface; the main subcommands are:

| Command | What it does |
|---|---|
| `collections` | List all collections in the library with their keys and names. Use this first if the user hasn't identified a specific collection. |
| `items` | List items. Accepts `--collection KEY`, `--tag TAG`, `--limit N`, `--fields`, `--format json\|table\|bib`. Defaults to the collection in `ZOTERO_COLLECTION_KEY` if set. |
| `count` | Print **only** the number of matching items (read from the `Total-Results` header — one request, no item bodies). Accepts `--collection`, `--tag`, `--q`, `--top`/`--all`. Use this for any "how many" question instead of listing and counting. |
| `item KEY` | Fetch a single item by its 8-character item key, including metadata and any child attachments/notes. |
| `search QUERY` | Full-text/quick search across the library (or scoped collection). Accepts `--fields`. |
| `tags` | List tags used in the library or a collection, with counts. |
| `export` | Export items as BibTeX, RIS, or CSL-JSON. Accepts the same filters as `items`. |
| `attachment KEY` | Download an attachment (usually a PDF) to a local path and, if it's a PDF, extract text. |
| `cache` | Inspect the local cache (entry count, size, library version); `--clear` empties it. |
| `tag-add` | Add tags to items. **DRY-RUN by default** — prints what it would do; pass `--commit` to write. Use `KEY --add tag` for one item or `--plan FILE.json` (`{"KEY": ["tag", …]}`) for bulk. Idempotent (skips tags already present), version-locked, and invalidates the cache after writing. Requires a write-scoped API key. |
| `prisma` | Report PRISMA-style counts across data sources. Recognizes both the `01-Import(s)/02-Screening` and legacy `Imports/Classification` layouts, the `04-Superseded` stage, and phase containers. Use `--root NAME` to scope to one phase (see below). |
| `review` | List items currently in-scope for review analysis. Defaults to Keep + Maybe across all data sources. |
| `dedupe` | Find items appearing in multiple data sources within the given stages. Defaults to Keep + Maybe. |
| `trace KEY` | Reconstruct one item's full lineage from its collection memberships + tags: sources, query batches, per-screener decisions, supersession, and every folder path it sits in. |
| `superseded` | List records parked in `04-Superseded` and what replaced them (via the `superseded-by:<key>` tag). `--resolve` fetches each replacement's itemType to flag type transitions (e.g. preprint → journalArticle); `--root` scopes it. Doubles as an integrity check — records lacking the tag have folder-only lineage. |

Output defaults to compact JSON so you can parse it; pass `--format table` for human-readable tables in the terminal.

### Token-efficient output

The full Zotero item object carries `library`/`links`/`meta`/`relations` blocks you rarely need. To keep responses small:
- For counts, use `count` — it returns a bare integer, never item bodies.
- For listing, prefer `--format table` (compact key/title/date columns) or `--fields title,date,creators` (compact JSON projected to just those `data` fields).
- Reach for full `--format json` only when you genuinely need the complete object (e.g. applying the discard-collection filter, which needs `data.collections`).

### Caching (fast repeat calls, fewer API hits)

Responses are cached on disk (default `~/.cache/claude-zotero/<libtype>-<libid>/`, override with `ZOTERO_CACHE_DIR`) and validated against Zotero's library version: a cached entry is reused **only while the library is unchanged**. Any edit anywhere bumps the version and invalidates the cache, so cached data is never stale. The version itself is re-probed at most once per `ZOTERO_CACHE_VERSION_TTL` seconds (default 60), so bursts of commands make near-zero network calls. This makes repeat `prisma`/`review`/`dedupe`/`collections` runs — and the collection-list fetch every analytical command repeats — effectively free (measured ~24× faster on `prisma`).

- Global flags (place **before** the subcommand): `--no-cache` bypasses the cache entirely; `--refresh` ignores cached reads and re-fetches, rewriting the cache.
- If you suspect the user just edited their library mid-session and the 60s window hasn't lapsed, use `--refresh` (or `cache --clear`) to force fresh data.

## Workflow

When the user asks something Zotero-shaped:

1. **Check scope.** If the user names a collection ("the AI governance collection"), first run `collections` to resolve the name to a key, unless `ZOTERO_COLLECTION_KEY` is set and the user means that one. If the scope is ambiguous, ask before hitting the API — library-wide queries can be slow on big libraries.
2. **Apply the discard-collection exclusion** (see section below) for any analytical query — counting, summarizing, exporting, reasoning about content. Skip it only when the user is explicitly asking about discarded/rejected items.
3. **Pick the narrowest command.** `search` beats `items` when the user has a query term. `item KEY` beats re-listing when you already know the key from a previous turn.
4. **Page through large results deliberately.** The Zotero API caps responses at 100 items per request; `items` and `search` handle pagination automatically, but warn the user before pulling thousands of records.
5. **For PDF questions**, use `attachment KEY` to download and extract text, then analyze it directly. Don't guess at PDF contents from titles alone.
6. **For bibliography requests**, prefer `export --format bibtex` (or `ris` / `csljson`) over hand-formatting — Zotero's server-side formatters handle edge cases (non-Latin names, DOIs, multi-author) far better than ad-hoc string building. But see the discard-exclusion section: if the exclusion applies, fetch as JSON and filter first.

## Reference material

For details on the Zotero Web API (endpoints, query parameters, rate limits, error codes), see `references/zotero-api.md`. Read it when a user request doesn't map cleanly onto one of the CLI subcommands — you may need to invoke the API directly through `zotero.py raw <path>` rather than extending the CLI mid-task.

## Excluding discarded items (systematic review convention)

This library uses a systematic literature review workflow. **Any collection whose name contains `"03 - Discard"` (case-insensitive) holds items that have been rejected from the review** and must be excluded from analytical queries. They are retained in Zotero for audit and history purposes only — deleting them would break the PRISMA-style record.

Apply this rule by default whenever the user asks questions that aggregate, summarize, count, or reason about "the literature," "the papers," "the sources," "the review," tags, themes, or content. Examples of where to exclude: "how many papers do I have on X", "summarize the themes in my review", "export a bibliography", "what's tagged `methodology`", "give me the full text of every paper about Y."

Do **not** apply the exclusion if the user is explicitly asking about discarded items, audit trail, or review history — e.g. "what did I reject and why", "show me the discard collection", "how many items did I screen out." In those cases the discard collections are the subject of the question.

**How to apply the exclusion.** The Zotero API doesn't support "exclude items in collections matching a pattern" as a single query parameter, so this requires a two-step approach:

1. Run `collections` and identify every collection whose name contains `"03 - Discard"` (case-insensitive, substring match — the user may have multiple discard collections nested under different screening phases).
2. For each candidate item returned by `items` / `search` / `export`, check its `data.collections` array. If any collection key in that array is in the discard set, drop the item.

When running `export`, fetch items via `items --format json`, filter them in memory, and format the bibliography from the filtered set — don't use the server-side `format=bibtex` export path, since you need item-level inspection to apply the filter.

When the exclusion applies, tell the user how many items were filtered out so they can sanity-check: "Excluded N items in discard collections; M items remain." If the discard collections are empty or don't exist yet, say so once and proceed normally.

## Counting items reliably

Counting looks easy but is the most common place to be subtly wrong. The Zotero API, the desktop client, and the CLI disagree with each other in predictable ways. Before reporting any count to the user, know which of these you're answering:

- **"How many papers"** — the user means unique top-level bibliographic records. Answer with `/items/top` counts, deduplicated across collections by item key.
- **"How many items"** — may include child attachments and notes. `/items` returns these; `/items/top` does not. The desktop client's sidebar counts align with `/items/top`, so when a user reads a number off their screen, that's what they mean.
- **"How many records did this query return"** — the raw per-collection count, which may include duplicates of items that appear in other batches.

### The authoritative count pattern

For any count, hit the endpoint with `limit=1` and read the `Total-Results` response header. This is one HTTP request with no pagination and no logic to get wrong:

```
GET /groups/{id}/collections/{key}/items/top?limit=1
→ header: Total-Results: 234
```

The `count` subcommand implements exactly this pattern (`count --collection KEY` → bare integer). Use it for "how many" questions. Use the `raw` subcommand when you need a total for an endpoint `count` doesn't cover, or verify the CLI's numbers against it when something looks off. **Never infer a total from the length of a returned page** — the API pages at 100 and a naive caller will silently truncate.

### Parent collections return 0

The API does not aggregate descendants. A query against a parent folder like `Imports` or `Classification` returns zero items even when the subtree contains hundreds — items belong to specific leaf collections, not their ancestors. The desktop client aggregates for display; the API does not. To count a subtree, enumerate the leaves and union their item keys.

### Per-collection sums are not unique counts

In a systematic review, the same item commonly appears in multiple import batches (overlapping database queries) or is linked from an `Imports/` batch into a `Classification/` stage. Summing per-collection counts double-counts these items. Always work with sets of item keys:

```python
unique = set()
for ck in collection_keys:
    for item in paginate(f'collections/{ck}/items/top'):
        unique.add(item['key'])
# len(unique) is the real count
```

### Gap analysis (Imports vs. Classification)

To find items that were imported but never screened — or the reverse, items classified but missing from imports (orphans indicating manual additions or bookkeeping drift) — gather top-level item keys from each subtree and take the set difference. The numbers are usually small and highly informative about workflow state. Do this proactively when a user asks about review progress or data integrity; it's fast and often surfaces issues they didn't know to ask about.

### Reconciling with the user's screen

When a user reports a count from their Zotero client that disagrees with yours, **trust the client first** and figure out what you're counting differently. Common causes, in order of frequency:

1. `/items` vs. `/items/top` (client shows top-level only).
2. Pagination truncation in an intermediate layer (verify with `Total-Results`).
3. Per-collection sum vs. unique across a subtree.
4. Active filters in the client (tag filter, search box, "show items in subcollections" toggle).

Ask which exact collection the user is looking at and which column — Zotero's sidebar shows a count, but it can also be affected by view filters.

## Notes on behavior

- The API is rate-limited. The script respects `Backoff` and `Retry-After` headers automatically; if you see repeated 429s, pause and tell the user.
- Keys (library ID, item key, collection key) are case-sensitive. Item and collection keys are always 8 alphanumeric characters.
- Group libraries work identically to user libraries; just set `ZOTERO_LIBRARY_TYPE=group` and use the group's numeric ID.
- The CLI never writes to the library unless the user explicitly asks for a write operation and provides an API key with write scope. Read operations are the default and safe.

## Systematic reviews and PRISMA workflows

If the user's collection structure suggests a systematic review, recognize and respect the following convention:

```
<Data Source Name>/
├── Imports/                 ← raw RIS imports; audit trail only
│   ├── 01, 02, 03, ...      ← numbered import batches
└── Classification/          ← screening workflow
    ├── 00 - Queue           ← awaiting review
    ├── 01 - Keep            ← included
    ├── 02 - Maybe           ← tie-break / needs more review
    └── 03 - Discard         ← excluded
```

Items in `Imports/` are typically *linked* into `Classification/` (not moved), so the same item key may appear in both.

### Example: adapting to a numbered-stage convention

Real reviews often depart from the default `Imports/` + `Classification/` layout. The tooling
recognizes common non-default conventions; the following is one worked example (numbered
containers + a supersession stage + multi-phase screening) — adapt the folder/tag names to your
own library:

- **Containers are numbered:** `01-Import(s)/` (with per-query batch folders `Q-<SRC>-NN`) and `02-Screening/` are the equivalents of `Imports/` and `Classification/`. Stage folders are `00-Queue / 01-Keep / 02-Maybe / 03-Discard / 04-Superseded` (the misspelling `04-Superceded` also appears).
- **`04-Superseded`** holds records replaced by a newer version (e.g. a preprint later published). It is **excluded from the default in-scope set**, like Discard. The replacement is linked by a `superseded-by:<itemKey>` tag on the old record.
- **Multi-phase:** `Phase 2/Phase 1 - 01-Keep/` and `Phase 2/Phase 1 - 02-Maybe/` are phase-2 screening of the phase-1 outcomes, each with its own stage folders. These are detected as data sources too, so a library-wide `prisma`/`review` **mixes both phases**. Scope to one phase with `--root "Database Queries"` (phase 1) or `--root "Phase 2"` (phase 2).
- **Tags are the durable ground truth** (they travel with the item regardless of folder moves, and are queryable via `items --tag`): `source:<db>` (provenance; an item may carry several), `s1:<screener>:<decision>` / `s2:<screener>:<decision>` (per-screener votes — screeners include `chatgpt`, `claude`, `human`, `machine`), and `superseded-by:<key>`. Query-batch provenance, by contrast, lives **only** in folder membership.
- **Zotero stores no field-level history.** "This used to be a preprint" is recoverable *only* because the old record is retained in `04-Superseded` with the `superseded-by:` tag — not from any Zotero feature. Use `superseded --resolve` to audit these; records missing the tag have lineage that survives only as folder placement.

### Default scope: Keep + Maybe

Unless the user explicitly asks otherwise, treat **only `01 - Keep` and `02 - Maybe` as in-scope** for downstream analysis. Specifically:

- **`Imports/` is audit material.** Don't include its contents when listing, searching, exporting, or summarizing the review. Don't suggest deleting it.
- **`00 - Queue` is unreviewed.** It shouldn't appear in bibliographies, summaries, or "what papers do I have on X" answers. An exception is if the user asks specifically about review progress ("what's left to screen?") — then Queue is the answer.
- **`03 - Discard` is explicitly excluded.** Don't surface it in content summaries. An exception is if the user is reviewing their own past exclusion decisions ("why did I discard this?") — then the user is asking about Discard directly and it's in scope.

This means a casual question like "summarize what I have on governance frameworks" should draw only from Keep and Maybe.

### Commands for this workflow

- **`prisma`** walks all data sources and reports the full flow: records identified (from `Imports/`), records in each Classification stage, and cross-source totals. This command deliberately reports all four stages because PRISMA flow diagrams need them.
- **`review`** lists items currently in-scope (Keep + Maybe by default) with their provenance — which source(s) and stage(s) each item came from. Use this for "show me my included set." Pass `--stages queue` or similar to broaden.
- **`dedupe --stages keep,maybe`** reports which items appear in multiple data sources, which matters when assembling the final bibliography and for the PRISMA "duplicates removed" count.

### Verifying counts before reporting them

A pagination bug in `_collection_item_keys` (fixed April 2026) caused `prisma`, `review`, and `dedupe` to silently truncate every collection at 100 items, producing dramatic undercounts on large stages (e.g., a 677-item Discard stage reported as 100). If you're working against a pre-fix copy of `zotero.py` and the numbers look suspicious — especially any count that hits exactly 100 — verify against `Total-Results`:

```
python scripts/zotero.py raw /groups/{id}/collections/{KEY}/items/top --params limit=1
```

More generally: for PRISMA reports especially, cross-check at least one stage count against `Total-Results` on your first run in any unfamiliar library. A mismatch suggests a pagination issue somewhere in the chain, and a wrong PRISMA count is the kind of error that invalidates a methods section.

### Behavioral rules

- **Never move items between Classification stages** without explicit user confirmation. Stage assignments are human screening decisions.
- **Never modify `Imports/` contents.** It's the immutable record of what came back from each database query.
- **When reporting counts, prefer deduplicated totals** across sources. The same paper can legitimately appear in multiple sources' Keep collections via linking — it's one paper, not several.
- **For bibliographic exports** (BibTeX/RIS/CSL-JSON), pull from `review --stages keep` unless the user asks otherwise. Use the CSL-JSON output and deduplicate by item key before writing.

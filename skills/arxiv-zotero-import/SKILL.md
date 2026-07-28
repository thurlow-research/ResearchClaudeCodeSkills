---
name: arxiv-zotero-import
description: Fetch results from an arXiv API search query and create Zotero items in a target collection for SLR workflows. Use this skill whenever the user wants to import arXiv search results into Zotero, runs a new arXiv query (Q-arXiv-NN), needs to translate ACM/IEEE/SCOPUS Boolean syntax to arXiv API syntax, or wants to check the result count for an arXiv query before importing. Also use when the user references the existing fetch_q_arxiv_*.py pattern or asks to import preprints via API rather than RIS files.
---

# arXiv to Zotero Import for SLR Workflows

This skill imports arXiv API search results as Zotero `preprint` items into a named target collection. Designed for the Vibe Coding Governance SLR workflow where each arXiv query gets its own Zotero collection (`Q-arXiv-01`, `Q-arXiv-02`, etc).

## When to Use

Trigger when the user:
- Wants to import arXiv results for a new query (e.g., "import Q-arXiv-08")
- Asks how many results an arXiv query returns
- Needs help translating Boolean queries from ACM/IEEE/SCOPUS to arXiv syntax
- References the `fetch_q_arxiv_*.py` pattern from prior queries
- Wants to fetch via API rather than the slow web search → RIS path

## Workflow

### Step 1: Construct the arXiv query

arXiv API syntax differs from ACM/IEEE/SCOPUS:
- Field operators: `abs:` (abstract), `ti:` (title), `au:` (author), `all:` (any field), `cat:` (category)
- Phrases: use double quotes `abs:"vibe coding"`
- Boolean: `AND`, `OR`, `NOT` (uppercase) with parentheses for grouping
- Wildcards (`*`) are NOT supported — drop them when translating
- Categories: `cat:cs.SE`, `cat:cs.HC`, etc.

Pattern for translating a three-cluster ACM query:
```
ACM:    Abstract: (A OR B) AND (C OR D) AND (E OR F)
arXiv:  (abs:"A" OR abs:"B") AND (abs:"C" OR abs:"D") AND (abs:"E" OR abs:"F")
```

### Step 2: Check result count first

Before writing any items, query arXiv to see how many results the query returns. If the count is unexpectedly high (>500), tighten the query rather than committing the data.

Use `scripts/check_count.py` to query arXiv and print the total result count without any Zotero writes:

```bash
python3 scripts/check_count.py "<arxiv-query-string>"
```

If the count is reasonable, proceed to Step 3.

### Step 3: Run the fetch + create script

`scripts/fetch_arxiv_query.py` takes a query string, target collection name, and working directory, then:

1. Fetches all results from arXiv (paginated, respecting the 3-second rate limit)
2. Caches results to `arxiv_results.json` for resume after interruption
3. Looks up target Zotero collection by name (must already exist)
4. Creates `preprint` items in batches with full metadata (title, authors, abstract, date, DOI, archiveID, primary category)
5. Dry-run by default; `--apply` commits to Zotero

```bash
# Dry-run: fetch only, show what would be created
python3 scripts/fetch_arxiv_query.py \
    --query "<arxiv-query>" \
    --collection "Q-arXiv-NN" \
    --workdir ~/slr/arxiv/q-arxiv-NN

# Apply: actually create items
python3 scripts/fetch_arxiv_query.py \
    --query "<arxiv-query>" \
    --collection "Q-arXiv-NN" \
    --workdir ~/slr/arxiv/q-arxiv-NN \
    --apply
```

### Step 4: After import, run Zotero client-side dedup

Important: this skill does NOT dedup against the existing Zotero library. Zotero's
client-side dedup is more sophisticated (DOI matching, title+author fuzzy matching)
and should be run after import. See the `zotero-merge-prep` skill for handling
items that the client dedup couldn't merge due to differing item types (`zotero-slr-dedup`,
mentioned in earlier drafts of this skill, was retired in its favor).

## Important Conventions

- **Date filter:** the SLR uses 2020+ as a methodological filter. arXiv API queries
  don't enforce date in the search string; results are sorted descending by
  submitted date by default. Filter manually if needed by checking results.
- **Always create the target Zotero collection first** in the Zotero client before
  running the script. The script confirms existence but does not create collections.
- **No client-side dedup in this script** — Zotero client handles that better.
  The script tracks within-run dedup by arXiv ID only.
- **State file (`state.json`)** tracks per-arxiv-ID creation for resume after
  interrupt. Within a single `--apply` run, items already created are skipped.
- **Working directory** defaults to current directory. For consistency, use
  `~/slr/arxiv/q-arxiv-NN/` per query.

## Compatibility

- Python 3.8+, no third-party dependencies
- arXiv API: respect the 3-second rate limit between requests
- Zotero credentials are **required environment variables** — no fallback defaults are
  embedded in the scripts (this skill may be shared as a `.skill` package, so it must
  never carry live keys):
  - `ZOTERO_API_KEY_RO` — read-only key, used for all lookups and dry-run
  - `ZOTERO_API_KEY_RW` — write-scoped key, only needed for `--apply`; omit for dry-run-only use
  - `ZOTERO_LIBRARY_ID` — numeric group or user ID
  - `ZOTERO_LIBRARY_TYPE` — `group` (default) or `user`
- **Shares its response cache with the main `zotero` skill** — same directory
  (`~/.cache/claude-zotero/<type>-<id>/`, override with `ZOTERO_CACHE_DIR`), same
  version-validated key scheme (override the version-probe TTL with
  `ZOTERO_CACHE_VERSION_TTL`, default 60s). The collection list this skill scans on every
  run is cached under the identical key `zotero.py collections` uses, so either skill can
  reuse the other's recent fetch. Invalidated automatically after this skill writes items.

## Reference Files

- `references/query_examples.md` — Examples of arXiv query translations from other databases
- `references/log_entry_template.md` — Template text for the Query_Composition_and_Log.xlsx entry

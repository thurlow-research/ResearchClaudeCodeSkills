---
name: arxiv
description: Query the arXiv API for a search query's results, for import into Zotero as preprint items, as part of SLR workflows. Use this skill whenever the user wants to import arXiv search results into Zotero, runs a new arXiv query (Q-arXiv-NN), needs to translate ACM/IEEE/SCOPUS Boolean syntax to arXiv API syntax, or wants to check the result count for an arXiv query before importing. Also use when the user references the existing fetch_q_arxiv_*.py pattern or asks to import preprints via API rather than RIS files. This skill is query-only — it never writes to Zotero itself; it hands off a create-items plan to the `zotero` skill's `zotero.py create-items`, which does the actual write.
---

# arXiv Import for SLR Workflows

This skill queries the arXiv API and prepares its results as a plan for creating Zotero `preprint` items in a named target collection. Designed for the Vibe Coding Governance SLR workflow where each arXiv query gets its own Zotero collection (`Q-arXiv-01`, `Q-arXiv-02`, etc).

**Query-only, by design.** This skill never talks to Zotero except to read the target
collection's key. All Zotero writes — including retries, batching, and resuming an
interrupted run — are owned by the main `zotero` skill's `create-items` command, the
same shared write path any future "external source → Zotero" import skill hands off to.
That mirrors how the `openalex` skill works: pure query tool, zero write awareness.

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

### Step 3: Run the fetch script to build a create-items plan

`scripts/fetch_arxiv_query.py` takes a query string, target collection name, and working
directory, then:

1. Fetches all results from arXiv (paginated, respecting the 3-second rate limit)
2. Caches results to `arxiv_results.json` for resume after interruption
3. Looks up the target Zotero collection by name (must already exist) — the only Zotero
   access this skill performs
4. Writes `create_plan.csv` (human-readable preview) and `create_plan.json` (the machine
   plan — full `preprint` metadata per entry: title, authors, abstract, date, DOI,
   archiveID, primary category)
5. **Does not write to Zotero.** It never has a write-scoped key.

```bash
python3 scripts/fetch_arxiv_query.py \
    --query "<arxiv-query>" \
    --collection "Q-arXiv-NN" \
    --workdir ~/slr/arxiv/q-arxiv-NN
```

### Step 4: Create the items via the `zotero` skill

Hand the plan off to the main `zotero` skill's write command — dry-run by default,
`--commit` to actually write:

```bash
ZOTERO_API_KEY_RW=... ZOTERO_LIBRARY_ID=... ZOTERO_LIBRARY_TYPE=group \
  python3 <path-to-zotero-skill>/scripts/zotero.py create-items \
    --plan ~/slr/arxiv/q-arxiv-NN/create_plan.json \
    --state ~/slr/arxiv/q-arxiv-NN/state.json \
    --commit
```

This step needs the `zotero` skill installed too (its own uploaded copy in this
environment), and a **write**-scoped `ZOTERO_API_KEY_RW`. Don't add that key to stored
project/global instructions — ask the user to paste it into the chat for this one
conversation instead.

`create-items` tracks progress in `--state` by a caller-defined `dedupe_key`
(`arxiv:<arxiv_id>` here), so re-running after an interruption skips whatever already
succeeded — the same resume guarantee the old single-script version had, just owned by
the shared write path instead of duplicated here.

### Step 5: After import, run Zotero client-side dedup

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
- **No client-side dedup in this script** — Zotero client handles that better. The
  `dedupe_key` this skill emits (`arxiv:<id>`) only dedupes against *this skill's own*
  prior creates, via `zotero.py create-items`'s state file.
- **Working directory** defaults to current directory. For consistency, use
  `~/slr/arxiv/q-arxiv-NN/` per query.

## Compatibility

- Python 3.8+, no third-party dependencies
- arXiv API: no key needed, but respect the 3-second rate limit between requests
## Credentials in this environment (Claude Desktop / claude.ai)

This skill runs in Claude's sandboxed code-execution environment: there is no shell
profile, no direnv, and no saved config files. The API keys are provided as `KEY=value`
lines in the **project instructions** (or the user's global preferences), using the same
variable names the CLI already reads:

```
ZOTERO_API_KEY_RO=xxxxxxxxxxxxxxxxxxxxxxxx   # read-only key for the one collection lookup
ZOTERO_LIBRARY_ID=1234567
ZOTERO_LIBRARY_TYPE=group
```

Read the values from the instructions and pass them **inline as environment variables on
every script invocation**:

```
ZOTERO_API_KEY_RO=... ZOTERO_LIBRARY_ID=... ZOTERO_LIBRARY_TYPE=group \
  python3 scripts/fetch_arxiv_query.py --query "..." --collection "Q-arXiv-NN" --workdir ~/slr/arxiv/q-arxiv-NN
```

Rules:
- Never print, echo, or quote key values back in your reply to the user.
- If a required key is missing from the project/global instructions, ask the user to add
  it there (or paste it in chat for this conversation only). Do not invent values.

Caches (`~/.cache/claude-*`) last only as long as this sandbox container, so the
collection-list sharing above only helps within one conversation, not across them.

## Reference Files

- `references/query_examples.md` — Examples of arXiv query translations from other databases
- `references/log_entry_template.md` — Template text for the Query_Composition_and_Log.xlsx entry

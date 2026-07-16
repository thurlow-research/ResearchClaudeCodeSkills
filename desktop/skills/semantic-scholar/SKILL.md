---
name: semantic-scholar
description: Search the Semantic Scholar academic graph and chase citations for systematic literature reviews. Use this skill whenever the user wants to find papers by topic, look up a paper/author by DOI/arXiv/title, traverse the citation graph, or — the headline use — do citation "snowballing" (forward = who cites a paper, backward = what a paper cites) to grow a seed set of papers into a candidate pool for a review. Trigger phrases include "snowball", "citation chasing", "who cites this", "what does this cite", "find papers citing X", "backward/forward references", "Semantic Scholar", "S2", "expand my seed set", "find related work", or building a literature-review candidate list from known key papers. This is the citation-graph counterpart to the `exa` skill (open-web discovery) and the `zotero` skill (the user's managed library).
---

# Semantic Scholar

Reach into the [Semantic Scholar](https://www.semanticscholar.org) academic graph (~200M+ papers with citation edges, abstracts, TLDRs, open-access PDFs, and fields of study) through a single stdlib-only Python CLI. Built for systematic literature reviews: the centerpiece is **snowballing** — expanding a seed set of known-good papers along citation edges.

How this fits the user's other skills:
- **`semantic-scholar`** (this) — the *citation graph*: snowballing, references, citations, structured metadata.
- **`exa`** — open-*web* discovery by meaning (preprints, blogs, grey literature) that may not be in the graph yet.
- **`zotero`** — the user's *curated library*; where vetted keepers land.

A typical SLR loop: seed papers (from a database search or Zotero) → **snowball** here to surface candidates → screen → user imports keepers into Zotero.

## Setup (one-time)

The API works without a key but on a heavily rate-limited shared pool (you'll see repeated `429`s), so use the user's key.

## Credentials in this environment (Claude Desktop / claude.ai)

This skill runs in Claude's sandboxed code-execution environment: there is no shell
profile, no direnv, and no saved config files. The API keys are provided as `KEY=value`
lines in the **project instructions** (or the user's global preferences), using the same
variable names the CLI already reads:

```
SEMANTIC_SCHOLAR_API_KEY=xxxxxxxxxxxxxxxxxxxxxxxx
```

Read the values from the instructions and pass them **inline as environment variables on
every script invocation**:

```
SEMANTIC_SCHOLAR_API_KEY=... python3 scripts/s2.py search "vibe coding" --limit 10
```

Rules:
- Never print, echo, or quote key values back in your reply to the user.
- If a required key is missing from the project/global instructions, ask the user to add
  it there (or paste it in chat for this conversation only). Do not invent values.

If the user has no key, they can request one at https://www.semanticscholar.org/product/api#api-key.

## The CLI

All operations go through `scripts/s2.py`. **Invoke with `python3`**, passing the key inline (see above). Run `python3 scripts/s2.py <command> --help` for the full surface.

| Command | What it does |
|---|---|
| `snowball SEED [SEED ...]` | **The main event.** Multi-hop citation expansion from seed papers. See below. |
| `search QUERY` | Relevance search. `--limit`, `--year 2018-2024`, `--venue`, `--fields-of-study`, `--open-access`, `--min-citations`. |
| `bulk QUERY` | Bulk search (boolean `AND`/`OR`/quotes), up to ~1000 results, `--sort citationCount:desc`. Use for large query sweeps. |
| `paper ID` | Full metadata for one paper. ID = S2 id, `DOI:…`, `ARXIV:…`, `CorpusId:…`, `PMID:…`, or a URL. |
| `citations ID` | Papers that **cite** this one — *forward* snowballing. `--influential-only` for high precision. |
| `references ID` | Papers this one **cites** — *backward* snowballing. |
| `batch [ID ...]` | Metadata for many ids at once (≤500); ids from args, `--ids-file`, or stdin. |
| `recommend ID` | Semantic Scholar's recommended papers for a seed. |
| `author QUERY` | Author search; `--papers <authorId>` lists an author's papers. |
| `cache` | Inspect the disk cache; `--clear` empties it. |

Output is compact JSON by default (parse it); `--format table` gives a scannable list (citation count, `★` influential count, OA flag, year, title, authors, DOI, paperId); `--pretty` indents JSON.

## Snowballing (the headline workflow)

```
python3 scripts/s2.py snowball DOI:10.1016/j.infsof.2008.09.009 ARXIV:1706.03762 \
    --direction both --hops 1 --influential-only --year-from 2015 --format table
```

- **Seeds**: pass ids as args, `--seeds-file path` (one id per line, `#` comments ok), or pipe on stdin. Seeds are resolved to canonical paperIds first, so the same paper given by DOI and by S2 id isn't double-counted.
- **`--direction`**: `forward` (who cites the seeds → newer work), `backward` (what they cite → foundations), or `both` (default).
- **`--hops N`**: iterations. Hop 1 = direct neighbors. **Hop counts explode fast** — keep `--hops 1` (occasionally 2) and lean on filters. Warn the user before a 2+ hop run on highly-cited seeds.
- **Filters**: `--influential-only` (follow only S2 "influential" edges — high precision, much smaller pool), `--year-from`, `--min-citations`, `--fields-of-study`, `--limit-per-paper` (cap edges pulled per paper/direction, default 200), `--max-papers` (hard stop).
- **Output**: deduplicated candidates ranked by how many seeds reached them (then citation count). Each carries `reachedVia` provenance — which seed, which direction, which hop, whether the edge was influential, and the citation `intents` (background / methodology / result). `--ids-only` prints bare paperIds (handy to pipe into `batch` or hand to the user for Zotero import).

**Why provenance matters for an SLR:** a paper reached from *multiple* seeds, or via an *influential methodology* citation, is a stronger inclusion candidate than one reached once as a background mention. Surface that signal when presenting candidates; don't flatten the list to titles.

## Caching

Every GET (and batch POST) is cached on disk at `~/.cache/claude-s2` (override `S2_CACHE_DIR`) with a TTL — default **7 days** (`S2_CACHE_TTL` seconds), since paper metadata and citation edges are near-static. Repeat snowball runs over overlapping seed sets become near-free, which matters because the API is the bottleneck.

- Global flags (before the subcommand or after — both work): `--no-cache` bypasses entirely; `--refresh` re-fetches and overwrites.
- `cache` shows entry count / size / TTL; `cache --clear` empties it.
- If citation counts look stale and freshness matters for a report, use `--refresh` on that call.

## Rate limits & etiquette

- The CLI throttles to ~1 request/sec (`S2_MIN_INTERVAL`) and honors `Retry-After` with exponential backoff on `429`/`503`. Even with a key, S2 is the slow part of any large snowball — caching and tight `--limit-per-paper` are how you stay fast.
- Without a key you'll see persistent `429`s on the shared pool (we saw exactly this in testing). The key fixes it — make sure it's set before a big run.
- Be deliberate with `--hops` and `--max-papers`; an unbounded 2-hop snowball off heavily-cited seeds can mean thousands of edge fetches.

## Reference material

For endpoint-level detail (every field name, the bulk-search query grammar, edge-object shape with `intents`/`contexts`/`isInfluential`, batch limits, recommendations API, error codes), see `references/semantic-scholar-api.md`.

## Notes

- IDs are flexible: bare S2 paperIds, or prefixed external ids (`DOI:`, `ARXIV:`, `CorpusId:`, `PMID:`, `PMCID:`, `MAG:`, `ACL:`, `URL:`). The CLI auto-prefixes obvious DOIs/arXiv ids, but when in doubt pass the explicit prefix.
- For reproducible review methods, record the exact seeds, direction, hops, filters, and date run — the graph changes over time as new citing papers appear.
- This skill is read-only against Semantic Scholar and never writes to Zotero. Handing candidates to the user (with DOIs/paperIds) is the boundary; importing is the `zotero` skill's job.

---
name: openalex
description: Query the OpenAlex open scholarly catalog (~250M works) for paper metadata, abstracts, and citation edges — with local on-disk caching and NO API key. Use this skill to enrich/backfill bibliographic records (authors, DOI, venue, real landing-page URL, publication date), recover missing abstracts (OpenAlex stores them as an inverted index with broad coverage incl. preprints), look up a work by DOI/arXiv/OpenAlex-id/title, or traverse citations (who-cites / references). Trigger phrases include "OpenAlex", "recover abstracts", "backfill metadata", "fill in authors/DOI/venue", "enrich these references", "clean up bibliographic data", or when Semantic Scholar / a source has sparse metadata and you need a second, higher-coverage source. Complements `semantic-scholar` (citation graph, needs a key) and `zotero` (the user's managed library); OpenAlex is the free, key-less, high-coverage metadata backstop.
---

# OpenAlex

[OpenAlex](https://openalex.org) is a free, open catalog of ~250M scholarly works with
authorships, venues, DOIs, open-access links, citation edges, and abstracts (stored as
an *inverted index*, reconstructed by this tool). It is the **key-less, high-coverage
metadata backstop** for the SLR stack:

- **`semantic-scholar`** — citation graph + snowballing (needs a key; sparser metadata).
- **`openalex`** (this) — free metadata enrichment + abstract recovery + citations, **cached**.
- **`zotero`** — the user's curated library where vetted records live.

Headline use in this project: **backfilling snowball / imported records** whose S2 metadata
was thin (missing authors, DOI, real URL, or abstract).

## No key required

OpenAlex is now **credit-metered**, and without a key you *will* get throttled (HTTP 429) on
any real workload — anonymous use is only ~100 requests/day. **A free account key is strongly
advised:** it raises the quota ~10× (~1,000/day) and is the difference between the skill working
and hanging on rate limits. Sign up in ~30 seconds at openalex.org/settings/api. Adding your
email joins the "polite pool" (more reliable) as well:

```
export OPENALEX_API_KEY="..."              # STRONGLY ADVISED — ~10x quota, avoids throttling
export OPENALEX_MAILTO="you@example.edu"   # your email — polite pool
```

The client prefers IPv4 (Cloudflare 429s the shared IPv6 pool) and, on a 429, fails fast with a
"quota exhausted, resets in ~Xh" message instead of hanging. Set `OPENALEX_IPV6=1` to opt out.

## Local caching (on by default)

Every response is cached on disk keyed by the full request URL, so repeated lookups are
instant and free. Configure via env:

- `OPENALEX_CACHE` — cache dir (default `~/.cache/claude-openalex`)
- `OPENALEX_CACHE_TTL` — seconds before a cache entry is considered stale; `0` = never (default)
- `--no-cache` — bypass the cache for one call

Inspect/clear: `python3 scripts/openalex.py cache stats` / `cache clear`.

## The CLI

All operations go through `scripts/openalex.py`. **Invoke with `python3`.**

| Command | What it does |
|---|---|
| `work ID` | Full normalized metadata (title, authors, doi, venue, url, date, type, abstract, cited_by_count). |
| `abstract ID` | Just the reconstructed abstract text. |
| `enrich [ID ...]` | **The backfill workhorse.** Normalize many works at once — ids from args, `--ids-file`, or stdin (one per line) → a JSON list. Pipe DOIs in, get clean records out. |
| `search QUERY` | Relevance search (`--limit`). |
| `cites ID` | Works that **cite** this one (forward). `--limit`. |
| `references ID` | Works this one **cites** (backward). `--limit`. |
| `cache {stats,clear}` | Inspect or clear the local cache. |

**ID forms:** OpenAlex id (`W2741809807`), bare DOI (`10.1145/3610721`), `DOI:…` or a
`doi.org` URL, `ARXIV:2107.03374`, `PMID:…`, or free text (→ title search).

### Normalized record fields (from `work` / `enrich`)

`openalex_id, doi, title, authors[] (display names), year, date, venue, type,
url (DOI link preferred, else landing page), oa_pdf, abstract, cited_by_count,
referenced_count`.

## Recipes

**Recover a missing abstract for a DOI**
```
python3 scripts/openalex.py abstract 10.1145/3610721
```

**Backfill metadata for a list of DOIs (e.g. thin Zotero/snowball records)**
```
printf '10.1145/3610721\nARXIV:2107.03374\n' | python3 scripts/openalex.py enrich > enriched.json
# each record has authors, doi, venue, url, date, abstract → PATCH into Zotero
```

**Look up one work fully**
```
python3 scripts/openalex.py work ARXIV:2107.03374
```

**Backward citations (references) of a paper**
```
python3 scripts/openalex.py references 10.1145/3610721 --limit 100
```

## Notes & gotchas

- Abstracts are an inverted index; this tool reconstructs readable text. Some works have
  no abstract in OpenAlex — you'll get an empty string (fall back to Crossref/arXiv).
- `type` uses OpenAlex vocabulary (`article`, `preprint`, `book-chapter`, `dataset`…);
  map to Zotero item types at the call site if needed.
- The polite-pool `mailto` is appended automatically from `OPENALEX_MAILTO`.
- Batch endpoints use OpenAlex OR-filters (≤50 ids/page); `enrich` fetches per-id (cached),
  which is simplest and fully cache-friendly for incremental backfills.
- See `references/openalex-api.md` for raw API details.

# Semantic Scholar Graph API reference

Base: `https://api.semanticscholar.org/graph/v1` · Recommendations: `https://api.semanticscholar.org/recommendations/v1`
Auth header: `x-api-key: <key>` (optional but strongly recommended). Docs: https://api.semanticscholar.org/api-docs/

Read this when a request doesn't map onto an `s2.py` subcommand and you need to build it directly.

## Paper identifiers

Most endpoints accept a flexible `{paper_id}`:

| Form | Example |
|---|---|
| S2 paperId (40 hex) | `a963d05b9d4acd347ad528e7d098eb53d8f555a2` |
| DOI | `DOI:10.1016/j.infsof.2008.09.009` |
| arXiv | `ARXIV:1706.03762` |
| Corpus ID | `CorpusId:215416146` |
| PubMed | `PMID:19872477` / `PMCID:...` |
| Other | `MAG:...`, `ACL:...`, `URL:...` |

## Fields

Endpoints return only `paperId` unless you pass `fields=` (comma-separated). Common paper fields:

`paperId, externalIds, url, title, abstract, venue, publicationVenue, year, publicationDate,
referenceCount, citationCount, influentialCitationCount, isOpenAccess, openAccessPdf,
fieldsOfStudy, s2FieldsOfStudy, publicationTypes, journal, authors, tldr, citationStyles, embedding`

Nested selection works: `authors.name`, `authors.affiliations`, `tldr.text`.

## GET /paper/search  (relevance)

Params: `query` (required, natural language), `fields`, `limit` (≤100), `offset`, `year` (`2019`, `2016-2020`, `2010-`, `-2015`), `venue` (comma list), `fieldsOfStudy` (comma list), `openAccessPdf` (presence-only flag), `publicationTypes`, `minCitationCount`.
Returns `{ total, offset, next, data: [paper, ...] }`. Best for "find a few relevant papers."

## GET /paper/search/bulk

Params: `query` supports a **boolean grammar** — `+` (AND), `|` (OR), `-` (NOT), `"phrase"`, `*` (prefix), `~N` (fuzzy/proximity). E.g. `("systematic review" | survey) + "software engineering" - tutorial`.
Also `fields`, `year`, `venue`, `fieldsOfStudy`, `sort` (`citationCount:desc`, `publicationDate:desc`, `paperId:asc`), and `token` for continuation.
Returns up to 1000 per page with a `token`; page until `token` is null or you have enough. No relevance ranking — use `sort`. Best for large query sweeps / coverage.

## GET /paper/{paper_id}

Single paper. Pass `fields`. 404 if the id is unknown.

## GET /paper/{paper_id}/citations  (forward: who cites it)
## GET /paper/{paper_id}/references  (backward: what it cites)

Params: `fields`, `offset`, `limit` (≤1000).
Each `data` item is an **edge wrapper**, not a bare paper:

```json
{
  "contexts": ["... sentence where the citation appears ..."],
  "intents": ["methodology"],            // background | methodology | result
  "isInfluential": true,                  // S2's influential-citation signal
  "citingPaper": { ...requested fields... }   // or "citedPaper" for /references
}
```

The `fields` list applies to the nested `citingPaper`/`citedPaper`; `contexts`, `intents`, `isInfluential` are returned at the wrapper level (you can also name them in `fields`). For snowballing, `isInfluential` and `intents` are the precision signals — an influential methodology citation is a stronger inclusion candidate than a background mention.

## POST /paper/batch

Body `{ "ids": [...] }` (≤500, mixed id forms ok); query param `fields`. Returns a list **positionally aligned** with the input ids; unknown ids come back as `null`. The cheapest way to hydrate many ids (e.g. snowball output) in one call.

## Authors

- `GET /author/search?query=...&fields=authorId,name,paperCount,citationCount,hIndex,affiliations`
- `GET /author/{author_id}?fields=...`
- `GET /author/{author_id}/papers?fields=...&limit=` — paginated.

## Recommendations API

- `GET /recommendations/v1/papers/forpaper/{paper_id}?fields=...&limit=` → `{ recommendedPapers: [...] }`.
- `POST /recommendations/v1/papers` with `{ "positivePaperIds": [...], "negativePaperIds": [...] }` for multi-seed recommendations — useful as a softer alternative to citation snowballing.

## Rate limits & errors

- With a key, the documented shared limit is ~1 request/second across the key for most endpoints (search/batch have their own pools). Without a key the anonymous pool is far stricter and returns frequent `429`s.
- Honor `Retry-After` on `429`/`503`; back off exponentially. `s2.py` does this and throttles to `S2_MIN_INTERVAL` (default 1s) between calls.
- `400` = bad params (often an unknown field name); `404` = unknown id; `5xx` = server-side.
- The graph is **mutable**: `citationCount` and the set of citing papers grow over time. For reproducible review methods, record seeds + parameters + the date run, and use `--refresh` when a report needs current counts.

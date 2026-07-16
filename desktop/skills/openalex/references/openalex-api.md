# OpenAlex API — reference notes

Base URL: `https://api.openalex.org` · Docs: https://docs.openalex.org · **No key.**

## Politeness / rate limits
- Anonymous pool works but is throttled. Add `mailto=<email>` (query param) or a
  `User-Agent` with your email to join the **polite pool**: 10 req/s, 100,000/day.
- This skill appends `mailto` automatically from `OPENALEX_MAILTO`.

## Entities
`/works`, `/authors`, `/sources` (venues), `/institutions`, `/concepts`, `/publishers`,
`/funders`. This skill focuses on **works**.

## Single work
`GET /works/{id}` where id is:
- OpenAlex id: `W2741809807`
- DOI: `/works/doi:10.1145/3610721` or `/works/https://doi.org/10.1145/3610721`
- arXiv: no native arxiv id filter; use the arXiv DOI `10.48550/arXiv.<id>` →
  `/works/doi:10.48550/arXiv.2107.03374`
- `pmid:<id>`, `mag:<id>`

## Search & filter
- Relevance: `/works?search=<text>`
- Field search: `/works?filter=title.search:<text>`
- Batch by ids (OR, ≤50 per page): `/works?filter=doi:10.x|10.y` or
  `filter=openalex_id:W1|W2`
- Forward citations (who cites W): `/works?filter=cites:W2741809807`
- `per-page` (≤200), `cursor=*` for deep pagination, `select=<fields>` to trim payload.

## Key work fields
| Field | Notes |
|---|---|
| `id` | `https://openalex.org/W…` |
| `doi` | `https://doi.org/…` (or null) |
| `title` / `display_name` | title |
| `authorships[]` | `.author.display_name`, `.institutions[]` |
| `publication_year` / `publication_date` | year / ISO date |
| `primary_location.source.display_name` | venue/journal |
| `primary_location.landing_page_url` | publisher landing page |
| `type` | `article`, `preprint`, `book-chapter`, `dataset`, … |
| `open_access.oa_url`, `best_oa_location.pdf_url` | free full text |
| `abstract_inverted_index` | `{word: [positions]}` — reconstruct to text |
| `referenced_works[]` | OpenAlex ids this work cites (backward) |
| `cited_by_count`, `cited_by_api_url` | forward-citation count / query |

## Abstract reconstruction
`abstract_inverted_index` maps each word to its positions. Rebuild by sorting
`(position, word)` and joining. (OpenAlex omits raw abstracts for licensing reasons;
the inverted index is provided instead.) Some works have none → empty.

## Coverage vs Semantic Scholar
OpenAlex generally has **broader abstract + DOI + venue coverage** and is key-less; S2
has richer citation-influence signals and TLDRs. For metadata backfill, prefer OpenAlex;
for influential-citation snowballing, prefer S2.

# Zotero Web API — reference notes

Full docs: https://www.zotero.org/support/dev/web_api/v3/start

Only read this when the CLI's built-in subcommands don't cover what the user wants. The `raw` subcommand lets you hit any path under the configured library, so most gaps can be filled without modifying the script.

## Auth

All requests send `Zotero-API-Key: <key>` and `Zotero-API-Version: 3`. Keys are created at https://www.zotero.org/settings/security and can be scoped to specific libraries and read/write permissions.

## URL shape

Everything is rooted at either:
- `https://api.zotero.org/users/{userID}/...` — personal libraries
- `https://api.zotero.org/groups/{groupID}/...` — group libraries

The CLI picks the prefix based on `--library-type` / `ZOTERO_LIBRARY_TYPE`.

## Useful paths

| Path | What it returns |
|---|---|
| `/items` | All items (including child attachments/notes unless filtered). |
| `/items/top` | Top-level items only — no children. Often what you actually want. |
| `/items/{itemKey}` | One item. |
| `/items/{itemKey}/children` | Attachments and notes belonging to an item. |
| `/items/{itemKey}/file` | The binary contents of an attachment. |
| `/items/trash` | Trashed items. |
| `/collections` | All collections (flat). |
| `/collections/top` | Top-level collections only. |
| `/collections/{key}/collections` | Sub-collections of a collection. |
| `/collections/{key}/items` | Items in a collection. |
| `/tags` | All tags in the library with usage counts. |
| `/searches` | Saved searches. |
| `/keys/current` | Metadata about the API key in use — handy for debugging auth. |

## Query parameters worth knowing

- `limit` — max 100 per request.
- `start` — offset for pagination. The `Total-Results` response header tells you how many there are in total.
- `sort` / `direction` — e.g. `sort=date&direction=desc`.
- `q` + `qmode` — quick search. `qmode=everything` hits title, creator, year, notes, tags, and attachment fulltext. `qmode=titleCreatorYear` is narrower and faster.
- `tag` — repeatable; multiple tags AND together. Use `-tag` prefix to exclude, `tag1 || tag2` to OR.
- `itemType` — filter by type. Supports boolean syntax, e.g. `-attachment || note` to exclude both.
- `since` — a library version number; returns only items changed since that version. Useful for incremental sync.
- `format` — `json` (default), `atom`, `bibtex`, `ris`, `csljson`, `tei`, `csv`, etc. See the docs for the complete list.
- `include` — when `format=json`, you can request additional representations: `include=data,bib,citation`.
- `style` — CSL style name for formatted citations, e.g. `style=apa` with `include=bib`.
- `locale` — locale for formatted citations, e.g. `locale=en-US`.

## Pagination

Every list endpoint returns at most 100 items. The `Total-Results` response header has the real count. The CLI handles this automatically in `_paginate`; for one-off `raw` calls, add `--limit` and `--start` yourself in the path query string.

## Rate limits

Zotero rarely rate-limits but reserves the right. When it does, you'll see:
- `Backoff: <seconds>` on a normal 200 response — a polite request to slow down. The CLI honors this.
- `429 Too Many Requests` with `Retry-After: <seconds>` — a hard stop. The CLI retries with backoff up to 4 times.

For bulk reads, stay under ~1 request/second and you'll basically never hit limits.

## Library versioning

Every response includes a `Last-Modified-Version` header representing the library's current version number. You can pass it back as `If-Modified-Since-Version` on subsequent requests to get 304s for unchanged data. The CLI doesn't use this yet, but it's the correct way to do incremental sync.

## Writes (not wired into the CLI by default)

Write operations use POST/PUT/PATCH/DELETE and require an API key with write permission for the target library. The JSON payloads are described in https://www.zotero.org/support/dev/web_api/v3/write_requests. Don't add write support unless the user explicitly asks for it — accidental writes to a reference library are painful to undo.

## Finding a collection key in the desktop app

Right-click a collection in the Zotero desktop client → "Show Collection Key" (newer versions) or check the URL when you open it on zotero.org. The CLI's `collections` subcommand is usually faster.

## Finding the user ID

https://www.zotero.org/settings/security → scroll to "Your userID for use in API calls". It's a ~7-digit integer. This is *not* the same as your Zotero username.

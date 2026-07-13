# Setup & Reference

Six Claude Code skills for systematic-literature-review and scholarly research: discovery,
metadata enrichment, de-duplication, and full-text prep against Zotero and scholarly APIs.

- 5 **custom skills** (stdlib-Python CLIs) live in [`skills/`](../skills).
- 1 **marketplace plugin** (`exa`) is installed separately (below).

---

## 0. How Claude Code skills work

A skill is a folder under `~/.claude/skills/<name>/` with a `SKILL.md` (frontmatter + docs Claude
reads to decide when to use it) and a `scripts/` dir. You invoke them in natural language ("dedupe
these Zotero records", "convert the PDFs to text") — Claude runs the right script. Every script
also runs standalone with `--help`.

---

## 1. Prerequisites

- **Claude Code** installed.
- **Python 3.9+** — the custom skills are **stdlib-only** (no `pip install`).
- **poppler** (`pdftotext`) — only for `zotero-pdf-to-text`:
  - macOS: `brew install poppler` · Debian/Ubuntu: `sudo apt-get install poppler-utils`
- **direnv** (optional) — for per-project env vars; otherwise use your shell rc file.

---

## 2. Install the skills

**Option A — from a clone of this repo:**
```bash
mkdir -p ~/.claude/skills
cp -R skills/* ~/.claude/skills/
```

**Option B — from the release archive:** download `research-claude-code-skills.zip` from the
[Releases page](https://github.com/thurlow-research/ResearchClaudeCodeSkills/releases), then:
```bash
mkdir -p ~/.claude/skills
unzip research-claude-code-skills.zip -d ~/.claude/skills/
```

Then restart Claude Code so it discovers them. Verify:
```bash
ls ~/.claude/skills
#   openalex  semantic-scholar  zotero  zotero-merge-prep  zotero-pdf-to-text
```

For **exa** (web/grey-lit discovery), install the marketplace plugin instead:
```
/plugin install exa@claude-plugins-official     # inside Claude Code
```
Authenticate once via `/mcp` (browser OAuth) or the `EXA_API_KEY` path (§4).

---

## 3. Environment variables

Set these where your shell (and Claude Code) will see them — a project `.envrc` (direnv) or your
shell rc file. **Use your own keys; never commit or share a filled-in copy.**

```bash
# --- Zotero (required for the three zotero skills) ---
export ZOTERO_API_KEY=...            # your Zotero API key
export ZOTERO_LIBRARY_ID=...         # numeric group id, or your user id
export ZOTERO_LIBRARY_TYPE=group     # 'group' or 'user'

# --- data sources ---
export OPENALEX_API_KEY=...          # STRONGLY ADVISED — see note below
export OPENALEX_MAILTO=you@uni.edu   # your email (OpenAlex "polite pool")
export SEMANTIC_SCHOLAR_API_KEY=...  # optional (works keyless at lower rate)
export EXA_API_KEY=...               # for the exa plugin
```

> **Get an OpenAlex key — don't skip it.** OpenAlex is now credit-metered. **Without a key you
> will be throttled (HTTP 429) on any real workload** (anonymous ≈ 100 requests/day). A free
> account key raises that ~10× (~1,000/day). Signup takes ~30 seconds and is the difference
> between the enrichment skill working and hanging on rate limits.

### Where to get each key
| Key | Where | Notes |
|---|---|---|
| `ZOTERO_API_KEY` / `LIBRARY_ID` | zotero.org/settings/keys → *Create new private key* | For a **group** library grant that group; check **write** if you'll create/modify items. Library id = the number in `groups/NNNNNN`, or your userID on the same page. |
| `OPENALEX_API_KEY` | openalex.org/settings/api | **Strongly advised** (see box above). Free, 30-sec signup. |
| `SEMANTIC_SCHOLAR_API_KEY` | semanticscholar.org/product/api | Free; optional. Raises rate limits. |
| `EXA_API_KEY` | exa.ai → dashboard | For the plugin's API-key auth path. |

If you use direnv, put the block in `.envrc` and run `direnv allow .`. (An already-running Claude
session won't see edits until you restart it or `source` the file in the shell it launches from.)

---

## 4. The six skills

### Zotero — library operations

**`zotero`** — query & retrieve from the library (search items, list collections/tags, resolve
lineage; the read layer for PRISMA/screening reporting).
```bash
python3 ~/.claude/skills/zotero/scripts/zotero.py --help
```
Env: `ZOTERO_API_KEY`, `ZOTERO_LIBRARY_ID`, `ZOTERO_LIBRARY_TYPE`.

**`zotero-merge-prep`** — make duplicates safe to merge. Zotero's native *Merge Items* keeps only
the master's fields and won't group different item types (preprint vs journal). This unions
metadata + normalizes types first; you still run the merge in the client.
```bash
python3 scripts/merge_prep.py scan COLLECTION_KEY [--prep]
python3 scripts/merge_prep.py prep "Paper Title" | --keys K1,K2,K3
```
Env: Zotero (a **write** key). Optional `OPENALEX_MAILTO`/`OPENALEX_API_KEY` for gap-fill.

**`zotero-pdf-to-text`** — give each item a TXT next to its PDF (plain text is far cheaper for AI
reading). Sources the PDF locally or via API, runs `pdftotext`, uploads the TXT. Idempotent; test
one first.
```bash
python3 scripts/pdf_to_text.py --collection KEY --limit 1   # ALWAYS test one
python3 scripts/pdf_to_text.py --collection KEY             # batch
```
Env: Zotero (a **write** key). Requires `pdftotext`.

### External data sources

**`semantic-scholar`** — citation-graph search & backward/forward snowballing (by topic/DOI/
arXiv/title).
```bash
python3 ~/.claude/skills/semantic-scholar/scripts/s2.py --help
```
Env: `SEMANTIC_SCHOLAR_API_KEY` (optional). Cache: `~/.cache/claude-s2`.

**`openalex`** — metadata backstop + citation edges (abstracts, authors, DOI, real URLs, venue).
On-disk cached. See the strongly-advised key note in §3.
```bash
python3 ~/.claude/skills/openalex/scripts/openalex.py work "DOI-or-title"
python3 ~/.claude/skills/openalex/scripts/openalex.py abstract "arXiv:2504.21205"
python3 ~/.claude/skills/openalex/scripts/openalex.py enrich --ids-file ids.txt
```
Env: `OPENALEX_API_KEY` (strongly advised) + `OPENALEX_MAILTO`.

**`exa`** (marketplace plugin) — open-web / grey-lit discovery via Exa's hosted MCP.
Install via `/plugin install exa@claude-plugins-official`; auth via `/mcp` or `EXA_API_KEY`.

---

## 5. How they interlock (a typical review)

1. **Discover** — `semantic-scholar` (citation snowball) + `exa` (open web) surface candidates.
2. **Import** — into Zotero.
3. **Enrich** — `openalex` backfills missing abstracts/DOIs/URLs.
4. **De-duplicate** — `zotero-merge-prep` consolidates cross-type dups; merge in the client.
5. **Screen / triage** — `zotero` queries drive the keep/maybe/discard and core/context stages.
6. **Extract-prep** — `zotero-pdf-to-text` gives each included item a TXT for full-text AI reading.

---

## 6. Gotchas

- **OpenAlex throttling** — see §3. Get a key. The client prefers **IPv4** (Cloudflare 429s the
  shared IPv6 pool) and fails fast on 429 with a reset-time message instead of hanging. For very
  large sweeps, prefer Crossref/Semantic Scholar or chunk across days.
- **Zotero file upload** — there's no simple upload endpoint; `zotero-pdf-to-text` implements the
  4-step S3 form-POST flow. Always `--limit 1` first and confirm the TXT attachment has an `md5`.
- **Exa plugin auth can revert** — if you use the `EXA_API_KEY` header path, a `claude plugin
  update exa` overwrites the manifest; re-add the `Authorization` header, or use the `/mcp` OAuth
  flow.
- **`zotero-merge-prep` / `zotero-pdf-to-text` need a WRITE key** and modify records
  (non-destructively — fill fields / add attachments). **Back up first** (Zotero → *File → Export
  Library*); the destructive merge stays a manual client action.

---

## 7. Security

- These scripts contain **no keys** — they read everything from environment variables.
- **Never commit or share your `.envrc`** / filled-in env block. Each person uses their own keys.
- A Zotero **write** key can modify/delete items — scope it minimally and keep a library export
  backup before bulk writes.

# Claude Desktop / claude.ai variant

The same five skills as [`skills/`](../skills), repackaged for the **Claude Desktop app
and claude.ai** (Settings-uploaded skills running in Claude's code-execution sandbox)
instead of Claude Code. The Python CLIs are byte-identical; only each `SKILL.md` differs —
in this environment there is no shell profile, direnv, or `.env` file, so the skills are
taught to read API keys from your **project instructions** (or global preferences) and
pass them inline as same-named environment variables when running scripts.

**Do not edit `desktop/skills/` directly** — it is generated. Edit the source in
`skills/` (or the patch anchors in `scripts/build-desktop.py`) and rebuild:

```bash
python3 scripts/build-desktop.py
```

## Install

1. In the Claude app: **Settings → Capabilities** — enable **code execution** (required
   for skills), and set network access to **All domains** (the skills call the Zotero,
   Semantic Scholar, and OpenAlex APIs; the Team/Enterprise default of
   "package managers only" blocks them).
2. **Settings → Capabilities → Skills → Upload skill** — upload each zip from
   [`zips/`](zips) (one zip per skill).

## Provide your API keys

Add a block like this to the **project instructions** of the Claude project you'll work
in (or to your global preferences), using exactly these variable names:

```
API keys for research skills (pass inline as env vars when running skill scripts;
never repeat these values in a reply):

ZOTERO_API_KEY=...
ZOTERO_LIBRARY_ID=...
ZOTERO_LIBRARY_TYPE=group
SEMANTIC_SCHOLAR_API_KEY=...
OPENALEX_API_KEY=...
OPENALEX_MAILTO=you@example.edu
```

Notes:
- Only include the keys for the skills you use. `zotero-pdf-to-text` needs a **write**
  Zotero key; read-only work does not.
- Project instructions are visible to anyone the project is shared with — keep projects
  holding keys private, and use least-privilege keys.
- You can also paste a key into the chat for one conversation, but instructions are the
  persistent, "set once" path.

## Differences from the Claude Code versions

- **Credentials** come from project/global instructions (see above) instead of shell
  env vars or `~/.config/*/.env` files. Same variable names everywhere.
- **`zotero-pdf-to-text`** never sees a local `~/Zotero/storage`; it API-downloads every
  PDF (slower, but automatic). It also requires `pdftotext` (poppler) which may not be
  available in the sandbox — the skill checks first and says so if it can't run.
- **Caches** (`~/.cache/claude-*`) last only as long as the sandbox container, so
  repeat lookups across conversations re-fetch.
- **`exa`** is not included — it's a Claude Code marketplace plugin (hosted MCP server),
  not an uploadable skill; claude.ai has built-in web search instead.

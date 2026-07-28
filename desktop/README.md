# Claude Desktop / claude.ai variant

The same five skills as [`skills/`](../skills), repackaged for the **Claude Desktop app
and claude.ai** (Settings-uploaded skills running in Claude's code-execution sandbox)
instead of Claude Code, **plus Exa** (added as a native Connector rather than a skill zip
— see [below](#exa-connector-not-a-skill-zip)). The Python CLIs are byte-identical; only
each `SKILL.md` differs — in this environment there is no shell profile, direnv, or `.env`
file, so the skills are taught to read API keys from your **project instructions** (or
global preferences) and pass them inline as same-named environment variables when running
scripts.

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
   [`zips/`](zips) independently (one zip per skill — install only the ones you need).
3. For **Exa**, don't upload a zip — see [Exa connector](#exa-connector-not-a-skill-zip)
   below.

## Provide your API keys

Add a block like this to the **project instructions** of the Claude project you'll work
in (or to your global preferences), using exactly these variable names:

```
API keys for research skills (pass inline as env vars when running skill scripts;
never repeat these values in a reply):

ZOTERO_API_KEY_RO=...
ZOTERO_LIBRARY_ID=...
ZOTERO_LIBRARY_TYPE=group
SEMANTIC_SCHOLAR_API_KEY=...
OPENALEX_API_KEY=...
OPENALEX_MAILTO=you@example.edu
```

Notes:
- Only include the keys for the skills you use.
- **Don't add `ZOTERO_API_KEY_RW`** to stored instructions — Claude's project/global
  instructions have no secure secret storage, and a write-scoped key sitting there is
  readable by anyone the project is shared with. `zotero-merge-prep` and
  `zotero-pdf-to-text` are write-only tools (there's no read-only mode), so if you install
  them, paste `ZOTERO_API_KEY_RW=...` into the chat for that one conversation instead —
  never into instructions.
- Project instructions are visible to anyone the project is shared with — keep projects
  holding keys private, and use least-privilege keys (e.g. `ZOTERO_API_KEY_RO` there,
  never `_RW`).

## Exa connector (not a skill zip)

Exa is a hosted MCP server, not a skill — there's nothing to upload. Add it as a
**Connector** instead:

1. **Settings → Connectors** (or the Plugins tab) → search "Exa" → install, **or**
   Add custom connector → paste `https://mcp.exa.ai/mcp`.
2. **Fully quit and reopen** the Claude app — a window reload doesn't pick up a new
   connector.
3. **Auth — use OAuth, not an embedded API key**, since anyone this is shared with
   would otherwise see the key: leave the URL as `https://mcp.exa.ai/mcp` (or add
   `?login` / use `/mcp/oauth` to force the sign-in prompt) and let each person
   authenticate with their own Exa account. Only use the `?exaApiKey=...` query-param
   form for a single-user setup you control — never in an instructions block or anything
   shared with others.
4. Default-enabled tools are `web_search_exa` and `web_fetch_exa`; additional tools
   (e.g. `web_search_advanced_exa`) need an explicit `?tools=` query param — see
   [exa-labs/exa-mcp-server](https://github.com/exa-labs/exa-mcp-server).

## Differences from the Claude Code versions

- **Credentials** come from project/global instructions (see above) instead of shell
  env vars or `~/.config/*/.env` files. Same variable names everywhere.
- **`zotero-pdf-to-text`** never sees a local `~/Zotero/storage`; it API-downloads every
  PDF (slower, but automatic). It also requires `pdftotext` (poppler) which may not be
  available in the sandbox — the skill checks first and says so if it can't run.
- **Caches** (`~/.cache/claude-*`) last only as long as the sandbox container, so
  repeat lookups across conversations re-fetch.
- **`exa`** isn't packaged the same way as the other five — Claude Code installs it as a
  marketplace plugin; Claude Desktop/claude.ai add it as a Connector (above). Same
  underlying hosted MCP server either way.

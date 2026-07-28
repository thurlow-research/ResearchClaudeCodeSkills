# Setting Up the Ideal Research Environment

This repo's six skills (`zotero`, `zotero-merge-prep`, `zotero-pdf-to-text`, `arxiv`,
`semantic-scholar`, `openalex`) plus the `exa` plugin cover the SLR pipeline end to end:
discover -> import -> enrich -> de-duplicate -> screen -> extract-prep. This doc adds the
remaining piece — a handful of **external MCP connectors** that extend the same environment
with paper discovery, citation verification, and full-text access — and walks through getting
the whole thing running on **Claude Code** and on **Claude Desktop / claude.ai**, since the two
platforms wire credentials and connectors up differently.

## The full toolset

| Tool | What it adds | Kind | Auth |
|---|---|---|---|
| `zotero` | query/retrieve + `create-items` write path (the shared write target for query-only import skills) | repo skill | Zotero API key (RO for reads, RW for writes) |
| `zotero-merge-prep` | consolidate duplicates before Zotero's native merge | repo skill | Zotero **write** key |
| `zotero-pdf-to-text` | PDF -> TXT attachments for cheap AI reading | repo skill | Zotero **write** key + `pdftotext` |
| `arxiv` | query arXiv, emit a Zotero create-items plan (query-only, no write key) | repo skill | Zotero **read** key only |
| `semantic-scholar` | citation-graph search & snowballing | repo skill | S2 key (optional) |
| `openalex` | metadata/abstract/citation backstop | repo skill | OpenAlex key (strongly advised) |
| `exa` | open-web / grey-literature discovery | marketplace plugin (Code) / connector (Desktop) | Exa key or OAuth |
| alphaXiv | paper reading, discovery (`discover_papers`), PDF/codebase analysis | MCP connector | OAuth (or API key, single-user only) |
| Scite | citation classification — is a claim *supported*, *contrasted*, or just *mentioned* by later work | MCP connector | OAuth |
| Scholar Gateway | scholarly full-text/search gateway | MCP connector | account connector (set up once, syncs everywhere) |
| papersflow | paper workflow tooling (`doxa.papersflow.ai`) | MCP connector | pending — authenticates on first use |

None of the four MCP connectors are part of this repo — they're hosted services you connect to
directly, independent of anything in `skills/`. They sit at the discovery/verification/synthesis
ends of the pipeline, alongside `exa`/`semantic-scholar` and after `zotero-pdf-to-text`,
respectively — not in the middle of the Zotero import/dedupe chain.

**A note on evaluating new connectors before adding them.** Before registering anything here,
check whether it's a genuine hosted API from an identifiable provider (the four above all are)
versus a third-party "skill package" pulled from an individual's GitHub repo via a tool like
`npx skills add`. The latter is a materially different risk — it's markdown/scripts landing in
your global skills directory, not an OAuth-gated API call. One evaluated during this environment's
setup (`lingzhi227/agent-research-skills`, offering `literature-search`/`deep-research` skills)
turned out to hardcode the *author's own* local file paths and a plaintext API-key location
(`/Users/lingzhi/Code/keys.md`) directly into its instructions — harmless to run, but a clear
signal of unreviewed, not-hardened-for-distribution content. Read a skill's `SKILL.md` and scripts
before installing anything sourced this way; don't install on the strength of a blog post
recommending it.

---

## How to set up for Claude Code

### 1. Install this repo's skills
```bash
mkdir -p ~/.claude/skills
cp -R skills/* ~/.claude/skills/
# ...or from a release: unzip research-claude-code-skills.zip -d ~/.claude/skills/
```
Restart Claude Code, then verify:
```bash
ls ~/.claude/skills
#   arxiv  openalex  semantic-scholar  zotero  zotero-merge-prep  zotero-pdf-to-text
```
Full per-skill reference and env-var setup: [`docs/SETUP.md`](SETUP.md).

### 2. Install the Exa plugin
Inside Claude Code:
```
/plugin install exa@claude-plugins-official
```

### 3. Connect the external MCP servers
alphaXiv, Scite, and Scholar Gateway are **account-level Connectors** — set them up once via
Settings -> Connectors on claude.ai or the Desktop app (see the Desktop section below) and they
sync into Claude Code automatically. You don't need to run `claude mcp add` for these unless you
specifically want a machine-local or project-scoped copy instead.

For a connector not in that directory (like papersflow), register it directly with the CLI.
Use `--scope user` so it's available in every project, not just the one you happen to be in:
```bash
claude mcp add papersflow --transport http --scope user https://doxa.papersflow.ai/mcp
```
The same pattern works for alphaXiv/Scite if you'd rather manage them per-machine instead of as
account connectors:
```bash
claude mcp add alphaxiv --transport http --scope user https://api.alphaxiv.org/mcp/v1
claude mcp add scite    --transport http --scope user https://api.scite.ai/mcp
```
Any of these will show `! Needs authentication` until first use, at which point they trigger an
OAuth browser flow (or, for alphaXiv, you can instead pass a non-interactive API key with
`--header "Authorization: Bearer <key>"` — see its docs at alphaxiv.org/docs/mcp).

Verify everything:
```bash
claude mcp list
```

### 4. Set your API keys
See [`docs/SETUP.md` section 3](SETUP.md#3-environment-variables) — a `.envrc`/shell-rc block
for `ZOTERO_API_KEY_RO`/`_RW`, `ZOTERO_LIBRARY_ID`, `OPENALEX_API_KEY`,
`SEMANTIC_SCHOLAR_API_KEY`, `EXA_API_KEY`. Restart Claude Code (or `direnv allow .`) after editing.

---

## How to set up for Claude Desktop

### 1. Enable code execution + network access
Settings -> Capabilities: turn on **code execution** (required to run any uploaded skill), and
set network access to **All domains** (the skills call the Zotero/Semantic Scholar/OpenAlex APIs;
the default "package managers only" blocks them).

### 2. Upload this repo's skills
Settings -> Capabilities -> Skills -> Upload skill, one zip at a time from
[`desktop/zips/`](../desktop/zips) — install only the ones you need. Full detail, including why
these zips differ from the Claude Code versions (credentials come from project instructions, not
shell env vars), is in [`desktop/README.md`](../desktop/README.md).

### 3. Provide your API keys
Add a block to the Claude **project instructions** (or global preferences) — never a stored
write-scoped Zotero key if others will use the project; see
[`desktop/README.md`](../desktop/README.md#provide-your-api-keys) for the exact block and the
reasoning (no secure secret storage in instructions).

### 4. Add the connectors
- **Exa, alphaXiv, Scite, Scholar Gateway**: Settings -> Connectors (or the Plugins tab) -> search
  the name and install, or **Add custom connector** and paste the URL directly
  (`https://mcp.exa.ai/mcp`, `https://api.alphaxiv.org/mcp/v1`, `https://api.scite.ai/mcp`).
  Fully quit and reopen the app afterward — a window reload doesn't pick up a new connector.
- **papersflow**: same flow, Add custom connector -> `https://doxa.papersflow.ai/mcp`.
- Prefer **OAuth sign-in** over pasting an API key into the URL for anything set up on a shared
  or team project — an embedded key in a connector URL is visible to anyone the project is
  shared with; OAuth lets each person authenticate with their own account instead.

Since account-level Connectors sync to Claude Code automatically (see the Code section above),
setting these up once in Desktop covers both platforms for the four MCP connectors — only the
repo's own skills and Exa need a separate install step per platform.

---

## Quick reference: what's set up where

| | Claude Code | Claude Desktop |
|---|---|---|
| This repo's 6 skills | `cp -R skills/* ~/.claude/skills/` | upload each zip from `desktop/zips/` |
| Exa | `/plugin install exa@claude-plugins-official` | Settings -> Connectors |
| alphaXiv / Scite / Scholar Gateway | syncs automatically once added as a Connector | Settings -> Connectors (do it here) |
| papersflow | `claude mcp add --scope user ...` | Settings -> Connectors -> Add custom connector |
| API keys | shell env / `.envrc` / `~/.config/claude-zotero/.env` | project or global instructions |

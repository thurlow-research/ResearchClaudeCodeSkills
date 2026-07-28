# Setting Up the Ideal Research Environment

This repo's six skills plus the `exa` plugin cover the SLR pipeline end to end (see the
[repo README](../README.md#a-typical-review) for that pipeline) — this doc adds the
remaining piece, a handful of **external MCP connectors** that extend the same environment
with paper discovery, citation verification, and full-text access, and walks through getting
the *whole* environment running on **Claude Code** and on **Claude Desktop / claude.ai**,
since the two platforms wire credentials and connectors up differently.

This is the map; it delegates to the docs that already own each piece rather than repeating
them: [`README.md`](../README.md) and [`docs/SETUP.md`](SETUP.md) for the repo's own 6 skills
+ `exa`, [`desktop/README.md`](../desktop/README.md) for the Desktop packaging of both.

## The external MCP connectors

These are not part of this repo — they're hosted services you connect to directly, with no
relationship to anything in `skills/`. They sit at the discovery/verification/synthesis ends
of the pipeline, alongside `exa`/`semantic-scholar` and after `zotero-pdf-to-text`,
respectively — not in the middle of the Zotero import/dedupe chain.

| Connector | What it adds | Auth |
|---|---|---|
| alphaXiv | paper reading, discovery (`discover_papers`), PDF/codebase analysis | OAuth (or API key, single-user only) |
| Scite | citation classification — is a claim *supported*, *contrasted*, or just *mentioned* by later work | OAuth |
| Scholar Gateway | scholarly full-text/search gateway | account connector (set up once, syncs everywhere) |
| papersflow | paper workflow tooling (`doxa.papersflow.ai`) | pending — authenticates on first use |

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

### 1. Install this repo's skills + the Exa plugin
See [`docs/SETUP.md` §2](SETUP.md#2-install-the-skills) — `cp -R skills/* ~/.claude/skills/`
(or the release zip), then `/plugin install exa@claude-plugins-official`.

### 2. Connect the external MCP servers
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

Verify everything: `claude mcp list`.

### 3. Set your API keys
See [`docs/SETUP.md` §3](SETUP.md#3-environment-variables) for the repo skills' env vars
(`ZOTERO_API_KEY_RO`/`_RW`, `ZOTERO_LIBRARY_ID`, `OPENALEX_API_KEY`,
`SEMANTIC_SCHOLAR_API_KEY`, `EXA_API_KEY`).

---

## How to set up for Claude Desktop

### 1. Install this repo's skills + Exa
See [`desktop/README.md`](../desktop/README.md) end to end — enabling code execution/network
access, uploading each skill zip from `desktop/zips/`, providing API keys via project/global
instructions, and adding **Exa** as a Connector (it's a hosted MCP server, not a skill zip).

### 2. Add the other three connectors
Same Connectors flow as Exa — Settings -> Connectors (or the Plugins tab) -> search the name and
install, or **Add custom connector** and paste the URL directly:
- alphaXiv: `https://api.alphaxiv.org/mcp/v1`
- Scite: `https://api.scite.ai/mcp`
- papersflow: `https://doxa.papersflow.ai/mcp`

(Scholar Gateway is typically already present as an account connector — check Settings ->
Connectors before adding it again.) Fully quit and reopen the app afterward — a window reload
doesn't pick up a new connector.

Prefer **OAuth sign-in** over pasting an API key into a connector URL for anything set up on a
shared or team project — an embedded key is visible to anyone the project is shared with; OAuth
lets each person authenticate with their own account instead.

Since account-level Connectors sync to Claude Code automatically, setting these up once in
Desktop covers both platforms — only the repo's own skills need a separate install step per
platform.

---

## Quick reference: what's set up where

| | Claude Code | Claude Desktop |
|---|---|---|
| This repo's 6 skills | `cp -R skills/* ~/.claude/skills/` | upload each zip from `desktop/zips/` |
| Exa | `/plugin install exa@claude-plugins-official` | Settings -> Connectors |
| alphaXiv / Scite / Scholar Gateway | syncs automatically once added as a Connector | Settings -> Connectors (do it here) |
| papersflow | `claude mcp add --scope user ...` | Settings -> Connectors -> Add custom connector |
| API keys | shell env / `.envrc` / `~/.config/claude-zotero/.env` | project or global instructions |

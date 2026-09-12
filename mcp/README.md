# `mcp/` — local MCP server

`research_mcp.py` exposes this repo's skill CLIs as MCP tools over stdio, so they can be
called from **Claude Desktop / Cowork cloud sessions** without pasting API keys into the
Claude instructions.

## The problem it solves

Cowork cloud sessions run in an ephemeral Linux container that cannot see this Mac's
filesystem or shell profile. The skills read their keys from environment variables, so
until now the only way to get keys into a cloud session was to paste them into the Claude
preferences — where they sit in every session's system prompt.

Locally-installed MCP servers *are* proxied into cloud sessions, surfacing as
`mcp__remote-devices__{server}__*`. So a server running here can hold the keys and do the
work, while the cloud session only ever sees tool calls and results.

macOS GUI apps don't source `~/.zshrc`, so the server hydrates itself: at startup it runs
`$SHELL -l -i -c 'env -0'`, harvests the credential variables, and merges in anything it's
missing. Variables already set (e.g. via an `env` block in the client config) win.

**The keys are still used.** Semantic Scholar's anonymous pool is shared and returns 429s
under snowballing; OpenAlex without a key is throttled to roughly 100 requests/day. This
changes where keys are stored, not whether they're sent.

## No vendored scripts

`resolve_script()` searches, in order:

1. `$RESEARCH_MCP_SKILLS_DIR`
2. `../skills` — this repo, the canonical source
3. `~/.claude/skills/synced`, `~/.claude/skills`
4. the Claude Desktop application-support skill dirs

`skills/` stays the single source of truth. Editing a skill takes effect on the next tool
call — no rebuild, no duplicate to drift.

## Tools

| Tool | Wraps | Highlights |
|---|---|---|
| `zotero` | `skills/zotero/scripts/zotero.py` | collections, items, count, search, export, tag-add, create-items, prisma, trace, superseded |
| `openalex` | `skills/openalex/scripts/openalex.py` | work, abstract, enrich, search, cites, references |
| `semantic_scholar` | `skills/semantic-scholar/scripts/s2.py` | snowball, search, bulk, paper, citations, references, batch |
| `zotero_merge_prep` | `skills/zotero-merge-prep/scripts/merge_prep.py` | scan, find, prep |
| `zotero_pdf_to_text` | `skills/zotero-pdf-to-text/scripts/pdf_to_text.py` | PDF → TXT child attachments |
| `arxiv_fetch` | `skills/arxiv/scripts/fetch_arxiv_query.py` | query → Zotero create-items plan |
| `arxiv_count` | `skills/arxiv/scripts/check_count.py` | size a query before fetching |

Each takes `args` (array of strings, passed straight to the CLI — no shell, no quoting) and
optional `stdin` for the commands that read ids that way. Dry-run defaults on the write
paths are untouched.

## Install

```sh
# 1. keys in ~/.zshrc (this repo commits none — see the root README's Security section)
# Create the keys account-wide (personal library + all groups): one RO/RW pair
# then covers every library — no per-library or per-collection keys.
export ZOTERO_API_KEY_RO=...
export ZOTERO_API_KEY_RW=...          # only if you want writes
export ZOTERO_USER_ID=1234567         # your userID → personal library (--library user)
export ZOTERO_SLR_LIBRARY_ID=6505702  # named group libraries (--library SLR), repeatable
# Libraries and their collections can also live in a YAML registry
# (~/.config/claude-zotero/libraries.yml, or $ZOTERO_LIBRARIES_FILE); generate it with:
#   python3 skills/zotero/scripts/zotero.py libraries --sync
# After that, --collection accepts collection *names* as well as 8-char keys.
export OPENALEX_API_KEY=...
export OPENALEX_MAILTO=sthurlow@purdue.edu
export SEMANTIC_SCHOLAR_API_KEY=...

# 2. smoke-test — must print the 7 tool names, and stderr must say "hydrated N var(s)" with N > 0
printf '%s\n' \
  '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{}}}' \
  '{"jsonrpc":"2.0","id":2,"method":"tools/list"}' \
  | python3 mcp/research_mcp.py
```

Then register it in `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "research": {
      "command": "python3",
      "args": ["/Users/scott/Code/Thurlow-Research/ResearchClaudeCodeSkills/mcp/research_mcp.py"]
    }
  }
}
```

There is deliberately no `env` block — that's the point.

Restart Claude Desktop, start a **new** Cowork session, and confirm
`mcp__remote-devices__research__zotero` is present and returns a real collection list.

## Only then

Remove the key lines from the Claude preferences, and rotate them — they've been sitting in
the system prompt. Removing them before the tools verify will break Zotero outright and drop
OpenAlex/S2 to anonymous limits.

## Notes

- Credential values ≥12 chars are redacted from tool output, so a script that echoes a key
  in a traceback can't leak it into the model's context. `*_MAILTO`, `*_ID`, and `*_TYPE`
  are exempt as non-secret.
- Timeout defaults to 900s (`RESEARCH_MCP_TIMEOUT`), sized for long snowball runs.
- Stdlib only, consistent with the rest of the repo — no `pip install`, no venv.
- Adding a tool is one dict entry in `TOOLS`, not a new server.

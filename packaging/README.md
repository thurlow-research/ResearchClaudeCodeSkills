# `packaging/` — installable Claude Desktop extension

Builds `mcp/research_mcp.py` and `skills/` into a single `.mcpb` bundle that installs from
**Claude Desktop → Settings → Extensions**, with API keys entered in a form instead of a config file.

## Why bother, given the config-file route already works

Registering the server in `claude_desktop_config.json` works and needs no packaging. The extension buys
three things:

1. **Keys leave plaintext storage.** The config route reads keys from the environment — `~/.zshrc` on
   macOS, or user environment variables on Windows, which live unencrypted in `HKCU\Environment` and are
   readable by anything running as you. Extension config fields marked `sensitive` are held by the
   host's secure credential storage instead.
2. **No JSON editing, no environment variables.** Install, fill four required fields, done. That matters
   most on a machine where you don't already have a shell profile carrying the keys.
3. **One artifact per machine.** The config route needs the repo cloned at a known path; the bundle
   carries the server and the skills together.

**Both routes remain supported**, and the server behaves the same under either.

## Build

```sh
bash scripts/build-mcpb.sh                     # → releases/research-skills.mcpb          (python3)
bash scripts/build-mcpb.sh --python python     # → releases/research-skills-windows.mcpb  (python)
```

### Why the interpreter is a build-time flag

`mcp_config.command` is a literal string in the manifest, and **no single value is correct everywhere**:
macOS ships `python3` and frequently has no `python`; the python.org Windows installer ships `python`
and usually no `python3`. Guessing wrong fails at launch with an unhelpful error. So the interpreter is
chosen when the bundle is built and recorded in the filename. Build both if you run both platforms.

For a non-standard interpreter, pass its full path:

```sh
bash scripts/build-mcpb.sh --python "C:/Users/scott/AppData/Local/Programs/Python/Python312/python.exe"
```

## Bundle layout

```
research-skills.mcpb
├── manifest.json
├── server/
│   └── research_mcp.py
└── skills/
    ├── zotero/ · openalex/ · semantic-scholar/ · arxiv/ …
```

`server/` sits one level down deliberately: the server resolves skills via
`Path(__file__).parent.parent / "skills"`, so this layout works **without patching it**. The manifest
also sets `RESEARCH_MCP_SKILLS_DIR` explicitly, so resolution survives if that relative assumption ever
changes.

Nothing is vendored twice — `skills/` is copied from the repo at build time, so the repo stays the
single source of truth. Rebuild after editing a skill.

## Configuration fields

**Required:** Zotero library ID · library type · Zotero read-only API key · OpenAlex contact email.

**Optional:** Zotero read-write key (leave blank to keep the install read-only) · OpenAlex API key ·
Semantic Scholar key · Exa key.

**A blank optional field does not disable the login-shell fallback.** On macOS and Linux the server still
harvests keys from your login shell for anything the form left empty, so an existing `~/.zshrc` setup
keeps working. This required a fix to the hydration guard — it now treats an empty value as absent,
because an extension substitutes `""` for unfilled optional fields and a blank must not shadow a real
value.

On Windows there is no login shell to harvest, so the form is the only source. That is the platform
where this packaging earns its keep.

## Build-time guards

The build refuses to produce a bundle if it finds:

- anything shaped like a credential literal in the staged tree;
- `.txt` files over 40 KB under `skills/` — paper full texts must never ship in a public artifact.

Editor debris (`*.bak-*`, `__pycache__`, `.DS_Store`, dotfiles) is stripped rather than shipped.

## Verifying an install

After installing, confirm all **seven** tools are present and that a read actually reaches Zotero — a
tool list alone does not prove skill resolution worked. The equivalent check outside Claude:

```sh
cd /tmp && unzip -q path/to/research-skills.mcpb -d bundle && cd bundle
printf '%s\n' \
  '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{}}}' \
  '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"zotero","arguments":{"args":["--collection","<KEY>","count"]}}}' \
  | ZOTERO_API_KEY_RO=… ZOTERO_LIBRARY_ID=… ZOTERO_LIBRARY_TYPE=group \
    RESEARCH_MCP_SKILLS_DIR="$PWD/skills" python3 server/research_mcp.py
```

A bare integer with `isError: false` means the whole path works.

## Status

Built and verified on macOS: bundle installs, all seven tools list, and a live Zotero count returns
correctly through the packaged skills tree. **The Windows bundle is built but untested** — the
interpreter name and the host's credential storage behaviour both need confirming on that machine.

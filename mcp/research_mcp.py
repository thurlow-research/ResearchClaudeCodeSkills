#!/usr/bin/env python3
"""
research-mcp — a local stdio MCP server exposing Scott's research skill CLIs.

Why this exists
---------------
Cowork sessions run in an ephemeral cloud container that cannot see your Mac's
shell profile, so API keys had to be pasted into the Claude instructions to
reach the skills. This server flips that around: it runs *on your Mac*, gets
proxied into cloud sessions over the device bridge, and hydrates its own
environment from your login shell. Keys live in ~/.zshrc and nowhere else.

Zero third-party dependencies: raw JSON-RPC 2.0 over stdin/stdout, stdlib only.
"""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
import threading
from pathlib import Path

SERVER_NAME = "research"
SERVER_VERSION = "1.0.0"
DEFAULT_PROTOCOL = "2025-06-18"
CALL_TIMEOUT = int(os.environ.get("RESEARCH_MCP_TIMEOUT", "900"))

# Environment variables this server cares about. Hydrated from the login shell.
CREDENTIAL_VARS = (
    "ZOTERO_API_KEY",
    "ZOTERO_API_KEY_RO",
    "ZOTERO_API_KEY_RW",
    "ZOTERO_LIBRARY_ID",
    "ZOTERO_LIBRARY_TYPE",
    "ZOTERO_COLLECTION_KEY",
    "OPENALEX_API_KEY",
    "OPENALEX_MAILTO",
    "SEMANTIC_SCHOLAR_API_KEY",
    "EXA_API_KEY",
)

# Named libraries are matched by pattern rather than listed, so adding a new
# ZOTERO_<NAME>_LIBRARY_ID to ~/.zshrc needs no change here.
CREDENTIAL_VAR_SUFFIXES = ("_LIBRARY_ID", "_LIBRARY_TYPE")


def is_credential_var(key: str) -> bool:
    if key in CREDENTIAL_VARS:
        return True
    return key.startswith("ZOTERO_") and key.endswith(CREDENTIAL_VAR_SUFFIXES)


# Values that must never appear in tool output returned to the model.
_SECRET_VALUES: list[str] = []

log_lock = threading.Lock()


def log(msg: str) -> None:
    """Diagnostics go to stderr; stdout is reserved for the JSON-RPC stream."""
    with log_lock:
        print(f"[research-mcp] {msg}", file=sys.stderr, flush=True)


# ---------------------------------------------------------------------------
# Environment hydration
# ---------------------------------------------------------------------------

def hydrate_env_from_login_shell() -> None:
    """
    GUI-launched processes on macOS do not source ~/.zshrc, so a server spawned
    by the Claude desktop app starts with a bare environment. Run the user's
    login shell once, harvest its environment, and merge in anything we're
    missing. This is what makes ~/.zshrc the single source of truth for keys.

    Explicitly-set variables win, so an `env` block in the MCP client config
    still overrides the shell profile.
    """
    shell = os.environ.get("SHELL", "/bin/zsh")
    try:
        proc = subprocess.run(
            [shell, "-l", "-i", "-c", "env -0"],
            capture_output=True,
            # CRITICAL: an interactive shell inherits our stdin unless told
            # otherwise, and our stdin is the live JSON-RPC stream. Without
            # DEVNULL the shell can consume protocol frames.
            stdin=subprocess.DEVNULL,
            timeout=20,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        log(f"could not hydrate from login shell ({exc}); using inherited env only")
        return

    if proc.returncode != 0:
        log(f"login shell exited {proc.returncode}; using inherited env only")

    harvested = 0
    for entry in proc.stdout.split(b"\0"):
        if not entry or b"=" not in entry:
            continue
        key, _, value = entry.decode("utf-8", "replace").partition("=")
        # Treat an empty value as absent. An MCPB extension substitutes
        # ${user_config.x} to "" for optional fields the user left blank, and a
        # blank must not shadow a real value in the login shell.
        if is_credential_var(key) and not os.environ.get(key):
            os.environ[key] = value
            harvested += 1

    present = [v for v in CREDENTIAL_VARS if os.environ.get(v)]
    log(f"hydrated {harvested} var(s) from {shell}; {len(present)} credential var(s) available")
    missing = [v for v in ("ZOTERO_API_KEY_RO", "OPENALEX_MAILTO") if not os.environ.get(v)]
    if missing:
        log(f"WARNING: expected but not found: {', '.join(missing)}")

    for var in [k for k in os.environ if is_credential_var(k)]:
        value = os.environ.get(var, "")
        # Only redact things long enough to actually be secrets, and never
        # redact the non-secret contact/id fields.
        if len(value) >= 12 and not var.endswith(("_MAILTO", "_ID", "_TYPE")):
            _SECRET_VALUES.append(value)


def redact(text: str) -> str:
    for secret in _SECRET_VALUES:
        text = text.replace(secret, "[REDACTED]")
    return text


# ---------------------------------------------------------------------------
# Locating the skill scripts
# ---------------------------------------------------------------------------

# This file lives at <repo>/mcp/research_mcp.py, so the repo's canonical skill
# sources are one level up. No scripts are vendored here — skills/ is the single
# source of truth, and this server is a thin transport in front of it.
REPO_SKILLS = Path(__file__).resolve().parent.parent / "skills"

SKILL_ROOT_CANDIDATES = [
    Path(p).expanduser()
    for p in (
        os.environ.get("RESEARCH_MCP_SKILLS_DIR", ""),
        str(REPO_SKILLS),
        "~/.claude/skills/synced",
        "~/.claude/skills",
        "~/Library/Application Support/Claude/skills/synced",
        "~/Library/Application Support/Claude/skills",
    )
    if p
]


def resolve_script(skill: str, script: str) -> Path:
    """
    Resolve against the repo's skills/ tree first, then any installed copies.
    Nothing is vendored, so a skill edit takes effect on the next call with no
    rebuild and no chance of a stale duplicate drifting out of sync.
    """
    for root in SKILL_ROOT_CANDIDATES:
        candidate = root / skill / "scripts" / script
        if candidate.is_file():
            return candidate
    raise FileNotFoundError(
        f"could not locate {skill}/scripts/{script}. Searched: "
        + ", ".join(str(r) for r in SKILL_ROOT_CANDIDATES)
    )


# ---------------------------------------------------------------------------
# Tool definitions
# ---------------------------------------------------------------------------

def _args_schema(example: str) -> dict:
    return {
        "type": "object",
        "properties": {
            "args": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Argument vector passed straight to the CLI, one token per "
                    f"element. No shell is involved, so quoting is unnecessary. "
                    f"Example: {example}"
                ),
            },
            "stdin": {
                "type": "string",
                "description": "Optional text piped to the script's stdin (used by "
                               "openalex enrich and s2 batch, which accept ids that way).",
            },
        },
        "required": ["args"],
    }


TOOLS = [
    {
        "name": "zotero",
        "skill": "zotero",
        "script": "zotero.py",
        "description": (
            "Zotero library operations via the Zotero Web API. Credentials come from "
            "the local shell, not from the caller.\n"
            "MULTIPLE LIBRARIES: pass --library NAME (e.g. --library SLR) to target a "
            "specific library by name. Run the `libraries` subcommand first to see what "
            "is configured. Without it, commands hit the default library, which is "
            "usually NOT the one you want.\n"
            "Subcommands: collections | items | count | item KEY | search QUERY | tags | "
            "export | attachment KEY | cache | tag-add | create-items | prisma | review | "
            "dedupe | trace KEY | superseded.\n"
            "Writes (tag-add, create-items, update-items) are DRY-RUN unless --commit is "
            "passed. update-items edits fields on existing records via a plan file mapping "
            "item keys to field/value pairs.\n"
            "Use `count` for any 'how many' question — it returns a bare integer instead "
            "of item bodies. Prefer --format table or --fields to keep output small."
        ),
        "inputSchema": _args_schema('["items", "--collection", "ABCD1234", "--format", "table"]'),
    },
    {
        "name": "openalex",
        "skill": "openalex",
        "script": "openalex.py",
        "description": (
            "OpenAlex metadata and citation lookups (~250M works). Uses the polite pool "
            "via OPENALEX_MAILTO plus OPENALEX_API_KEY from the local shell, which is what "
            "keeps bulk enrichment off the anonymous rate limit.\n"
            "Subcommands: work ID | abstract ID | enrich [ID ...] | search QUERY | "
            "cites ID | references ID | cache {stats,clear}.\n"
            "IDs accept DOI:10.x/y, ARXIV:1234.5678, OpenAlex ids, or titles. "
            "`enrich` is the backfill workhorse and reads ids from stdin, one per line."
        ),
        "inputSchema": _args_schema('["work", "DOI:10.1145/3597503"]'),
    },
    {
        "name": "semantic_scholar",
        "skill": "semantic-scholar",
        "script": "s2.py",
        "description": (
            "Semantic Scholar academic graph — search and citation snowballing. "
            "SEMANTIC_SCHOLAR_API_KEY is read from the local shell; without it the "
            "shared anonymous pool throws 429s on multi-hop expansion.\n"
            "Subcommands: snowball SEED [SEED ...] | search QUERY | bulk QUERY | paper ID | "
            "citations ID | references ID | batch [ID ...] | recommend ID | author QUERY | cache.\n"
            "`snowball` is the headline workflow: multi-hop forward+backward expansion "
            "from seed papers into a candidate pool."
        ),
        "inputSchema": _args_schema('["snowball", "DOI:10.1016/j.infsof.2008.09.009", "--hops", "1"]'),
    },
    {
        "name": "zotero_merge_prep",
        "skill": "zotero-merge-prep",
        "script": "merge_prep.py",
        "description": (
            "Consolidate duplicate Zotero records BEFORE running Zotero's native Merge "
            "Items, so the merge is lossless (native merge keeps only the master's field "
            "values and never groups across item types).\n"
            "Subcommands: scan COLLECTION_KEY [--prep] | find \"TITLE\" | prep \"TITLE\" "
            "[--dry-run] | prep --keys K1,K2,K3.\n"
            "`prep` unions metadata across copies, gap-fills from OpenAlex, and normalizes "
            "item types with orig-type:/orig-date: lineage tags. It writes to Zotero."
        ),
        "inputSchema": _args_schema('["find", "Attention is all you need"]'),
    },
    {
        "name": "zotero_pdf_to_text",
        "skill": "zotero-pdf-to-text",
        "script": "pdf_to_text.py",
        "description": (
            "Convert Zotero items' PDF attachments into TXT child attachments, so each "
            "item ends up with both. TXT is far cheaper to read downstream than PDF.\n"
            "Flags: --collection KEY[,KEY...] | --items KEY[,KEY...] | --limit N | --dry-run.\n"
            "Idempotent (skips items that already have a .txt). ALWAYS test with "
            "--limit 1 before a full run."
        ),
        "inputSchema": _args_schema('["--collection", "ABCD1234", "--limit", "1"]'),
    },
    {
        "name": "arxiv_fetch",
        "skill": "arxiv",
        "script": "fetch_arxiv_query.py",
        "description": (
            "Run an arXiv API query and build a Zotero create-items plan (query-only; it "
            "never writes to Zotero itself — hand the resulting plan to the `zotero` tool's "
            "create-items subcommand).\n"
            "Flags: --query \"...\" --collection \"Q-arXiv-NN\" --workdir PATH."
        ),
        "inputSchema": _args_schema('["--query", "all:\\"vibe coding\\"", "--collection", "Q-arXiv-07", "--workdir", "~/slr/arxiv/q-arxiv-07"]'),
    },
    {
        "name": "arxiv_count",
        "skill": "arxiv",
        "script": "check_count.py",
        "description": (
            "Return the result count for an arXiv query string without fetching records. "
            "Always run this before arxiv_fetch to size a query."
        ),
        "inputSchema": _args_schema('["all:\\"human oversight\\" AND all:\\"LLM\\""]'),
    },
]

TOOLS_BY_NAME = {t["name"]: t for t in TOOLS}


def public_tools() -> list[dict]:
    return [
        {"name": t["name"], "description": t["description"], "inputSchema": t["inputSchema"]}
        for t in TOOLS
    ]


# ---------------------------------------------------------------------------
# Tool execution
# ---------------------------------------------------------------------------

def run_tool(name: str, arguments: dict) -> tuple[str, bool]:
    spec = TOOLS_BY_NAME.get(name)
    if spec is None:
        return f"Unknown tool: {name}", True

    args = arguments.get("args", [])
    if not isinstance(args, list) or not all(isinstance(a, str) for a in args):
        return "`args` must be an array of strings.", True

    try:
        script = resolve_script(spec["skill"], spec["script"])
    except FileNotFoundError as exc:
        return str(exc), True

    cmd = [sys.executable, str(script), *args]
    log(f"exec: {shlex.join(cmd)}")

    try:
        proc = subprocess.run(
            cmd,
            input=arguments.get("stdin", "") or None,
            capture_output=True,
            text=True,
            timeout=CALL_TIMEOUT,
            cwd=str(script.parent.parent),
            env=os.environ,
        )
    except subprocess.TimeoutExpired:
        return f"Timed out after {CALL_TIMEOUT}s: {shlex.join(cmd)}", True
    except OSError as exc:
        return f"Failed to launch {script}: {exc}", True

    out = proc.stdout.strip()
    err = proc.stderr.strip()

    if proc.returncode != 0:
        body = f"exit {proc.returncode}\n"
        if err:
            body += f"stderr:\n{err}\n"
        if out:
            body += f"stdout:\n{out}"
        return redact(body.strip()), True

    if err:
        out = f"{out}\n\n[stderr]\n{err}" if out else f"[stderr]\n{err}"
    return redact(out) or "(no output)", False


# ---------------------------------------------------------------------------
# JSON-RPC plumbing
# ---------------------------------------------------------------------------

def respond(msg_id, result=None, error=None) -> None:
    payload = {"jsonrpc": "2.0", "id": msg_id}
    if error is not None:
        payload["error"] = error
    else:
        payload["result"] = result
    sys.stdout.write(json.dumps(payload) + "\n")
    sys.stdout.flush()


def handle(msg: dict) -> None:
    method = msg.get("method")
    msg_id = msg.get("id")
    params = msg.get("params") or {}

    # Notifications carry no id and must not be answered.
    if msg_id is None:
        log(f"notification: {method}")
        return

    if method == "initialize":
        requested = params.get("protocolVersion") or DEFAULT_PROTOCOL
        respond(msg_id, {
            "protocolVersion": requested,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
        })

    elif method == "tools/list":
        respond(msg_id, {"tools": public_tools()})

    elif method == "tools/call":
        name = params.get("name", "")
        arguments = params.get("arguments") or {}
        text, is_error = run_tool(name, arguments)
        respond(msg_id, {"content": [{"type": "text", "text": text}], "isError": is_error})

    elif method == "ping":
        respond(msg_id, {})

    else:
        respond(msg_id, error={"code": -32601, "message": f"Method not found: {method}"})


def main() -> None:
    hydrate_env_from_login_shell()
    log(f"ready — {len(TOOLS)} tools")
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError as exc:
            log(f"bad JSON: {exc}")
            continue
        try:
            handle(msg)
        except Exception as exc:  # never let one bad call kill the server
            log(f"handler error: {exc!r}")
            if msg.get("id") is not None:
                respond(msg["id"], error={"code": -32603, "message": str(exc)})


if __name__ == "__main__":
    main()

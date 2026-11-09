"""research_mcp: subprocess stdin handling, redaction and JSON-RPC dispatch."""

from __future__ import annotations

import json
import subprocess
import sys

import pytest

from conftest import load_script


@pytest.fixture
def mcp(tmp_path, monkeypatch):
    module = load_script("mcp/research_mcp.py")
    # A throwaway skill whose script reports exactly what it saw on stdin.
    script = tmp_path / "skills" / "echo-skill" / "scripts" / "echo.py"
    script.parent.mkdir(parents=True)
    script.write_text(
        "import sys, json\n"
        "data = sys.stdin.read()\n"
        "print(json.dumps({'stdin': data, 'argv': sys.argv[1:]}))\n"
        "if '--fail' in sys.argv: sys.exit(3)\n"
    )
    monkeypatch.setattr(module, "SKILL_ROOT_CANDIDATES", [tmp_path / "skills"])
    spec = {"name": "echo", "skill": "echo-skill", "script": "echo.py",
            "description": "test", "inputSchema": module._args_schema("[]")}
    monkeypatch.setattr(module, "TOOLS", [spec])
    monkeypatch.setattr(module, "TOOLS_BY_NAME", {"echo": spec})
    module._SECRET_VALUES.clear()
    return module


def call(mcp, arguments):
    text, is_error = mcp.run_tool("echo", arguments)
    return text, is_error


# ---------------------------------------------------------------------------
# stdin plumbing (issue #7)
# ---------------------------------------------------------------------------

def test_stdin_text_reaches_the_script(mcp):
    text, is_error = call(mcp, {"args": ["--plan", "-"], "stdin": '{"K": {"date": "2024"}}'})
    assert not is_error
    assert json.loads(text) == {"stdin": '{"K": {"date": "2024"}}', "argv": ["--plan", "-"]}


@pytest.mark.parametrize("arguments", [{"args": []}, {"args": [], "stdin": ""}, {"args": [], "stdin": None}])
def test_without_stdin_the_child_gets_devnull(mcp, monkeypatch, arguments):
    seen = {}
    real_run = subprocess.run

    def spy(cmd, **kwargs):
        seen.update(kwargs)
        return real_run(cmd, **kwargs)

    monkeypatch.setattr(mcp.subprocess, "run", spy)
    text, is_error = call(mcp, arguments)
    assert not is_error
    assert seen.get("stdin") is subprocess.DEVNULL
    assert "input" not in seen
    assert json.loads(text)["stdin"] == ""


def test_without_stdin_the_child_never_reads_the_servers_stdin(mcp, monkeypatch, tmp_path):
    # Point fd 0 at a file holding a fake JSON-RPC frame. Before the fix the
    # child inherited it and would have consumed the frame.
    frame = tmp_path / "frame.jsonl"
    frame.write_text('{"jsonrpc": "2.0", "id": 99, "method": "ping"}\n')
    with frame.open() as fh:
        monkeypatch.setattr(sys, "stdin", fh)
        import os
        saved = os.dup(0)
        os.dup2(fh.fileno(), 0)
        try:
            text, is_error = call(mcp, {"args": []})
        finally:
            os.dup2(saved, 0)
            os.close(saved)
    assert not is_error
    assert json.loads(text)["stdin"] == ""


def test_non_string_stdin_is_rejected(mcp):
    assert call(mcp, {"args": [], "stdin": {"K": 1}}) == ("`stdin` must be a string.", True)


def test_non_string_args_are_rejected(mcp):
    assert call(mcp, {"args": ["ok", 3]}) == ("`args` must be an array of strings.", True)
    assert call(mcp, {"args": "items"}) == ("`args` must be an array of strings.", True)


# ---------------------------------------------------------------------------
# Results, errors and redaction
# ---------------------------------------------------------------------------

def test_nonzero_exit_is_an_error_with_stdout(mcp):
    text, is_error = call(mcp, {"args": ["--fail"]})
    assert is_error
    assert text.startswith("exit 3\nstdout:\n")


def test_unknown_tool(mcp):
    assert mcp.run_tool("nope", {"args": []}) == ("Unknown tool: nope", True)


def test_missing_script(mcp, monkeypatch, tmp_path):
    monkeypatch.setattr(mcp, "SKILL_ROOT_CANDIDATES", [tmp_path / "empty"])
    text, is_error = call(mcp, {"args": []})
    assert is_error and "could not locate echo-skill/scripts/echo.py" in text


def test_secrets_are_redacted_from_output(mcp):
    mcp._SECRET_VALUES.append("sekrit-value-1234")
    text, _ = call(mcp, {"args": ["sekrit-value-1234"]})
    assert "sekrit-value-1234" not in text
    assert "[REDACTED]" in text


@pytest.mark.parametrize("key,expected", [
    ("ZOTERO_API_KEY_RW", True),
    ("ZOTERO_SLR_LIBRARY_ID", True),
    ("ZOTERO_SLR_LIBRARY_TYPE", True),
    ("EXA_API_KEY", True),
    ("ZOTERO_SOMETHING_ELSE", False),
    ("PATH", False),
])
def test_is_credential_var(mcp, key, expected):
    assert mcp.is_credential_var(key) is expected


# ---------------------------------------------------------------------------
# JSON-RPC dispatch
# ---------------------------------------------------------------------------

@pytest.fixture
def rpc(mcp, capsys):
    def send(msg):
        capsys.readouterr()
        mcp.handle(msg)
        raw = capsys.readouterr().out
        return json.loads(raw) if raw else None

    return send


def test_initialize_echoes_protocol(rpc):
    resp = rpc({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2099-01-01"}})
    assert resp["result"]["protocolVersion"] == "2099-01-01"
    assert resp["result"]["serverInfo"]["name"] == "research"


def test_tools_list_hides_internal_fields(rpc):
    tools = rpc({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})["result"]["tools"]
    assert tools == [{"name": "echo", "description": "test", "inputSchema": tools[0]["inputSchema"]}]


def test_tools_call_passes_stdin(rpc):
    resp = rpc({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                "params": {"name": "echo", "arguments": {"args": [], "stdin": "abc"}}})
    assert resp["result"]["isError"] is False
    assert json.loads(resp["result"]["content"][0]["text"])["stdin"] == "abc"


def test_notifications_get_no_reply(rpc):
    assert rpc({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None


def test_unknown_method(rpc):
    assert rpc({"jsonrpc": "2.0", "id": 4, "method": "bogus"})["error"]["code"] == -32601


def test_ping(rpc):
    assert rpc({"jsonrpc": "2.0", "id": 5, "method": "ping"})["result"] == {}

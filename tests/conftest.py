"""Shared fixtures.

The skills are standalone scripts, not an installed package, so tests load
them by path. Every test runs with HOME, the cache dir and the library
registry pointed at a temp dir and all ZOTERO_* variables cleared, so a
developer's real ~/.config/claude-zotero/.env or shell keys can never leak in
(or be written to).
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent


def load_script(relpath: str):
    """Import a repo script as a fresh module (no state shared across tests).

    The module is named after its path (skills/zotero/scripts/zotero.py ->
    skills.zotero.scripts.zotero) because that is how mutmut keys mutants.
    """
    name = relpath[:-3].replace("/", ".")
    spec = importlib.util.spec_from_file_location(name, REPO / relpath)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(autouse=True)
def isolated_env(tmp_path, monkeypatch):
    import os

    for key in list(os.environ):
        if key.startswith(("ZOTERO_", "OPENALEX_", "SEMANTIC_SCHOLAR_", "EXA_", "RESEARCH_MCP_")):
            monkeypatch.delenv(key, raising=False)
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("ZOTERO_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("ZOTERO_LIBRARIES_FILE", str(tmp_path / "libraries.yml"))
    # With any proxy variable set, urllib reads proxies from the environment
    # instead of asking macOS (_scproxy). That call aborts in a forked process,
    # which is how mutmut runs tests, popping a "Python quit unexpectedly" dialog.
    monkeypatch.setenv("no_proxy", "*")
    monkeypatch.chdir(tmp_path)
    return tmp_path


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """Tests must never reach a real API: fail any outbound connection."""
    import socket

    def refuse(*args, **kwargs):
        raise RuntimeError("network access attempted in a test")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)


@pytest.fixture
def zotero():
    return load_script("skills/zotero/scripts/zotero.py")


class FakeZotero:
    """In-memory stand-in for the two HTTP entry points zotero.py uses."""

    def __init__(self, items: dict[str, dict] | None = None):
        self.items = items or {}
        self.writes: list[tuple[str, str, dict | list, int | None]] = []
        self.next_key = 0

    def request(self, cfg, url, *, accept="application/json"):
        key = url.split("/items/")[1].split("?")[0]
        if key not in self.items:
            raise SystemExit(f"error: HTTP 404 from Zotero: {key}")
        data = self.items[key]
        return json.dumps({"key": key, "version": data["version"], "data": data}).encode(), {}

    def write_request(self, cfg, url, method, body, version=None):
        payload = json.loads(body)
        self.writes.append((method, url, payload, version))
        if method == "PATCH":
            key = url.split("/items/")[1].split("?")[0]
            self.items[key].update(payload)
            self.items[key]["version"] += 1
            return 204, b"", {}
        if method == "POST":
            successful = {}
            for i, _ in enumerate(payload):
                self.next_key += 1
                successful[str(i)] = {"key": f"NEW{self.next_key:05d}"}
            return 200, json.dumps({"successful": successful, "failed": {}}).encode(), {}
        raise AssertionError(f"unexpected {method}")


@pytest.fixture
def fake_zotero(zotero, monkeypatch):
    fake = FakeZotero({
        "J4DMDE7C": {"version": 7, "itemType": "journalArticle", "title": "A paper",
                     "date": "2024", "collections": []},
    })
    monkeypatch.setattr(zotero, "_request", fake.request)
    monkeypatch.setattr(zotero, "_write_request", fake.write_request)
    monkeypatch.setenv("ZOTERO_API_KEY", "test-key-not-real")
    monkeypatch.setenv("ZOTERO_LIBRARY_ID", "1234567")
    monkeypatch.setenv("ZOTERO_LIBRARY_TYPE", "group")
    return fake

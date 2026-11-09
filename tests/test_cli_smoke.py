"""Every CLI imports and answers --help on the oldest supported Python.

Cheap insurance for the stdlib-only, Python 3.9+ promise: a 3.10+ syntax slip
or a broken import fails here instead of on a user's machine.
"""

from __future__ import annotations

import runpy
import sys

import pytest

from conftest import REPO

CLIS = [
    "skills/arxiv/scripts/fetch_arxiv_query.py",
    "skills/openalex/scripts/openalex.py",
    "skills/semantic-scholar/scripts/s2.py",
    "skills/zotero-merge-prep/scripts/merge_prep.py",
    "skills/zotero-pdf-to-text/scripts/pdf_to_text.py",
    "skills/zotero/scripts/zotero.py",
]


def test_every_script_is_listed():
    found = {str(p.relative_to(REPO)) for p in REPO.glob("skills/*/scripts/*.py")}
    libraries = {"skills/arxiv/scripts/zotero_common.py"}  # imported, not run
    no_argparse = {"skills/arxiv/scripts/check_count.py"}  # covered below
    assert found - libraries - no_argparse == set(CLIS)


@pytest.mark.parametrize("relpath", CLIS)
def test_help_exits_zero(relpath, monkeypatch, capsys):
    # Some scripts read credentials at import time; dummies are enough for --help.
    monkeypatch.setenv("ZOTERO_LIBRARY_ID", "1234567")
    monkeypatch.setenv("ZOTERO_API_KEY_RO", "dummy-not-a-key")
    monkeypatch.setattr(sys, "argv", [relpath, "--help"])
    monkeypatch.syspath_prepend(str((REPO / relpath).parent))
    with pytest.raises(SystemExit) as exc:
        runpy.run_path(str(REPO / relpath), run_name="__main__")
    assert exc.value.code in (0, None)
    assert "usage" in capsys.readouterr().out.lower()


def test_check_count_without_query_prints_usage(monkeypatch, capsys):
    relpath = "skills/arxiv/scripts/check_count.py"
    monkeypatch.setattr(sys, "argv", [relpath])
    with pytest.raises(SystemExit) as exc:
        runpy.run_path(str(REPO / relpath), run_name="__main__")
    assert exc.value.code == 1
    assert "Usage:" in capsys.readouterr().err

"""`--plan -` (stdin) support for the zotero write commands (issue #7)."""

from __future__ import annotations

import hashlib
import io
import json
import sys

import pytest


def run_cli(zotero, monkeypatch, argv, stdin=None):
    monkeypatch.setattr(sys, "argv", ["zotero.py", *argv])
    if stdin is not None:
        monkeypatch.setattr(sys, "stdin", io.StringIO(stdin))
    zotero.main()


# ---------------------------------------------------------------------------
# _read_plan_text / _load_plan
# ---------------------------------------------------------------------------

def test_dash_reads_stdin(zotero, monkeypatch):
    monkeypatch.setattr(sys, "stdin", io.StringIO('{"K": ["t"]}'))
    assert zotero._load_plan("-") == {"K": ["t"]}


def test_path_reads_file(zotero, tmp_path):
    plan = tmp_path / "plan.json"
    plan.write_text('{"K": {"date": "2024"}}')
    assert zotero._load_plan(str(plan)) == {"K": {"date": "2024"}}


def test_file_named_dash_is_not_read_from_disk(zotero, monkeypatch, tmp_path):
    (tmp_path / "-").write_text('{"from": "disk"}')
    monkeypatch.setattr(sys, "stdin", io.StringIO('{"from": "stdin"}'))
    assert zotero._load_plan("-") == {"from": "stdin"}


@pytest.mark.parametrize("text", ["", "   \n\t"])
def test_empty_stdin_is_a_clear_error(zotero, monkeypatch, text):
    monkeypatch.setattr(sys, "stdin", io.StringIO(text))
    with pytest.raises(SystemExit) as exc:
        zotero._load_plan("-")
    assert str(exc.value) == "error: --plan - was given but stdin is empty"


def test_invalid_json_from_stdin_names_stdin(zotero, monkeypatch):
    monkeypatch.setattr(sys, "stdin", io.StringIO("{not json"))
    with pytest.raises(SystemExit) as exc:
        zotero._load_plan("-")
    assert str(exc.value).startswith("error: plan from stdin is not valid JSON:")


def test_invalid_json_from_file_names_file(zotero, tmp_path):
    plan = tmp_path / "bad.json"
    plan.write_text("[1,")
    with pytest.raises(SystemExit) as exc:
        zotero._load_plan(str(plan))
    assert str(exc.value).startswith(f"error: plan from {plan} is not valid JSON:")


# ---------------------------------------------------------------------------
# create-items resume-state location
# ---------------------------------------------------------------------------

CFG = {"library_type": "group", "library_id": "1234567"}


def test_state_explicit_flag_wins(zotero, tmp_path):
    assert zotero._create_state_path(CFG, "-", str(tmp_path / "s.json"), "[]") == tmp_path / "s.json"


def test_state_defaults_next_to_plan_file(zotero, tmp_path):
    plan = tmp_path / "create_plan.json"
    assert zotero._create_state_path(CFG, str(plan), None, "[]") == tmp_path / "create_plan.json.state.json"


def test_stdin_state_is_keyed_by_plan_content(zotero, tmp_path):
    a1 = zotero._create_state_path(CFG, "-", None, '[{"dedupe_key": "a"}]')
    a2 = zotero._create_state_path(CFG, "-", None, '[{"dedupe_key": "a"}]')
    b = zotero._create_state_path(CFG, "-", None, '[{"dedupe_key": "b"}]')
    assert a1 == a2 != b
    assert a1.parent == tmp_path / "cache" / "group-1234567" / "create-state"
    assert a1.parent.is_dir()
    digest = hashlib.sha256(b'[{"dedupe_key": "a"}]').hexdigest()[:16]
    assert a1.name == f"stdin-{digest}.state.json"
    # Never lands in the working directory as "-.state.json".
    assert not (tmp_path / "-.state.json").exists()


# ---------------------------------------------------------------------------
# End to end through main()
# ---------------------------------------------------------------------------

def test_update_items_stdin_dry_run_matches_issue_acceptance(zotero, fake_zotero, monkeypatch, capsys):
    run_cli(zotero, monkeypatch, ["update-items", "--plan", "-"],
            stdin='{"J4DMDE7C": {"date": "2024-12-22"}}')
    out = json.loads(capsys.readouterr().out)
    assert out["mode"].startswith("DRY-RUN")
    assert out["fields_to_write"] == 1
    assert out["results"][0]["status"] == "would-update"
    assert out["results"][0]["changes"] == {"date": {"from": "2024", "to": "2024-12-22"}}
    assert fake_zotero.writes == []


def test_update_items_stdin_table_format(zotero, fake_zotero, monkeypatch, capsys):
    run_cli(zotero, monkeypatch, ["update-items", "--plan", "-", "--format", "table"],
            stdin='{"J4DMDE7C": {"date": "2024-12-22"}}')
    out = capsys.readouterr().out
    assert "J4DMDE7C" in out and "would-update" in out


def test_update_items_stdin_commit_writes(zotero, fake_zotero, monkeypatch, capsys):
    run_cli(zotero, monkeypatch, ["update-items", "--plan", "-", "--commit"],
            stdin='{"J4DMDE7C": {"date": "2024-12-22"}}')
    out = json.loads(capsys.readouterr().out)
    assert out["results"][0]["status"] == "updated"
    assert fake_zotero.writes == [
        ("PATCH", fake_zotero.writes[0][1], {"date": "2024-12-22"}, 7),
    ]
    assert fake_zotero.items["J4DMDE7C"]["date"] == "2024-12-22"


def test_update_items_rejects_non_object_plan(zotero, fake_zotero, monkeypatch):
    with pytest.raises(SystemExit) as exc:
        run_cli(zotero, monkeypatch, ["update-items", "--plan", "-"], stdin='["J4DMDE7C"]')
    assert "plan must be a JSON object" in str(exc.value)


def test_tag_add_stdin(zotero, fake_zotero, monkeypatch, capsys):
    fake_zotero.items["J4DMDE7C"]["tags"] = []
    run_cli(zotero, monkeypatch, ["tag-add", "--plan", "-"], stdin='{"J4DMDE7C": ["core"]}')
    out = json.loads(capsys.readouterr().out)
    assert out["tags_to_write"] == 1
    assert fake_zotero.writes == []


def test_collection_add_and_remove_stdin(zotero, fake_zotero, monkeypatch, capsys):
    run_cli(zotero, monkeypatch, ["collection-add", "--plan", "-"], stdin='{"J4DMDE7C": ["ABCD1234"]}')
    assert json.loads(capsys.readouterr().out)["memberships_to_add"] == 1
    fake_zotero.items["J4DMDE7C"]["collections"] = ["ABCD1234"]
    run_cli(zotero, monkeypatch, ["collection-remove", "--plan", "-"], stdin='{"J4DMDE7C": ["ABCD1234"]}')
    out = json.loads(capsys.readouterr().out)
    assert out["results"][0]["status"].startswith("would-")
    assert fake_zotero.writes == []


def test_create_items_stdin_commit_then_resume(zotero, fake_zotero, monkeypatch, capsys, tmp_path):
    plan = json.dumps([
        {"dedupe_key": "arxiv:1", "item": {"itemType": "preprint", "title": "One"}},
        {"dedupe_key": "arxiv:2", "item": {"itemType": "preprint", "title": "Two"}},
    ])
    run_cli(zotero, monkeypatch, ["create-items", "--plan", "-", "--commit"], stdin=plan)
    first = json.loads(capsys.readouterr().out)
    assert first["summary"] == {"created": 2}
    assert len(fake_zotero.writes) == 1

    # Same plan piped again: the hash-keyed state file makes it a no-op.
    run_cli(zotero, monkeypatch, ["create-items", "--plan", "-", "--commit"], stdin=plan)
    second = json.loads(capsys.readouterr().out)
    assert second["already_created_skipped"] == 2
    assert second["processed"] == 0
    assert len(fake_zotero.writes) == 1
    assert not (tmp_path / "-.state.json").exists()


def test_create_items_stdin_honours_explicit_state(zotero, fake_zotero, monkeypatch, capsys, tmp_path):
    state = tmp_path / "my.state.json"
    plan = json.dumps([{"dedupe_key": "arxiv:1", "item": {"itemType": "preprint", "title": "One"}}])
    run_cli(zotero, monkeypatch, ["create-items", "--plan", "-", "--state", str(state), "--commit"], stdin=plan)
    capsys.readouterr()
    assert json.loads(state.read_text()) == {"created": ["arxiv:1"]}
    assert not (tmp_path / "cache" / "group-1234567" / "create-state").exists()


def test_create_items_stdin_invalid_json(zotero, fake_zotero, monkeypatch):
    with pytest.raises(SystemExit) as exc:
        run_cli(zotero, monkeypatch, ["create-items", "--plan", "-"], stdin="nope")
    assert str(exc.value).startswith("error: plan is not valid JSON:")


def test_tag_add_single_key_without_plan(zotero, fake_zotero, monkeypatch, capsys):
    fake_zotero.items["J4DMDE7C"]["tags"] = []
    run_cli(zotero, monkeypatch, ["tag-add", "J4DMDE7C", "--add", "core", "--add", "lineage"])
    out = json.loads(capsys.readouterr().out)
    assert out["tags_to_write"] == 2


def test_tag_add_needs_plan_or_key(zotero, fake_zotero, monkeypatch):
    with pytest.raises(SystemExit) as exc:
        run_cli(zotero, monkeypatch, ["tag-add", "J4DMDE7C"])
    assert "provide either --plan FILE or KEY" in str(exc.value)

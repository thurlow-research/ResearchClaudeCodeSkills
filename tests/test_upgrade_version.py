"""zotero-version-upgrade: arXiv/Crossref matching, plan building, and the zotero.py handoff."""

from __future__ import annotations

import io
import json
import subprocess
import sys
import urllib.error
from types import SimpleNamespace

import pytest

from conftest import load_script

TODAY = "2026-11-09"


@pytest.fixture
def uv(monkeypatch):
    module = load_script("skills/zotero-version-upgrade/scripts/upgrade_version.py")
    monkeypatch.setattr(module, "TODAY", TODAY)
    monkeypatch.setattr(module, "_SCHEMA", None)
    return module


def opts(**kw):
    base = dict(no_abstract=False, original_date=False, no_published=False, library=None,
                item_json=None, commit=False, plan_dir="plans")
    base.update(kw)
    return SimpleNamespace(**base)


def atom(abs_id="http://arxiv.org/abs/2401.01234v4", updated="2025-03-02T10:00:00Z",
         published="2024-01-03T09:00:00Z", title="A  Study\n of Things", summary=" New\n abstract. ",
         doi=None, journal_ref=None):
    extra = ""
    if doi:
        extra += f"<arxiv:doi>{doi}</arxiv:doi>"
    if journal_ref:
        extra += f"<arxiv:journal_ref>{journal_ref}</arxiv:journal_ref>"
    return (
        '<feed xmlns="http://www.w3.org/2005/Atom" xmlns:arxiv="http://arxiv.org/schemas/atom">'
        f"<entry><id>{abs_id}</id><updated>{updated}</updated><published>{published}</published>"
        f"<title>{title}</title><summary>{summary}</summary>{extra}</entry></feed>"
    ).encode()


PREPRINT = {
    "key": "BJQGEUQH", "version": 3, "itemType": "preprint", "title": "A Study of Things",
    "creators": [{"creatorType": "author", "firstName": "Ada", "lastName": "Lovelace"}],
    "date": "2024-01-03", "abstractNote": "Old abstract.",
    "url": "https://arxiv.org/abs/2401.01234v2", "archiveID": "arXiv:2401.01234",
    "repository": "arXiv", "DOI": "10.48550/arXiv.2401.01234",
    "extra": "arXiv: 2401.01234\nLast Updated: 2024-02-01\nPDF: https://arxiv.org/pdf/2401.01234v2",
    "tags": [], "collections": ["ABCD1234"],
}

CROSSREF_JOURNAL = {
    "type": "journal-article", "DOI": "10.1145/1234567", "title": ["A Study of Things"],
    "author": [{"family": "Lovelace", "given": "Ada"}], "container-title": ["Journal of Things"],
    "page": "1–20", "publisher": "ACM", "volume": "12", "issue": "3", "ISSN": ["1234-5678", "8765-4321"],
    "published-print": {"date-parts": [[2025, 6, 1]]},
}


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def test_http_get_retries_then_returns(uv, monkeypatch):
    sleeps, calls = [], []

    class Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def urlopen(req, timeout):
        calls.append((req.full_url, req.get_header("User-agent"), timeout))
        if len(calls) == 1:
            raise urllib.error.URLError("reset")
        if len(calls) == 2:
            return Resp(b"  \n")
        return Resp(b"body")

    monkeypatch.setattr(uv.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr("time.sleep", sleeps.append)
    assert uv.http_get("https://x.test/a", timeout=7) == b"body"
    assert sleeps == [3, 6]
    assert calls[0] == ("https://x.test/a", uv.UA["User-Agent"], 7)


def test_http_get_raises_last_error_after_all_tries(uv, monkeypatch):
    sleeps = []

    def urlopen(req, timeout):
        raise urllib.error.URLError(f"fail {len(sleeps)}")

    monkeypatch.setattr(uv.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr("time.sleep", sleeps.append)
    with pytest.raises(urllib.error.URLError, match="fail 3"):
        uv.http_get("https://x.test/a")
    assert sleeps == [3, 6, 9, 12]


def test_locate_zotero_py_prefers_env(uv, monkeypatch, tmp_path):
    fake = tmp_path / "zotero.py"
    fake.write_text("")
    monkeypatch.setenv("ZOTERO_PY", str(fake))
    assert uv.locate_zotero_py() == str(fake)


def test_locate_zotero_py_falls_back_to_sibling_skill(uv, monkeypatch):
    assert uv.locate_zotero_py().endswith("skills/zotero/scripts/zotero.py")


def test_locate_zotero_py_exits_when_missing(uv, monkeypatch, tmp_path):
    monkeypatch.setattr(uv, "__file__", str(tmp_path / "a" / "b" / "upgrade_version.py"))
    with pytest.raises(SystemExit, match="could not locate zotero.py; set ZOTERO_PY"):
        uv.locate_zotero_py()


def test_zotero_runs_cli_with_library(uv, monkeypatch):
    seen = {}

    def run(cmd, capture_output, text):
        seen.update(cmd=cmd, capture_output=capture_output, text=text)
        return subprocess.CompletedProcess(cmd, 0, stdout="out", stderr="")

    monkeypatch.setattr(uv, "locate_zotero_py", lambda: "/z/zotero.py")
    monkeypatch.setattr(uv.subprocess, "run", run)
    assert uv.zotero(["count"], "SLR") == "out"
    assert seen == {"cmd": [sys.executable, "/z/zotero.py", "--library", "SLR", "count"],
                    "capture_output": True, "text": True}
    uv.zotero(["count"])
    assert seen["cmd"] == [sys.executable, "/z/zotero.py", "count"]


def test_zotero_failure_exits_with_stderr(uv, monkeypatch):
    monkeypatch.setattr(uv, "locate_zotero_py", lambda: "/z/zotero.py")
    monkeypatch.setattr(uv.subprocess, "run",
                        lambda cmd, **k: subprocess.CompletedProcess(cmd, 1, stdout="", stderr=" boom \n"))
    with pytest.raises(SystemExit) as exc:
        uv.zotero(["item", "K"])
    assert str(exc.value) == "zotero.py item K failed:\nboom"


def test_load_item_from_json_file_and_api(uv, monkeypatch, tmp_path):
    f = tmp_path / "item.json"
    f.write_text(json.dumps([{"key": "K", "data": {"title": "T"}}]))
    assert uv.load_item("K", None, str(f)) == {"title": "T"}
    calls = []
    monkeypatch.setattr(uv, "zotero", lambda args, lib: calls.append((args, lib)) or '{"title": "bare"}')
    assert uv.load_item("K", "SLR", None) == {"title": "bare"}
    assert calls == [(["raw", "/items/K"], "SLR")]


def test_norm_and_title_sim(uv):
    assert uv.norm("A-Study: of THINGS!") == "astudyofthings"
    assert uv.norm(None) == ""
    assert uv.title_sim("A Study", "a study") == 1.0
    assert uv.title_sim("abc", "xyz") == 0.0


def test_type_fields_fetches_schema_once(uv, monkeypatch):
    calls = []
    schema = {"itemTypes": [{"itemType": "journalArticle", "fields": [{"field": "volume"}, {"field": "DOI"}]},
                            {"itemType": "note"}]}
    monkeypatch.setattr(uv, "http_get", lambda url: calls.append(url) or json.dumps(schema).encode())
    assert uv.type_fields("journalArticle") == {"volume", "DOI"}
    assert uv.type_fields("note") == set()
    assert uv.type_fields("nope") == set()
    assert calls == ["https://api.zotero.org/schema"]


# ---------------------------------------------------------------------------
# arXiv
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("record,expected", [
    ({"archiveID": "arXiv:2401.01234"}, ("2401.01234", None)),
    ({"DOI": "10.48550/arXiv.2401.01234"}, ("2401.01234", None)),
    ({"DOI": "10.1145/2401.01234"}, (None, None)),
    ({"url": "https://arxiv.org/abs/2401.01234v3"}, ("2401.01234", 3)),
    ({"url": "https://example.com/2401.01234v3"}, (None, None)),
    ({"extra": "arXiv: 2401.12345"}, ("2401.12345", None)),
    ({"extra": "arXiv: 2401.12345\nversion: v5"}, ("2401.12345", 5)),
    ({"archiveID": "arXiv:2401.01234", "url": "https://arxiv.org/abs/2401.01234v2",
      "extra": "PDF: https://arxiv.org/pdf/2401.01234v4"}, ("2401.01234", 4)),
    ({"title": "nothing"}, (None, None)),
])
def test_arxiv_id(uv, record, expected):
    assert uv.arxiv_id(record) == expected


def test_arxiv_meta_parses_entry(uv, monkeypatch):
    urls = []
    monkeypatch.setattr(uv, "http_get", lambda url: urls.append(url) or atom(doi="10.1145/1234567",
                                                                            journal_ref="J. Things 12"))
    assert uv.arxiv_meta("2401.01234") == {
        "id": "2401.01234", "latest_version": 4, "latest_date": "2025-03-02", "v1_date": "2024-01-03",
        "title": "A Study of Things", "abstract": "New abstract.", "doi": "10.1145/1234567",
        "journal_ref": "J. Things 12",
    }
    assert urls == ["https://export.arxiv.org/api/query?id_list=2401.01234"]


def test_arxiv_meta_unversioned_id_and_missing_fields(uv, monkeypatch):
    monkeypatch.setattr(uv, "http_get", lambda url: atom(abs_id="http://arxiv.org/abs/2401.01234"))
    meta = uv.arxiv_meta("2401.01234")
    assert meta["latest_version"] == 1
    assert meta["doi"] == "" and meta["journal_ref"] == ""


def test_arxiv_meta_no_entry(uv, monkeypatch):
    monkeypatch.setattr(uv, "http_get", lambda url: b'<feed xmlns="http://www.w3.org/2005/Atom"></feed>')
    assert uv.arxiv_meta("2401.01234") is None
    monkeypatch.setattr(uv, "http_get",
                        lambda url: b'<feed xmlns="http://www.w3.org/2005/Atom"><entry></entry></feed>')
    assert uv.arxiv_meta("2401.01234") is None


# ---------------------------------------------------------------------------
# Crossref
# ---------------------------------------------------------------------------

def test_crossref_by_doi(uv, monkeypatch):
    urls = []
    monkeypatch.setattr(uv, "http_get", lambda url: urls.append(url) or b'{"message": {"DOI": "x"}}')
    assert uv.crossref_by_doi("10.1/a b") == {"DOI": "x"}
    assert urls == ["https://api.crossref.org/works/10.1/a%20b"]

    def boom(url):
        raise RuntimeError("down")

    monkeypatch.setattr(uv, "http_get", boom)
    assert uv.crossref_by_doi("10.1/a") is None


def crossref_items(*items):
    return json.dumps({"message": {"items": list(items)}}).encode()


def test_crossref_search_picks_best_valid_match(uv, monkeypatch):
    urls = []
    good = dict(CROSSREF_JOURNAL)
    better_title_wrong_author = dict(CROSSREF_JOURNAL, DOI="10.9/other", author=[{"family": "Babbage"}])
    arxiv_doi = dict(CROSSREF_JOURNAL, DOI="10.48550/arXiv.2401.01234")
    wrong_type = dict(CROSSREF_JOURNAL, type="posted-content", DOI="10.9/posted")
    weak_title = dict(CROSSREF_JOURNAL, DOI="10.9/weak", title=["A Study of Other Things Entirely"])
    monkeypatch.setattr(uv, "http_get", lambda url: urls.append(url) or crossref_items(
        arxiv_doi, wrong_type, weak_title, better_title_wrong_author, good))
    assert uv.crossref_search("A Study of Things", ["lovelace"])["DOI"] == "10.1145/1234567"
    assert urls == ["https://api.crossref.org/works?query.bibliographic=A+Study+of+Things&rows=8"]


def test_crossref_search_keeps_highest_similarity(uv, monkeypatch):
    close = dict(CROSSREF_JOURNAL, DOI="10.9/close", title=["A Study of Thing"])
    exact = dict(CROSSREF_JOURNAL, DOI="10.9/exact")
    monkeypatch.setattr(uv, "http_get", lambda url: crossref_items(exact, close))
    assert uv.crossref_search("A Study of Things", [])["DOI"] == "10.9/exact"
    monkeypatch.setattr(uv, "http_get", lambda url: crossref_items(close, exact))
    assert uv.crossref_search("A Study of Things", [])["DOI"] == "10.9/exact"


def test_crossref_search_threshold_and_untitled(uv, monkeypatch):
    # "abcdefghijklmnopqrstuvwxy" vs "abcdefghijklmnopqrstuvwxyz": ratio 0.98 passes;
    # a 0.92-exact edge is checked via title_sim to pin >=, not >.
    monkeypatch.setattr(uv, "title_sim", lambda a, b: 0.92 if b else 0.0)
    monkeypatch.setattr(uv, "http_get", lambda url: crossref_items(
        dict(CROSSREF_JOURNAL, title=None, DOI="10.9/untitled"), dict(CROSSREF_JOURNAL)))
    assert uv.crossref_search("x", [])["DOI"] == "10.1145/1234567"
    monkeypatch.setattr(uv, "title_sim", lambda a, b: 0.9199)
    assert uv.crossref_search("x", []) is None


def test_crossref_search_failure_and_no_items(uv, monkeypatch):
    monkeypatch.setattr(uv, "http_get", lambda url: crossref_items())
    assert uv.crossref_search("t", ["x"]) is None

    def boom(url):
        raise RuntimeError("down")

    monkeypatch.setattr(uv, "http_get", boom)
    assert uv.crossref_search("t", ["x"]) is None


@pytest.mark.parametrize("message,expected", [
    ({"published-print": {"date-parts": [[2025, 6, 1]]}}, "2025-06-01"),
    ({"published-print": {"date-parts": [[None]]}, "published-online": {"date-parts": [[2025, 3]]}}, "2025-03"),
    ({"issued": {"date-parts": [[2024]]}}, "2024"),
    ({"published-online": {}}, ""),
    ({}, ""),
])
def test_cr_date(uv, message, expected):
    assert uv.cr_date(message) == expected


# ---------------------------------------------------------------------------
# plans
# ---------------------------------------------------------------------------

def test_history_line(uv):
    assert uv.history_line("preprint", "2024", "Old", "note", {}) == \
        "orig-type: preprint; orig-date: 2024; orig-title: Old (note)"
    assert uv.history_line("", "2024", "", "note", {"pages": "1–2"}) == \
        'orig-date: 2024; note; dropped fields: {"pages": "1–2"}'


def test_published_plan_journal(uv, monkeypatch):
    monkeypatch.setattr(uv, "type_fields", lambda t: {"publicationTitle", "volume", "issue", "pages",
                                                      "DOI", "ISSN", "publisher"})
    fields, tags, dropped = uv.published_plan(PREPRINT, CROSSREF_JOURNAL, opts())
    hist = ("orig-type: preprint; orig-date: 2024-01-03; orig-title: A Study of Things "
            f"(upgraded to the published version, Journal of Things, {TODAY}, by zotero-version-upgrade; "
            "source: Crossref 10.1145/1234567; verify cited figures against the published full text); "
            'dropped fields: {"archiveID": "arXiv:2401.01234", "repository": "arXiv", '
            '"DOI": "10.48550/arXiv.2401.01234", "url": "https://arxiv.org/abs/2401.01234v2"}')
    assert fields == {
        "itemType": "journalArticle", "DOI": "10.1145/1234567", "url": "https://doi.org/10.1145/1234567",
        "date": "2025-06-01", "pages": "1-20", "publisher": "ACM", "publicationTitle": "Journal of Things",
        "volume": "12", "issue": "3", "ISSN": "1234-5678",
        "extra": PREPRINT["extra"] + "\n" + hist,
    }
    assert tags == ["orig-type:preprint", "orig-date:2024-01-03"]
    assert dropped == ["DOI", "archiveID", "repository", "url"]


def test_published_plan_conference_minimal(uv, monkeypatch):
    monkeypatch.setattr(uv, "type_fields", lambda t: {"DOI", "pages"})
    record = {"itemType": "preprint", "date": "2024", "title": "", "extra": "",
              "DOI": "10.1/SAME", "pages": "", "tags": ["x"], "collections": []}
    m = {"type": "proceedings-article", "DOI": "10.1/same", "event": {"name": "ICSE"}}
    fields, tags, dropped = uv.published_plan(record, m, opts(original_date=True))
    assert fields == {
        "itemType": "conferencePaper", "DOI": "10.1/same", "url": "https://doi.org/10.1/same", "date": "2024",
        "conferenceName": "ICSE",
        "extra": ("original-date: 2024\norig-type: preprint; orig-date: 2024; upgraded to the published "
                  f"version, conferencePaper, {TODAY}, by zotero-version-upgrade; source: Crossref 10.1/same; "
                  "verify cited figures against the published full text"),
    }
    assert dropped == []
    assert tags == ["orig-type:preprint", "orig-date:2024"]


def test_published_plan_book_section_keeps_existing_original_date(uv, monkeypatch):
    monkeypatch.setattr(uv, "type_fields", lambda t: set())
    record = {"itemType": "preprint", "date": "2024", "extra": "original-date: 2020"}
    m = {"type": "book-chapter", "DOI": "10.1/b", "container-title": ["Handbook"],
         "volume": "9", "ISSN": ["x"], "event": {"name": "ignored"}}
    fields, _, _ = uv.published_plan(record, m, opts(original_date=True))
    assert fields["bookTitle"] == "Handbook"
    assert "volume" not in fields and "ISSN" not in fields and "conferenceName" not in fields
    assert fields["extra"].startswith("original-date: 2020\norig-type: preprint")
    assert fields["extra"].count("original-date:") == 1


def test_published_plan_journal_without_optional_parts(uv, monkeypatch):
    monkeypatch.setattr(uv, "type_fields", lambda t: set())
    record = {"itemType": "preprint", "date": "2024"}
    m = {"type": "journal-article", "DOI": "10.1/j", "published-online": {"date-parts": [[2025, 1, 2]]}}
    fields, _, _ = uv.published_plan(record, m, opts())
    assert set(fields) == {"itemType", "DOI", "url", "date", "extra"}
    assert fields["date"] == "2025-01-02"


def test_arxiv_plan_newer_version(uv):
    am = {"id": "2401.01234", "latest_version": 4, "latest_date": "2025-03-02", "abstract": "New abstract."}
    fields, tags, dropped = uv.arxiv_plan(PREPRINT, am, 2, opts())
    note = (f"updated to arXiv v4 (2025-03-02) on {TODAY} by zotero-version-upgrade; previous version v2; "
            "re-verify cited figures against the v4 full text; abstract replaced with the v4 abstract")
    assert fields == {
        "date": "2025-03-02", "abstractNote": "New abstract.",
        "extra": ("arXiv: 2401.01234\nLast Updated: 2025-03-02\nPDF: https://arxiv.org/pdf/2401.01234v4\n"
                  f"orig-date: 2024-01-03; {note}"),
        "url": "https://arxiv.org/abs/2401.01234v4",
    }
    assert tags == ["orig-date:2024-01-03"]
    assert dropped == []


def test_arxiv_plan_version_line_no_abstract_and_original_date(uv):
    record = {"date": "2024-01-03", "abstractNote": "Same.", "extra": "version: v1",
              "url": "https://example.org/paper"}
    am = {"id": "2401.01234", "latest_version": 2, "latest_date": "2024-05-05", "abstract": "Other."}
    fields, tags, _ = uv.arxiv_plan(record, am, 1, opts(no_abstract=True, original_date=True))
    assert "abstractNote" not in fields and "url" not in fields
    assert fields["extra"] == (
        "original-date: 2024-01-03\nversion: v2\norig-date: 2024-01-03; updated to arXiv v2 (2024-05-05) on "
        f"{TODAY} by zotero-version-upgrade; previous version v1; re-verify cited figures against the v2 full text")


def test_arxiv_plan_original_date_on_empty_extra_has_no_blank_line(uv):
    am = {"id": "2401.01234", "latest_version": 2, "latest_date": "2024-05-05", "abstract": ""}
    fields, _, _ = uv.arxiv_plan({"date": "2024-01-03"}, am, 1, opts(original_date=True))
    assert fields["extra"].startswith("original-date: 2024-01-03\norig-date: 2024-01-03; updated")


def test_arxiv_plan_unrecorded_version_uses_dates(uv):
    am = {"id": "2401.01234", "latest_version": 3, "latest_date": "2025-01-01", "abstract": "Same."}
    record = {"date": "2024-06-01", "abstractNote": "Same.", "extra": ""}
    fields, tags, _ = uv.arxiv_plan(record, am, None, opts())
    assert "previous version unrecorded" in fields["extra"]
    assert "abstractNote" not in fields
    assert uv.arxiv_plan(dict(record, date="2025-01-01"), am, None, opts()) is None
    assert uv.arxiv_plan(dict(record, date="2025-02-01"), am, None, opts()) is None


def test_arxiv_plan_current_version_is_a_noop(uv):
    am = {"id": "2401.01234", "latest_version": 4, "latest_date": "2025-03-02", "abstract": ""}
    assert uv.arxiv_plan(PREPRINT, am, 4, opts()) is None
    assert uv.arxiv_plan(PREPRINT, am, 5, opts()) is None


def test_arxiv_plan_tags_only_when_date_changes(uv):
    am = {"id": "2401.01234", "latest_version": 2, "latest_date": "2024-01-03", "abstract": ""}
    _, tags, _ = uv.arxiv_plan({"date": "2024-01-03"}, am, 1, opts())
    assert tags == []
    _, tags, _ = uv.arxiv_plan({"date": ""}, am, 1, opts())
    assert tags == []


# ---------------------------------------------------------------------------
# process / main
# ---------------------------------------------------------------------------

@pytest.fixture
def offline(uv, monkeypatch, tmp_path):
    """Item from a JSON file, arXiv/Crossref answered from fixtures."""
    item = tmp_path / "item.json"
    item.write_text(json.dumps({"data": PREPRINT}))
    state = {"atom": atom(), "doi": None, "search": None, "zotero": []}
    # Only arxiv_meta still reaches http_get (Crossref and the schema are faked below).
    monkeypatch.setattr(uv, "http_get", lambda url: state["atom"])
    monkeypatch.setattr(uv, "crossref_by_doi", lambda doi: state["doi"])

    def search(title, surnames):
        state["searched"] = (title, surnames)
        return state["search"]

    monkeypatch.setattr(uv, "crossref_search", search)
    monkeypatch.setattr(uv, "type_fields", lambda t: set())
    monkeypatch.setattr(uv, "zotero", lambda args, lib: state["zotero"].append((args, lib)) or " {\"ok\": 1}\n")
    state["item"] = str(item)
    state["plans"] = tmp_path / "plans"
    return state


def test_process_arxiv_revision_offline(uv, offline):
    report = uv.process("BJQGEUQH", opts(item_json=offline["item"], plan_dir=str(offline["plans"])))
    assert report["status"] == "upgrade-available"
    assert report["kind"] == "arxiv-revision"
    assert report["arxiv"] == {"id": "2401.01234", "recorded_version": 2, "latest_version": 4,
                               "latest_date": "2025-03-02"}
    assert report["full_text"] == "https://arxiv.org/pdf/2401.01234v4"
    assert report["abstract_replaced"] is True
    assert report["changes"]["date"] == {"from": "2024-01-03", "to": "2025-03-02"}
    assert "abstractNote" not in report["changes"]
    assert report["tags_to_add"] == ["orig-date:2024-01-03"]
    assert report["dropped_fields"] == []
    assert report["reminder"] == "Re-verify any cited figures against the new full text before citing."
    assert offline["searched"] == ("A Study of Things", ["lovelace"])
    plan = json.loads((offline["plans"] / "upgrade_BJQGEUQH.json").read_text())
    assert plan["BJQGEUQH"]["date"] == "2025-03-02"
    assert json.loads((offline["plans"] / "upgrade_BJQGEUQH_tags.json").read_text()) == \
        {"BJQGEUQH": ["orig-date:2024-01-03"]}
    assert report["plan_files"] == [str(offline["plans"] / "upgrade_BJQGEUQH.json"),
                                    str(offline["plans"] / "upgrade_BJQGEUQH_tags.json")]
    assert offline["zotero"] == []  # offline mode never writes
    assert "mode" not in report


def test_process_published_via_arxiv_doi_integrated_commit(uv, offline, monkeypatch):
    offline["atom"] = atom(doi="10.1145/1234567")
    offline["doi"] = CROSSREF_JOURNAL
    monkeypatch.setattr(uv, "load_item", lambda key, lib, item_json: dict(PREPRINT))
    report = uv.process("BJQGEUQH", opts(library="SLR", commit=True, plan_dir=str(offline["plans"])))
    assert report["kind"] == "published"
    assert report["published"] == {"doi": "10.1145/1234567", "venue": "Journal of Things", "type": "journalArticle"}
    assert report["full_text"] == "https://doi.org/10.1145/1234567"
    assert "searched" not in offline
    upd = str(offline["plans"] / "upgrade_BJQGEUQH.json")
    tags = str(offline["plans"] / "upgrade_BJQGEUQH_tags.json")
    assert offline["zotero"] == [
        (["update-items", "--plan", upd, "--commit", "--allow-field-loss", "--format", "json"], "SLR"),
        (["tag-add", "--plan", tags, "--commit", "--format", "json"], "SLR"),
    ]
    assert report["update_items"] == '{"ok": 1}' and report["tag_add"] == '{"ok": 1}'
    assert report["mode"] == "COMMIT"


def test_process_published_via_search_dry_run_no_field_loss(uv, offline, monkeypatch):
    record = {k: v for k, v in PREPRINT.items() if k not in ("archiveID", "repository", "DOI")}
    record["url"] = "https://doi.org/10.1145/1234567"
    record["extra"] = ""
    record["creators"] = [{"name": "Ada Lovelace"}, {"creatorType": "editor"}]
    offline["search"] = dict(CROSSREF_JOURNAL, title=["A Study of Things"])
    monkeypatch.setattr(uv, "load_item", lambda key, lib, item_json: record)
    report = uv.process("K", opts(plan_dir=str(offline["plans"])))
    assert offline["searched"] == ("A Study of Things", ["lovelace"])
    assert report["kind"] == "published" and "arxiv" not in report
    assert offline["zotero"][0][0] == ["update-items", "--plan", str(offline["plans"] / "upgrade_K.json"),
                                       "--format", "json"]
    assert offline["zotero"][1][0][-2:] == ["--format", "json"] and "--commit" not in offline["zotero"][1][0]
    assert report["mode"] == "DRY-RUN"


def test_process_untagged_upgrade_skips_tag_add(uv, offline, monkeypatch):
    offline["atom"] = atom(updated="2024-01-03T00:00:00Z")
    record = dict(PREPRINT, itemType="journalArticle")
    monkeypatch.setattr(uv, "load_item", lambda key, lib, item_json: record)
    report = uv.process("K", opts(plan_dir=str(offline["plans"])))
    assert report["kind"] == "arxiv-revision" and report["tags_to_add"] == []
    assert [c[0][0] for c in offline["zotero"]] == ["update-items"]
    assert report["plan_files"] == [str(offline["plans"] / "upgrade_K.json")]


def test_process_crossref_match_of_unknown_type_falls_back_to_arxiv(uv, offline):
    offline["search"] = dict(CROSSREF_JOURNAL, type="dataset")
    report = uv.process("K", opts(item_json=offline["item"], plan_dir=str(offline["plans"])))
    assert report["kind"] == "arxiv-revision"


@pytest.mark.parametrize("record,expected", [
    (dict(PREPRINT, url="https://arxiv.org/abs/2401.01234v4"), "up-to-date"),
    ({"itemType": "preprint", "title": "No arXiv here", "creators": []}, "no-newer-version-found"),
    ({"itemType": "journalArticle", "title": "Plain"}, "up-to-date"),
])
def test_process_nothing_to_do(uv, offline, monkeypatch, record, expected):
    monkeypatch.setattr(uv, "load_item", lambda key, lib, item_json: record)
    report = uv.process("K", opts(plan_dir=str(offline["plans"])))
    assert report == {"key": "K", "title": record.get("title", ""), "itemType": record.get("itemType"),
                      "date": record.get("date"), "status": expected,
                      **({"arxiv": report["arxiv"]} if "arxiv" in report else {})}
    assert not offline["plans"].exists()


def test_process_no_published_flag_skips_crossref(uv, offline):
    offline["search"] = CROSSREF_JOURNAL
    report = uv.process("K", opts(item_json=offline["item"], no_published=True, plan_dir=str(offline["plans"])))
    assert report["kind"] == "arxiv-revision"
    assert "searched" not in offline


def test_process_arxiv_failure_is_reported(uv, monkeypatch, tmp_path):
    def boom(aid):
        raise RuntimeError("503")

    monkeypatch.setattr(uv, "arxiv_meta", boom)
    monkeypatch.setattr(uv, "load_item", lambda *a: dict(PREPRINT))
    assert uv.process("K", opts())["status"] == "arxiv-lookup-failed: 503"


def test_main_prints_reports(uv, monkeypatch, capsys):
    seen = []
    monkeypatch.setattr(uv, "process", lambda k, args: seen.append((k, args.plan_dir, args.library)) or {"key": k})
    monkeypatch.setenv("ZOTERO_PLAN_DIR", "/plans")
    monkeypatch.setattr(sys, "argv", ["upgrade_version.py", "A", "B", "--library", "SLR"])
    uv.main()
    assert json.loads(capsys.readouterr().out) == [{"key": "A"}, {"key": "B"}]
    assert seen == [("A", "/plans", "SLR"), ("B", "/plans", "SLR")]


def test_main_default_plan_dir(uv, monkeypatch, capsys):
    seen = []
    monkeypatch.setattr(uv, "process", lambda k, args: seen.append(args.plan_dir) or {})
    monkeypatch.setattr(sys, "argv", ["upgrade_version.py", "A"])
    uv.main()
    assert seen == ["~/Code/Thurlow-Research/zotero-plans"]


def test_main_item_json_takes_one_key(uv, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["upgrade_version.py", "A", "B", "--item-json", "x.json"])
    with pytest.raises(SystemExit, match="--item-json takes exactly one key"):
        uv.main()


# ---------------------------------------------------------------------------
# tightening: behaviour the tests above don't pin down
# ---------------------------------------------------------------------------

def test_http_get_all_empty_raises_empty_response(uv, monkeypatch):
    class Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(uv.urllib.request, "urlopen", lambda req, timeout: Resp(b""))
    monkeypatch.setattr("time.sleep", lambda s: None)
    with pytest.raises(RuntimeError) as exc:
        uv.http_get("https://x.test/a", tries=2)
    assert str(exc.value) == "empty response"


def test_locate_zotero_py_home_fallback(uv, monkeypatch, tmp_path):
    monkeypatch.setattr(uv, "__file__", str(tmp_path / "a" / "b" / "upgrade_version.py"))
    home_copy = tmp_path / "home" / ".claude" / "skills" / "zotero" / "scripts" / "zotero.py"
    home_copy.parent.mkdir(parents=True)
    home_copy.write_text("")
    assert uv.locate_zotero_py() == str(home_copy)


def test_arxiv_id_versions_from_url_and_extra_together(uv):
    record = {"archiveID": "arXiv:2401.01234", "url": "https://arxiv.org/abs/2401.01234v3",
              "extra": "x 2401.01234v2"}
    assert uv.arxiv_id(record) == ("2401.01234", 3)
    # A version hint split across the url/extra boundary must not be read as one.
    assert uv.arxiv_id({"archiveID": "arXiv:2401.01234", "url": "https://arxiv.org/abs/2401.01234",
                        "extra": "v9"}) == ("2401.01234", None)


def test_crossref_search_equal_similarity_keeps_first(uv, monkeypatch):
    first = dict(CROSSREF_JOURNAL, DOI="10.9/first")
    second = dict(CROSSREF_JOURNAL, DOI="10.9/second")
    monkeypatch.setattr(uv, "http_get", lambda url: crossref_items(first, second))
    assert uv.crossref_search("A Study of Things", ["lovelace"])["DOI"] == "10.9/first"


def test_crossref_search_skips_records_without_doi_or_authors(uv, monkeypatch):
    no_doi = {k: v for k, v in CROSSREF_JOURNAL.items() if k != "DOI"}
    no_authors = {k: v for k, v in CROSSREF_JOURNAL.items() if k != "author"}
    monkeypatch.setattr(uv, "http_get", lambda url: crossref_items(no_doi, no_authors))
    assert uv.crossref_search("A Study of Things", []) == no_doi
    assert uv.crossref_search("A Study of Things", ["lovelace"]) == no_doi


def test_cr_date_year_is_not_zero_padded(uv):
    assert uv.cr_date({"issued": {"date-parts": [[987, 1, 2]]}}) == "987-01-02"


def test_history_line_keeps_unicode(uv):
    assert uv.history_line("", "2024", "", "n", {"pages": "1–2"}).endswith('{"pages": "1–2"}')


def test_published_plan_record_missing_type_date_title(uv, monkeypatch):
    asked = []
    monkeypatch.setattr(uv, "type_fields", lambda t: asked.append(t) or set())
    fields, tags, _ = uv.published_plan({}, {"type": "journal-article", "DOI": "10.1/j"}, opts())
    assert asked == ["journalArticle"]
    assert tags == ["orig-type:", "orig-date:"]
    assert fields["date"] == ""
    assert fields["extra"].startswith("orig-date: ; upgraded to the published version, journalArticle")


def test_published_plan_keeps_superseded_pages_and_trims_extra(uv, monkeypatch):
    monkeypatch.setattr(uv, "type_fields", lambda t: {"pages", "DOI"})
    record = {"itemType": "preprint", "date": "2024", "pages": "5", "extra": "keep me\n\n  "}
    fields, _, dropped = uv.published_plan(record, dict(CROSSREF_JOURNAL), opts())
    assert dropped == ["pages"]
    assert fields["extra"].startswith("keep me\norig-type: preprint")


def test_arxiv_plan_trims_extra_and_compares_dates_by_day(uv):
    am = {"id": "2401.01234", "latest_version": 2, "latest_date": "2024-05-05", "abstract": ""}
    fields, tags, _ = uv.arxiv_plan({"date": "2024-01-03", "extra": "keep\n\n "}, am, 1, opts())
    assert fields["extra"].startswith("keep\norig-date: 2024-01-03; updated")
    # A timestamped date on the release day is the same day: no orig-date tag, no upgrade.
    _, tags, _ = uv.arxiv_plan({"date": "2024-05-05T08:00:00"}, am, 1, opts())
    assert tags == []
    assert uv.arxiv_plan({"date": "2024-05-05 "}, am, None, opts()) is None


def test_arxiv_plan_original_date_already_present(uv):
    am = {"id": "2401.01234", "latest_version": 2, "latest_date": "2024-05-05", "abstract": ""}
    fields, _, _ = uv.arxiv_plan({"date": "2024-01-03", "extra": "original-date: 2020"}, am, 1,
                                 opts(original_date=True))
    assert fields["extra"].count("original-date:") == 1


def test_arxiv_plan_history_has_no_type_or_title(uv):
    am = {"id": "2401.01234", "latest_version": 2, "latest_date": "2024-05-05", "abstract": ""}
    fields, _, _ = uv.arxiv_plan({"date": "2024-01-03"}, am, 1, opts())
    assert fields["extra"].startswith("orig-date: 2024-01-03; updated to arXiv v2")
    assert "dropped fields" not in fields["extra"]


def test_process_passes_key_library_and_doi_through(uv, offline, monkeypatch):
    loads, dois = [], []
    offline["atom"] = atom(doi="10.1145/1234567")
    monkeypatch.setattr(uv, "load_item", lambda *a: loads.append(a) or dict(PREPRINT))
    monkeypatch.setattr(uv, "crossref_by_doi", lambda doi: dois.append(doi) or None)
    record = dict(PREPRINT, creators=[{"name": "Ada King Lovelace"}])
    monkeypatch.setattr(uv, "load_item", lambda *a: loads.append(a) or record)
    uv.process("BJQGEUQH", opts(library="SLR", item_json=None, plan_dir=str(offline["plans"])))
    assert loads == [("BJQGEUQH", "SLR", None)]
    assert dois == ["10.1145/1234567"]
    assert offline["searched"] == ("A Study of Things", ["lovelace"])


def test_process_handles_missing_title_and_creators(uv, offline, monkeypatch):
    monkeypatch.setattr(uv, "load_item", lambda *a: {"itemType": "preprint"})
    report = uv.process("K", opts(plan_dir=str(offline["plans"])))
    assert report["title"] == ""
    assert offline["searched"] == ("", [])
    assert report["status"] == "no-newer-version-found"


def test_process_published_report_venue_and_changes(uv, offline, monkeypatch):
    record = {"itemType": "preprint", "title": "A Study of Things", "date": "2024",
              "creators": [{"lastName": "Lovelace"}]}
    offline["search"] = {k: v for k, v in CROSSREF_JOURNAL.items() if k != "container-title"}
    monkeypatch.setattr(uv, "load_item", lambda *a: record)
    report = uv.process("K", opts(plan_dir=str(offline["plans"])))
    assert report["published"]["venue"] == ""
    assert report["changes"]["DOI"] == {"from": "", "to": "10.1145/1234567"}
    assert "date" in report["changes"] and report["changes"]["date"]["from"] == "2024"


def test_process_unchanged_fields_are_not_reported_as_changes(uv, offline, monkeypatch):
    record = dict(PREPRINT, url="https://arxiv.org/abs/2401.01234v2")
    monkeypatch.setattr(uv, "load_item", lambda *a: record)
    monkeypatch.setattr(uv, "arxiv_plan", lambda d, am, v, args: (
        {"date": "2025", "url": record["url"], "abstractNote": "x"}, [], []))
    report = uv.process("K", opts(item_json="x", plan_dir=str(offline["plans"])))
    assert report["changes"] == {"date": {"from": "2024-01-03", "to": "2025"}}


def test_process_plan_files_exact_and_rerunnable(uv, offline, monkeypatch):
    record = dict(PREPRINT, abstractNote="", extra="")
    offline["atom"] = atom(summary="Résumé")
    monkeypatch.setattr(uv, "load_item", lambda *a: record)
    plans = offline["plans"] / "nested" / "dir"
    for _ in range(2):  # the second run reuses the existing directory
        uv.process("K", opts(item_json="x", plan_dir=str(plans)))
    upd = (plans / "upgrade_K.json").read_text()
    fields = json.loads(upd)["K"]
    assert upd == json.dumps({"K": fields}, indent=2, ensure_ascii=False)
    assert fields["abstractNote"] == "Résumé" and "Résumé" in upd
    tags = (plans / "upgrade_K_tags.json").read_text()
    assert tags == json.dumps({"K": ["orig-date:2024-01-03"]}, indent=2)


def test_main_wires_every_flag(uv, monkeypatch, capsys):
    seen = []
    monkeypatch.setattr(uv, "process", lambda k, args: seen.append(vars(args)) or {"title": "Étude"})
    monkeypatch.setattr(sys, "argv", ["upgrade_version.py", "K", "--item-json", "i.json", "--commit",
                                      "--no-abstract", "--original-date", "--no-published", "--plan-dir", "/p"])
    uv.main()
    assert seen == [{"keys": ["K"], "library": None, "item_json": "i.json", "commit": True, "no_abstract": True,
                     "original_date": True, "no_published": True, "plan_dir": "/p"}]
    assert capsys.readouterr().out == json.dumps([{"title": "Étude"}], indent=2, ensure_ascii=False) + "\n"


def test_main_flags_default_off(uv, monkeypatch, capsys):
    seen = []
    monkeypatch.setattr(uv, "process", lambda k, args: seen.append(vars(args)) or {})
    monkeypatch.setattr(sys, "argv", ["upgrade_version.py", "K"])
    uv.main()
    assert {k: seen[0][k] for k in ("commit", "no_abstract", "original_date", "no_published", "item_json")} == \
        {"commit": False, "no_abstract": False, "original_date": False, "no_published": False, "item_json": None}


def test_main_help_shows_docstring_and_option_help(uv, monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["upgrade_version.py", "--help"])
    with pytest.raises(SystemExit):
        uv.main()
    out = capsys.readouterr().out
    # RawDescriptionHelpFormatter keeps the docstring's own line breaks.
    assert "  1. arXiv revision    preprint has a newer arXiv version (v2 -> v4): update date,\n" in out
    assert "offline mode: read the item from a JSON file; plan only" in " ".join(out.split())


def test_main_item_json_error_message_exact(uv, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["upgrade_version.py", "A", "B", "--item-json", "x.json"])
    with pytest.raises(SystemExit) as exc:
        uv.main()
    assert str(exc.value) == "--item-json takes exactly one key"

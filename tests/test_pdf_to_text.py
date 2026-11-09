"""zotero-pdf-to-text: --fetch, the generic file upload, and the library overrides."""

from __future__ import annotations

import hashlib
import io
import socket
import sys

import pytest

from conftest import load_script

SCRIPT = "skills/zotero-pdf-to-text/scripts/pdf_to_text.py"


@pytest.fixture
def p2t(monkeypatch):
    monkeypatch.setenv("ZOTERO_API_KEY_RW", "rw-test-key")
    monkeypatch.setenv("ZOTERO_LIBRARY_ID", "1234567")
    previous = socket.getdefaulttimeout()
    module = load_script(SCRIPT)
    yield module
    socket.setdefaulttimeout(previous)  # the script sets a process-wide default at import


class Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class FakeApi:
    def __init__(self, children=None, auth=None, fail_on=None):
        self.calls = []
        self.children = children or {}
        self.auth = auth if auth is not None else {
            "url": "https://s3.test/upload", "params": {"key": "k1", "policy": "p"}, "uploadKey": "UK"}
        self.fail_on = fail_on

    def __call__(self, method, path, data=None, headers=None):
        self.calls.append((method, path, data, headers))
        if self.fail_on and self.fail_on(method, path):
            raise RuntimeError(f"api failed on {method} {path}")
        if method == "POST" and path == "/items":
            return {"successful": {"0": {"key": "ATT00001"}}}
        if method == "POST" and path.endswith("/file"):
            return self.auth if data.startswith(b"md5=") else None
        if method == "GET" and path.endswith("/children"):
            return self.children.get(path.split("/")[2], [])
        if method == "GET" and path.startswith("/items/"):
            return {"version": 9, "data": {"key": path.split("/")[2], "itemType": "journalArticle"}}
        return None


# ---------------------------------------------------------------------------
# library selection
# ---------------------------------------------------------------------------

def test_import_without_library_id_does_not_crash(monkeypatch):
    previous = socket.getdefaulttimeout()
    try:
        module = load_script(SCRIPT)
        assert module.LIB == ""
        monkeypatch.setattr(sys, "argv", ["pdf_to_text.py", "--items", "K"])
        with pytest.raises(SystemExit) as exc:
            module.main()
        assert str(exc.value) == "no library: set ZOTERO_LIBRARY_ID or pass --library-id"
    finally:
        socket.setdefaulttimeout(previous)


def test_set_library_rebuilds_base(p2t):
    p2t.set_library("6505702", "group")
    assert (p2t.LIB, p2t.TYPE, p2t.BASE) == ("6505702", "group", "https://api.zotero.org/groups/6505702")
    p2t.set_library("42", "user")
    assert p2t.BASE == "https://api.zotero.org/users/42"


@pytest.mark.parametrize("argv,expected", [
    (["--library-id", "6505702"], "library: group 6505702"),
    (["--library-type", "user"], "library: user 1234567"),
    ([], "library: group 1234567"),
])
def test_main_library_overrides(p2t, monkeypatch, capsys, argv, expected):
    monkeypatch.setattr(sys, "argv", ["pdf_to_text.py", *argv])
    with pytest.raises(SystemExit) as exc:
        p2t.main()
    assert str(exc.value) == "need --collection, --items, or --fetch"
    assert capsys.readouterr().out.splitlines() == [expected]


# ---------------------------------------------------------------------------
# upload_file / upload_txt
# ---------------------------------------------------------------------------

def test_upload_file_pdf_full_flow(p2t, monkeypatch):
    api = FakeApi()
    posted = []
    monkeypatch.setattr(p2t, "api", api)
    monkeypatch.setattr(p2t.time, "time", lambda: 1700000000.123)

    def urlopen(req, timeout):
        posted.append((req.full_url, req.get_method(), req.headers, req.data, timeout))
        return Resp(b"")

    monkeypatch.setattr(p2t.urllib.request, "urlopen", urlopen)
    data = b"%PDF-1.7 body"
    assert p2t.upload_file("PARENT01", "my paper.pdf", data, "application/pdf", "Full Text PDF") == \
        ("ATT00001", "uploaded")

    create, authorize, register = api.calls
    assert create == ("POST", "/items", [{
        "itemType": "attachment", "parentItem": "PARENT01", "linkMode": "imported_file",
        "title": "Full Text PDF", "filename": "my paper.pdf", "contentType": "application/pdf"}], None)
    md5 = hashlib.md5(data).hexdigest()
    form_headers = {"Content-Type": "application/x-www-form-urlencoded", "If-None-Match": "*"}
    assert authorize == ("POST", "/items/ATT00001/file",
                         f"md5={md5}&filename=my%20paper.pdf&filesize={len(data)}&mtime=1700000000123&params=1"
                         .encode(), form_headers)
    assert register == ("POST", "/items/ATT00001/file", b"upload=UK", form_headers)

    (url, method, headers, body, timeout), = posted
    boundary = "----ZoteroTxtUpload7MA4YWxkTrZu0gW"
    assert (url, method, timeout) == ("https://s3.test/upload", "POST", 180)
    assert headers == {"Content-type": f"multipart/form-data; boundary={boundary}"}
    assert body == (
        f'--{boundary}\r\nContent-Disposition: form-data; name="key"\r\n\r\nk1\r\n'
        f'--{boundary}\r\nContent-Disposition: form-data; name="policy"\r\n\r\np\r\n'
        f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="my paper.pdf"\r\n'
        f"Content-Type: application/pdf\r\n\r\n"
    ).encode() + data + f"\r\n--{boundary}--\r\n".encode()


def test_upload_txt_sets_charset_and_title(p2t, monkeypatch):
    api = FakeApi(auth={"exists": 1})
    monkeypatch.setattr(p2t, "api", api)
    assert p2t.upload_txt("PARENT01", "paper.txt", b"text") == ("ATT00001", "exists")
    assert api.calls[0][2] == [{
        "itemType": "attachment", "parentItem": "PARENT01", "linkMode": "imported_file",
        "title": "paper.txt", "filename": "paper.txt", "contentType": "text/plain", "charset": "utf-8"}]
    assert len(api.calls) == 2  # create + authorize; no S3 POST, no register


def test_upload_failure_deletes_the_half_created_attachment(p2t, monkeypatch):
    api = FakeApi(fail_on=lambda m, p: m == "POST" and p.endswith("/file"))
    monkeypatch.setattr(p2t, "api", api)
    with pytest.raises(RuntimeError, match="api failed on POST /items/ATT00001/file"):
        p2t.upload_file("PARENT01", "x.pdf", b"%PDF", "application/pdf", "Full Text PDF")
    assert api.calls[-2:] == [
        ("GET", "/items/ATT00001", None, None),
        ("DELETE", "/items/ATT00001", None, {"If-Unmodified-Since-Version": "9"}),
    ]


# ---------------------------------------------------------------------------
# fetch_pdf
# ---------------------------------------------------------------------------

def test_fetch_pdf_returns_pdf_bytes(p2t, monkeypatch):
    seen = []

    def urlopen(req, timeout):
        seen.append((req.full_url, req.get_header("User-agent"), timeout))
        return Resp(b"%PDF-1.5 data")

    monkeypatch.setattr(p2t.urllib.request, "urlopen", urlopen)
    assert p2t.fetch_pdf("https://arxiv.org/pdf/2501.00001") == b"%PDF-1.5 data"
    assert seen == [("https://arxiv.org/pdf/2501.00001", "Mozilla/5.0 (zotero-pdf-to-text fetch)", 180)]


def test_fetch_pdf_refuses_html(p2t, monkeypatch):
    monkeypatch.setattr(p2t.urllib.request, "urlopen", lambda req, timeout: Resp(b"<!DOCTYPE html><html>login"))
    with pytest.raises(ValueError) as exc:
        p2t.fetch_pdf("https://paywall.test/x")
    assert str(exc.value) == "not a PDF (got b'<!DOCTYPE html>'); likely paywalled or a landing page"


# ---------------------------------------------------------------------------
# main --fetch
# ---------------------------------------------------------------------------

@pytest.fixture
def fetch_env(p2t, monkeypatch):
    api = FakeApi(children={"HASPDF01": [{"data": {"contentType": "application/pdf", "itemType": "attachment",
                                                     "parentItem": "HASPDF01", "filename": "a.pdf"}}]})
    monkeypatch.setattr(p2t, "api", api)
    fetched, uploads, sleeps = [], [], []

    def fetch(url):
        fetched.append(url)
        if "bad" in url:
            raise ValueError("not a PDF")
        return b"%PDF" + b"x" * 4096

    monkeypatch.setattr(p2t, "fetch_pdf", fetch)
    monkeypatch.setattr(p2t, "upload_file", lambda *a: uploads.append(a) or ("ATTNEW01", "uploaded"))
    monkeypatch.setattr(p2t.time, "sleep", sleeps.append)
    return {"api": api, "fetched": fetched, "uploads": uploads, "sleeps": sleeps}


def run_main(p2t, monkeypatch, capsys, *argv):
    monkeypatch.setattr(sys, "argv", ["pdf_to_text.py", *argv])
    p2t.main()
    return capsys.readouterr().out.splitlines()


def test_fetch_dry_run(p2t, fetch_env, monkeypatch, capsys):
    out = run_main(p2t, monkeypatch, capsys, "--fetch", "NEWITEM1= https://arxiv.org/pdf/1 ")
    assert out[:2] == ["library: group 1234567", "  NEWITEM1 (dry) would attach PDF 4kb from  https://arxiv.org/pdf/1 "]
    assert fetch_env["fetched"] == ["https://arxiv.org/pdf/1"]
    assert fetch_env["uploads"] == []
    # The fetched key is then handed to the normal conversion pass.
    assert ("GET", "/items/NEWITEM1", None, None) in fetch_env["api"].calls
    assert out[2] == "1 items | 0 w/pdf | 0 already txt | 0 to convert"


def test_fetch_dry_run_flag_wins_over_commit(p2t, fetch_env, monkeypatch, capsys):
    out = run_main(p2t, monkeypatch, capsys, "--fetch", "NEWITEM1=https://a/1", "--commit", "--dry-run")
    assert "(dry) would attach" in out[1]
    assert fetch_env["uploads"] == []


def test_fetch_commit_skip_and_failure(p2t, fetch_env, monkeypatch, capsys):
    out = run_main(p2t, monkeypatch, capsys, "--commit", "--items", "OTHER001",
                   "--fetch", "HASPDF01=https://a/1, NEWITEM1=https://a/2,BADITEM1=https://bad/3")
    assert out[1:4] == [
        "  HASPDF01: already has a PDF, skipping fetch",
        "  NEWITEM1 -> PDF ATTNEW01 uploaded (4kb)",
        "  BADITEM1: FETCH FAIL not a PDF",
    ]
    assert fetch_env["fetched"] == ["https://a/2", "https://bad/3"]
    assert fetch_env["uploads"] == [("NEWITEM1", "NEWITEM1.pdf", b"%PDF" + b"x" * 4096,
                                     "application/pdf", "Full Text PDF")]
    assert fetch_env["sleeps"][0] == 0.5
    item_gets = [c[1] for c in fetch_env["api"].calls if c[0] == "GET" and c[1].count("/") == 2]
    assert item_gets == ["/items/OTHER001", "/items/HASPDF01", "/items/NEWITEM1", "/items/BADITEM1"]


# ---------------------------------------------------------------------------
# tightening
# ---------------------------------------------------------------------------

def test_fetch_url_may_contain_equals_and_sizes_are_kib(p2t, fetch_env, monkeypatch, capsys):
    monkeypatch.setattr(p2t, "fetch_pdf", lambda url: fetch_env["fetched"].append(url) or b"%" * 10240)
    out = run_main(p2t, monkeypatch, capsys, "--fetch", "AAAAAAA1=https://x/get?id=1&v=2,BBBBBBB2=https://x/b")
    assert fetch_env["fetched"] == ["https://x/get?id=1&v=2", "https://x/b"]
    assert out[1] == "  AAAAAAA1 (dry) would attach PDF 10kb from https://x/get?id=1&v=2"
    assert out[2].startswith("  BBBBBBB2 (dry)")  # a dry item doesn't stop the loop
    assert out[3] == "2 items | 0 w/pdf | 0 already txt | 0 to convert"


def test_fetch_failure_does_not_stop_later_fetches(p2t, fetch_env, monkeypatch, capsys):
    monkeypatch.setattr(p2t, "fetch_pdf", lambda url: b"%" * 10240 if "good" in url else
                        (_ for _ in ()).throw(ValueError("nope")))
    out = run_main(p2t, monkeypatch, capsys, "--commit", "--fetch", "BADITEM1=https://bad,GOODITM1=https://good")
    assert out[1:3] == ["  BADITEM1: FETCH FAIL nope", "  GOODITM1 -> PDF ATTNEW01 uploaded (10kb)"]


def test_library_type_choices_enforced(p2t, monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["pdf_to_text.py", "--library-type", "team"])
    with pytest.raises(SystemExit) as exc:
        p2t.main()
    assert exc.value.code == 2
    assert "invalid choice: 'team'" in capsys.readouterr().err


def test_help_documents_new_options(p2t, monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["pdf_to_text.py", "--help"])
    with pytest.raises(SystemExit):
        p2t.main()
    out = " ".join(capsys.readouterr().out.split())
    assert "comma-list of KEY=URL: download an open-access PDF, attach it to KEY, then convert" in out
    assert "override ZOTERO_LIBRARY_ID (e.g. the SLR group id)" in out
    assert "--library-type {group,user}" in out

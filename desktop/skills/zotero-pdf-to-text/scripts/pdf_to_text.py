#!/usr/bin/env python3
"""Convert the PDF attachments of Zotero items to TXT and attach the TXT back to
each item, so every item ends up with both a PDF and a TXT attachment (TXT is far
cheaper/cleaner for downstream AI reading than a PDF).

Sources the PDF from local Zotero storage (~/Zotero/storage/<key>/<file>) when the
group library is synced locally, else downloads it via the Zotero API. Uploads the
TXT via Zotero's S3 form-POST flow (create attachment -> authorize -> multipart POST
to S3 -> register). Idempotent: items that already have a .txt attachment are skipped;
a failed upload deletes its half-created attachment so retries stay clean.

--fetch KEY=URL[,KEY=URL...] first downloads an open-access PDF (arXiv, OSTI, etc.),
attaches it to KEY as "Full Text PDF" via the same upload flow, then converts it.
Anything that isn't a real PDF (paywall or landing page) is refused, not attached.

Env: ZOTERO_API_KEY_RW (writes; legacy ZOTERO_API_KEY still works), ZOTERO_LIBRARY_ID, ZOTERO_LIBRARY_TYPE (group|user). DRY-RUN by default; pass --commit to upload.
--library-id / --library-type override the env (the env default is often the personal library).
Requires: pdftotext (poppler) on PATH.

Usage:
  python3 pdf_to_text.py --collection KEY[,KEY...]      # all items in these collections
  python3 pdf_to_text.py --items KEY[,KEY...]           # explicit parent item keys
  python3 pdf_to_text.py --collection KEY --limit 1     # validation batch (test 1 first!)
  python3 pdf_to_text.py --collection KEY               # DRY-RUN (default): convert, do NOT upload
  python3 pdf_to_text.py --collection KEY --commit      # actually upload the TXT attachments
  python3 pdf_to_text.py --library-id 6505702 --fetch ABCD1234=https://arxiv.org/pdf/2501.00001 --commit
"""
import argparse, hashlib, json, os, subprocess, sys, tempfile, time, urllib.parse, urllib.request, urllib.error, socket
from pathlib import Path
socket.setdefaulttimeout(120)
KEY=(os.environ.get("ZOTERO_API_KEY_RW") or os.environ.get("ZOTERO_API_KEY") or os.environ.get("ZOTERO_API_KEY_RO",""))
LIB=os.environ.get("ZOTERO_LIBRARY_ID","")
TYPE=os.environ.get("ZOTERO_LIBRARY_TYPE","group")
BASE=f"https://api.zotero.org/{'groups' if TYPE=='group' else 'users'}/{LIB}"


def set_library(lib_id, lib_type):
    """--library-id/--library-type override the env (the env default is often the personal library)."""
    global LIB, TYPE, BASE
    LIB, TYPE = lib_id, lib_type
    BASE = f"https://api.zotero.org/{'groups' if TYPE=='group' else 'users'}/{LIB}"
H={"Zotero-API-Key":KEY,"Zotero-API-Version":"3"}
STORAGE=Path.home()/"Zotero"/"storage"


def api(method,path,data=None,headers=None):
    hs=dict(H)
    if headers: hs.update(headers)
    body=data
    if isinstance(data,(dict,list)): body=json.dumps(data).encode(); hs["Content-Type"]="application/json"
    req=urllib.request.Request(f"{BASE}{path}",data=body,headers=hs,method=method)
    for a in range(5):
        try:
            with urllib.request.urlopen(req,timeout=120) as r:
                raw=r.read(); return json.loads(raw) if raw else None
        except urllib.error.HTTPError as e:
            if e.code in (429,500,502,503): time.sleep(int(e.headers.get("Retry-After","3"))+a); continue
            raise


def get_all(path):
    o,s=[],0
    while True:
        req=urllib.request.Request(f"{BASE}/{path}?limit=100&start={s}",headers=H)
        with urllib.request.urlopen(req,timeout=60) as r:
            d=json.load(r); tot=int(dict(r.headers).get("Total-Results",len(o)+len(d)))
        o+=d; s+=100
        if s>=tot or not d: break
    return o


def get_pdf_bytes(att):
    fn=att.get("filename") or ""
    local=STORAGE/att["key"]/fn
    if local.exists(): return local.read_bytes()
    with urllib.request.urlopen(urllib.request.Request(f"{BASE}/items/{att['key']}/file",headers=H),timeout=180) as r:
        return r.read()


def _delete_item(akey):
    try:
        v=api("GET",f"/items/{akey}")["version"]
        api("DELETE",f"/items/{akey}",headers={"If-Unmodified-Since-Version":str(v)})
    except Exception: pass


def upload_txt(parent, filename, data):
    return upload_file(parent, filename, data, "text/plain", filename)


def upload_file(parent, filename, data, ctype, title):
    """Zotero file upload: create attachment -> authorize (params=1) -> S3 multipart POST -> register."""
    att={"itemType":"attachment","parentItem":parent,"linkMode":"imported_file",
         "title":title,"filename":filename,"contentType":ctype}
    if ctype.startswith("text/"): att["charset"]="utf-8"
    res=api("POST","/items",[att])
    akey=res["successful"]["0"]["key"]
    try:
        md5=hashlib.md5(data).hexdigest(); mtime=int(time.time()*1000)
        form=f"md5={md5}&filename={urllib.parse.quote(filename)}&filesize={len(data)}&mtime={mtime}&params=1"
        auth=api("POST",f"/items/{akey}/file",form.encode(),
                 {"Content-Type":"application/x-www-form-urlencoded","If-None-Match":"*"})
        if auth.get("exists"): return akey,"exists"
        # S3 browser-POST: params fields first (order preserved), 'file' field LAST
        b="----ZoteroTxtUpload7MA4YWxkTrZu0gW"
        parts=[]
        for name,val in auth["params"].items():
            parts.append(f'--{b}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{val}\r\n'.encode())
        parts.append(f'--{b}\r\nContent-Disposition: form-data; name="file"; filename="{filename}"\r\n'
                     f'Content-Type: {ctype}\r\n\r\n'.encode()+data+b"\r\n")
        parts.append(f"--{b}--\r\n".encode())
        urllib.request.urlopen(urllib.request.Request(auth["url"],data=b"".join(parts),
            headers={"Content-Type":f"multipart/form-data; boundary={b}"},method="POST"),timeout=180).read()
        api("POST",f"/items/{akey}/file",(f"upload={auth['uploadKey']}").encode(),
            {"Content-Type":"application/x-www-form-urlencoded","If-None-Match":"*"})
        return akey,"uploaded"
    except Exception:
        _delete_item(akey)  # never leave an empty attachment (would block idempotent retry)
        raise


def fetch_pdf(url):
    """Download a PDF from an open URL; refuse anything that isn't actually a PDF (login pages, HTML)."""
    req=urllib.request.Request(url,headers={"User-Agent":"Mozilla/5.0 (zotero-pdf-to-text fetch)"})
    with urllib.request.urlopen(req,timeout=180) as r:
        data=r.read()
    if not data.startswith(b"%PDF"):
        raise ValueError(f"not a PDF (got {data[:15]!r}); likely paywalled or a landing page")
    return data


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--collection",help="comma-list of collection keys")
    ap.add_argument("--items",help="comma-list of explicit parent item keys")
    ap.add_argument("--limit",type=int,default=0)
    ap.add_argument("--dry-run",action="store_true")
    ap.add_argument("--commit",action="store_true",help="actually upload TXTs (default: dry-run, no writes)")
    ap.add_argument("--fetch",help="comma-list of KEY=URL: download an open-access PDF, attach it to KEY, then convert")
    ap.add_argument("--library-id",help="override ZOTERO_LIBRARY_ID (e.g. the SLR group id)")
    ap.add_argument("--library-type",default=None,choices=["group","user"])
    a=ap.parse_args()
    if a.library_id or a.library_type:
        set_library(a.library_id or LIB, a.library_type or TYPE)
    if not LIB: sys.exit("no library: set ZOTERO_LIBRARY_ID or pass --library-id")
    print(f"library: {TYPE} {LIB}")
    if a.fetch:
        keys=[]
        for pair in a.fetch.split(","):
            k,url=pair.split("=",1); k=k.strip(); keys.append(k)
            kids=api("GET",f"/items/{k}/children") or []
            if any((c["data"].get("contentType")=="application/pdf") for c in kids):
                print(f"  {k}: already has a PDF, skipping fetch"); continue
            try:
                pdf=fetch_pdf(url.strip())
            except Exception as e:
                print(f"  {k}: FETCH FAIL {e}"); continue
            if a.dry_run or not a.commit:
                print(f"  {k} (dry) would attach PDF {len(pdf)//1024}kb from {url}"); continue
            akey,how=upload_file(k,f"{k}.pdf",pdf,"application/pdf","Full Text PDF")
            print(f"  {k} -> PDF {akey} {how} ({len(pdf)//1024}kb)")
            time.sleep(0.5)
        a.items=",".join(filter(None,[a.items]+keys)) if a.items else ",".join(keys)
    if not a.collection and not a.items: sys.exit("need --collection, --items, or --fetch")

    parents={}; atts=[]
    if a.collection:
        for c in a.collection.split(","):
            for it in get_all(f"collections/{c.strip()}/items"):
                d=it["data"]
                if d.get("itemType")=="attachment": atts.append(d)
                elif d.get("itemType") not in ("note",): parents[d["key"]]=d
    if a.items:
        for k in a.items.split(","):
            k=k.strip(); parents[k]=api("GET",f"/items/{k}")["data"]
            for it in api("GET",f"/items/{k}/children") or []:
                if it["data"].get("itemType")=="attachment": atts.append(it["data"])
    pdfs={}; hastxt=set()
    for at in atts:
        p=at.get("parentItem"); ct=at.get("contentType",""); fn=(at.get("filename") or "")
        if p not in parents: continue
        if ct=="application/pdf" or fn.lower().endswith(".pdf"): pdfs[p]=at
        if ct=="text/plain" or fn.lower().endswith(".txt"): hastxt.add(p)
    todo=[k for k in parents if k in pdfs and k not in hastxt]
    if a.limit: todo=todo[:a.limit]
    print(f"{len(parents)} items | {len(pdfs)} w/pdf | {len(hastxt)} already txt | {len(todo)} to convert")
    ok=fail=0
    for i,k in enumerate(todo,1):
        att=pdfs[k]; fn=(att.get("filename") or f"{k}.pdf")
        txtname=(fn.rsplit(".",1)[0] if "." in fn else fn)+".txt"
        try:
            pdf=get_pdf_bytes(att)
            with tempfile.NamedTemporaryFile(suffix=".pdf",delete=False) as tf: tf.write(pdf); pp=tf.name
            tp=pp[:-4]+".txt"
            subprocess.run(["pdftotext","-enc","UTF-8",pp,tp],check=True,timeout=120)
            txt=Path(tp).read_bytes(); os.unlink(pp); os.unlink(tp)
            warn=" WARN-tiny" if len(txt)<200 else ""
            if a.dry_run or not a.commit: print(f"  [{i}/{len(todo)}] {k} (dry) -> {txtname} ({len(txt)}b){warn}"); ok+=1; continue
            akey,how=upload_txt(k,txtname,txt)
            print(f"  [{i}/{len(todo)}] {k} -> TXT {akey} {how} ({len(txt)//1024}kb){warn} :: {txtname[:48]}")
            ok+=1; time.sleep(0.3)
        except Exception as e:
            print(f"  [{i}/{len(todo)}] {k} FAIL: {e}"); fail+=1
    print(f"\ndone: {ok} converted, {fail} failed")


if __name__=="__main__": main()

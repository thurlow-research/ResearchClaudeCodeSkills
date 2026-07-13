---
name: zotero-pdf-to-text
description: Convert the PDF attachments of Zotero items into TXT attachments, so each item ends up with BOTH a PDF and a TXT — the TXT being far cheaper and cleaner for downstream AI reading (full-text extraction, coding, screening) than a PDF. Given collection keys or explicit item keys, it finds each item's PDF (from local ~/Zotero/storage if the group library is synced, else downloaded via the Zotero API), runs pdftotext, and uploads the TXT back as a child attachment via Zotero's S3 form-POST upload flow. Idempotent (skips items that already have a .txt), and a failed upload deletes its half-created attachment so retries stay clean. Use whenever the user says "convert the PDFs to text", "make TXT attachments", "each item should have a TXT and PDF", "extract full text for AI reading", or is prepping a Zotero core/extraction set for full-text AI processing. Complements the `zotero` and `zotero-merge-prep` skills.
---

# Zotero PDF → TXT

**Why:** downstream AI reading (extraction, coding, full-text screening) is much cheaper
and more reliable on plain text than on PDFs. This skill gives every item a `.txt`
sibling to its `.pdf`, in place, synced through Zotero.

## Setup
Env: `ZOTERO_API_KEY` (a **WRITE** key — this creates attachments), `ZOTERO_LIBRARY_ID`,
`ZOTERO_LIBRARY_TYPE` (`group`|`user`). Requires **`pdftotext`** (poppler) on PATH.
Because it writes, confirm a library backup first (Zotero `File → Export Library`).

## Usage
```
python3 scripts/pdf_to_text.py --collection KEY[,KEY...]   # every item in these collections
python3 scripts/pdf_to_text.py --items KEY[,KEY...]        # explicit parent item keys
python3 scripts/pdf_to_text.py --collection KEY --limit 1  # ALWAYS test 1 first
python3 scripts/pdf_to_text.py --collection KEY --dry-run  # convert locally, don't upload
```

## What it does, per item
1. Find the item's **PDF** child attachment. Read it from `~/Zotero/storage/<attachKey>/<file>`
   if synced locally, else **download it via the API** (`/items/<attachKey>/file`) — so it
   works even when files aren't downloaded to this machine.
2. `pdftotext -enc UTF-8` → a UTF-8 `.txt` (named after the PDF).
3. **Upload the TXT** as a child `imported_file` attachment of the same parent.
4. Skip if the item already has a `.txt` attachment (idempotent / resumable).

## The Zotero upload flow (the fiddly part — captured here so it isn't re-derived)
Zotero has no simple "PUT a file" endpoint; uploading is four steps:
1. `POST /items` — create the attachment item (`itemType:attachment`,
   `linkMode:imported_file`, `contentType:text/plain`, `filename`, `charset:utf-8`).
2. `POST /items/<key>/file` with `md5&filename&filesize&mtime&params=1` and header
   `If-None-Match: *` — returns **`{url, params, uploadKey}`** (an AWS S3 browser-POST
   authorization). *(Omitting `params=1` returns a `prefix`/`suffix` single-POST variant —
   that path 404'd in testing; the `params=1` S3 form-POST is the reliable one.)*
3. **multipart/form-data POST to `url`**: emit every `params` field first (order preserved),
   then a **`file` field LAST** carrying the bytes. S3 returns 201.
4. `POST /items/<key>/file` with `upload=<uploadKey>` and `If-None-Match: *` — registers it.

`filesize`/`md5` in step 2 must exactly match the bytes sent (the S3 policy pins
`content-length-range`). On any failure the half-created attachment is **deleted**, so a
leftover empty `.txt` never blocks the idempotency check on retry.

## Notes
- **Always run `--limit 1` first** and verify the TXT attachment appears with an `md5`
  (file actually uploaded) before batching — the multi-step upload is easy to get subtly wrong.
- A `WARN-tiny` on a `<200 byte` TXT usually means a scanned/image-only PDF (no text layer);
  those need OCR, which this skill does not do.
- Pairs with `build_txt_map.py`-style harnesses that then *consume* the `.txt` attachments
  for extraction/coding.

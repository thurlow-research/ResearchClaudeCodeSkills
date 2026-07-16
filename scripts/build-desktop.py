#!/usr/bin/env python3
"""Build the Claude Desktop (claude.ai) variant of the skills.

Copies skills/ -> desktop/skills/, patches each SKILL.md so credentials are
sourced from the Claude project instructions (or global preferences) instead of
shell env / .env files, then zips each skill (one zip per skill, folder at zip
root) into desktop/zips/ ready to upload via Settings > Capabilities > Skills.

Patches use exact-match anchors against skills/*/SKILL.md and FAIL LOUDLY if an
anchor no longer matches, so a drifted source can't silently ship unpatched.

Usage: python3 scripts/build-desktop.py
"""
import shutil
import sys
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SRC = REPO / "skills"
OUT = REPO / "desktop"
SKILLS_OUT = OUT / "skills"
ZIPS_OUT = OUT / "zips"


def block(keys_example: str, invocation: str) -> str:
    return f"""## Credentials in this environment (Claude Desktop / claude.ai)

This skill runs in Claude's sandboxed code-execution environment: there is no shell
profile, no direnv, and no saved config files. The API keys are provided as `KEY=value`
lines in the **project instructions** (or the user's global preferences), using the same
variable names the CLI already reads:

```
{keys_example}
```

Read the values from the instructions and pass them **inline as environment variables on
every script invocation**:

```
{invocation}
```

Rules:
- Never print, echo, or quote key values back in your reply to the user.
- If a required key is missing from the project/global instructions, ask the user to add
  it there (or paste it in chat for this conversation only). Do not invent values."""


ZOTERO_KEYS = """ZOTERO_API_KEY_RO=xxxxxxxxxxxxxxxxxxxxxxxx   # reads
ZOTERO_API_KEY_RW=xxxxxxxxxxxxxxxxxxxxxxxx   # writes (e.g. tag-add --commit); omit if you only read
ZOTERO_LIBRARY_ID=1234567
ZOTERO_LIBRARY_TYPE=group"""

# (old, new) exact-match replacement pairs per skill
PATCHES = {
    "zotero": [
        (
            """The script looks for these values in the following order (first hit wins for files; already-set environment variables always take precedence over file values):

1. Command-line flags: `--api-key`, `--library-id`, `--library-type`, `--collection`
2. Existing shell environment variables
3. A file pointed to by `$ZOTERO_ENV_FILE` (explicit override)
4. `./.env` in the current working directory
5. `~/.config/claude-zotero/.env` — the standard per-user config location

The recommended setup is to keep a single `.env` at `~/.config/claude-zotero/.env`. That way the skill works from any directory and the file lives outside any git repo.

**Read/write key split (least-privilege).** The script also accepts a split pair — `ZOTERO_API_KEY_RO` (reads) and `ZOTERO_API_KEY_RW` (writes, e.g. `tag-add --commit`): it prefers RO for reads and RW for writes, and falls back to a single `ZOTERO_API_KEY` if that's all that's set. Prefer the split so read-only work never carries a write-capable key.

If any required variable is missing when the skill is first used, ask the user to supply it. Do not invent values.""",
            block(
                ZOTERO_KEYS,
                "ZOTERO_API_KEY_RO=... ZOTERO_LIBRARY_ID=... ZOTERO_LIBRARY_TYPE=group \\\n  python3 scripts/zotero.py collections",
            ),
        ),
        (
            "**Invoke it with `python3` (not `python`)** — the `python` alias is not on this machine's PATH.",
            "**Invoke it with `python3`**, passing the credentials inline (see above).",
        ),
    ],
    "semantic-scholar": [
        (
            """The API works without a key but on a heavily rate-limited shared pool (you'll see repeated `429`s). The user **has a key**, so configure it:

- Env var (canonical): `SEMANTIC_SCHOLAR_API_KEY` — e.g. `export SEMANTIC_SCHOLAR_API_KEY="..."` in `~/.zshrc`.
- Or a file: `SEMANTIC_SCHOLAR_API_KEY=...` in `~/.config/claude-s2/.env` (works from any directory).
- Or pass `--api-key` per call.

Resolution order (shell env wins over file): `--api-key` → `SEMANTIC_SCHOLAR_API_KEY` (also accepts `S2_API_KEY` / `SEMANTICSCHOLAR_API_KEY`) → `$S2_ENV_FILE` → `./.env` → `~/.config/claude-s2/.env`. Request a key at https://www.semanticscholar.org/product/api#api-key if missing — don't invent one.""",
            "The API works without a key but on a heavily rate-limited shared pool (you'll see repeated `429`s), so use the user's key.\n\n"
            + block(
                "SEMANTIC_SCHOLAR_API_KEY=xxxxxxxxxxxxxxxxxxxxxxxx",
                'SEMANTIC_SCHOLAR_API_KEY=... python3 scripts/s2.py search "vibe coding" --limit 10',
            )
            + "\n\nIf the user has no key, they can request one at https://www.semanticscholar.org/product/api#api-key.",
        ),
        (
            "**Invoke with `python3`** (the `python` alias isn't on PATH).",
            "**Invoke with `python3`**, passing the key inline (see above).",
        ),
    ],
    "openalex": [
        (
            """```
export OPENALEX_API_KEY="..."              # STRONGLY ADVISED — ~10x quota, avoids throttling
export OPENALEX_MAILTO="you@example.edu"   # your email — polite pool
```""",
            block(
                "OPENALEX_API_KEY=xxxxxxxxxxxxxxxxxxxxxxxx\nOPENALEX_MAILTO=you@example.edu",
                "OPENALEX_API_KEY=... OPENALEX_MAILTO=... python3 scripts/openalex.py work DOI:10.1145/3597503",
            ),
        ),
    ],
    "zotero-merge-prep": [
        (
            """## Setup
Env: `ZOTERO_API_KEY`, `ZOTERO_LIBRARY_ID`, `ZOTERO_LIBRARY_TYPE` (`group`|`user`).
Optional: `OPENALEX_MAILTO` — enables OpenAlex gap-filling (via the `openalex` skill).""",
            """## Setup
Required: `ZOTERO_API_KEY` (a **WRITE** key — this edits records), `ZOTERO_LIBRARY_ID`,
`ZOTERO_LIBRARY_TYPE` (`group`|`user`). Optional: `OPENALEX_MAILTO`, `OPENALEX_API_KEY`
— enable OpenAlex gap-filling.

"""
            + block(
                ZOTERO_KEYS + "\nOPENALEX_MAILTO=you@example.edu",
                'ZOTERO_API_KEY=... ZOTERO_LIBRARY_ID=... ZOTERO_LIBRARY_TYPE=group \\\n  python3 scripts/merge_prep.py find "TITLE"',
            ),
        ),
    ],
    "zotero-pdf-to-text": [
        (
            """## Setup
Env: `ZOTERO_API_KEY` (a **WRITE** key — this creates attachments), `ZOTERO_LIBRARY_ID`,
`ZOTERO_LIBRARY_TYPE` (`group`|`user`). Requires **`pdftotext`** (poppler) on PATH.
Because it writes, confirm a library backup first (Zotero `File → Export Library`).""",
            """## Setup
Required: `ZOTERO_API_KEY` (a **WRITE** key — this creates attachments), `ZOTERO_LIBRARY_ID`,
`ZOTERO_LIBRARY_TYPE` (`group`|`user`). Requires **`pdftotext`** (poppler) on PATH.
Because it writes, confirm a library backup first (Zotero `File → Export Library`).

"""
            + block(
                ZOTERO_KEYS,
                "ZOTERO_API_KEY=... ZOTERO_LIBRARY_ID=... ZOTERO_LIBRARY_TYPE=group \\\n  python3 scripts/pdf_to_text.py --collection KEY --limit 1",
            )
            + """

Environment caveats (claude.ai sandbox):
- There is no local `~/Zotero/storage` here — the script detects that and API-downloads
  each PDF automatically; nothing to configure.
- **Check `pdftotext -v` before starting.** If poppler isn't available in the sandbox,
  stop and tell the user this skill can't run in this environment (run it from Claude
  Code on their machine instead).""",
        ),
    ],
}


# claude.ai rejects skill uploads whose frontmatter description exceeds this
DESCRIPTION_LIMIT = 1024

# Desktop-only replacements for descriptions over the limit (keep the trigger
# phrases — the description is what Claude matches against to pick the skill)
SHORT_DESCRIPTIONS = {
    "zotero-merge-prep": (
        "Consolidate duplicate Zotero records BEFORE running Zotero's native \"Merge Items\", "
        "so the merge is lossless. Zotero's merge keeps only the master record's field values "
        "(silently dropping metadata the other copies had) and only groups items of the SAME "
        "item type — so cross-type duplicates (preprint vs journalArticle vs conferencePaper) "
        "are never detected. Given a title (or explicit item keys), this skill finds the "
        "duplicates, confirms they're the same work, UNIONS their metadata (authors, abstract, "
        "DOI, URL, venue, date — gap-filling from OpenAlex), and NORMALIZES their item types "
        "(tagging orig-type:/orig-date: for lineage), so Zotero's Duplicate Items merge drops "
        "nothing. Use when the user says \"prep duplicates before merging\", \"Zotero dedupe "
        "isn't detecting these\", \"consolidate these duplicate records\", \"these two entries "
        "are the same paper\", or \"fix the metadata before I dedupe\". Complements the "
        "`zotero` and `openalex` skills."
    ),
}


def shorten_description(name: str, text: str, errors: list) -> str:
    import re

    m = re.search(r"^description: (.+)$", text, flags=re.MULTILINE)
    if not m:
        errors.append(f"{name}: no description line found in frontmatter")
        return text
    if len(m.group(1)) <= DESCRIPTION_LIMIT:
        return text
    short = SHORT_DESCRIPTIONS.get(name)
    if short is None:
        errors.append(
            f"{name}: description is {len(m.group(1))} chars (limit {DESCRIPTION_LIMIT}) "
            "and no SHORT_DESCRIPTIONS entry exists"
        )
        return text
    if len(short) > DESCRIPTION_LIMIT:
        errors.append(f"{name}: SHORT_DESCRIPTIONS entry is itself {len(short)} chars")
        return text
    return text[: m.start(1)] + short + text[m.end(1):]


def main() -> None:
    if SKILLS_OUT.exists():
        shutil.rmtree(SKILLS_OUT)
    if ZIPS_OUT.exists():
        shutil.rmtree(ZIPS_OUT)
    SKILLS_OUT.mkdir(parents=True)
    ZIPS_OUT.mkdir(parents=True)

    errors = []
    for name, patches in sorted(PATCHES.items()):
        src_dir = SRC / name
        if not src_dir.is_dir():
            errors.append(f"{name}: missing source dir {src_dir}")
            continue
        dst_dir = SKILLS_OUT / name
        shutil.copytree(
            src_dir, dst_dir,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".DS_Store"),
        )

        skill_md = dst_dir / "SKILL.md"
        text = skill_md.read_text(encoding="utf-8")
        for old, new in patches:
            if old not in text:
                errors.append(f"{name}: anchor not found (source drifted?):\n  {old.splitlines()[0]}...")
                continue
            if text.count(old) > 1:
                errors.append(f"{name}: anchor matches more than once:\n  {old.splitlines()[0]}...")
                continue
            text = text.replace(old, new)
        text = shorten_description(name, text, errors)
        skill_md.write_text(text, encoding="utf-8")

        zip_path = ZIPS_OUT / f"{name}.zip"
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for f in sorted(dst_dir.rglob("*")):
                if f.is_file():
                    zf.write(f, f.relative_to(SKILLS_OUT))
        print(f"built {zip_path.relative_to(REPO)}")

    if errors:
        sys.exit("FAILED:\n" + "\n".join(errors))
    print(f"\nOK — {len(PATCHES)} desktop skills in {SKILLS_OUT.relative_to(REPO)}, zips in {ZIPS_OUT.relative_to(REPO)}")


if __name__ == "__main__":
    main()

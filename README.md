# Research Claude Code Skills

Claude Code skills for **systematic literature reviews and scholarly research** — connectors and
tools for **Zotero**, **Semantic Scholar**, **OpenAlex**, and **Exa**. Discover papers, enrich
bibliographic metadata, de-duplicate a reference library, and prepare full text for AI reading —
all driven by natural-language requests inside Claude Code.

## Skills

| Skill | Purpose | Source / auth |
|---|---|---|
| **`zotero`** | Query & retrieve items, collections, tags from a Zotero library | Zotero API key |
| **`zotero-merge-prep`** | Consolidate duplicates (union metadata + normalize item types) so Zotero's *Merge Items* is lossless | Zotero **write** key |
| **`zotero-pdf-to-text`** | Convert each item's PDF into a TXT attachment for cheap AI reading | Zotero **write** key + `pdftotext` |
| **`semantic-scholar`** | Citation-graph search & backward/forward snowballing | S2 key (optional) |
| **`openalex`** | Metadata/abstract/citation backstop, cached | OpenAlex key **(strongly advised)** |
| **`exa`** | Open-web / grey-literature discovery (marketplace plugin) | Exa key / OAuth |

## Quick start

```bash
# 1. install the custom skills
cp -R skills/* ~/.claude/skills/
# ...or download the zip from the Releases page and: unzip research-claude-code-skills.zip -d ~/.claude/skills/

# 2. install the exa plugin (inside Claude Code)
#    /plugin install exa@claude-plugins-official

# 3. set your own API keys (see docs/SETUP.md section 3), then restart Claude Code
```

Then just ask Claude: *"dedupe the duplicates in this Zotero collection"*, *"backfill missing
abstracts from OpenAlex"*, *"convert the PDFs in my Core collection to text"*, etc.

**Full setup, per-skill reference, and gotchas -> [`docs/SETUP.md`](docs/SETUP.md).**

> **Get an OpenAlex API key.** OpenAlex is credit-metered; without a key you'll be throttled
> (HTTP 429) on any real workload (anonymous ~100 req/day). A free key (~30-sec signup at
> openalex.org/settings/api) raises it ~10x and is the difference between the skill working and
> hanging on rate limits.

## Prerequisites

- Claude Code · Python 3.9+ (stdlib only — no `pip install`) · `poppler`/`pdftotext` (for
  `zotero-pdf-to-text` only: `brew install poppler` or `apt-get install poppler-utils`).

## Repository layout

```
skills/      the 5 custom skills (SKILL.md + scripts + reference docs)
docs/        SETUP.md — full setup & reference guide
releases/    build-output dir; the archive is published via GitHub Releases (not committed)
scripts/     build-release.sh — regenerates the release archive from skills/
.github/     CODEOWNERS
```

## Download

Grab the packaged skills archive from the
[**Releases**](https://github.com/thurlow-research/ResearchClaudeCodeSkills/releases) page, or
just clone and `cp -R skills/*` (above). Build it yourself with `scripts/build-release.sh`.

## A typical review

discover (`semantic-scholar`, `exa`) -> import (Zotero) -> enrich (`openalex`) ->
de-duplicate (`zotero-merge-prep`) -> screen/triage (`zotero`) -> extract-prep (`zotero-pdf-to-text`).

## Security

The scripts contain **no keys** — everything is read from environment variables. Never commit
your `.envrc` or filled-in env block; each user supplies their own keys. Zotero **write** keys can
modify items, so keep a library export backup before bulk writes.

## License

MIT — see [LICENSE](LICENSE).

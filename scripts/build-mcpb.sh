#!/usr/bin/env bash
# Build the installable Claude Desktop extension (.mcpb) from this repo.
#
#   bash scripts/build-mcpb.sh                    # python3  (macOS / Linux)
#   bash scripts/build-mcpb.sh --python python    # python   (Windows)
#   bash scripts/build-mcpb.sh --python "C:/Python312/python.exe"
#
# Why a flag rather than runtime detection: `mcp_config.command` is a literal
# string in the manifest, and no single value works everywhere. macOS ships
# `python3` and usually has no `python`; the python.org Windows installer ships
# `python` and usually no `python3`. Guessing wrong fails at launch with an
# unhelpful error, so the interpreter is chosen at build time and recorded in
# the artifact's filename.
#
# Output: releases/research-skills[-<suffix>].mcpb  (git-ignored)

set -euo pipefail

PYTHON_CMD="python3"
SUFFIX=""
while [ $# -gt 0 ]; do
  case "$1" in
    --python) PYTHON_CMD="$2"; shift 2 ;;
    -h|--help) sed -n '2,17p' "$0"; exit 0 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done
case "$PYTHON_CMD" in
  python3) SUFFIX="" ;;
  python)  SUFFIX="-windows" ;;
  *)       SUFFIX="-custom" ;;
esac

REPO="$(cd "$(dirname "$0")/.." && pwd)"
BUILD="$(mktemp -d)"
OUT="$REPO/releases/research-skills${SUFFIX}.mcpb"
trap 'rm -rf "$BUILD"' EXIT

# --- layout ---------------------------------------------------------------
# manifest.json at the root; the server one level down so that its own
# `Path(__file__).parent.parent / "skills"` resolves to the bundled skills tree
# without patching the server. RESEARCH_MCP_SKILLS_DIR is also set in the
# manifest, so resolution works even if that relative assumption ever changes.
mkdir -p "$BUILD/server" "$BUILD/skills"
cp "$REPO/mcp/research_mcp.py" "$BUILD/server/"
cp -R "$REPO/skills/." "$BUILD/skills/"

# never ship editor debris or caches
find "$BUILD" \( -name '*.bak-*' -o -name '*.pyc' -o -name '.DS_Store' \) -delete
find "$BUILD" -name '__pycache__' -type d -prune -exec rm -rf {} +

# --- manifest -------------------------------------------------------------
python3 - "$REPO/packaging/manifest.json" "$BUILD/manifest.json" "$PYTHON_CMD" <<'PY'
import json, sys
src, dst, cmd = sys.argv[1], sys.argv[2], sys.argv[3]
m = json.load(open(src))
m["server"]["mcp_config"]["command"] = cmd
json.dump(m, open(dst, "w"), indent=2)
print(f"  manifest: command={cmd}, {len(m['tools'])} tools, {len(m['user_config'])} config fields")
PY

# --- guard: no secrets, no stray full texts -------------------------------
if grep -rIqE "(api[_-]?key|secret|token)[[:space:]]*[:=][[:space:]]*['\"][A-Za-z0-9_-]{16,}" "$BUILD"; then
  echo "ABORT: something that looks like a credential is in the bundle" >&2
  exit 1
fi
STRAY="$(find "$BUILD/skills" -maxdepth 2 -name '*.txt' -size +40k | head -5)"
if [ -n "$STRAY" ]; then
  echo "ABORT: large .txt files in skills/ (paper full texts must not ship):" >&2
  echo "$STRAY" >&2
  exit 1
fi

# --- pack -----------------------------------------------------------------
mkdir -p "$REPO/releases"
rm -f "$OUT"
( cd "$BUILD" && zip -qr "$OUT" . -x '.*' )

echo "built: $OUT"
echo "  $(cd "$BUILD" && find . -type f | wc -l | tr -d ' ') files, $(du -h "$OUT" | cut -f1)"
echo
echo "Install: Claude Desktop -> Settings -> Extensions -> install from file."
echo "Verify : the 7 tools appear, and a zotero collections call returns real data."

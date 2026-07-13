#!/usr/bin/env bash
# Rebuild the distributable skills archive from skills/ into releases/.
# The archive unpacks directly into ~/.claude/skills/ (skill folders at its root).
set -euo pipefail
cd "$(dirname "$0")/.."
out="releases/research-claude-code-skills.zip"
rm -f "$out"
( cd skills && zip -rq "../$out" . -x '*__pycache__*' '*.pyc' '*.DS_Store' )
echo "built $out"

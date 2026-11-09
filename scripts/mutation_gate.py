#!/usr/bin/env python3
"""Fail CI when mutants on lines a change touched survive.

`mutmut run` mutates whole files, and most of this repo's code predates its
tests, so a repo-wide mutation score mostly measures legacy debt. This gate
is the mutation-testing analogue of diff-cover: it maps every mutant back to
the source line it altered and scores only mutants on lines changed relative
to a base ref. Untested changed code counts against the score ("no tests"
mutants are treated as survivors).

Run after `mutmut run`:

    python3 scripts/mutation_gate.py --base origin/main --fail-under 90

Functions that are declarative config rather than logic (argparse wiring,
JSON schemas) can be excluded in pyproject.toml:

    [tool.mutation-gate]
    exclude_functions = ["build_parser"]
"""

from __future__ import annotations

import argparse
import ast
import difflib
import re
import subprocess
import sys
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # Python < 3.11
    tomllib = None

REPO = Path(__file__).resolve().parent.parent
MUTANTS = REPO / "mutants"
KILLED = {"killed"}
COUNTED = KILLED | {"survived", "no tests", "timeout", "suspicious"}
_HUNK_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")
_MUTANT_RE = re.compile(r"^(?P<prefix>.+?)__mutmut_(?P<n>\d+)$")


def changed_lines(base: str, path: str) -> set[int]:
    """Line numbers in the working tree's `path` added or modified since `base`."""
    diff = subprocess.run(
        ["git", "diff", "-U0", f"{base}...HEAD", "--", path],
        capture_output=True, text=True, check=True, cwd=REPO,
    ).stdout
    lines: set[int] = set()
    for line in diff.splitlines():
        m = _HUNK_RE.match(line)
        if m:
            start, count = int(m.group(1)), int(m.group(2) or "1")
            lines.update(range(start, start + count))
    return lines


def function_starts(source: str) -> dict[str, int]:
    """Map mutmut's mangled function name to the `def` line in the real source."""
    starts: dict[str, int] = {}
    tree = ast.parse(source)
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            starts[f"x_{node.name}"] = node.lineno
        elif isinstance(node, ast.ClassDef):
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    starts[f"xǁ{node.name}ǁ{item.name}"] = item.lineno
    return starts


def mutant_lines(rel: str) -> dict[str, tuple[str, set[int]]]:
    """For each mutant in one file: (function, source lines it altered)."""
    mutated_src = (MUTANTS / rel).read_text()
    mutated = mutated_src.splitlines()
    source = (REPO / rel).read_text().splitlines()
    starts = function_starts("\n".join(source))
    # Locate each mutant by parsing the mutated file rather than trusting
    # mutmut's .spans sidecar, whose offsets don't line up with `def` lines.
    spans: dict[str, tuple[int, int]] = {}
    for node in ast.walk(ast.parse(mutated_src)):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and "__mutmut_" in node.name:
            spans[node.name] = (node.lineno, node.end_lineno)

    out: dict[str, tuple[str, set[int]]] = {}
    for name, (first, last) in spans.items():
        m = _MUTANT_RE.match(name)
        if not m or m.group("n") == "orig":
            continue
        prefix = m.group("prefix")
        orig = spans.get(f"{prefix}__mutmut_orig")
        if orig is None or prefix not in starts:
            continue
        # Bodies are compared with the mangled def name restored, so only the
        # mutated line(s) differ.
        orig_body = [line.replace(f"{prefix}__mutmut_orig", "") for line in mutated[orig[0] - 1:orig[1]]]
        mut_body = [line.replace(name, "") for line in mutated[first - 1:last]]
        start = starts[prefix]
        real = [line.replace(prefix.split("\u01c1")[-1].removeprefix("x_"), "", 1) if i == 0 else line
                for i, line in enumerate(source[start - 1:start - 1 + len(orig_body)])]
        if orig_body[1:] != real[1:]:
            sys.exit(f"mutation gate: {rel}: mutmut's copy of {prefix} doesn't match the source; "
                     "line mapping would be wrong (mutmut output format changed?)")
        touched: set[int] = set()
        matcher = difflib.SequenceMatcher(a=orig_body, b=mut_body, autojunk=False)
        for tag, i1, i2, _, _ in matcher.get_opcodes():
            if tag == "equal":
                continue
            # Offset 0 is the `def` line; the source function starts there too.
            touched.update(start + i for i in range(i1, max(i2, i1 + 1)))
        out[name] = (prefix, touched)
    return out


def mutant_statuses() -> dict[str, str]:
    res = subprocess.run(
        [sys.executable, "-m", "mutmut", "results", "--all", "true"],
        capture_output=True, text=True, check=True, cwd=REPO,
    ).stdout
    statuses = {}
    for line in res.splitlines():
        name, sep, status = line.strip().rpartition(": ")
        if sep:
            statuses[name] = status.strip()
    return statuses


def load_config() -> dict:
    pyproject = REPO / "pyproject.toml"
    if tomllib is None or not pyproject.exists():
        return {}
    data = tomllib.loads(pyproject.read_text())
    return {
        "source_paths": data.get("tool", {}).get("mutmut", {}).get("source_paths", []),
        **data.get("tool", {}).get("mutation-gate", {}),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--base", default="origin/main", help="git ref to diff against")
    ap.add_argument("--fail-under", type=float, default=90.0, help="minimum score (%%) on changed lines")
    args = ap.parse_args()

    config = load_config()
    excluded = set(config.get("exclude_functions", []))
    statuses = mutant_statuses()

    counted, killed, survivors = 0, 0, []
    for rel in config.get("source_paths", []):
        changed = changed_lines(args.base, rel)
        if not changed:
            continue
        module = rel[:-3].replace("/", ".")
        for name, (prefix, lines) in mutant_lines(rel).items():
            func = prefix.split("ǁ")[-1].removeprefix("x_")
            if func in excluded or not lines & changed:
                continue
            status = statuses.get(f"{module}.{name}", "not checked")
            if status not in COUNTED:
                continue
            counted += 1
            if status in KILLED:
                killed += 1
            else:
                survivors.append((rel, min(lines & changed), name, status))

    if not counted:
        print("mutation gate: no mutants on changed lines")
        return 0
    score = 100.0 * killed / counted
    print(f"mutation gate: {killed}/{counted} mutants on changed lines killed ({score:.1f}%, "
          f"need {args.fail_under:.1f}%)")
    for rel, line, name, status in sorted(survivors):
        print(f"  {rel}:{line}  {status:<10} mutmut show {rel[:-3].replace('/', '.')}.{name}")
    return 0 if score >= args.fail_under else 1


if __name__ == "__main__":
    sys.exit(main())

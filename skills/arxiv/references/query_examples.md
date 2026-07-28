# arXiv Query Translation Examples

Examples of translating queries from other databases to arXiv API syntax.

## Field Operators

| arXiv | Meaning |
|-------|---------|
| `abs:` | Abstract |
| `ti:` | Title |
| `au:` | Author |
| `all:` | Any field (title + abstract + full text) |
| `cat:` | arXiv category (e.g., `cat:cs.SE`) |

## Syntax Notes

- **Phrases:** double quotes — `abs:"vibe coding"`
- **Boolean:** UPPERCASE `AND`, `OR`, `NOT`
- **Grouping:** parentheses — `(abs:"A" OR abs:"B")`
- **No wildcards:** `developer*` does NOT work — drop the asterisk
- **Hyphens in phrases:** quoted phrases match literally — `"vibe-coding"` and `"vibe coding"` are different searches; include both as separate OR terms when the construct may appear either way

## Translation Examples

### Three-cluster Boolean query

**ACM source:**
```
Abstract: ("multi-agent" OR "adversarial agent" OR "LLM-as-judge")
  AND ("code review" OR "code generation" OR "AI-generated code")
  AND ("human oversight" OR "human-in-the-loop" OR "scalable oversight")
```

**arXiv equivalent:**
```
(abs:"multi-agent" OR abs:"adversarial agent" OR abs:"LLM-as-judge")
  AND (abs:"code review" OR abs:"code generation" OR abs:"AI-generated code")
  AND (abs:"human oversight" OR abs:"human-in-the-loop" OR abs:"scalable oversight")
```

Note: each term gets its own `abs:` prefix — there's no shorthand for "abstract contains any of these."

### Prompts-as-artifact construct (Q-arXiv-07)

```
(abs:"prompt engineering" OR abs:"prompt versioning" OR abs:"prompt management"
 OR abs:"prompt as artifact" OR abs:"prompt artifact" OR abs:"prompt template"
 OR abs:"prompt repository")
AND (abs:"software engineering" OR abs:"software development"
 OR abs:"code generation" OR abs:"developer")
AND (abs:"reproducibility" OR abs:"version control" OR abs:"artifact"
 OR abs:"governance" OR abs:"oversight" OR abs:"provenance")
```

### Category restriction

Restrict to CS.SE + CS.HC:
```
(abs:"X" OR abs:"Y") AND (cat:cs.SE OR cat:cs.HC)
```

### Date restriction

arXiv API doesn't have a clean date filter in the search syntax. Two options:
1. **Sort descending by submitted date** (script default) and stop fetching once results pre-date the cutoff
2. **Post-filter** by year in the results

For SLR consistency (2020+ filter), use option 2 — post-filter the entries after fetch.

## Tightening an Over-Broad Query

If `check_count.py` returns >500 results, the query is too broad. Tighten by:

1. **Adding a third cluster** (the AND requirement narrows aggressively)
2. **Using exact phrases instead of single terms** (`"prompt engineering"` instead of `prompt`)
3. **Adding domain anchors** (`software engineering`, `developer`)
4. **Removing the broadest terms** from each cluster

Example: a single-cluster query like `(abs:"prompt" AND abs:"reproducibility")` will match thousands. Adding `(abs:"software engineering" OR abs:"code generation")` narrows by an order of magnitude.

## Common Pitfalls

- **Wildcards don't work** — `developer*` matches literally, not as a stem
- **Lowercase boolean** — `and` is treated as a search term, not an operator
- **No nested grouping limit** — use parentheses freely
- **Result cap** — arXiv returns up to 30,000 results in a single query, but pagination is required for >50 at a time

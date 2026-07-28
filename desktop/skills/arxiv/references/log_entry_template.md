# Query Log Entry Template

When adding a new arXiv query to `Query_Composition_and_Log.xlsx`, use this template.

## Required Columns

| Column | Value |
|--------|-------|
| # | `Q-arXiv-NN` (next sequential number) |
| Category | Category name — match existing query categories (e.g., `Human Oversight & Capacity`, `Org Governance & Policy`) |
| Query | Full arXiv API query string |
| Database | `arXiv` |
| Field | `Abstract` (for `abs:` queries) |
| Date Run | ISO date when the API was hit (e.g., `2026-05-22`) |
| Hit Count | Total results returned by arXiv |
| Comment | Description of the construct, date filter applied, related queries, import method |

## Comment Template

```
<Construct description>. Pairs with Q-XXX-NN, Q-YYY-NN. Date filter 2020+ applied. Imported via API script (fetch_arxiv_query.py).
```

Example:

```
Multi-agent adversarial oversight as scaling mechanism. Pairs with Q-IEX-23, Q-ACM-07, Q-SCO-06. Date filter 2020+ applied. Imported via API script (fetch_arxiv_query.py).
```

## Standard Workflow

1. **Construct the query** following `references/query_examples.md`
2. **Run `check_count.py`** to verify the result count is reasonable (<500)
3. **If too high**, tighten the query and repeat
4. **Create the Zotero collection** (`Q-arXiv-NN`) in the Zotero client under `arXiv / 01-Imports` or wherever the query collections live
5. **Run `fetch_arxiv_query.py`** with `--apply`
6. **Log the entry** in `Query_Composition_and_Log.xlsx` using the template above
7. **After all related queries imported**, run Zotero client-side dedup
8. **Use the zotero-merge-prep skill** to handle cross-source duplicates that survived client dedup

# Release artifacts

The packaged skills archive is **not committed to the repo**. It is published as a
**GitHub Release** asset:

  https://github.com/thurlow-research/ResearchClaudeCodeSkills/releases

## Build it locally
```
bash scripts/build-release.sh      # writes releases/research-claude-code-skills.zip (git-ignored)
```
Then attach that file to a GitHub Release (e.g. `gh release create v0.1.0 releases/research-claude-code-skills.zip`).

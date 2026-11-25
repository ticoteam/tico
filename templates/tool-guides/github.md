---
name: GitHub
category: engineering
summary: "Repositories, pull requests, issues, checks and releases, through the gh CLI"
commands: [gh]
---
## What it is for
Reading and changing what lives on GitHub: pull requests, their checks and reviews, issues, releases and workflow runs.
Use `gh` rather than scraping pages or calling the API by hand.

## How to use it
`gh` reads `GH_TOKEN` from your environment; on a Tico computer the GitHub App gives you one for the repositories you
may reach. Ask for a repository you lack with `tico access request <owner/name>`.

    gh pr list --state open --limit 20
    gh pr view 123 --json title,state,mergeable,statusCheckRollup
    gh pr checks 123 --watch --interval 60
    gh run view <run id> --log-failed
    gh api repos/{owner}/{repo}/pulls/123/files --paginate --jq '.[].filename'

## Good to know
- `--json` with `--jq` gives exact fields; prefer it over reading the text output.
- One watcher per pull request: polling many in parallel uses up the rate limit.

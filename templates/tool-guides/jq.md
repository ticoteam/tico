---
name: jq
category: data
summary: "Pick, filter and reshape JSON on the command line"
commands: [jq]
---
## What it is for
Pulling exact fields out of JSON from an API or a CLI, and turning it into a list, a count or CSV.

## How to use it

    gh api repos/OWNER/REPO/pulls | jq -r '.[] | [.number, .title] | @tsv'
    curl -s "$URL" | jq '[.items[] | select(.status == "open")] | length'
    jq -r '.rows[] | [.name, .total] | @csv' report.json > report.csv

## Good to know
- Count and sum with jq rather than by reading the output.

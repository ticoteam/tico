---
name: "build-a-forecast-call-pack"
description: "Build the forecast part of the weekly summary or a forecast call: commit, best case and the gap to target, each number with its source and date, and the few deals the call should inspect."
---
# Build a forecast call pack

When to use: the weekly summary or a forecast call needs the period's numbers, or a person asks what the forecast really is.

Steps:
1. Take the totals from the latest roll-up that `sales-ops` published. Write the report's name, its read date and the period it covers.
2. Write commit, best case and pipeline using the team's own definitions in `knowledge/pipeline-rules.md`. Forecast category sets differ between companies, so do not assume one.
3. Give the gap to the period's target in one line, if `hub goal list` shows a goal. If there is none, say so. Do not invent a target.
4. Check each commit deal for a buyer step with a date and a named signer. A commit deal missing either one goes on the inspect list.
5. Check the other direction: a deal due to close this period that is still in pipeline is a question for the owner, not a number to add.
6. Pick at most five deals to inspect. Order: largest commit without evidence, closing this period but still in pipeline, moved close date twice, a stalled deal above the always-show size.
7. Write the pack as a headline, the commit and best case with sources, the gap, and the inspect list with one question per deal.

Checks before you finish:
- Every number has a source and a date. A number with no source is left out.
- Commit and best case are shown separately and never blended to make the headline look better.
- Each inspect item asks a question about the deal, not a verdict on its owner.
- If a source could not be read, the first line of the pack says what is missing.

Example (invented, for illustration): "Q4 commit 38k, best case 29k (roll-up read 2026-09-28). Gap to target 23k even if all best case lands (goal read 2026-09-28). Inspect: Pine Test (commit, no signer named: who signs?); Oak Test (due to close 2026-10-09, still in pipeline: has the buyer confirmed that date?)."

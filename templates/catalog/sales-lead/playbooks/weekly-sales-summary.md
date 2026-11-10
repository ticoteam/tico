# Weekly sales team summary

Schedule: Mondays at 08:00 team time (routine `weekly-sales-summary`), after setup. Also run by hand. Budget 35 minutes. The outcome is one page for the owner: how the
pipeline moved, which deals need a human, and what to route where. Nothing is changed or sent.

---

## 1. Read where things stand

    hub task show <id>

Then `knowledge/pipeline-rules.md`, `knowledge/team.md`, `knowledge/routing.md` and last week's summary in `reports/`. If
last week flagged an action, check whether it happened; say so in the summary.

## 2. Read the pipeline

Use the source the rules name: a read of the CRM (only ever a read), `sales-ops`' latest pipeline
report, or the pipeline notes. Get each open deal's stage, amount, days since last activity and next
step. Compare with last week's summary to find what entered, moved, closed and slipped.

## 3. Read what the team produced

`hub update list --kind weekly` and `hub task list` for each sales bot in `knowledge/team.md`, plus the
newest file in its `reports/`. Count drafts made, leads researched, proposals started, renewals
flagged. List tasks waiting more than 5 days on a human or on a missing input.

## 3b. Read the forecast

From `sales-ops`' latest report: commit, best case and pipeline for the period, and the deals it flagged
(commit without evidence, closing this period but still pipeline). Put the gap to the period's target, if
`hub goal list` shows one, in one line. Pick the deals to inspect on the forecast call: at most five.

## 4. Choose the priority deals

Three to five, in this order: closest to close and needing a step; stalled past the threshold (default
14 days); large deals over the "always show" size; new deals with no next step. Skip healthy ones.

## 5. Draft routing proposals

For each new lead, account or request with no owner: pick the owner from `knowledge/routing.md` and
propose it in one line with the reason. Create tasks for requested follow-through with your Tools. When the work requests it, create `hub task create --owner <slug>` (see `playbooks/route-a-request.md`).

## 5b. Note coaching and hiring

For each stalled priority deal, one coaching line for its owner: the question the deal cannot answer yet
(who signs, what the buyer must see, the date). If the same kind of work went unowned again this week,
note it under Hiring and follow `playbooks/propose-a-hire.md`.

## 6. Write and hand over

Write `reports/YYYY-MM-DD-sales-summary.md` in the shape of `knowledge/examples/sales-summary.md`, then
`hub file publish reports/YYYY-MM-DD-sales-summary.md`. Commit, and `hub task done <id> -m "..." --version N`: the headline, the path, which sources you could not read. Always finish the task.

## When a source fails

Name it and what is therefore unknown, and keep going with the rest. A summary built from the notes
alone says so in its first line.

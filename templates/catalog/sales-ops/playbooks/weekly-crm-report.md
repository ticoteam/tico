# Weekly CRM hygiene and pipeline report

Schedule: Mondays at 06:30 team time (routine `weekly-crm-report`), after setup. Also run by hand. Budget 40 minutes. The outcome is one report: the pipeline, and the
exceptions with a proposed fix for each. The CRM is read only and nothing changes.

---

## 1. Read where things stand

    hub task show <id>

Then `knowledge/hygiene-rules.md`, `knowledge/stages.md`, `knowledge/exceptions.md` and last week's report.

## 2. Read the CRM, once, and stamp it

Read open opportunities with stage, amount, owner, close date, next step, last activity and created
date, and the stage history where available. Note the date and time of the read at the top of the
report. If the read fails or is partial, stop and say so: a report from a partial read is labelled so.

## 3. Build the pipeline picture

Open deals and dollars by stage; deals entered, moved, won, lost since last week's read; average days in
each stage against `knowledge/stages.md`; close dates that moved out, and how many times. Weight by
probability only if `knowledge/stages.md` carries the team's own probabilities; otherwise count and sum. Where the CRM cannot give a figure, write "not available" and why; do not estimate it.

## 3b. Roll up the forecast

If deals carry a forecast category, sum commit, best case and pipeline for the period as sellers set them.
List each commit deal missing the evidence in `knowledge/forecast-rules.md`, and each deal whose close date
is in the period but whose category is still pipeline. You never change a category; the Sales Manager does.

## 3c. Check routing

List leads created since the last read with no owner, or owned by someone marked out in
`knowledge/routing-rules.md`, with the owner the rules would give. Follow `playbooks/lead-routing.md`
for anything no rule covers.

## 4. Find the exceptions

For each open deal above the review size, check: owner present, stage matches its exit criteria, close
date valid (not in the past, not pushed three or more times), amount present, next step specific and
dated, activity within the stale threshold (default 14 days). Every exception is one line: record id,
owner, the problem, the proposed fix, the date first seen (from `knowledge/exceptions.md`). A blank field is not a clean record: name the fields you read and the date range before you call a record clean.

## 5. Monthly and quarterly checks

On the first Monday of a month add duplicate candidates (`playbooks/duplicate-review.md`), ownerless
records, opportunities inactive over 12 months and closed-lost with no reason. In the first week of a
quarter add picklist and unused-field observations as proposals.

## 6. Write and hand over

Write `reports/YYYY-MM-DD-crm-report.md` in the shape of `knowledge/examples/crm-report.md`, update
`knowledge/exceptions.md`, then `hub file publish reports/YYYY-MM-DD-crm-report.md`. Commit and `hub
task update <id> --status done --note`: the headline, the path, the size of the read, what failed.
Apply requested fixes with your Tools and verify the result. Always finish the task.

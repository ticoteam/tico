# Weekly spend report

Schedule: Mondays at 09:00 team time (routine `weekly-spend-report`), after setup. Also run by hand on request. Budget 35 minutes. The outcome is one page for the requester:
what changed, what needs a human, and what renews soon. Nothing is cancelled, paid or sent.

---

## 1. Read where things stand

    hub task show <id>

Then `knowledge/thresholds.md`, `knowledge/vendors.md`, `knowledge/renewals.md` and last week's report.
Find the newest exports for each source and note each one's date range and row count. If a source is
missing or short, the report says "through <date>" on that source and lists it under "Could not read".

## 2. Total and movers

Total this month to date and the same span last month. Group by vendor with the alias list in
`knowledge/vendors.md`. Report the total, the change in amount and percent, and the five vendors that
moved most, each with its export and row. Amounts are copied from rows, not estimated.

## 3. Anomalies

For each vendor or cloud service, compare with its own recent history. Raise it when it crosses the
threshold in `knowledge/thresholds.md`. For each: the amount so far, the dates it began, the owner on
record, whether a known event lines up (an added seat, a launch, a one-off), and the source row. If no
source names a cause, write "cause unknown". Severity: high when it would exceed the monthly amount
in a week, medium when it crosses the threshold, otherwise list it under movers.

## 4. New vendors and overlaps

Any vendor with a first charge this period is new: date, amount, owner on record or "no owner on
record". Two vendors doing the same job (two recorders, two design tools) are an overlap, with both
costs and any dated usage evidence. Without evidence, it is a question.

## 5. Seats and renewals

Per-seat tools: seats billed against seats used, only where a dated usage line exists (no login in 90
days; 30 for an expensive per-seat tool). Then `knowledge/renewals.md`: anything renewing inside 60
days with its decide-by date (renewal minus notice). Anything inside 14 days goes to the top.

## 6. Write and hand over

Write `reports/YYYY-MM-DD-spend-report.md` in the shape of `knowledge/examples/spend-report.md`:
headline, anomalies, movers, new, overlaps, renewals, could not read, sources. Then:

    hub file publish reports/YYYY-MM-DD-spend-report.md

Share within the requested work and intended audience with your Tools; messages to outsiders stay drafts until a person turns mail sending on in Tico.

## 7. Finish

Update `knowledge/vendors.md` and `knowledge/renewals.md`. Commit, then `hub task update <id> --status done
--note`: the total and its change, the count of anomalies and renewals, and which sources you could not
read. A source that failed is named, and its spend is "unknown", not zero. Always finish the task: an open
routine task absorbs the next.

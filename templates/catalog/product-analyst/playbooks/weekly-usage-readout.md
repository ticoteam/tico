# Weekly product usage readout

Schedule: Wednesdays at 09:00 team time (routine `weekly-usage-readout`), after setup. Budget 40 minutes. The outcome is one page the Head of Product reads in five minutes.
Nothing is written to any data source.

Inputs: `knowledge/definitions.md`, `knowledge/launches.md`, last week's readout, and the product data read through `hub db` (read only).

Done when: the readout is in the shape of `knowledge/examples/usage-readout.md`, every number names its definition and period, every query is saved in `queries/`, and anything that moved has passed `skills/check-event-counts-before-a-trend/SKILL.md`.

---

## 1. Read where things stand

    hub task show <id>
    hub db list

Then `knowledge/definitions.md`, `knowledge/launches.md` and last week's readout. `hub db doctor <name>`
on each source; a failing one is the first line of the report.

## 2. Adoption per recent launch

For each launch in `knowledge/launches.md` shipped in the last 90 days: users who could use it (plan,
platform, flag), users who used it at least once, users who used it in two separate weeks. Adoption is
the share of eligible users, with the week-by-week trend since ship date. Name the denominator in the table header.

## 3. Activation funnel

New signups in the last full week, through each step to the activation event in
`knowledge/definitions.md`. Show the step with the biggest drop and how it compares with the four-week
average.

## 4. Retention by cohort

Weekly signup cohorts for the last eight weeks: share active in week 1, 2, 4 and 8. Flag a cohort more
than five points off the average and say what was different about that week (a launch, a campaign, an
outage), as a possibility, not a cause. A missing week is shown as missing, not zero.

## 5. What moved

Anything outside its normal range (beyond the last eight weeks' min and max). Check tracking first: a
drop to zero is usually a broken event. Run `skills/check-event-counts-before-a-trend/SKILL.md` on each item and put the checks on the line.

## 6. Write and hand over

Write `reports/YYYY-MM-DD-usage-readout.md` in the shape of `knowledge/examples/usage-readout.md`, with the
queries saved as `queries/YYYY-MM-DD-usage-readout.sql`. `hub file publish reports/YYYY-MM-DD-usage-readout.md`,
commit, and `hub task update <id> --status done --note`: the headline and the biggest caveat.

# Weekly finance summary

Schedule: Mondays at 08:30 team time (routine `weekly-finance-summary`), after setup. Also run by hand. Budget 40 minutes.

Inputs: this week's bank balances or bank export, the aging report, the finance bots' newest reports, `knowledge/thresholds.md`,
`knowledge/cash-forecast.md`, `knowledge/finance-calendar.md`, `knowledge/team.md` and last week's summary.

Outcome: one page for the owner: cash now and over the next 13 weeks, what finance work is late, what is due, and the
decisions needed. Nothing is paid, changed or shared.

Done when: the summary is published with its source for every figure, the forecast is rolled, the lowest week is named
against the minimum, every blocked item has an owner, and the task is closed with the headline.

---

## 1. Read where things stand

    hub task show <id>

Then `knowledge/thresholds.md`, `knowledge/cash-forecast.md`, `knowledge/finance-calendar.md`, `knowledge/team.md` and last
week's summary. If last week named a decision, check whether it was made.

## 2. Cash today

From this week's balances or bank export: cash by account (named, never numbered), the total, and the change since last
week. If balances were not supplied, the summary opens by saying so and the rest is labelled "forecast only".

## 3. Roll the 13-week forecast

Follow `skills/build-the-13-week-cash-forecast/SKILL.md`: drop last week, add week 13, put last week's actuals beside the
forecast line by line, and log each miss. Update collections from the Accounts Receivable Specialist's aging report and the
payment run from the Accounts Payable Specialist's. Find the lowest week and compare it with the minimum cash line. Any
figure two sources disagree on goes through `skills/reconcile-a-cash-figure/SKILL.md` first.

## 4. The group

Read each finance bot's newest report and open tasks: close status (Bookkeeper), invoices out (Billing), overdue customers
(AR), bills due and the proposed payment run (AP), material variances (FP&A), payroll changes (Payroll), filing dates (Tax),
deferred revenue (Revenue Accountant). List any task waiting on a human for more than five days, and any report that did
not arrive.

## 5. The next 14 days

From `knowledge/finance-calendar.md` and `hub calendar list`, using `skills/check-a-finance-date-before-it-is-ready/SKILL.md`:
every date in the window, its owner, and whether its inputs are ready.

## 6. Decisions and routing

At most five decisions for the owner, each one line with the figure, the deadline and the options. New finance requests
with no owner get a routing proposal (see "The finance team's lines"). Create routing tasks only when the work requests
them and your Tools allow it. If uncovered work keeps recurring, follow `playbooks/propose-a-hire.md`.

## 7. Write and hand over

Write `reports/YYYY-MM-DD-finance-summary.md` in the shape of `knowledge/examples/finance-summary.md`, then
`hub file publish reports/YYYY-MM-DD-finance-summary.md`. Commit, and `hub task update <id> --status done --note`: cash,
the lowest week, the path, and what you could not read. Always finish the task.

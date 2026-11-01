# Month-end close

Triggered on the first working day of the month, or by a task asking "where is the close?". Budget 25 minutes, repeated
daily until the close is done.

Inputs: the close deadline in `knowledge/finance-calendar.md`, the close checklist (`hub finance report close --month YYYY-MM`),
and the Bookkeeper's (`bookkeeper`) status on the task. If the books are kept outside Tico, the name of who keeps them is in
`knowledge/team.md`.

Outcome: a close that finishes by the team's deadline, with every blocker named and owned. You coordinate. The Bookkeeper
keeps the checklist. Posting and period locks happen only when a task requests them and a granted accounting Tool allows them.

Done when: every line of the checklist is done or has a named owner and a date, the close section is on the task, and on
the last day the task is closed with the outcome.

---

## 1. Read the plan

    hub task show <id>

Read the close deadline in `knowledge/finance-calendar.md` and the month's close checklist, which the Bookkeeper works
through. If the books are kept outside Tico, name who keeps them from `knowledge/team.md`.

## 2. Check the inputs, in order

Each is done, open or blocked, with who owes it:
1. Bank and card feeds complete to the last day of the month.
2. Invoices out for the month all issued (Billing Specialist) and payments received applied.
3. Bills received entered, and accruals listed for bills not yet in (Accounts Payable Specialist).
4. Expense reports submitted and reviewed (Expense Auditor).
5. Payroll for the month matches the payroll register (Payroll Specialist).
6. Deferred revenue schedule rolled (Revenue Accountant), when the team bills ahead.
7. Uncategorised transactions cleared and missing receipts under the team's rule.
8. Bank and card accounts reconciled by a human.
9. Statements compared with last month; the FP&A Analyst's variance review can start.
10. The period locked by a human, after the accountant's review if they have one.

## 3. Unblock

For each blocked line, one routing proposal: who, what, by when. Ask once with `hub task ask <id>`. Create the requested
follow-through with `hub task create --owner <slug or person> --parent <id>` and the evidence.

## 4. Report

Add a close section to the task: day N of the close against the deadline, lines done, the blockers with owners, and the one
thing that would finish it soonest. When a human says the period is locked, record the date in
`knowledge/finance-calendar.md` and tell the FP&A Analyst on its task that budget-against-actual can run.
`hub task update <id> --status done --note` on the last day.

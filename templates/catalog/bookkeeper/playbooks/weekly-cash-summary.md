# Weekly cash summary

Schedule: Mondays at 08:30 team time (routine `weekly-cash-summary`), and by hand when the owner asks. Budget 40 minutes.

Inputs: the bank balances (`hub api GET finance/pay-from`), the cash-flow report (`hub finance report cash_flow`), the invoices
and bills due (`hub finance inbox --kind invoice_overdue`, `--kind bill_due`), the close checklist, the dates on
`hub calendar list`, `knowledge/cash-thresholds.md`, `knowledge/cash-forecast.md`, last week's summary in `reports/`, and
the usage figures (`playbooks/usage-against-budget.md`).

Outcome: one page for the owner: cash now and over the next 13 weeks, what is late or due, the usage line, and the
decisions needed. Nothing is paid, changed or sent outside the team.

Done when: the summary is published with its source for every figure, the forecast is rolled, the lowest week is named
against the minimum, every blocked item has an owner, and the task is done with its headline.

## 1. Read where things stand

    hub task show <id>

Then `knowledge/cash-thresholds.md`, `knowledge/cash-forecast.md` and last week's summary. If `cash-thresholds.md` does not
exist, ask the owner once, with `hub task ask`, for the minimum cash line and the runway alert. Until they answer, the
summary says no minimum is set and shows the lowest week without a comparison. If last week named a decision, check whether
it was made.

## 2. Cash today

From the balances `hub api GET finance/pay-from` returns: each account by name (never by number), the total, and the change
since last week. Say which cash it is: bank available (spendable today) or books cash after the cut-off. If the balances
cannot be read, the summary opens by saying so, and the rest is labelled "forecast only".

## 3. The 13-week outlook

Roll the forecast following `playbooks/cash-outlook-13-weeks.md`, then find the lowest week and compare it with the minimum
cash line. Any figure two sources disagree on is traced first (`skills/reconcile-a-cash-figure/SKILL.md`).

## 4. The books

- Overdue invoices: `hub finance inbox --kind invoice_overdue`, then `hub api GET finance/invoices/<invoice id>` for the
  remaining amount and days late. Name the total and the count.
- Bills due in the next 14 days: `hub finance inbox --kind bill_due`, and for each its status and whether a payment is
  already requested (`hub finance search --kind bill --q "<vendor>"`).
- The close: `hub finance report close --month <YYYY-MM>` for last month, in one line.
- Budgets: `hub finance report budget_vs_actual --start <first day> --end <last day>`; name each budget at or near its limit.

A figure you could not read is "not available", never zero.

## 5. Usage

Follow `playbooks/usage-against-budget.md`, and add one line: the estimated spend for the last seven days, or that it was not
readable by this bot.

## 6. The next 14 days

From `hub calendar list` and any dates the owner has put on the task: each date in the window, its owner, and whether its
inputs are ready. A payroll or tax date less than five working days away with its inputs missing goes first.

## 7. Decisions

At most five decisions for the owner, each one line with the figure, the deadline and the options, asked on a task with
`hub task ask`. A payment that needs a decision is a question to the owner, never a payment you request yourself.

## 8. Write and hand over

Write `reports/cash-<YYYY-MM-DD>.md` in the shape of `knowledge/examples/weekly-cash-summary.md`, then publish it with
`hub doc write finance/reports/cash-<YYYY-MM-DD> --title "Cash summary: <date>" --body-file reports/cash-<YYYY-MM-DD>.md`.
Commit the rolled `knowledge/cash-forecast.md` and the summary. Then `hub task comment <task id> "Cash summary: <doc path>"`
and `hub task done <task id> -m "<cash, the lowest week, what you could not read>" --version N`.

Write only what the reports and reads say. A number you could not read is "not available", never a guess.

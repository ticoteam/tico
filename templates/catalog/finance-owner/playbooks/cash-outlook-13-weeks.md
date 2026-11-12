# The 13-week cash outlook

Schedule: rolled every Monday inside the weekly cash summary (`playbooks/weekly-cash-summary.md`). Built the first time the
owner asks, or when `knowledge/cash-forecast.md` does not exist. Budget 30 minutes.

Method: `skills/build-the-13-week-cash-forecast/SKILL.md`, the direct method, cash in and out by week and line. Read it before
you build or roll the forecast, and `skills/reconcile-a-cash-figure/SKILL.md` before a cash or runway figure goes to the owner
as an alarm.

Inputs: the bank balances (`tico api GET finance/pay-from`); open invoices and their payment history (`tico finance search
--kind invoice`, `tico api GET finance/invoices/<id>`); bills due and scheduled (`tico finance search --kind bill`); payroll and
tax dates from `tico calendar list`; rent and loan schedules the owner gave on a task; last week's
`knowledge/cash-forecast.md`; and the cash-flow report for the last three closed months (`tico finance report cash_flow`).

## Steps

1. Read last week's `knowledge/cash-forecast.md` and its variance log. Put last week's actuals beside each line.
2. Log each miss over 10 percent: line, week, forecast, actual, cause, owner and date. A miss with no cause stays open.
3. Drop the first week and add week 13. Near weeks (1 to 4) come from documents: invoices due, bills scheduled, payroll.
   Later weeks (5 to 13) are estimates from collection history and known commitments, labelled as estimates.
4. Keep to 8 to 12 lines, each naming its source and date.
5. Find the lowest week, its closing cash and the gap to the minimum cash line in `knowledge/cash-thresholds.md`. Runway is
   `tico finance metric runway_months`, shown with its as-of time and its inputs.
6. Rewrite `knowledge/cash-forecast.md` with the rolled weeks. Commit it with the summary.

## Checks

- Every row names its source and date. An unsourced figure is left out.
- A missing export is "not supplied", never zero.
- Collections are timed from payment history, not from invoice terms.
- The lowest week is named in the summary, against the minimum, or the summary says no minimum is set.

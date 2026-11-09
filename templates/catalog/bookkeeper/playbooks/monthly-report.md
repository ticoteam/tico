# Monthly finance report

Write last month's report into Docs.

Inputs: last month's first and last day, and the month name, from the routine's date.
Done when: the doc is written at its path, the task names that path, and the task is marked done.

1. Read `hub finance report pnl --start <first of last month> --end <last day> --compare prior`,
   `hub finance report balance_sheet --end <last day>`, `hub finance report cash_flow` for the same month,
   `hub finance report budget_vs_actual` for the same month, and `hub finance metric runway_months`.
2. If last month is not closed yet, say so at the top: the numbers may still move.
3. Write the report in this order: a status line (closed or not, and as of when); a three-line summary (revenue, spend,
   cash and runway, each with the change from the month before); the P&L by line; cash in and out; budgets over or near
   their limit; anything unusual (a duplicate charge, a price increase, a new subscription). Amounts as the reports give
   them. Save it as `reports/finance-<YYYY-MM>.md`.
4. Publish it to Docs with `hub doc write finance/reports/<YYYY-MM> --title "Finance report: <Month YYYY>" --body-file
   reports/finance-<YYYY-MM>.md`. Leave the doc's path on this task. Mark the task done.

Write only what the reports say. A number you could not read is "not available", never a guess.

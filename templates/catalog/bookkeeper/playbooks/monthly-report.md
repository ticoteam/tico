# Monthly finance report

Write last month's report into Docs.

1. Read `hub finance report pnl --start <first of last month> --end <last day> --compare prior`,
   `hub finance report balance_sheet --end <last day>`, `hub finance report cash_flow` for the same month,
   `hub finance report budget_vs_actual` for the same month, and `hub finance metric runway_months`.
2. If last month is not closed yet, say so at the top: the numbers may still move.
3. Write one doc titled "Finance report: <Month YYYY>" with: a three-line summary (revenue, spend, cash and runway,
   each with the change from the month before), the P&L by line, cash in and out, budgets over or near their limit,
   and anything unusual (a duplicate charge, a price increase, a new subscription). Amounts as the reports give them.
4. Publish it with `hub doc` and leave the link on this task. Mark the task done.

Write only what the reports say. A number you could not read is "not available", never a guess.

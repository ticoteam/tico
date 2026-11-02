# Month ready to close

Finance raised `finance.period.ready`: the month in this task's data has nothing left that blocks its close.

1. `hub finance report close --month <month>`: finish what the checklist lists that is yours (categorize, attach,
   reconcile).
2. Read `hub finance report budget_vs_actual --start <first day> --end <last day>` and write a short note: the
   three biggest variances and why, in plain words. The note goes on the task, not into the close.
3. Ask for `close_period` with the month only: `hub finance act close_period --payload '{"month":"<month>"}'
   --operation-id close:<month>`.
   The owner approves the close. On the task, put the note from step 2 and say the close waits on the owner.

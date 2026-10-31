# Month ready to close

Finance raised `finance.period.ready`: the month in this task's data has nothing left that blocks its close.

1. `hub finance report close --month <month>`: finish what the checklist lists that is yours (categorize, attach,
   reconcile).
2. Read `hub finance report budget_vs_actual --start <first day> --end <last day>` and write a short note: the
   three biggest variances and why, in plain words.
3. Ask for `close_period` with the month and the note, `--operation-id close:<month>`. The owner approves the close;
   say on the task that it waits on them.

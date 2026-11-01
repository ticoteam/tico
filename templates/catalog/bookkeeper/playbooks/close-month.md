# Month ready to close

Finance raised `finance.period.ready`: the month in this task's data has nothing left that blocks its close.

Inputs: the month from the task.
Done when: `close_period` is asked for with the note, the task says it waits on the owner, and the task is marked done.

1. `hub finance report close --month <month>`: finish what the checklist lists that is yours (categorize, attach,
   reconcile).
2. Read `hub finance report budget_vs_actual --start <first day> --end <last day>` and write the note as
   `skills/write-the-close-variance-note/SKILL.md` describes: the basis and the threshold, the largest explained
   variances in plain words, and any unexplained line named as unexplained.
3. Ask for `close_period` with the month and the note, `--operation-id close:<month>`. The owner approves the close;
   say on the task that it waits on them.

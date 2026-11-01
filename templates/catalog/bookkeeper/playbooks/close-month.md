# Month ready to close

Finance raised `finance.period.ready`: the month in this task's data has nothing left that blocks its close.

Inputs: the month from the task, and the task's id.
Done when: the close checklist is read, the variance note is a comment on this task, `close_period` is asked for with the
month only, the task says it waits on the owner, and the task is marked done with one line.

1. `hub finance report close --month <month>`. Finish what the checklist lists that is yours (categorize, attach,
   reconcile). Name anything left that is not yours in the note.
2. `hub finance report budget_vs_actual --start <first day> --end <last day>`. Its amounts are minor units: divide by
   100 for dollars (315000 is 3,150.00).
3. For each line the variance skill says to test, find its cause with
   `hub finance report spend_by --start <first day> --end <last day> --by vendor` and
   `hub finance search --kind bill --q <vendor name>`. Write the note as `skills/write-the-close-variance-note/SKILL.md`
   describes.
4. `hub task comment <task id> "<the note>"`. The note goes on this task, not into the close request.
5. `hub finance act close_period --payload '{"month":"<month>"}' --operation-id close:<month>`. Send the month only: a
   `note` field is refused. The owner approves the close.
6. `hub task done <task id> --note "<one line: close asked for and waits on the owner; what is unexplained>"`.

| If | Do |
|---|---|
| a checklist item is yours | finish it before step 2 |
| a checklist item is not yours | name it in the note |
| a variance line has a bill or entry that explains it | name that bill or entry |
| no bill or entry explains a line | write it as unexplained; never guess |
| no threshold is set | ask the owner once: `hub task question` (see playbooks/commands.md). Until they answer, test the three largest lines by dollars |

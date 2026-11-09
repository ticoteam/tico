# Pre-payroll change summary

Schedule: Mondays at 09:00 team time (routine `pre-payroll-change-summary`), after setup. Budget 35 minutes in a cut-off week, 5 otherwise. The outcome is one
summary the payroll owner can enter line by line. Nothing is entered.

Inputs: `knowledge/payroll-calendar.md`, the input tasks and files from each owner, the last register and `knowledge/baseline.md`.

Done when: the summary is published to the payroll task, every missing input has an owner and a date, and the task is closed with the change count.

---

## 1. Is this a cut-off week?

From `knowledge/payroll-calendar.md`. If not: one line on the task (next pay date, cut-off, inputs
received so far, inputs still owed and by whom) and finish.

## 2. Collect the inputs

From the tasks and files of the input owners: joiners with start dates and documented pay; leavers with
last day and any final-pay items the HR owner gave; pay rate changes with effective dates; recorded timesheets and overtime; bonuses and commissions with their source records; new or changed deductions. An
input still missing two working days before cut-off is asked for now, on its owner's task.

## 3. Check each change

- It has a source (HR record, timesheet, commission report) and a recorded source, or it is a question.
- Dates fall in the pay period; a partial period shows the days.
- Hours: over the overtime rule outside the stated rules, missing a day, or a timesheet not confirmed by its source.
- The person is on the roster (no joiner paid before their start, no leaver after their last day).

## 4. Reconcile

Last run's headcount plus joiners minus leavers equals this run's expected headcount, per pay group.
Expected gross moves from `knowledge/baseline.md` only by the listed changes; say by how much.

## 5. Write and hand over

`reports/YYYY-MM-DD-payroll-changes.md` in the shape of `knowledge/examples/payroll-changes.md`.
Publish it to the payroll task only (`hub file publish <path> --task <id> --scope task`), commit, and `hub task update <id> --status done
--note` with the change count, questions and missing inputs; no figures in the note.

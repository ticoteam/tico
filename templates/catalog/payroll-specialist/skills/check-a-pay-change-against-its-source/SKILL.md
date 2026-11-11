---
name: check-a-pay-change-against-its-source
description: Check one payroll change (joiner, leaver, rate change, hours, overtime, bonus, commission or deduction) against its source record, its effective date, the pay period and the roster before it goes on the pay summary. A change with no source is listed as a question.
---
# Check a pay change against its source

When to use: for every change in a pay run's summary, before it is listed as ready to enter.

Steps:
1. Name the change in one line: the employee (by name and employee id), the kind, the amount or hours, and the effective date.
2. Find its source record: the HR record for a joiner, leaver or rate change; the recorded timesheet for hours and overtime; the
   commission report for commissions; the approved bonus letter or record for bonuses; the benefits or deduction form for deductions.
   Write the record's name and date next to the change.
3. Check the effective date falls in the pay period. A partial period is shown with its dates and days, and the method is the
   team's, from `knowledge/approvals.md`.
4. Check the roster. A joiner has no pay before their start date. A leaver has no pay after their last day, and any final-pay
   item is the HR owner's to confirm.
5. Check hours. Over the overtime rule in `knowledge/approvals.md`, a missing day, or a timesheet not confirmed by its source is a question.
6. If any step has no source, the change is a question for the owner on the task, not part of the ready list.

Checks before you finish:
- Each listed change has a named source record and date.
- Effective dates are inside the pay period, or the partial period is shown.
- No joiner or leaver is paid outside the roster dates.
- No change is listed on a recollection or a message alone.

Example (invented, for illustration): "Employee E-1042, hourly rate change from 0 to 24.00 per hour, effective 2026-10-06 (HR record, 2026-10-01). Inside the period 10-01 to 10-14. On the roster. Ready to enter."

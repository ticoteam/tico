# Pay run checklist

Work top to bottom. Each item ends in a fact written on the run summary, not a tick.

## 1. Dates

- Period first day and last day.
- Pay date.
- Cut-off for changes, from `knowledge/payroll-calendar.md`. Default: 3 business days before the provider's deadline.
- The provider's submit deadline for this run, read from the provider today. A bank holiday between the deadline and
  pay date moves it earlier.

## 2. The provider is ready

- Ask the provider for the run's blockers and list them. With Gusto's connector the calls were named
  `list_payroll_blockers`, `list_pay_schedules`, `list_employees`, `list_time_records`, `list_payrolls`, `get_payroll`
  and `update_payroll` when read on 2026-11-18; read the Tool's current list.
- Prove the source returns data. An empty list of employees or time records is a failed read until shown otherwise.
- Each employee's work state has withholding and unemployment accounts active in the provider.
- The funding bank account is verified.

## 3. Changes, one line each

For each change write: the person (name and the provider's id), the kind, the amount or hours, the effective date, the
source record and its date.

| Kind | The source is |
| --- | --- |
| Joiner | Signed offer or contract with the start date |
| Leaver | Resignation or termination record with the last day |
| Rate or salary change | Signed letter or an OK on a task from the person named in `knowledge/payroll.md` |
| Hours and overtime | The approved timesheet from the time system |
| Bonus or commission | A signed plan and its calculation, or an OK on a task |
| Deduction or benefit | The enrolment form or the benefits provider's report |
| Reimbursement | An approved expense claim that meets the accountable plan |

Checks on each line:

- The effective date is inside the period. A part period shows its dates, the days counted and the method (working
  days or calendar days, as `knowledge/payroll.md` says; default working days).
- A joiner has no pay before the start date. A leaver has none after the last day.
- Hours over the overtime rule, a missing day, or a timesheet nobody approved is a question, not an entry.
- When you write to the provider, know whether the call replaces a value or adds to it. With Gusto's
  `update_payroll`, values replace what was there. Read the line back after writing.
- Never silently correct something that looks wrong. Flag it with what you saw and what you expected.

## 4. Reconcile to the baseline

For each pay group:

```
expected headcount = baseline headcount + joiners - leavers
expected gross     = baseline gross + each listed change (pro-rated where part period)
```

Write the basis in words: "baseline register 2026-11-30, plus 1 joiner, minus 0 leavers". Compare with the roster in
the provider. Name every difference with its group and amount. A difference with no listed cause is a question for
the approver.

## 5. Preview

Have the provider calculate the run without submitting it. Compare:

- Headcount and gross by group against expected. They must match.
- Each person's net pay against their last run. A move over 10 percent with no listed change is a question.
- Employer taxes against last run. A jump usually means a wage base reset in January, a new state, or a rate change:
  name which.
- Anyone with zero tax withheld, or zero net pay.

## 6. Cash

- Total debit: net pay plus employee and employer taxes plus fees, from the preview.
- Debit date, from the provider.
- Funding account balance from `tico finance metric cash`, less bills and transfers already scheduled before the
  debit date.
- Result, on these default lines: covered (balance at least twice the debit), tight (covered, with less than
  that), short. Tell
  `forecast-cash-and-budget` the amount and date. Short is a hard stop.

## 7. Submit

- Every gate in the skill is true.
- Inside the bot's authority: submit, save the confirmation, write the time on the task.
- Outside it: ask for the OK on the summary's exact version. If any total changes after the OK, ask again.

## 8. The day after pay date

1. Get the final register. If it is not final, label the result "preliminary" in the first line.
2. Match people by id and name. Anyone in one list and not the other is the first difference.
3. For each person compare gross and each item: rate, hours, overtime, bonus, commission, deductions,
   reimbursements. Each mismatch gets a line: expected, register, source.
4. Classify: **timing** (effective next period), **data** (the input or source was wrong), **processing** (the
   provider did not apply what was approved), or **unknown**. Do not guess.
5. Propose the fix (next run or an off-cycle run) and name who decides. Corrections go through the provider, never
   the bank.
6. Write "Pass, n people checked" or the count of differences with one line each.
7. On a pass, write the new baseline with the register date. On differences, the baseline stays.

## 9. Books

- The run's journal is in the book for the right period: wages, employer taxes, and the liabilities.
- Payroll liabilities in the book equal what the provider shows as owed and not yet deposited.
- Reimbursements sit in their expense categories, not in wages.

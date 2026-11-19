---
name: run-payroll
description: Pay the company's people correctly and on time through its payroll provider (such as Gusto), for a US company with mostly contractors and few employees. Decides contractor or employee before anyone starts, sets up a new worker (W-9, W-4, I-9, new-hire report), registers a new state before the first pay there, runs each pay run (changes checked against their source, headcount and gross reconciled, cash checked, submitted, register compared afterwards), pays contractor invoices and expense reimbursements, checks the provider's quarterly filings, and closes the year with W-2s and 1099s. Use when a task says payroll, pay run, off-cycle, bonus, raise, pay change, timesheet, overtime, contractor, contractor vs employee, classification, misclassification, new hire, W-4, I-9, W-9, new state, withholding, unemployment insurance, reimbursement, expense claim, mileage, final pay, 941, 940, W-2 or year-end payroll. Not for benefits plan choices or for sales and income tax (stay-on-top-of-taxes).
metadata:
  function: finance
  roles: [payroll-specialist, finance-owner, controller, people-operations-specialist, hr-generalist, head-of-people]
  actions: [read, draft, send, spend, change-records]
  review: professional
---
# Run payroll

When to use: on every pay cycle, whenever a person is about to start being paid or stop, when a contractor invoice or an
expense claim arrives, when someone starts working from a new state, at each quarter end, and from November to January
for the year end.

Words used here: **the company** is the legal employer. **The provider** is the payroll service (Gusto or similar) that
calculates pay, moves the money, deposits payroll taxes and files payroll returns. An **employee** is paid through
payroll with tax withheld and gets a Form W-2. A **contractor** is an independent business paid on invoice with nothing
withheld, and gets a Form 1099-NEC. A **run** is one payroll. The **register** is the provider's report of what each
person was paid in a run. The **baseline** is the headcount and gross pay of the last run that passed its checks,
kept in `knowledge/baseline.md`. A **source** is the record that authorises a pay change.

This skill is written for a US company. Wage and hour law, final pay, pay frequency, pay stubs, withholding and
unemployment insurance are set state by state, and sometimes city by city. Rules here are US defaults with the date they
were read; check the state where the person works.

## What great looks like

Everyone is paid the right amount on the right day, every time, and nobody has to think about it. An expert payroll
person treats a run as a reconciliation, not data entry: the number of people equals last run plus joiners minus
leavers, gross pay moves only by changes that each have a record behind them, and anything else is a question asked
before the money moves, never a correction after.

The provider does the arithmetic and the filings. The bot's craft is everything around them: the right status for each
worker before they start, the right state registrations before the first pay, clean inputs, enough cash on the debit
date, and proof afterwards that the provider did what was approved. This company prefers bots to hires, so most people
it pays are contractors, and the biggest risk on the page is a contractor who is really an employee.

Pay is private. One person's pay is never visible to another teammate through anything this skill writes.

## First time at a company

Read `knowledge/payroll.md`. If it has a "Setup" section, skip this. If not, ask the owner these in one numbered
message (`tico task ask <task id> --to <owner> "<the questions>" --version N`), save the answers there with the date, and work on
the defaults until the answers arrive.

1. Which payroll provider does the company use, and can the bot reach it? Default: look in Tools for a payroll
   connection. If there is none, the bot prepares each run and a person submits it until the owner connects one.
2. What are the pay schedule and pay dates, and how are contractors paid? Default: keep the schedule set in the
   provider; contractors are paid on invoice through the provider's contractor payments, so their 1099s come from one
   place.
3. Who can authorise a pay change, and what counts as the record? Default: the owner, by a signed offer or contract,
   or an OK on a task. A chat message or a remembered conversation is never a source.
4. May the bot submit a regular run on its own? Default: yes, from the second run on, when every gate passes and gross
   pay is within the payroll budget. See Autonomy.
5. Which states do employees work from, and where is the company registered for withholding and unemployment
   insurance? Default: read the provider's company tax setup and compare it with each employee's work address.
6. How are expenses reimbursed? Default: an accountable plan as in `references/reimbursements.md`: receipts within 60
   days, paid outside wages with the next run, mileage at the IRS rate.

## Steps

The provider is reached through its Tool. Read the Tool's own list of calls first; names differ by provider. Where
there is only a website, use `tico browser open|read|click|type|download <site>` and `tico login fill <site>
username|password|code`, and `tico browser needs-you <site> "<what to do>" --task <id>` when a step needs a person.
Money figures from the book come from `tico finance metric` and `tico finance report`.

**A. Before anyone is paid**

1. **Bot first.** A task to "hire" is first a question of whether a bot can fill the role. This skill starts only once
   a person will be paid.
2. **Decide contractor or employee before the first day**, with `references/contractor-or-employee.md`. Write the
   decision record. This check is for new arrangements. If an existing contractor looks like an employee, that is a
   hard stop under Gates.
3. **Set up a contractor**: signed contract with the rate and scope, Form W-9 and payment details entered by the
   contractor through the provider's own onboarding, and a line in `knowledge/contractors.md`. No payment before the
   W-9 is on file. A contractor who works on site or with their hands also gives a certificate of insurance
   (`manage-insurance`).
4. **Set up an employee** with `references/new-worker-and-new-state.md`: registrations in the work state first, then
   the offer as the source, self-onboarding in the provider, Form I-9, the state new-hire report, and a note to
   `manage-insurance` for workers' compensation before day one.

**B. Each pay run** (the full checklist is `references/pay-run-checklist.md`)

5. **Work back from the pay date.** Read `knowledge/payroll-calendar.md` for the cut-off, and the provider's own
   submit deadline for this run. Bank holidays move it. Put both on the task.
6. **Fix the period and clear blockers.** Write the period's first and last day. Ask the provider what is blocking the
   run (missing forms, a failed bank verification, a state account number not entered) and clear each one.
7. **Collect the changes since the last run** and check each against its source: joiners, leavers, rate changes,
   hours and overtime, bonuses, commissions, deductions, reimbursements. A change with no source is a question on the
   task, and stays out of the run.
8. **Reconcile headcount and gross** against the baseline, by pay group. Headcount equals baseline plus joiners minus
   leavers. Gross moves only by the listed changes. A difference with no cause is never hidden in a total.
9. **Preview the run in the provider** and compare its totals with what you expect. Any person whose net pay, or any
   employer tax line, moves more than 10 percent from the last run with no listed cause is a question before
   submission.
10. **Check the cash.** Total debit (net pay, employee and employer taxes, provider fees) against
    `tico finance metric cash` for the funding account on the debit date, less other payments already scheduled. Write
    covered, tight or short. Short goes to the owner at once with the amount and the date.
11. **Submit, or ask.** When every gate passes and the run is inside the bot's authority, submit it and save the
    provider's confirmation. Otherwise ask for the OK, showing the summary.
12. **The day after pay date, compare the register** with the summary, person by person. Classify each difference as
    timing, data, processing or unknown, propose the fix and say who decides. Only after a pass, update the baseline.
13. **Check the books.** The run's journal is in the book, and the payroll liability accounts tie to the provider's
    report (`close-the-month` relies on this).

**C. Contractor invoices.** Check the invoice against the contract (rate, hours or milestone, period), confirm the
W-9, enter it as a bill, and pay it on the contract's terms through the provider or the normal bill run
(`pay-bills-and-buy`). A request to change where a contractor is paid is never acted on from a message: the
contractor changes it in the provider, or the bank detail change rule in `pay-bills-and-buy` applies.

**D. Reimbursements.** Follow `references/reimbursements.md`. Reimbursements under the plan are not wages. Anything
that fails the plan's three tests is wages and goes through the run with tax.

**E. Each quarter.** In the month after quarter end, once the provider shows the returns as filed, download them
(federal and each state) with the deposit confirmations. If nothing shows as filed five business days before a due
date, ask the provider why that day. Tie total wages on the returns to the quarter's registers and to the book. Read
every agency letter in the Finance mail tasks: a new unemployment tax rate goes into the provider before the next
run, and anything else follows the notice step in `stay-on-top-of-taxes`. Mark the lines filed on the tax calendar.

**F. Year end.** Follow `references/year-end.md` from November: addresses and names confirmed, taxable benefits
entered before the last run, W-2 and 1099-NEC previews checked, forms delivered and filed by the due date.

**G. When someone leaves.** Read the work state's labour department page for when final pay is due and whether unused
leave is paid out; some states require it on the last day. Enter the last day from its source, ask for the OK on the
final pay figure, stop deductions, and run `check-leaver-access-is-gone`. For a contractor: final invoice, property
back, access removed.

## Gates

Before a run is submitted, all of these are true:

1. **Every change has a named source record and date**, and its effective date is inside the period or the partial
   period is shown with its days and method.
2. **Headcount and gross reconcile to the baseline**, with every difference named.
3. **No joiner is paid before their start date and no leaver after their last day.**
4. **The provider shows no blockers**, and every employee's work state has active withholding and unemployment
   accounts in the provider.
5. **Cash covers the debit** on the debit date.
6. **Every contractor paid has a decision record and a W-9 on file.**
7. **The OK, where one is needed, is for the totals being submitted.** If the totals change, ask again.
8. **The run goes on the scheduled pay date.** If one change fails a gate, leave that change out, pay everything that
   is sourced, and say so on the task. Paying people late is not a way to resolve a question.

Hard stops. Do not act; tell the owner the same day, and brief the lawyer or CPA (`write-a-counsel-brief`):

- An existing contractor who works like an employee, or a contractor asking to be made an employee.
- A letter from a labour department, a tax agency or a court about a worker: a wage claim, an audit, an unemployment
  claim, a garnishment or support order. These carry short deadlines; write the deadline first.
- A correction that changes a quarter already filed, or a W-2 already issued.
- Paying anyone who lives or works outside the US.
- An owner's own pay in an S corporation, equity pay, or a severance agreement.
- Cash that will not cover a run.

Never:

- Pay employee wages outside the provider, by bank transfer or as an "expense".
- Enter or change someone's bank details for them. The person does it in the provider.
- Fix a run in the bank, or reverse a payment, without the provider's correction process.
- Write a bank account, tax identifier, home address or one person's pay in a task, page, knowledge file or status
  line. Person-level detail stays in the provider and in files shared to the one task
  (`tico file publish <file> --task <id> --scope task`).
- Call someone a contractor because it is cheaper or because they asked.

Where law varies by place: classification tests, minimum wage, overtime, the salary floor for exempt staff, pay
frequency, pay stub contents, final pay, leave payout, new-hire reporting, withholding and unemployment insurance. The
state where the person physically works decides. Read that state's labour and revenue pages, and write the page and
date in `knowledge/payroll.md`.

## Autonomy

The bot does on its own: keep the payroll calendar; collect and check changes; chase timesheets and missing forms;
clear blockers; submit a regular run that passes every gate and is within the payroll budget; pay contractor invoices
that match their contract, inside the book's payment rules; pay reimbursements that meet the plan; file new-hire
reports; enter a new unemployment rate from the state's notice; download and tie the provider's filings; run the
year-end checks; ask for W-9s.

The bot asks for an OK only for:

- The first run at a company, and the first after a change of provider or pay schedule.
- An off-cycle run, a bonus or commission not set by a signed plan, a raise, and any final pay.
- A run with an unexplained difference, or gross pay above the payroll budget.
- Hiring an employee or signing a contractor (each is a legal commitment), and the classification decision when any
  factor points to "employee".
- Registering the company with a new state, changing the provider or its plan, or anything signed for the company.

The OK is asked with `tico task ask <id> --to <approver> --kind ok "<one line>" --show page:<page id> --version <n>`
and covers exactly the version shown.

## Output

- **The run summary**, an HTML file shared to the run's task only (`tico file publish
  reports/payroll/run-<pay date>.html --task <id> --scope task`): period and pay date; headcount and gross by pay group
  as a small before-and-after table (baseline, changes, expected, provider preview); the list of changes with each
  source and date; the cash check; the gates as a checklist; after the run, the register comparison.
- **The payroll page** for the owner (`tico page publish reports/payroll/payroll.html --title "Payroll" --cadence
  monthly`), with no person-level figures: gross and headcount by group as a 12-run bar chart, contractors against
  employees, the next three pay dates, a map or table of states with registration status, W-9s on file against
  contractors paid, open blockers and open agency letters.
- **Records**: the provider's confirmation and register attached to the run's task; `knowledge/payroll.md`,
  `knowledge/payroll-calendar.md`, `knowledge/baseline.md`, `knowledge/contractors.md`; a decision record for every
  worker's status.

## Checks before you finish

- Every person in the register or the expected list is accounted for.
- Each change shows its source and date; nothing rests on a message or a memory.
- The baseline was updated only after the post-run comparison passed.
- The summary names the period, the pay date, the provider's deadline and when you submitted.
- No bank detail, identifier, address or individual pay appears outside the task-scoped file.
- Every rule you relied on (a rate, a threshold, a due date) shows its source and the date read.
- The next run's cut-off is on the calendar.

## Example

Invented, for illustration. The numbers are made up.

Run for pay date 2026-12-15, period 2026-12-01 to 2026-12-15. Provider deadline to submit: 2026-12-10, 4 p.m.

- Baseline (register 2026-11-30): salaried group 3 people, 14,250.00 gross; hourly group 1 person, 1,680.00.
- Changes: one joiner in the salaried group, start 2026-12-08, 4,000.00 per period (offer signed 2026-11-24).
  Six of eleven working days in the period, so 2,181.82 on the company's working-day method. Hourly staff are paid
  one period behind: timesheet for 2026-11-16 to 2026-11-30, approved 2026-12-02, 70 hours at 24.00, no overtime.
- Expected: salaried 4 people, 16,431.82; hourly 1 person, 1,680.00. Provider preview matches both.
- New state: the joiner works from a state where the company had no accounts. Withholding and unemployment
  registrations were confirmed on 2026-12-02 and entered in the provider; `manage-insurance` added the state to the
  workers' compensation policy on 2026-12-04. Blockers: none.
- Cash: total debit 24,310.00 on 2026-12-11; funding account 61,900.00 after scheduled bills. Covered.
- Authority: a regular run, every gate passes, inside budget. The bot submits on 2026-12-09 and saves the
  confirmation.
- 2026-12-16: register compared, 5 people, no differences. Pass. Baseline updated to register 2026-12-15.
- Same week, contractors: 7 paid this year, 6 W-9s on file, 1 missing (set up 2026-11-20, first invoice held).

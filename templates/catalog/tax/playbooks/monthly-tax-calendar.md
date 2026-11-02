# Monthly tax calendar

Schedule: the 1st of each month at 09:00 team time (routine `monthly-tax-calendar`), after setup. Budget 40 minutes.

Inputs: `knowledge/tax-calendar.md`, `knowledge/thresholds.md`, `knowledge/contractors.md`, the sales exports for each place's test window (trailing twelve months, or the calendar year its rule names), and any notice received since the last run.

Outcome: one page: what is due in the next 90 days, who files it, whether its inputs are ready, which places are near a
threshold, and what is changing. Nothing is filed or sent.

Done when: every deadline in the window has a source and an input status, every threshold line is dated against its official
source, the report is published, and the task is closed with the next deadline.

---

## 1. Refresh the dates

For each line of `knowledge/tax-calendar.md` falling in the next 90 days, confirm the due date on the authority's page if it
was last checked more than a year ago, using `skills/build-a-filing-calendar-line/SKILL.md`. Add any deadline from a notice
received since the last run.

## 2. Status of the inputs

For each deadline: the inputs its filer needs (sales by jurisdiction for the period, the payroll provider's filings, the trial
balance, the contractor list), who owes each, and the lead time. Each is ready, due now (inside the lead time), or late. Ask
the owners of "due now" inputs on their tasks.

## 3. Threshold watch

For every place the team sells into and is not registered, follow `skills/check-a-sales-tax-threshold/SKILL.md`. Show the
amount against the threshold and the percent. Mark anything over 80 percent, and anything whose threshold changed, with the
official source.

## 4. Contractors (monthly, and weekly in December and January)

Follow `skills/gather-contractor-tax-forms/SKILL.md`: contractors paid over the reporting amount this year with no tax form on
file, and new contractors with no form collected during setup.

## 5. Write and hand over

Write `reports/YYYY-MM-tax-calendar.md` in the shape of `knowledge/examples/tax-calendar.md`, `hub file publish` it, commit,
and `hub task update <id> --status done --note` with the next deadline and anything late. Questions for the accountant are
drafts on the task, under the Sending rules in AGENT.md.

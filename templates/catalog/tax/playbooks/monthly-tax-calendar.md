# Monthly tax calendar

Schedule: the 1st of each month at 09:00 team time (routine `monthly-tax-calendar`), after setup. Budget 40 minutes. The outcome is one page: what is due in the next
90 days, who files it, whether its inputs are ready, and what is changing. Nothing is filed or sent.

---

## 1. Refresh the dates

For each line of `knowledge/tax-calendar.md` falling in the next 90 days, confirm the due date on the
authority's page if it was last checked more than a year ago, and apply the authority's weekend and
holiday rule. Add any deadline from a notice received since the last run.

## 2. Status of the inputs

For each deadline: the inputs its filer needs (sales by jurisdiction for the period, the payroll
provider's filings, the trial balance, the contractor list), who owes each, and the lead time. Each is
ready, due now (inside the lead time), or late. Ask the owners of "due now" inputs on their tasks.

## 3. Threshold watch

Update trailing twelve-month sales (and transaction counts where a place still counts them) for every
place the team sells into and is not registered. Show amount against threshold and percent. Mark
anything over 80 percent, and anything whose threshold changed, with the official source.

## 4. Contractors (monthly, and weekly in December and January)

Contractors paid over the reporting amount this year with no tax form on file; new contractors with no
form collected during setup.

## 5. Write and hand over

`reports/YYYY-MM-tax-calendar.md` in the shape of `knowledge/examples/tax-calendar.md`, `hub file
publish` it, commit, and `hub task update <id> --status done --note` with the next deadline and
anything late. Questions for the accountant are prepared on the task to send with Tools when a person has turned mail sending on in Tico.

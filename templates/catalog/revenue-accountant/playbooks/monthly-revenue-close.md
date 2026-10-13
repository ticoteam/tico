# Monthly revenue close pack

Schedule: the 2nd of each month at 09:00 team time (routine `monthly-revenue-close`), after setup. Budget 50 minutes. The outcome is one close pack from verified accounting records. Nothing is
posted or changed.

---

## 1. Gather

    hub task show <id>

The month's billing export (invoices, credit notes, refunds, payments), the ledger export for the
revenue, deferred revenue and receivables accounts, and new contracts since last month. Note each
file's date range.

## 2. Billing to books

Total invoiced, credited and collected in billing against the ledger's postings for the month. List
every difference by invoice: missing in the books, posted twice, wrong period, wrong amount.

## 3. New and changed contracts

For each, follow `playbooks/new-contract-note.md` and add or change its line in the schedule.

## 4. Roll the deferred schedule

Per contract: opening deferred, plus this month's billings, minus this month's release, equals closing.
Release follows the policy (ratably by day over the service period for subscriptions, on delivery for
one-off services, as the policy says for setup fees). The closing total must equal the ledger after the
proposed entries; any remainder is a listed difference, never a plug.

## 5. Propose entries

One line each: date, debit account, credit account, amount, the schedule or invoice lines behind it.
Group by kind: monthly release, corrections from step 2, new contracts.

## 6. Bridge recurring revenue

Using the definition in `state.md`: opening, new, expansion, contraction, churn, closing, each figure
tied to named contracts. Explain the movement in one sentence.

## 7. Write and hand over

`reports/YYYY-MM-revenue-close.md` in the shape of `knowledge/examples/revenue-close.md`, `hub file
publish` it, commit, and `hub task update <id> --status done --note`: reconciled or the difference, the
entries count, the path. Questions for the accountant go on the task, prepared to send with Tools when a person has turned mail sending on in Tico.

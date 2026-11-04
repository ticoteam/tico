# Monthly revenue close pack

Schedule: the 2nd of each month at 09:00 team time (routine `monthly-revenue-close`), after setup. Budget 50 minutes.

Inputs: the month's billing export (invoices, credit notes, refunds, payments), the ledger export for the revenue, deferred
revenue and receivables accounts, new contracts since last month, and `knowledge/deferred-schedule.md`.

Outcome: one close pack from verified accounting records: billing to books, the deferred roll-forward, proposed entries, new
contract notes and the recurring revenue bridge. Nothing is posted or changed.

Done when: billing and the books are reconciled or every difference has a cause, the roll-forward ties to the ledger after the
proposed entries, the pack is published, and the task is closed with the difference and the entry count.

---

## 1. Gather

    hub task show <id>

Note each file's date range. A billing export that ends early is a finding.

## 2. Billing to books

Follow `skills/reconcile-billing-to-the-books/SKILL.md`: total invoiced, credited and collected in billing against the ledger's
postings for the month. List every difference by invoice, with its cause: missing in the books, posted twice, wrong period, or
wrong amount.

## 3. New and changed contracts

For each, follow `playbooks/new-contract-note.md` and add or change its line in the schedule.

## 4. Roll the deferred schedule

Follow `skills/roll-forward-a-deferred-schedule/SKILL.md`. Per contract: opening deferred, plus this month's billings, minus this
month's release, equals closing. The closing total must equal the ledger after the proposed entries. Any remainder is a listed
difference, never a plug.

## 5. Propose entries

One line each: date, debit account, credit account, amount, and the schedule or invoice lines behind it. Group by kind: monthly
release, corrections from step 2, new contracts.

## 6. Bridge recurring revenue

Follow `skills/bridge-recurring-revenue/SKILL.md`, using the definition in `state.md`. Explain the movement in one sentence.

## 7. Write and hand over

Write `reports/YYYY-MM-revenue-close.md` in the shape of `knowledge/examples/revenue-close.md`, `hub file publish` it, commit, and
`hub task update <id> --status done --note`: reconciled or the difference, the entries count, the path. Questions for the
accountant are drafts on the task, under the Sending rules in AGENT.md.

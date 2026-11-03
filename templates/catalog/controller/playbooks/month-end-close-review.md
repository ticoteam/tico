# Month-end close review

Schedule: the 3rd of each month at 09:00 team time (routine `month-end-close-review`), after setup. Budget 30 minutes. Nothing is sent, paid, signed or changed.

**Output:** `reports/YYYY-MM-DD-close-review.md`, in the shape of `knowledge/examples/close-review.md`: headline, checklist status, reconciliations, entries and balances to explain, open items by owner, adjustments proposed, verdict, sources.

**Done when:** every checklist step has a status backed by evidence, or is marked open. Every reconciliation is tied, or has its residual and aged items. The verdict on whether the period can be called closed is written. The report is published, and the task is marked done with the headline.

---

## 1. Inputs

- On the task: the trial balance, the reconciliation exports, and the bank and card statements for the period. If any is missing, ask once with `hub task ask <id>`, list what is missing, and carry on. Mark the parts that need it as open.
- `knowledge/close-checklist.md` (steps, owners, tolerance, review limits), `knowledge/reconciliations.md`, and last month's review.

## 2. Steps

1. Set each checklist step's status from the exports and the owners. A step with no evidence is open, not done.
2. Reconcile each account in `knowledge/reconciliations.md` with `skills/reconcile-an-account/SKILL.md`. A residual is escalated the same day, as `AGENT.md` says.
3. Review the books with `skills/review-the-books-for-unusual-entries/SKILL.md`, using the review limit in the checklist.
4. Explain each movement over the variance limit with `skills/explain-a-variance/SKILL.md`.
5. List open items by owner, with what each one blocks.
6. Propose adjustments as lines for the accountant: account, debit or credit, amount, reason. Apply none.
7. Write the verdict: can the period be called closed? If not, list exactly what is missing.

## 3. Write and hand over

Write the report and publish it with `hub file publish reports/<name>.md`. Commit it. Finish with `hub task update <id> --status done --note` and the headline.

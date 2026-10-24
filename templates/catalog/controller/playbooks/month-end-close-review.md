# Month-end close review

Schedule: the 3rd of each month at 09:00 team time (routine `month-end-close-review`), after setup. Budget 30 minutes. The outcome is the report below, ready for a
person to act on. Nothing is sent, paid, signed or changed.

---

## 1. Inputs

The trial balance, the reconciliation exports and the bank statements on the task (ask once if missing), `knowledge/close-checklist.md`, and last month's review.

## 2. Work

1. Set each checklist step's status from what the exports and the owners show. A step with no evidence is open, not done.
2. Check every reconciliation: the book balance against the statement balance at month end, and the difference with its reason. An unexplained difference is on the escalation list.
3. Review the books: entries over the threshold the Head of Finance set, uncategorized items, round-number or duplicate entries, and balances that moved more than usual without a reason.
4. List open items by owner with what each blocks. Propose adjustments as lines for the accountant; apply none.
5. Say plainly whether the period can be called closed, and if not what is missing.

## 3. Write and hand over

Write `reports/YYYY-MM-DD-close-review.md` in the shape of `knowledge/examples/close-review.md`, `hub file
publish` it, commit, and `hub task update <id> --status done --note` with the headline.

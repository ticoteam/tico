# Monthly close checklist

Schedule: the 1st of each month at 09:00 team time (routine `monthly-close-checklist`), after setup. On a weekend or when the bank feed has not reached month end,
still run, and say which lines are waiting on the feed. Budget 40 minutes. The outcome is one status
the owner or accountant can act on in five minutes. Nothing is posted.

The line order follows public month-end guidance (see the sources in docs/starter-bots.md); replace
it with `knowledge/close-checklist.md` wherever the team's own differs.

---

## 1. Read where things stand

    hub task show <id>

Then `knowledge/close-checklist.md`, `knowledge/open-questions.md` and last month's status. Find this
month's exports; note date range and row count for each.

## 2. Walk the checklist, in order

For each line write done, open or blocked, who owes it, and the evidence:
1. Bank, card and payment-processor feeds reach the last day of the month (last row date is the check).
2. Transactions that never hit a feed (cash, manual cheques, personal outlays for the team).
3. Receipts and bills: every transaction over the receipt rule has one; list the ones that do not.
4. Uncategorised transactions: run `playbooks/categorize-transactions.md`; count what is left.
5. Bank, card and processor reconciliation: needs the month-end statements. Reconciling is a
   human's job; you only say whether the statement is in and whether the export's ending balance
   matches the statement's if both are given.
6. Payroll entries against the payroll report, if given. If not, "unchecked", never "fine".
7. Recurring and adjusting entries a human should make (prepaids, accruals, depreciation): list them.
8. Receivables aging: invoices over 30 days go to `hub task create --owner ar-followup`.
9. Payables: bills that arrived but are not in the export.
10. Compare draft income and expense totals with last month; name any category that moved by more than
    the threshold in `knowledge/rhythm.md` (default 25 percent and 500 in the currency) with the
    two rows behind it.
11. Period lock: never yours; state that it waits for the accountant.

## 3. Batch the questions

One numbered list for the owner, each answerable in a few words. One separate list for the accountant:
tax, payroll, capital or personal treatment, with the facts and no recommendation.

## 4. Write and hand over

Write `reports/YYYY-MM-close-status.md` in the shape of `knowledge/examples/close-status.md`: headline,
checklist, proposed categories, owner questions, accountant list, could not read, sources. Then:

    hub file publish reports/YYYY-MM-close-status.md

Share within the requested work and intended audience with your Tools. Messages to outsiders stay drafts until a person turns mail sending on in Tico.

## 5. Finish

Update `knowledge/open-questions.md`. Commit, then `hub task update <id> --status done --note`: lines
done out of total, lines that need a human, and which sources you could not read. When a source
failed, say which and what is therefore unchecked. Always finish the task: an open routine task
absorbs next month's.

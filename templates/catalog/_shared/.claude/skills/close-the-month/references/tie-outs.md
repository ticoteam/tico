# Balance sheet tie-outs and the close calendar

## The tie-out checks

Run all of them at level 4. Write each on the close page as pass or fail with both numbers. A failed check in the
first group blocks the close.

**Must hold**

| # | Check | Left side | Right side |
|---|---|---|---|
| 1 | The balance sheet balances | Total assets | Total liabilities plus total equity |
| 2 | Cash is real | Cash on the balance sheet | Sum of the reconciled money account balances |
| 3 | Cash moved as reported | Closing cash minus opening cash | Net change on `tico finance report cash_flow` |
| 4 | Profit reached equity | Net income on `tico finance report pnl` | Change in retained earnings, plus draws, minus contributions |
| 5 | Opening is last closing | Each account's opening balance | Its closing balance on last month's close page |
| 6 | Nothing is parked | Suspense, clearing, undeposited funds, Opening balance equity | Zero, or each amount explained |

**Subledgers**

| # | Check | Left side | Right side |
|---|---|---|---|
| 7 | Receivables | `ar_aging` total | Accounts receivable |
| 8 | Payables | `ap_aging` total | Accounts payable |
| 9 | Deferred revenue | Contract schedule total | Deferred revenue account |
| 10 | Prepaids | Prepaid schedule total | Prepaid expenses account |
| 11 | Accruals | List of open accruals | Accrued expenses account |
| 12 | Fixed assets | Asset list, cost less depreciation | Fixed asset accounts |
| 13 | Loans | Lender statements | Loan accounts |
| 14 | Payroll and tax liabilities | Provider report, tax workpaper | Their liability accounts |

**Sense checks** (a failure is a question, not a block)

- No customer has a negative receivable without a note (an unapplied payment or a credit).
- No vendor has a debit payable without a note (an overpayment to recover).
- No asset account is negative. No liability account is positive the wrong way.
- Revenue for the month is within reason of billings plus the change in deferred revenue.
- Payroll expense is within reason of headcount times last month's cost per head.
- Other expenses holds nothing a named account should hold.
- A closed prior month still matches its snapshot. Tico flags a closed month that was restated; read the flag on
  `tico finance report budget_vs_actual` (`snapshot.restatements`) and explain any count above zero.

## The close calendar

Business days after month end. Tune the days in `knowledge/close-practice.md`; keep the order.

| Level | Day | Work | Done when |
|---|---|---|---|
| 0 Prepare | -3 to 0 | Feeds healthy, bills and receipts in, invoices out, payroll posted, missing documents asked for | Inbox shows no failed sync and no draft bill dated in the month |
| 1 Cash | 1 | Suspense emptied, every money account reconciled, transfers matched | `uncategorized_count` 0, `unreconciled_days` 0 |
| 2 Subledgers | 1 to 2 | AR, AP, billing, payroll, Gateway credit, sales tax tied | Each has a row with a residual and items |
| 3 Adjustments | 2 | Accruals and reversals, prepaids, deferred revenue, fixed entries | Recurring list fully ticked |
| 4 Tie-outs | 2 to 3 | Roll-forwards foot, checks 1 to 14 run | Checks 1 to 6 pass |
| 5 Review | 3 | Unusual entries tested, surprises explained, note written, close asked for | `close_period` waits on the owner |

Dependencies: nothing in level 2 is final before level 1, because cash application changes receivables and payables.
Nothing in level 3 before level 2, because an unbilled cost is found by comparing bills with the vendor list. Level 4
needs every entry posted.

The critical path is usually: a failed bank feed, then one late vendor bill, then the deferred revenue schedule. Start
those first.

## When a level is blocked

- Say who holds what, on the page and on the task, and file a sub-task for them.
- Work every line that does not depend on it.
- A blocker older than one business day is raised with the owner in one line: what is missing, what it holds up, and
  what you will do if it does not arrive (close with an estimate marked as such, or hold the close).
- The close is held, not rushed, when a must-hold check fails.

## A faster close

Most of a close can happen during the month:
- Categorize and match daily (the hourly Books check does this).
- Reconcile money accounts weekly, so month end is one more week, not thirty days.
- Keep the schedules (prepaid, deferred revenue, accruals) current as contracts are signed, not at month end.
- Fix the cause of anything that was a reconciling item two months running: a posting rule, a vendor that needs a
  purchase order, a statement that should be connected.

Measure the close by business days to "ready" and by the count and age of items carried. Both should fall.

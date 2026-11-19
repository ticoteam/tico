---
name: close-the-month
description: Close a month's books so every number after it can be trusted. Runs the close calendar in dependency order, reconciles every money account and balance sheet account to its statement or subledger, posts accruals, prepaid amortization and deferred revenue, builds roll-forwards that foot, ties out the balance sheet, and asks the owner for the one OK that closes the period. Use when a task says month-end, close the books, close the month, period ready, reconcile, reconciliation, bank rec, accrual, prepaid, deferred revenue, revenue recognition, roll-forward, tie-out, trial balance, balance sheet does not balance, suspense, or "are the books right".
metadata:
  function: finance
  roles: [finance-owner, controller, revenue-accountant, accounts-payable-specialist, accounts-receivable-specialist, payroll-specialist, tax-specialist]
  actions: [read, draft, change-records]
  review: professional
---
# Close the month

When to use: when Tico opens a "month ready to close" task (`finance.period.ready`), in the last three business days of
every month to prepare, and whenever a person asks whether a balance or a month can be relied on.

Words used here: **the company** is the business whose books these are. **The book** is its ledger in Tico Finance. A
**money account** is a bank or card account. A **subledger** is the detailed list behind one balance: open invoices
behind Accounts receivable, open bills behind Accounts payable, the contract schedule behind Deferred revenue. A
**reconciling item** is one specific thing that explains part of a difference. A **plug** is an entry made only to
force a number to agree. Plugs are never made.

On `review: professional`: the monthly close runs without an outside reviewer. A CPA checks three things only: the
revenue policy when it is first written or changed, the year-end close, and any statements that go to a lender, an
investor or a tax authority.

## What great looks like

A great close is boring. It happens on the same days every month, takes three business days, and ends with a balance
sheet where every account has a piece of paper behind it.

- **Every balance is supported.** Each balance sheet account ties to something outside the ledger: a bank statement, a
  subledger, a schedule, a contract. "It's what the system says" is not support.
- **Differences are explained, never forced.** Each reconciling item is one of three kinds: timing (it clears by
  itself, and you say when), adjustment (it needs an entry, and you post it), or investigate (nobody knows yet, and it
  has an owner and an age).
- **Roll-forwards foot.** Opening plus what came in, minus what went out, equals closing. When it does not, the gap is
  shown with its amount.
- **The month holds the right costs and revenue.** What was used in the month is in the month, whether or not the bill
  arrived. What was billed for later months waits on the balance sheet.
- **Nothing waits for the close to be found.** Most of the work was done during the month, so the close is checking,
  not catching up.

## First time at a company

Read `knowledge/close-practice.md`. If it has an "Our settings" section, skip this. If not, ask the owner these in one
numbered message, save the answers there with the date, and work on the defaults until they reply.

1. **Cash or accrual?** Default: the basis the book is set to (every report returns it). Recommend accrual if the
   company invoices customers, sells annual plans, or gets bills after it uses a service. On a cash book, steps 9 to 12
   shrink to a check that nothing is misdated.
2. **How fast?** Default: books ready by business day 3 after month end, close asked for the same day.
3. **How small is too small?** Default: money accounts tie to the cent. Elsewhere, a difference under 50.00 on one
   account is listed and carried, not chased. An unbilled cost under 250.00 is not accrued. These are starting points;
   tune them to the company's size.
4. **How do customers pay?** Monthly, annual up front, by usage, by milestone? This sets the revenue policy in
   `knowledge/revenue-policy.md`. Default: subscriptions earn day by day over the service period; one-off services earn
   when delivered; usage earns as used; a setup fee with no separate value earns over the subscription it starts.
5. **Who is the outside accountant, and what do they want from us?** Default: none named. The year-end pack is the
   twelve close pages and the workpapers behind them.
6. **What else is on the balance sheet?** Loans, inventory, equipment, payroll through a provider, sales tax? Default:
   read the balance sheet; every account with a balance or activity gets a line on the close page.

## Steps

Tools: `tico finance report`, `tico finance search`, `tico finance metric`, `tico finance act`. Amounts are integer
minor units: 315000 is 3,150.00. A write is `tico finance act <kind> --payload '<json>' --operation-id <key>`; the key
is built from what you act on (`reconcile:<money account id>:<statement end>`), so a retry lands once. `--dry-run` shows
what would happen. `tico finance act help --payload '{"kind":"<kind>"}'` lists a kind's fields. An action ends
`committed`, `needs_approval`, `denied` or `failed`.

The close runs in six levels. A level starts when the one before it is done; inside a level, do the work in any order.
Track each line on the close page as **not started**, **in progress**, **blocked** (with who), **done** or **signed**.

**Level 0. Before month end (last three business days)**

1. **Open the close page** for the month from last month's (see Output). List every balance sheet account with a
   balance or activity; each is a line.
2. **Clear the way.** Read `tico finance inbox`. Fix failed syncs first (`sync_failed`): nothing reconciles on a feed
   that stopped. Enter draft bills dated in the month (`enter-a-bill-from-a-file`). Match receipts. Confirm the invoice
   run went out and run `find-unbilled-work`. Confirm payroll for the month is posted.
3. **Ask for what only others have**, as one sub-task each (`tico task file --parent <id> --owner <teammate> "<what, by
   when>"`): a statement for a bank Tico cannot connect, a contract signed this month, a delivery confirmation.

**Level 1. Cash and cards (business day 1)**

4. **Empty suspense.** `tico finance metric uncategorized_count`. Categorize every settled row of the month with
   `categorize-a-feed-row`. Tico refuses to close a month with rows still waiting.
5. **Reconcile every money account through month end** with the method in `references/reconciliations.md`, then record
   it with the `reconcile` action. For an account with no connection, post its statement first (`statement_post`, after
   a preview). `tico finance metric unreconciled_days` must reach zero for each account.
6. **Check transfers and card payments.** A move between the company's own accounts shows on both sides and touches no
   P&L line. A card payment settles the card balance and is not an expense.

**Level 2. Subledgers (business day 1 to 2)**

7. **Tie each subledger to its account** at month end:
   - `tico finance report ar_aging` total against Accounts receivable on `tico finance report balance_sheet --end <last
     day>`. Negative customer balances are unapplied payments or credits: list them. Cash application and the
     doubtful-accounts figure come from `collect-receivables`.
   - `tico finance report ap_aging` total against Accounts payable. A vendor with a debit balance was overpaid or paid
     twice: list it as a recovery for `pay-bills-and-buy`.
   - Billing against revenue: `reconcile-billing-to-the-books`.
   - Payroll liabilities against the payroll provider's report.
   - Gateway credit (the prepaid balance AI usage draws down) against the Gateway's own balance.
   - Sales tax payable against `tico finance report sales_tax` if you may read it.
8. **Each difference becomes reconciling items**, classified timing, adjustment or investigate, with an owner and an
   age (`references/reconciliations.md`).

**Level 3. Adjustments (business day 2, accrual books)**

9. **Accrue what was used and not billed.** Use `references/close-entries.md`. The usual ones: contractor work done in
   the month, hosting and AI billed in arrears, payroll earned and not yet paid, interest. Each accrual names its
   support (a contract rate times hours, last month's usage, a statement of work) and **reverses on the first day of
   next month**, so the real bill does not count twice.
10. **Spread what was paid ahead.** An annual bill for a service that runs past the month sits in prepaid expenses and
    comes out one month at a time (`prepaid_amortize`). Check that this month's slice was taken for every prepaid line.
11. **Roll the deferred revenue schedule** with `references/deferred-revenue.md`: for every contract, opening plus
    billings minus revenue earned equals closing, by the policy in `knowledge/revenue-policy.md`. Post the month's
    release. Revenue delivered but not yet invoiced is accrued as unbilled revenue.
12. **Post the fixed entries**: depreciation, loan principal against interest, the doubtful-accounts adjustment
    (`collect-receivables` gives the figure by aging bucket), anything else on the recurring list in
    `knowledge/close-practice.md`.

**Level 4. Roll-forwards and tie-outs (business day 2 to 3)**

13. **Roll forward every balance sheet account that is a schedule**: prepaid expenses, accrued expenses, deferred
    revenue, fixed assets, loans, equity. One table each: opening, additions, releases or payments, closing. Opening
    equals last month's closing. Each line cites the search or report it came from. If it does not foot, the gap is a
    line called "unexplained" with its amount. **Surface it; never plug it.**
14. **Tie out the balance sheet.** Run every check in `references/tie-outs.md`. The ones that must hold:
    - Assets equal liabilities plus equity.
    - Cash on the balance sheet equals the reconciled balances of the money accounts.
    - The change in cash equals `tico finance report cash_flow` for the month.
    - Net income on `tico finance report pnl` equals the change in retained earnings, after owner draws and
      contributions.
    - Suspense, clearing and Opening balance equity are zero, or each remaining amount is explained.

**Level 5. Review and close (business day 3)**

15. **Test the entries** with `review-the-books-for-unusual-entries`: duplicates, round amounts, late postings, entries
    from outside finance, reversals with no original.
16. **Scan for surprises.** Compare each balance sheet line and each P&L line with last month. Anything that moved past
    the limit and has no cause yet goes to `build-the-pnl` (drivers) before the close is asked for, not after.
17. **Read the checklist**: `tico finance report close --month <YYYY-MM>`. Every check `ok`, and `closable` true.
18. **Write the close note** as a comment on the task: basis, as-of time, what was adjusted, what is carried open (with
    amount, age and owner), and the tie-out results. Publish the close page.
19. **Ask for the close**: `tico finance act close_period --payload '{"month":"<YYYY-MM>"}' --operation-id
    close:<YYYY-MM>`. Send the month only. Tico asks the owner; that is the one OK in a normal close. Mark the task
    done with one line saying the close waits on the owner and what is carried open.
20. **After the OK.** The month is locked. A late item goes into the open month with a note. Reopen only for an error
    large enough to change a decision someone made from the closed numbers; that is `reopen_period`, and the owner
    decides. Hand the closed month to `build-the-pnl` and `forecast-cash-and-budget`.

A book mirrored from QuickBooks or Xero may refuse a write that belongs to the provider. The refusal says so. Record
the proposed entry on the task for whoever keeps that book, and carry the item as an adjustment not yet posted.

## Gates

- **No close with an unexplained difference on a money account.** Not one cent.
- **No plugs.** An entry exists because a document supports it. An unknown is carried as "investigate" with its
  amount, owner and age.
- **A roll-forward that does not foot blocks its account** until the gap is listed.
- **The balance sheet balances** and net income ties to equity, or the close is not asked for.
- **Every accrual has support and a reversal date.** An accrual from memory is not posted.
- **An item marked timing names the day it clears.** If that day passes and it has not cleared, it becomes
  "investigate".
- **Items over 60 days old are escalated** to the account's owner on the close page; over 90 days, to the owner of the
  company with a proposed resolution.
- **You do not approve your own entry.** If Tico returns `needs_approval`, a person decides. By default (Tico, as of
  2026-11-18) a bot's single entry over 5,000.00 asks a finance admin.
- **A closed month is not edited.**
- **Nothing downstream runs on an open month** without the word "flash": no budget, forecast, board update or P&L.
- **Revenue policy is written before it is applied.** A new kind of contract (a bundle, a refund right, a multi-year
  deal) is recognised only after the policy says how. Until then its cash sits in deferred revenue and the question
  goes to the owner and the accountant.

## Autonomy

Do on your own, every month:
- Everything in levels 0 to 5: categorize, match, reconcile, post statements, tie subledgers, post accruals and their
  reversals, amortize prepaids, release deferred revenue by the written policy, build roll-forwards, write the note,
  publish the page and ask for the close.
- Post an adjustment a reconciliation proves (a bank fee, interest, a missed receipt).
- File sub-tasks for teammates who hold a missing document, and tasks for aged items.
- Propose posting rules when a pattern repeats.

Needs an OK:
- **Closing the month, and reopening one.** Tico always asks the owner; no rule turns this off.
- **Writing a balance off the balance sheet** above the "too small" limit: an old receivable, an unreturned deposit,
  an uncashed check. It cannot be cleanly undone, so show the evidence and ask.
- **A new or changed revenue policy**, and the first entry under it.
- Anything Tico returns as `needs_approval`. Tico has already put it in front of the right person; carry on with the
  rest of the close and note what waits.

Never: move money, pay, refund or reverse a bank transaction to make an account tie.

## Output

1. **The close page**: `tico page publish reports/close-<YYYY-MM>.html --title "Close <Month YYYY>" --cadence monthly
   --as-of <last day> --task <id>`. On it:
   - A status strip: the six levels, each done or not, and the business day it finished.
   - The account table: every balance sheet account, its balance, what it ties to, the difference, and its status.
   - Reconciling items: kind, amount, age, owner, next step. Oldest first.
   - Roll-forwards as small tables that visibly foot.
   - The entries posted in the close, each with its support.
   - The tie-out checks, pass or fail, with both numbers.
2. **The close note** on the task (about 120 words).
3. **The `close_period` action**, waiting on the owner.
4. **Tasks** for each "investigate" item and each missing document.
5. **`knowledge/close-practice.md`** updated when a recurring entry, a limit or an account list changed, and
   `knowledge/revenue-policy.md` when a contract type was added.

## Checks before you finish

- Every money account is reconciled through the last day, to the cent, against a named statement.
- Every balance sheet account on the page has support, a difference (zero or listed) and a status.
- Each reconciling item is timing, adjustment or investigate, with an owner and an age. None is "misc".
- Every roll-forward's opening equals last month's closing, and foots.
- Each accrual shows its support and its reversal.
- Assets equal liabilities plus equity. Net income ties to equity. Cash ties to the money accounts.
- No plug. Search the month's entries for memos like "adjust", "true-up", "to balance" and check each has a document.
- The checklist read `closable` before the close was asked for.
- The task says what is carried open, and who owns it.

## Example (invented, for illustration)

Close for October 2026, accrual book. Books ready on business day 3.

Operating bank, statement dated 2026-10-31. Book 52,318.40. Statement 53,118.40.
- Payment sent 2026-10-30, not yet on the statement: 1,050.00. **Timing**, clears 2026-11-02.
- Bank fee on the statement, not in the book: 25.00. **Adjustment**, posted.
- Debit of 225.00 on 2026-10-29, not in the book. **Investigate**. Traced the same day to a card processor chargeback
  (dispute notice of 2026-10-28). **Adjustment**, posted against the invoice it reverses.
- Adjusted statement 53,118.40 - 1,050.00 = 52,068.40. Adjusted book 52,318.40 - 25.00 - 225.00 = 52,068.40. Ties.

Accrual: the hosting vendor bills usage in arrears. September's bill was 4,090.00 and October's usage export shows
4,120.00. Accrued 4,120.00 on 2026-10-31 (support: usage export read 2026-11-02), reversing 2026-11-01.

Deferred revenue roll-forward: opening 96,000.00 + billings 24,000.00 - earned 21,500.00 - refunds 500.00 = closing
98,000.00. Ledger 98,240.00. Difference 240.00: invoice 4471, billed 2026-10-31 after the billing export was pulled.
Added to the schedule; it now foots to 98,240.00.

Tie-outs: assets 412,906.15 = liabilities 181,240.00 + equity 231,666.15. Net income 14,388.00 = change in retained
earnings. One item carried: a 310.00 vendor credit, 34 days old, owner payables. Close asked for; waits on the owner.

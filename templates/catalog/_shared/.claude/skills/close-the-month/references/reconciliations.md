# Reconciling an account

One method for every account. Use it for a bank statement, a card statement, a subledger or a schedule.

## The method

1. **Fix the date.** Use the statement's closing date, not the day you pulled the export. If the export is later, use
   only transactions dated on or before the statement date.
2. **Write both balances** with their source and time: the book balance for the account at that date (the balance
   sheet or a search on the account), and the statement or subledger balance for the same date. Neither is estimated.
3. **Compute the difference**, book minus statement. Keep the sign.
4. **List the reconciling items.** Each is one specific thing: date, amount, counterparty, description. The usual
   ones:
   - In the book, not yet on the statement: payments sent, deposits in transit, checks not cashed.
   - On the statement, not in the book: bank fees, interest, a chargeback, a direct debit, a card refund.
   - Posted to the wrong account, the wrong period or twice.
   - In a subledger and not in the ledger, or the reverse.
5. **Classify each item**, exactly one of:
   - **Timing.** It clears by itself. Name the day it will. No entry.
   - **Adjustment.** The book is wrong or incomplete. Post the entry, with the document attached.
   - **Investigate.** Nobody knows yet. It gets an owner, and its age is counted from the day it arose.
6. **Age each open item** from the date it arose to the close date: 0 to 30 days, 31 to 60, 61 to 90, over 90.
7. **Look for duplicates**: the same amount and counterparty within 60 days on the list, or one item appearing both as
   a reconciling item and as a posted entry.
8. **Work out the adjusted balances.**
   - Statement side: statement, plus deposits in transit, minus payments not yet cleared.
   - Book side: book, plus receipts not yet recorded, minus payments and fees not yet recorded.
   - The residual is adjusted statement minus adjusted book. Zero means the account ties.
9. **Record it.** For a money account, the `reconcile` action in Tico matches the statement to the feed
   (`reconcile_undo` takes it back while nothing depends on it). For every account, the close page gets one row: ties
   or not, the residual, the open items by owner, the next step for each.

## Rules

- **A residual is never forced to zero.** Any amount nobody can explain is an "investigate" item with that amount.
- **A money account ties to the cent** before the close. Other accounts tie within the limit in
  `knowledge/close-practice.md`; a difference under it is listed and carried, with the limit named.
- **Each explanation names a document.** Not a guess, not a memory.
- **A pending item is not a loss.** A payment takes about a business day to post on both sides, and some take several
  days to settle. Check its status and give it its normal window before calling it missing
  (`reconcile-a-cash-figure`).
- **A timing item that misses its day becomes "investigate".** Do not re-date it.
- **Old items are not reworded.** An outstanding check from three months ago is escalated: confirm with the payee, void
  and reissue, or follow the state's unclaimed property rules (they vary by state; ask the accountant).
- **Never move money to fix a reconciliation.** Corrections are entries, proposed or posted in the book. A refund or a
  reversal at the bank is the record owner's decision.
- **A statement upload that does not tie is a warning, not a refusal** in Tico: the account then shows how far off it
  is until a reconciliation runs through the statement's end. Do not treat an uploaded statement as reconciled.

## How often to reconcile each account

Rank by risk, not by habit. Risk is higher with more transactions, more people posting, more judgment, or a history of
differences.

| Account | How often | Against |
|---|---|---|
| Bank and card accounts | Daily matching, monthly reconciliation | The statement |
| Payment processor balances | Monthly | The processor's payout report |
| Accounts receivable | Monthly | The aging report |
| Accounts payable | Monthly | The aging report, and vendor statements for the five largest vendors |
| Deferred revenue | Monthly | The contract schedule |
| Prepaid expenses, accrued expenses | Monthly | Their schedules |
| Payroll liabilities, sales tax payable | Monthly | The provider's report, the tax workpaper |
| Gateway credit | Monthly | The Gateway's own balance |
| Fixed assets, loans | Monthly if anything moved, else quarterly | The asset list, the lender's statement |
| Equity | Quarterly, and whenever anything moved | Signed documents |

## The row on the close page

| Account | Book | Statement or subledger | Residual | Open items | Oldest | Status |
|---|---|---|---|---|---|---|
| Operating bank | 52,068.40 | 52,068.40 (after items) | 0.00 | 1 timing | 1 day | done |

The numbers in this row are invented, to show the shape.

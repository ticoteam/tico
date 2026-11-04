---
name: reconcile-an-account
description: Tie one balance sheet account to its statement or subledger at a stated date, list each reconciling item with its age and owner, and say whether the account ties. Use before saying an account is reconciled or is not.
---

# Reconcile an account

## When to use
Before you report that an account ties, or that it does not, for a close. Also when a person asks why one account's balance differs from its statement.

## Steps
1. Fix the date. Use the statement's closing date, not the date the export was pulled. If the export is later, use only transactions dated on or before the statement date.
2. Take the book balance from the trial balance export for that account, and the statement or subledger balance for the same date. Write both with their source and time stamp.
3. Compute the difference, book minus statement, and keep the sign.
4. List the reconciling items. Typical ones: deposits in transit, unrecorded receipts, outstanding checks, unrecorded bank fees or interest, pending card transactions, subledger items not posted to the ledger. For each: date, amount, counterparty, description, owner.
5. Age each item from the date it arose to the close date. Bands: up to 30 days, 31 to 60, 61 to 90, over 90. The limits are in `knowledge/close-practice.md`.
6. Check for duplicates: the same amount and counterparty within 60 days on the list, or an item that appears both as a reconciling item and as a posted entry.
7. Work out the adjusted balances. Bank side: statement plus deposits in transit minus outstanding checks. Book side: book plus unrecorded receipts minus unrecorded payments. The residual is the difference between the two. Zero means the account ties. Any residual is unexplained and goes on the escalation list with its amount.
8. Write the result in this order: ties or not, the residual, the aged items by owner, the next step for each. Use the table in `knowledge/examples/close-review.md`.

## Checks before you finish
- Both balances carry a date and a source. Neither is estimated.
- Every item has an owner and an age. Name who can answer when the owner is unknown.
- The residual equals the adjusted bank balance minus the adjusted book balance, to the cent. Add it again.
- No item is marked explained on a guess. Each explanation names its document.
- Items past the age limit are flagged, not reworded or re-dated.

## Example (invented)
Operating bank, statement dated 2026-10-31. Book balance 84,210.55. Statement balance 83,990.55. Difference: 220.00 (book higher).

Reconciling items:
- Deposit in transit, 2026-10-31: 1,500.00 (bank side, adds to the statement)
- Outstanding check 4412, 2026-10-12: 320.00, age 19 days (bank side, deducts)
- Outstanding check 4390, 2026-08-20: 1,200.00, age 72 days (bank side, deducts; owner told in writing)
- Bank fee charged 2026-10-30, not in the book: 240.00 (book side, deducts)

Adjusted bank: 83,990.55 + 1,500.00 - 320.00 - 1,200.00 = 83,970.55.
Adjusted book: 84,210.55 - 240.00 = 83,970.55.
Residual: 0.00. The account ties. Next step on check 4390: confirm with the payee whether it was received or voided.

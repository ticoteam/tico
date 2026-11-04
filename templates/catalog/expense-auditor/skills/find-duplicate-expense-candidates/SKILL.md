---
name: find-duplicate-expense-candidates
description: Find expense lines that may record the same spend twice across reports, cards and people, rate each match on its evidence, and write both lines with the rule. Use before flagging any duplicate.
---
# Find duplicate expense candidates

When to use: when you audit a month or review a report and need to decide whether two lines, or a line and a receipt,
are the same spend.

Steps:
1. Normalise the merchant. Strip store numbers, branch names and city suffixes. Treat a card's merchant name and a
   receipt's name as the same merchant when the first word and the category match.
2. Pull candidates in three passes:
   - same receipt: a receipt file name, receipt number or image that appears on more than one line;
   - same spend: the same amount to the cent (or the card's statement amount, when the receipt is in another currency),
     with a date within 3 days, since card posting can lag the purchase;
   - same event: the same amount on two people's claims where the attendee list shows the same event.
3. Exclude first. Recurring charges (subscriptions, a monthly fee on the same day) are not duplicates. Split bills, with
   two cards, two receipts and different totals, are not duplicates. Refunds and reversals are their own lines.
4. Rate each candidate:
   - strong: the same receipt on two lines, or the same amount, merchant and date on one person's lines;
   - medium: the same amount and merchant, dates 1 to 3 days apart, on different people or cards;
   - weak: the same amount alone, or the same merchant on the same day with different amounts.
   Report strong and medium. Mention weak ones only where they change a total.
5. Write each candidate: both lines with their row numbers, the date, the amount, the rule ("one reimbursement per
   expense", with its policy section), and what would clear it (a second receipt, a different event, a different attendee).

Checks before you finish:
- Each candidate names two lines, not one.
- No candidate is worded as a conclusion. Write "duplicate candidate" and the rule, never "duplicate payment" or "fraud".
- Recurring charges and split bills have been excluded.
- The export totals tied out before the check ran.

Example (invented, for illustration): "Duplicate candidate, strong. 2026-09-14, Harbor Grill, 412.00: card row 44 (card 7)
and claim CL-0913, line 2 (Lena). Same receipt file `r-0914-harbor.jpg`. Rule: one reimbursement per expense (policy 2.1).
Clears with: a second receipt for a separate event."

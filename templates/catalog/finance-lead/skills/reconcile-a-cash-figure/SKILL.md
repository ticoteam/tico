---
name: reconcile-a-cash-figure
description: Before a cash, runway or balance figure reaches the owner as an alarm, trace a disagreement between two sources to the transaction, wait for pending items to settle, and state which source the verdict comes from.
---
# Reconcile a cash figure

When to use: when two sources disagree on cash, a balance drops sharply, a payment looks missing, or a figure is about to be
called an alarm.

Steps:
1. Name both sources and their dates: the bank export, the books, the payment platform, the card statement.
2. Find the transaction in each. Match by amount, date and counterparty, allowing for posting lag. A payment usually takes
   about one business day to post on both sides; some take several days to settle. Check its status first.
3. Wait before you alarm. A pending or processing item is not a loss. Give it its normal window, and escalate only a payment
   still unresolved after that window.
4. Explain the gap in one line: the cause, the transaction, and what settles it. A difference that is only timing is labelled
   timing, not an error.
5. Say where the verdict comes from. "Bank export 2026-10-02, matched to the books entry" is a verdict. "The balance looks
   low" is not.
6. Hand any correction to the owner of the record. You never reverse, refund or move money to close a gap.

Checks before you finish:
- Both sources are named with their dates.
- Each gap is explained to a transaction, or labelled unexplained.
- No pending item was counted as lost.
- The owner sees the verdict and its source in the first line.

Example (invented, for illustration): "Cash in the books is 18k below the bank export as of 2026-10-02. Cause: a customer's
payment of 18k was received on 10-01 and posted in the books on 10-02. Both entries match on amount and counterparty.
Timing only. Verdict from the bank export and the matching books entry."

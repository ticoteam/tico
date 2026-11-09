---
name: match-a-bill-to-its-order
description: Match a bill to its purchase order, receipt or contract (three-way or two-way), apply the tolerance in the approvals file, and list each difference, so only matched bills enter a payment run.
---
# Match a bill to its order

When to use: for every bill that needs a match before payment. Decide first which match applies: three-way (order, receipt
of goods or service, bill) for purchases against a purchase order; two-way (contract or rate card, bill) for recurring
contracts; none where `knowledge/approvals.md` says so.

Steps:
1. Find the order or contract the bill cites. If the bill cites none, look up the vendor's open orders in the register. Two or
   more open orders for the same amount: hold and ask.
2. Check the bill's own arithmetic first: its lines sum to its total, and tax is applied as the contract says.
3. Compare each line with the order or contract: quantity, unit price, tax, total, currency and terms. Use the current rate on
   the contract, not the rate on an older version.
4. For a three-way match, confirm the receipt: a signed delivery note, a receipt in the system, or an approved milestone. For a
   partial receipt, pay only the received part, and only if the order allows partial payment.
5. Apply the tolerance in `knowledge/approvals.md`. Default: a difference over 2% of the bill or 100 in the currency, whichever is
   smaller, is held. A smaller difference is paid and noted with its reason.
6. Record each difference on one line: field, order figure, bill figure, source of each. Mark the bill matched only when every
   field agrees within tolerance; otherwise it is held with the list.

Checks before you finish:
- A matched bill shows its order or contract number and the receipt date.
- Any tolerance used is named on the line.
- A bill matched to an order placed by the same person who will approve its payment is flagged for a second approver.
- A bill with no order, where one is required, is held for the owner, not matched by guesswork.

Example (invented, for illustration): "Brightline Design, BD-0417, 6,750.00. Order PO-1182: three deliverables at 2,250 each.
Three signed receipts (2026-09-12, 09-19, 09-26). Bill total equals the order total. Matched, three-way." A variant: a bill at
6,765 against the same order is a 15 difference, about 0.2%, within tolerance, so it is paid and the difference is noted.

# Process a bill

Triggered by a bill attached to a task, forwarded to the bills mailbox, or a question "did we get or pay the invoice from X?".
Budget 10 minutes per bill. The outcome is a register line with its match and owner, and any flag raised the same day.

---

## 1. Read it

Vendor legal name, bill number, date, due date, currency, amount, tax, line items, the payment details printed on it (last four
digits only), and the sender's address. A lookalike address, or a layout that differs from the vendor's usual one, is a flag.

## 2. Check for a duplicate

Run `skills/find-a-duplicate-bill/SKILL.md`. A probable duplicate is held and the bill it repeats is named.

## 3. Check the payment details

Compare the printed bank details with `knowledge/vendor-details.md`. Any difference, and any message asking to change them, is a
bank-detail change: run `skills/check-a-bank-detail-change/SKILL.md`. Hold every bill from the vendor until a callback on a number
already on file confirms the details.

## 4. Match

Run `skills/match-a-bill-to-its-order/SKILL.md` against the order, contract or receipt the rule requires. A bill with no order where
one is required is held for its owner.

## 5. Route

Route by the approval rules in `knowledge/approvals.md`. A payment goes into a run only after its approver has approved it. Ask for
missing facts or access on the task, one batched question per owner. A vendor's question about payment is answered as a draft on
the task. It is sent only when the task asks for it and mail sending is on in Tico.

## 6. Register

Add the line to `knowledge/bills.md`: received, matched or held (and why), owner, due date, status.

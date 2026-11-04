---
name: "reconcile-billing-to-the-books"
description: "Match the month's billing export (invoices, credit notes, refunds, payments) to the ledger's revenue and receivable postings at invoice level, and sort each difference by cause: missing, posted twice, wrong period or wrong amount."
---
# Reconcile billing to the books

When to use: at the start of each month's revenue close, or when billing and the books disagree.

Steps:
1. Confirm the date range of both exports. A billing export that ends before the month does is a finding, not a reconciliation.
2. Total each side for the month: invoiced, credited, refunded and collected in billing; revenue and receivable postings in the ledger.
3. Match at invoice level on invoice number, amount and date. Credit notes and refunds match to the invoice they reverse.
4. Sort each unmatched item into one cause: missing in the books, posted twice, wrong period (dated on the other side of month end),
   or wrong amount.
5. For each cause, name the record it needs (the entry, the invoice, the period) and the owner who corrects it. You propose
   entries; you do not post them.
6. Restate the totals after the proposed entries and show what is left, by cause.

Checks before you finish:
- Both totals are shown, with their date ranges.
- Every difference has one cause and one owner.
- Credits and refunds sit against their invoices, not in a separate lump.
- Nothing is matched on amount alone; each match has a second key.

Example (invented, for illustration): "Billing invoiced 84,300; ledger revenue postings 84,060; difference 240. Matched 61 of 62
invoices. Unmatched: invoice 4471, 240, posted in the ledger as October (wrong period; billed 2026-09-30). Proposed entry: move
240 to September. Owner: Bookkeeper."

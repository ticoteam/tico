# Correct an invoice

Triggered by a customer's query, a returned invoice, or an error found by the Accounts Receivable Specialist or the Revenue Accountant. Budget 15 minutes.
The outcome is a credit note and a corrected invoice prepared for review, and the cause fixed in the register.

---

## 1. Find the cause

    hub task show <id>

Read the invoice and its source (contract, usage, hours). Name what was wrong: amount, rate, period, PO number, contact, tax, or a duplicate. If the invoice
was right and the customer disagrees, say so with the source. That is a conversation for the account owner, not a credit.

## 2. Prepare the correction

A credit note for the full wrong invoice, with its own number and a reference to the original. Build a new invoice with `skills/check-an-invoice-before-issue/SKILL.md`.
Never edit a sent invoice. A partial credit, such as a goodwill discount, is the seller's decision: prepare it only if they gave the amount.

## 3. Hand over

On the task: the cause in one line, the credit note and the new invoice for the owner, and a reply to the customer prepared for the account owner. Tell the
Accounts Receivable Specialist on its task so reminders stop on the old invoice. Tell the Revenue Accountant if the period is closed.

## 4. Stop it recurring

Fix the register line (a rate, a PO rule, a contact) and record the cause in `memory/learnings.md`.

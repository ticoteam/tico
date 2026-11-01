---
name: check-an-invoice-before-issue
description: Run the pre-issue checks on each invoice in a run (amount against source, rate against contract, legal entity and contact, PO and tax, terms and sequence, duplicates), and mark it ready only when every check passes.
---
# Check an invoice before issue

When to use: for every invoice in a run, before it is marked ready. A customer who returns an invoice for a missing detail pays late, so the checks look
at the details the customer's accounts team looks at first.

Steps:
1. **Amount against the source.** Recompute the total from the contract, usage export or timesheet, not from the billing system's draft. Round as the
   contract says. A difference is a hold until explained.
2. **Rate against the contract.** Take the rate from the signed document. The billing system's rate is checked against it, not the other way round.
   A rate that changed by amendment needs the amendment's date and reference.
3. **Entity, address and contact.** The legal name, billing address and billing contact match the register and the contract. A subsidiary billed in
   place of the parent is a hold.
4. **PO and tax.** A PO number is present where required and has not expired, with its validity date noted. Tax is as the register gives it for that
   customer. A new place of supply or a tax status change is a question for the Tax Specialist, and the invoice is held.
5. **Terms, route and sequence.** Payment terms and the payment route match the contract. The invoice number continues the sequence without a gap.
6. **Duplicate.** The same customer and period is not already invoiced. Check the prior run and the register.

Mark the invoice ready only when all six pass. Otherwise hold it with the number of the check that failed and the reason in one clause.

Checks before you finish:
- Each ready invoice shows its source reference, the rate check and the PO check.
- A held invoice names the check and what would clear it.
- No invoice was issued with an estimated input.

Example (invented, for illustration): "Northside Movement, 14 locations at 90 each. Contract NM-2026-02 clause 4: 90 from 2026-09-01. Billing system draft: 85. Check 2 failed. Invoice built at 90 from the contract, and the owner is asked to confirm the amendment date before issue."

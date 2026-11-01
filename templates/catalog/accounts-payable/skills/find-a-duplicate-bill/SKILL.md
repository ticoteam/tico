---
name: find-a-duplicate-bill
description: Check a bill against the register for a probable duplicate or resend before it enters a payment run, using normalised bill numbers, amounts and date windows, and name the bill it repeats.
---
# Find a duplicate bill

When to use: before every bill enters a payment run, and when a vendor says a bill is unpaid, so the same bill is not paid
twice.

Steps:
1. Normalise the bill number: upper case, strip spaces, dashes, dots and leading zeros. "HP 0412" and "HP-412" are one number.
2. Match the vendor, including its known names and aliases in `knowledge/vendor-details.md`.
3. Search the register for the same vendor and amount in the last 60 days, and for the normalised bill number in the last
   12 months. Numbers can be reused after a year, so the number search runs longer.
4. Compare the candidate and the new bill side by side. A credit note, a reminder, a statement and a pro forma are not bills to
   pay. A recurring charge at the same amount in two months is two bills, told apart by the period on each.
5. Classify the result:
   - duplicate: same vendor, same normalised number, same amount. Hold and name the earlier bill.
   - probable resend: same vendor and amount, different number, same period. Hold for the owner to confirm.
   - not a duplicate: a different period, or a different scope. Pay it on its own terms and say why.
6. Record the result on the register line, even when it is "none": "checked against PW-3301 (2026-09-03): not a duplicate,
   different period."

Checks before you finish:
- Every bill has a duplicate check recorded.
- A hold names the earlier bill, its date and its status (paid, open or voided).
- A credit note is linked to the bill it reduces and is not paid as a payment.
- A duplicate that was already paid is reported to the owner as a recovery to claim, not netted off in silence.

Example (invented, for illustration): "Harbour Print invoice HP 0412, 1,240.00, dated 2026-10-02. Register: HP-412, 1,240.00,
dated 2026-08-30, paid 2026-09-08. The numbers match once normalised. Held as a duplicate. The owner is asked whether the vendor
sent a resend, which would be voided, or a second order, which would need its own purchase order."

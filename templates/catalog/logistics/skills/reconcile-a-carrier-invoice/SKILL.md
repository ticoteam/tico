---
name: reconcile-a-carrier-invoice
description: Check each line of a carrier invoice against the contracted rates and surcharges in knowledge/carriers.md, list every overcharge with the charged and contracted amounts and the rate's source, and prepare a dispute for a person to decide, without paying, approving or disputing anything.
---
# Reconcile a carrier invoice

When to use: each carrier invoice that arrives as a task attachment, before anyone pays it.

Steps:
1. Record the invoice: carrier, invoice number and date, billing period, currency and total. Add the lines. If they do not
   equal the total, say so first, before any other check.
2. Match each line to a shipment by tracking number, order or date. A line that matches no shipment in the export is
   listed as unmatched. It is not an overcharge until the shipment is found or ruled out.
3. For each matched line, compare the rate with `knowledge/carriers.md`: the base rate for the weight band or zone, and
   each contracted surcharge (residential, fuel, peak, remote area, oversize). Record the clause or rates sheet and its
   date.
4. Look for the common errors: a rate above the contract; a surcharge the contract does not name; a surcharge on a
   shipment type the contract excludes; the same tracking number charged twice; a charge for a shipment with no
   collection scan.
5. For each overcharge, write one line: invoice line, charged, contracted, difference, and the source of the contracted
   rate with its date. Add the differences to a total.
6. Prepare the dispute on the task for review, with the total and each line. The bot does not pay, approve or dispute the
   invoice. That waits for the person named in `knowledge/carriers.md` and a granted Tool.
7. List the lines that matched, with their total, so the reader can see what was checked.

Checks before you finish:
- The invoice lines add to its total, or the mismatch is named.
- Every overcharge shows charged, contracted, difference, and the rate's source with its date.
- Unmatched lines are listed as unmatched, not counted as overcharges.
- Nothing was paid, approved or disputed.

Example (invented, for illustration): "Parcelway invoice PW-2611, dated 2026-09-26; lines add to the total. 38 business
addresses charged a residential surcharge of $4.20 each, $159.60. Contract clause 4.2 (rates sheet read 2026-09-28) allows
no residential surcharge on business addresses. Tracking PW-88213 charged twice, $55.00. Overcharges: $214.60, prepared as
a dispute on the task. Not paid, not sent."

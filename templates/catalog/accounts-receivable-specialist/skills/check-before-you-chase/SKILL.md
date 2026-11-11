---
name: "check-before-you-chase"
description: "Run the five checks, in order, before any reminder is drafted for an overdue invoice: paid, disputed or promised, do-not-chase, recent thread, and personal-touch amount. Any hit means no draft, with the reason recorded."
---
# Check before you chase

When to use: for every invoice that is overdue or due inside the "before due" window, before a reminder is drafted. The order matters:
the cheapest check that can stop a chase runs first.

Steps:
1. **Paid?** Look for the invoice in the latest export with a payment, or a zero balance. Also look for a payment in the bank or
   processor export if one is attached. If it is paid, stop and note "paid, per export dated X".
2. **Disputed or promised?** Read the customer's entry in `knowledge/customers.md`. A dispute, an open query, or a promise to pay
   with a date not yet passed stops the reminder. Note the date and the source of the entry.
3. **Do-not-chase?** Check the list. A name on it is skipped and named as skipped, whatever the aging says.
4. **Recent thread?** Where the sender's mailbox is connected, search the customer's address and read the last exchange. A reply from
   the customer that no person has answered goes to "needs you now", not to a reminder. A payment query from the customer is a
   dispute until a person says otherwise.
5. **Personal-touch amount or past the last step?** Above the amount in `knowledge/ladder.md`, or past the last automated step, the
   invoice goes to "needs you now" with no draft. A person writes or calls.

Only an invoice that passes all five gets a draft. Record every stop with its check number and the reason.

Checks before you finish:
- Each invoice in the pack shows either a draft or the check that stopped it.
- A promise to pay with a date in the past is treated as broken, and the owner is asked, not the customer chased on autopilot.
- Nothing is chased on the strength of an export older than three days without saying so first.

Example (invented, for illustration): "Invoice 2052, Lakeside Yoga, 850, 22 days overdue. Check 2: disputed on 2026-09-15 (customers.md,
source: email from the owner). Skipped. Held back in the pack with the date. Invoice 2060, Birch Cafe, 400: check 1 stopped it, shown paid
in the export dated 2026-09-28."

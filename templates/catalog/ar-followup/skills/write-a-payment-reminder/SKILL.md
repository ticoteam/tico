---
name: write-a-payment-reminder
description: Write one payment reminder for an overdue invoice in the sender's voice, at its step of the ladder, under 125 words, with one ask, the invoice facts and no term that was not given.
---
# Write a payment reminder

When to use: each time a reminder is drafted, after `check-before-you-chase` has passed the invoice. A reminder is a draft for a person to send
until mail sending is on in Tico and the task asks for the send.

Steps:
1. Find the step in `knowledge/ladder.md`: before due, 1 to 7 days late, 15 days, 30 days. At 45 days and beyond there is no draft; a human writes.
2. Take the facts from the export row only: invoice number, amount, due date, and the payment route printed on the invoice. Do not rephrase an amount.
3. Write the greeting and one opening sentence that states the invoice and its due date. Step 1: assume an oversight. Step 2: state the due date
   plainly and ask whether anything is holding it up. Step 3: reference the earlier reminder by its date and ask for a payment date.
4. Give one ask: pay by the route, tell us the payment date, or reply if there is a problem. Never two asks.
5. Add one line for a crossed payment: "If this crossed with your payment, please ignore it." Use this line at every step.
6. Sign with the sender's name from `knowledge/voice.md`. No signature block beyond the name and the company.
7. Count the words. 50 to 125. Cut any sentence that does not carry a fact, an ask or a route.
8. Stop-words: no late fee, interest, suspension, collections or legal wording unless the human gave the exact term; no guess about the
   customer's finances; no shaming; no private detail. If a term or a plan is needed, write "[for you to decide]" and ask the owner on the task.

Checks before you finish:
- The draft's invoice number, amount and due date match the export row it cites.
- It has one ask and one way to reply.
- It names no term that is not in `knowledge/customers.md` or the human's instructions.
- It reads like the sender wrote it: compare with the two examples in `knowledge/voice.md`.

Example (invented, for illustration), step 2, 6 days late: "Hi Sam, a quick note that invoice 2057 for 1,150 was due on 2026-09-22, and I do not see it as
paid yet. The payment link is on the invoice. If it has crossed with your payment, please ignore this, and tell me if anything is holding it up.
Thank you, Alex." Sixty-one words. No fee is mentioned because none was given.

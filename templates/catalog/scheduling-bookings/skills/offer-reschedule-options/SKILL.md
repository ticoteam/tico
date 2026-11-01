---
name: offer-reschedule-options
description: Answer a customer's request to move or cancel a booking with two or three slots that fit the booking rules and the customer's limits, the exact effect on any deposit or fee, and a clear route for any exception.
---
# Offer reschedule options

When to use: when a customer asks to move, shorten, split or cancel a booking, by email, form or task.

Steps:
1. Read the booking and the request. Note the customer's stated limits: days, times, location, and the latest date they can accept.
2. Read the cancellation window, lead time, buffer, and any deposit or fee in `knowledge/booking-rules.md`. Work out the notice given, in hours, from the time the request arrived.
3. Find two or three open slots, in the same service and resource, that fit the rules and the customer's limits. Prefer the nearest slot that keeps the same service length. Do not offer a slot that breaks a buffer or an opening hour.
4. State the effect on any deposit or fee exactly as the rules state it. If the rules say a fee applies, say so. Do not waive a fee; a waiver is a person's decision.
5. If no slot fits, name the rule that blocks it and who can grant an exception, using `knowledge/exceptions.md` for any precedent. Route the request to that person with `hub task create --owner <person>`, or ask the requester with `hub task ask <id>`.
6. Draft the reply: the options as a short list, the fee line if there is one, and one ask (pick a slot, or reply by a stated time). Keep it under 100 words.
7. Attach the options and the draft to the task. The booking changes only when the task asks for the change, a granted Tool allows it and a person has confirmed the slot.

Checks before you finish:
- Each option fits the rules and the customer's stated limits.
- The fee or deposit line matches the rules as written.
- No option or sentence promises an exception.
- The draft asks for one thing and gives a date or a way to answer.

Example (generic): "Your Tuesday 09:00 visit can move to Wednesday 09:30 or 14:00, both with the same crew and the full two-hour slot. Moving inside 48 hours has no fee under our rules, so there is nothing to pay. Could you pick one by Monday 17:00? Reply to this email and we will confirm."

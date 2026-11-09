# Reschedule

Triggered by a task with a customer's request to move or cancel a booking. Budget 10 minutes.

Inputs: the request, the booking, the rules in `knowledge/booking-rules.md`, and the open slots from the current export.

Outcome: two or three options that fit the rules, the effect on any deposit or fee as the rules state it, and a draft reply.
Both are attached to the task for the person who confirms.

Done when the options and the draft are on the task, any rule the request breaks is named and routed to the person who
decides exceptions, and the task is updated. The booking is not changed in this playbook.

---

1. Read the request and the booking. Check the cancellation window and any deposit in `knowledge/booking-rules.md`.
2. Follow `skills/offer-reschedule-options/SKILL.md`: find the slots, state the effect on any deposit or fee exactly as the
   rules say, and never waive one.
3. Draft the reply with the options, in the team's voice. If the request breaks a rule, say which rule and send the question
   to the person who decides exceptions, with `hub task create --owner <person>`.
4. Attach the options and the draft to the task. Apply the change only when the task asks for it, a granted Tool allows it,
   and a person has confirmed the new slot.
5. Finish with `hub task update <id> --status done --note "..."`: the request, the options offered, and what is waiting on
   the customer or a person.

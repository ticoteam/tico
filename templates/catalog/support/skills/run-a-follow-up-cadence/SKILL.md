---
name: run-a-follow-up-cadence
description: Chase a ticket that waits on a customer, a colleague or a fix, with one nudge that adds something new, a stop rule, and a closed-quiet record that says why.
---
# Run a follow-up cadence

When to use: on each queue pass, for every line in `knowledge/follow-ups.md`, and whenever a ticket is set to wait
on someone.

Steps:
1. Read the record: who the ticket waits on, since when, and the last touch. If the thread has moved since then, read
   the thread first.
2. Waiting on a customer: once the nudge rule's wait has passed (default 3 days), draft one nudge. It restates the
   open question, adds one new thing (a link, a step, a screenshot to send), and asks for one answer.
3. Waiting on a colleague or a fix: name who holds it and since when. Ask that person once on the task, with the one
   question you need answered. Do not ask again until the answer is due.
4. Send no more nudges than the rule allows (default: a first nudge after 3 days, a second 7 days later, then stop).
5. After the last nudge with no reply, mark the ticket "closed quiet" with the date and the reason, and say so on the
   task so the requester sees it.
6. Before each draft, check for a newer customer message. Never nudge about something the customer has already answered.
7. A ticket that waits on a fix is not quiet. Keep it open, put the fix's status in its line, and tell the customer
   when the fix ships (see the status wording in the Method section of AGENT.md).
8. Update the date in `follow-ups.md` each time you touch the line.

Checks before you finish:
- Each waiting ticket has who it waits on, since when, and the next date.
- No ticket was marked closed quiet while a newer customer message was unread.
- Each nudge adds something new and asks one question.
- Each closed-quiet ticket says why and on which date.

Example (invented, for illustration only): a ticket has waited on the customer for 5 days. The nudge restates the question
about which billing address is correct, adds where the field sits in settings, and asks for the address. After the
second nudge with no reply, the next pass marks it closed quiet with both dates and the reason.

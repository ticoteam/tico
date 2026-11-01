---
name: write-a-holding-statement
description: Prepare a short holding statement for one known risk in advance, with the facts that are confirmed, the facts that are not, and the owner who may approve and speak, so the team is not improvising when press calls.
---
# Write a holding statement

When to use: when a risk is known in advance (an outage, a pricing mistake, a complaint that could be covered, a
staff change), and when a reporter asks about something no statement yet covers.

Steps:
1. Name the risk in one line and the date it could become public.
2. List the confirmed facts, each with its source (the incident record, the owner's note, the public page). Mark a
   fact unconfirmed if its source is only a rumour or a single message.
3. Write the statement in three to five sentences: what happened, what is known, what is not yet known, what the
   team is doing about it, and when the next update comes. Use no number, name or cause that is not confirmed.
4. Write two or three likely questions with answers that say only what is confirmed. Write "we will confirm that"
   for the rest.
5. Name who approves it (the owner, and the General Counsel when a legal matter is possible) and who speaks.
6. Save the statement in `reports/holding-statements/<risk>.md` and link it from `knowledge/rules.md`, with the date
   it was last reviewed. Do not publish it.

Checks before you finish:
- Every fact is marked confirmed, with its source, or it is left out.
- No person is named, and no blame is put on a customer, a vendor or a staff member.
- The approver and the speaker are named, and the approval is pending until they sign off on the task.
- The statement says when the next update comes, and the date is in a calendar or task.

Example (invented, for illustration): "Risk: a sync delay between the booking app and the calendar. Confirmed: started
on 2026-08-12, fixed for new bookings on 2026-08-13 (incident record). Not confirmed: how many bookings were
affected. Statement: 'On 12 August some calendar syncs were delayed. New bookings were fixed on 13 August. We are
checking how many bookings were affected and will confirm once we have the count.' Approver: the owner. Speaker:
the founder."

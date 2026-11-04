---
name: triage-an-office-request
description: Sort an office request by safety first, then route it to the right fixer, owner or team, set a promised date and a named fixer on every request, and log it so that nothing sits as "reported".
---
# Triage an office request

When to use: on every new office request, before anyone is asked to fix, buy or book anything.

Steps:
1. Read the request as it was written, then `hub task show <id>`. Note what, where, since when, and how bad.
2. Check safety first. A gas smell, water leak, electrics, a broken lock, a blocked exit, or fire equipment out of date is
   unsafe. Say so in the first line of the reply, tell the Operations Manager now, and do not wait for the weekly page.
3. Route it:
   - a laptop, account, Wi-Fi password or software problem goes to `it-support` (`hub task create --owner it-support`);
   - a fault in the building goes to the fixer named in `knowledge/fixers.md`; building-owned items (heating, lifts,
     doors) go to the landlord or building manager;
   - a purchase under the owner's limit is prepared for that owner; over it, it becomes a purchase request to
     `procurement`;
   - a preference (a snack, a plant) goes on the supplies proposal, not into an order.
4. Give the request a fixer and a promised date. If the fixer has not given a date, write "not given" and ask once. "Reported"
   is not a status.
5. Add the row to `knowledge/requests.md`: raised by, date, fixer, promised date, status. Reply to the requester in one
   line: who fixes it and by when.
6. Put the message to the fixer or landlord on the task for review, with what, where, since when, access times and a photo
   if one was attached. Send it only when mail sending is on in Tico and a granted Tool allows it.
7. The day after a promised date passes, chase the fixer. The chase goes on the weekly page under "Requests".

Checks before you finish:
- Anything unsafe is named in the first line, and the Operations Manager was told.
- Every open row has a fixer, a promised date or "not given", and a status.
- Nothing was ordered, booked or sent that the task did not ask for.

Example (invented, for illustration): "Meeting room 2 screen shows no input, reported by Sofia on 2026-09-19. Not unsafe.
Routed to it-support. Fixer: it-support, promised 2026-09-24. On 2026-09-25 the promise had passed, so the chase went to
it-support on the task. Reply to Sofia on 2026-09-19: it-support has it, and a date is promised for 2026-09-24."

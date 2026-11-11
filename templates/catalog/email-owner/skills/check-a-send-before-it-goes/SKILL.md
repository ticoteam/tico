---
name: "check-a-send-before-it-goes"
description: "Run the send checklist on a finished email before it is handed over: audience and consent, the do-not-email list, sender and authentication, unsubscribe and postal address, links, the plain-text version and the complaint guardrail."
---
# Check a send before it goes

When to use: a campaign or sequence email is finished and about to be handed to a person to load or send.

Steps:
1. Confirm the audience in the draft matches its segment in `knowledge/segments.md`, and that the promise that segment signed
   up under is the one this email keeps.
2. Remove anyone on `knowledge/do-not-email.md` from the audience note, and state how many were removed.
3. Mark each checklist item "confirm for the sender": real sender name and address; sending domain authenticated (SPF,
   DKIM, DMARC); a visible unsubscribe link and the postal address in the footer; for marketing mail, a one-click
   unsubscribe header with one HTTPS link.
4. Check that each link opens the page the email names, and that the link text says where it goes.
5. Confirm the plain-text version exists and reads well with images off.
6. Name the guardrails to watch after the send: unsubscribes, and complaints under 0.3 percent (the target is under 0.1).
7. Any item you cannot confirm stays open with a reason. Never tick it yourself.

Checks before you finish:
- Every item is marked confirm, open with a reason, or confirmed by a person.
- The audience note states the do-not-email count.
- Every price, date, result or quote in the email has a source in the notes.

Example (generic): "Newsletter to customers. Audience note: 412 on the list, 9 removed as do-not-email. Checklist: sender
name left for the account owner to confirm; authentication left open for the sender to check in the sending tool; the
one-click header marked open, since the sending tool setting was not visible. The 'read the guide' link opens the guide
page. The email quotes a 14-day window; the source is the policy page, read 2026-10-30."

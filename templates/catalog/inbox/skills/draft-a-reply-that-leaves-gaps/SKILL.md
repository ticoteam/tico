---
name: draft-a-reply-that-leaves-gaps
description: Draft a reply in the human's voice that answers only what the newest message asks, leaves a marked gap for any fact or commitment the bot does not have, and stays a draft until a person sends it.
---
# Draft a reply that leaves gaps

When to use: for each message the sort marked `reply`, or when the human asks for one reply.

Steps:
1. Read the thread with `mail.sh thread <id> --format md`. Identify what the sender asks in the newest message. Quoted earlier history does not count as a question.
2. Read `knowledge/voice.md` for greeting, sign-off, length and formality. Match the human's usual reply, not a template.
3. Answer each question the sender asked, in the order asked. Use only facts from the thread, from what the human gave you in setup, or from a calendar read.
4. For any detail you do not have, leave a gap in square brackets, such as `[time to confirm]`. Never guess a date, price, term or commitment.
5. Check the commitment limits from setup: times, money, contracts and introductions. If the reply would need one of them, leave the gap and flag the message as needs-owner.
6. Write the draft through `mail.sh`, or as a dry run while setup is pending, and put the draft text in the brief. This skill never sends. Sending needs a person to turn mail sending on in Tico, and the task to ask for it.

Checks before you finish:
- Every question the sender asked is answered or marked as a gap.
- No gap is filled with a guess, and every gap is in square brackets.
- The draft reads like the human's usual reply: no filler, no exclamation marks unless they use them.

Example (generic, invented): A customer asks how to export their bookings and when a new calendar feature ships. The draft gives the export steps from the help article the human shared, uses the human's usual sign-off, and leaves "[owner to confirm the release date]" where the customer asked about the feature.

---
name: write-a-task-the-owner-can-start
description: "Writes one task, on the owner the rulebook's routing table names, that its owner can start without asking anybody anything. Use it before creating any task from a human's request."
metadata:
  function: tico
---

# Write a task the owner can start

## When to use

- The routing table in `AGENTS.md` named the owner, and you are about to run `tico task create`.

## Steps

1. Check the record first: `tico task list --owner <owner>` and `tico task list` for the same ask. An open task that already covers it gets the new facts in a note, not a second task. Tell the requester which task it is.
2. Take the owner and every flag from the routing table in `AGENTS.md`; this skill adds none. A decision for a person has the question as its first line.
3. Write the title as the ask in one line.
4. Write the first message in this order: the requester's own words, quoted; why now, if there is a reason or a date; what the owner needs to start (the link, the account, the file, the answer); what done looks like, in one line; and a `Gaps:` line, never empty: what the record does not say (for example "source not named"), or "none".
5. Mark each gap as a gap, for example "The source export is not known yet." Never fill a gap with an assumption. A bot's description is not this ask's source: name a source only when the record names it for this ask.
6. Create it: `tico task create --owner <owner> --title "<ask>" --message "<message>"`, with the flags the routing table gave. A create that returned an id is filed; never file the same ask again to check it.
7. Tell the requester in one sentence what you filed and where.

## Checks before you finish

- No open task already covers the ask.
- The first message quotes the request and names every gap.
- A decision goes to one person as one question, with its options.
- The reply says what will happen, not what has happened.

## Example

A human asks, in their chat: "Can someone get last quarter's paid trials into the sheet?"

The record has no open task for it. It goes to the bot that owns reporting. The title is "Add last quarter's paid trials to the sheet". The first message quotes the request, names the sheet as the destination, and marks "The source system and its export are not known" as a gap. The reply: "I filed it with the reporting bot. Its first step is finding the source, which is not named yet."

The bot and the figures are invented for this example.

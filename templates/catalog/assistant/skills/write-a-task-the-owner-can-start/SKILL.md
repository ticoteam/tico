---
name: write-a-task-the-owner-can-start
description: "Files one task for a bot, BotOps or a person that its owner can start without asking anybody anything. Use it before creating any task from a human's request."
---

# Write a task the owner can start

## When to use

- A human asked for work that a bot, BotOps or a person should do.
- You are about to run `hub task create`.

## Steps

1. Check the record first: `hub task list --owner <owner>` and `hub task list` for the same ask. An open task that already covers it gets the new facts in a note, not a second task. Tell the requester which task it is.
2. Pick the owner from `knowledge/routing.md`. If nothing there fits, ask the requester one question about who should own it. Do not give it to the nearest bot.
3. Choose the kind of owner:
   - work a bot owns: `--owner <bot slug>`;
   - a new bot, a broken bot, or a change to a bot's instructions or schedule: `--owner botops`, with `--request-id turn` when the human asked for it in this chat;
   - a decision, a price, a promise or an exception: `--owner human:<person id>`, with the question as the first line of the body.
4. Write the title as the ask in one line.
5. Write the body in this order: the requester's own words, quoted; why now, if there is a reason or a date; what the owner needs to start (the link, the account, the file, the answer); what done looks like, in one line; and a `Gaps:` line, never empty: what the record does not say (for example "source not named"), or "none".
6. Mark each gap as a gap, for example "The source export is not known yet." Never fill a gap with an assumption. A bot's description is not this ask's source: name a source only when the record names it for this ask.
7. Create it: `hub task create --owner <owner> --title "<ask>" --body "<body>"`. A create that returned an id is filed; never file the same ask again to check it.
8. Tell the requester in one sentence what you filed and where.

## Checks before you finish

- No open task already covers the ask.
- The owner matches `knowledge/routing.md`, or the question went to the requester instead.
- The body quotes the request and names every gap.
- A decision goes to one person as one question, with its options.
- The reply says what will happen, not what has happened.

## Example

A human asks, in their chat: "Can someone get last quarter's paid trials into the sheet?"

The record has no open task for it. It goes to the bot that owns reporting. The title is "Add last quarter's paid trials to the sheet". The body quotes the request, names the sheet as the destination, and marks "The source system and its export are not known" as a gap. The reply: "I filed it with the reporting bot. Its first step is finding the source, which is not named yet."

The bot and the figures are invented for this example.

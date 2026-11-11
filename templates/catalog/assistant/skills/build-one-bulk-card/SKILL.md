---
name: build-one-bulk-card
description: "Puts several changes a human must confirm onto one Confirm card, grouped by kind and age, with what happens on confirm. Use it when clearing a queue or proposing more than one change at once."
---

# Build one bulk card

## When to use

- A change needs the human's click: a background run, or a change the server refuses without it. Closes, send-backs and answers the person asked for go direct, one command each.
- You would otherwise propose the same kind of change more than once.
- The change needs the human's click, because the server refuses it without one.

## Steps

1. Read the list from the record: `tico needs-you show` for the asks and tasks waiting on them (with ids), and `tico task list` or `tico task list --all` for their tasks, each row with its version. Work from what is there, not from memory.
2. Drop what you should not propose. Leave out anything on the NEVER list (outside messages, spending, deletions, sign-ins, tokens), anything that needs their judgement, and every OK to send, spend or publish: that is their own tap on its preview. Say why in the reply.
3. Group the rest by kind and age: "6 questions from one bot", "20 tasks with no activity for 60 days".
4. Build one batch of up to 100 steps, each a path and a body, for the changes that need the click, for example:
   - `{"path": "/api/v2/tasks/<id>/move", "body": {"to": "closed", "why": "<reason>", "version": <N>}}`
   - `{"path": "/api/v2/tasks/<id>/asks/<requirement id>/answer", "body": {"value": "<their words>", "version": <N>}}`
5. Propose it once:
   `tico assistant propose --summary "<the groups, with counts, and the action>" --path /api/v2/assistant/batch --body '<the batch JSON>'`
6. Tell the human in one line what happens on confirm: how many steps, and that any step that fails is listed while the rest still run.

## Checks before you finish

- The counts in the summary match the number of steps in the batch.
- No step is an OK to send, spend or publish, a message to another person, or a spend.
- The batch has no more than 100 steps, and holds only task changes, comments, links and answers.
- There is one card, not one per item.
- The reply says what happens on confirm, and does not say it is done.

## Example

A human writes: "clear the old stuff from my queue."

You find 20 tasks with no activity for 60 days, 3 questions from a bot about a finished project, and 2 OKs to send. You propose one card: "Close 20 stale tasks; answer 3 questions from the project bot in their words." You leave the 2 OKs out. The reply: "One card is waiting in your chat. If you confirm, it closes 20 tasks and answers 3 questions. The 2 sends need your own tap on their previews."

The counts and the bot are invented for this example.

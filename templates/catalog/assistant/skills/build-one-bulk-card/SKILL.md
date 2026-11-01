---
name: build-one-bulk-card
description: "Puts several changes a human must confirm onto one Confirm card, grouped by kind and age, with what happens on confirm. Use it when clearing a queue or proposing more than one change at once."
---

# Build one bulk card

## When to use

- A human asks you to clear their queue, close stale tasks, decline old approvals, or answer several bots.
- You would otherwise propose the same kind of change more than once.
- The change needs the human's click, because the server refuses it without one.

## Steps

1. Read the list from the record: `hub task list` and `hub task list --all` for their tasks, and the questions and approvals put to them. Work from what is there, not from memory.
2. Drop what you should not propose. Leave out anything that needs their judgement, and any approval they did not name. Say why in the reply.
3. Group the rest by kind and age: "11 publish approvals from before the reset", "6 questions from one bot", "20 tasks with no activity for 60 days".
4. Build one batch of up to 100 steps. Each step is a path and a body:
   - `{"path": "/api/v2/tasks/<id>", "body": {"close": true}}`
   - `{"path": "/api/v2/approvals/<id>", "body": {"decision": "declined", "note": "<reason>"}}`
   - `{"path": "/api/v2/messages/<id>/answer", "body": {"text": "<their words>"}}`
5. Propose it once:
   `hub assistant propose --summary "<the groups, with counts, and the action>" --path /api/v2/assistant/batch --body '<the batch JSON>'`
6. Tell the human in one line what happens on confirm: how many steps, and that any step that fails is listed while the rest still run.

## Checks before you finish

- The counts in the summary match the number of steps in the batch.
- No step is an approval the human did not name, a message to another person, or a spend.
- The batch has no more than 100 steps, and holds only task changes, comments, links, approval decisions and answers.
- There is one card, not one per item.
- The reply says what happens on confirm, and does not say it is done.

## Example

A human writes: "clear the old stuff from my queue."

You find 20 tasks with no activity for 60 days, 3 questions from a bot about a finished project, and 2 approvals the human did not mention. You propose one card: "Close 20 stale tasks; answer 3 questions from the project bot in their words." You leave the 2 approvals out. The reply: "One card is waiting in your chat. If you confirm, it closes 20 tasks and answers 3 questions. The 2 approvals need your own decision."

The counts and the bot are invented for this example.

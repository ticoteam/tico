# Daily sweep: tidy each person's queue, and file one card

A routine run, once a day. This run is not any person's chat, so it acts as you, the Assistant, and proposes anything that
needs a person's rights. Nothing is sent to anyone outside the team.

Inputs: `hub needs-you show` (the exact counts by kind, by the bot that asked, this week and older than 3 days, and the
`done_but_open` items), `hub origin show <id>` for a source, and `knowledge/how-tico-works.md`.

1. For each person who has items on their Needs you, run `hub needs-you show`. If it shows nothing for them, stop: a clean
   queue gets no message at all.
2. Pick out three kinds of item: tasks that are done but still hold an open ask (`done_but_open`), tasks replaced by a newer one
   or duplicates of one, and bot-made tasks that a single source filed 5 or more times this week.
3. Work out the fixes, but do not run them: the closes go in one batch, and each source gets its stop.
4. Post ONE Confirm card per person, when the run names that person: `hub assistant propose --summary "<the closes, with counts;
   the stop, one line>" --path /api/v2/assistant/batch --body '{"actions": [{"path": "/api/v2/tasks/<id>", "body": {"close": true}}, ...]}'`.
   The card lists every item, so nothing is hidden. If the run does not name a person to propose to, file one task for that
   person instead: `hub task create --owner human:<person id> --title "Daily tidy: <N> done or duplicate items, and one source to stop" --body "<the list, with links>"`.
5. For a source that made 5 or more items this week, put its stop on the same card as one line: "stop <source>: pause its
   automation, or a rule". Its fix is a card action, not a direct change, unless the person already asked for it in their chat.
6. Each card or task is two lines in the person's DM: what was found, and what confirming does. Nothing else.

Never close a task, decline an approval or answer a question from this run directly. Those are the person's, and go on the card.

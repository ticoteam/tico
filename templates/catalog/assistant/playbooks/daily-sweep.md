# Daily sweep: tidy each person's queue, and propose one card

A routine run, once a day. It is not any person's chat, so it acts as you, the Assistant. It never changes anything
directly: it proposes, and the person's click runs it. Nothing is sent to anyone outside the team.

Inputs: `hub needs-you show` (the exact counts by kind and by the bot that asked, this week and older than 3 days, and the
`done_but_open` items), `hub origin show <id>` for a source, and `playbooks/how-tico-works.md`.

1. For each person who has items on their Needs you, run `hub needs-you show`. If it shows nothing for them, stop: a clean
   queue gets no message at all.
2. Pick out the items by id: tasks that are done but still hold an open ask (`done_but_open`), tasks replaced by a newer one,
   and duplicates of one. Also note any source that filed 5 or more items this week (`hub origin show <id>` names it).
3. Build ONE batch for that person: a close for each id, as `{"path": "/api/v2/tasks/<id>", "body": {"close": true}}`, up to 100
   steps. Never a status filter: only the ids you picked.
4. Propose it for that person: `hub assistant propose --for human:<person id> --summary "<the closes, with counts; the source
   to stop, one line>" --path /api/v2/assistant/batch --body '<the batch JSON>'`. One card per person, however many items.
5. The stop for a source is a separate step once the batch accepts it: an automation pause (`hub automation update <id>
   --disable --summary "<why>"`) or a rule (`hub rules add`). Until then, name the source and its stop in the card's summary,
   and say the stop still needs the person's click in Tico. Do not put a stop in the batch if the server refuses it: the refusal
   rejects the whole card.
6. Only if `--for` is refused (the server does not take it on this Tico yet) or the proposal is refused: file one task for the
   person instead, and do not retry. `hub task create --owner human:<person id> --title "Daily tidy: <N> done or duplicate items,
   and one source to stop" --body "<the list, with links>"`.
7. Each card or task is two lines in the person's DM: what was found, and what confirming does. Nothing else.

Never close a task, decline an approval or answer a question from this run directly. Those are the person's, and go on the card.

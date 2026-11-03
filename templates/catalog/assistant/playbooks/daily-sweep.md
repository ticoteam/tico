# Daily sweep: tidy each person's queue, and propose one card

A routine run, once a day. It is not any person's chat, so it acts as you, the Assistant. It never changes anything
directly: it proposes, and the person's click runs it. Nothing is sent to anyone outside the team.

Inputs: `hub needs-you show` (the exact counts by kind and by the bot that asked, this week and older than 3 days, and the
`done_but_open` items), `hub origin show <id>` for a source, and `playbooks/how-tico-works.md`.

1. For each person who has items on their Needs you, run `hub needs-you show`. If it shows nothing for them, stop: a clean
   queue gets no message at all.
2. Pick out the items by id: every task with status done that still sits on Needs you (each is a close), tasks replaced or superseded by a newer one, duplicates of one (every copy, the newest too), and bot-made tasks with no activity for 14 days or more (close each, with a note). Name a source to stop only when it made 5 or more items this week (`hub origin show <id>` names it).
3. Build ONE batch for that person: a close for each id, as `{"path": "/api/v2/tasks/<id>", "body": {"close": true}}`, up to 100
   steps. Never a status filter: only the ids you picked.
4. Propose it for that person: `hub assistant propose --for human:<person id> --summary "<the closes, with counts; the source
   to stop, one line>" --path /api/v2/assistant/batch --body '<the batch JSON>'`. One card per person, however many items: done, duplicate and superseded tasks all go on it. Identical items (same title, same source) go on it together, as one line, and all are closed in the batch, the newest included.
5. The stop for a source is a separate step once the batch accepts it: an automation pause (`hub automation update <id>
   --disable --summary "<why>"`) or a rule (`hub rules add`). Until then, name the source and its stop in the card's summary,
   and say the stop still needs the person's click in Tico. Do not put a stop in the batch if the server refuses it: the refusal
   rejects the whole card.
6. Only if `--for` is refused (the server does not take it on this Tico yet) or the proposal is refused: file one task for the
   person instead, and do not retry. `hub task create --owner human:<person id> --title "Daily tidy: <N> done or duplicate items,
   and one source to stop" --body "<the list, with links>"`.
7. Your final reply is the person's DM summary: three lines at most, what was found and what confirming does. No operator notes or commit details. Send no message with `hub message send`.

Close nothing directly: no `hub task close`, no bulk close, no decline, no answer. Those are the person's, and go on the card.

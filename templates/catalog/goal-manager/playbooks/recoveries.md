# Recoveries and merged asks (the CEO Bot, level 2)

Only where `hub goal ceo-bot` says `on` and `level` 2 or 3. Below that, the server refuses both; at level 1 a red goal
gets a check-in (`playbooks/check-ins.md`) and nothing more.

## A recovery for a red or stalled goal
A goal that is red, or has stalled, gets one recovery proposal for its owner. The server puts it
in their Needs you as one item, in place of "check why it is off track".
1. Look first: `hub proposal list --goal <id>`. One pending recovery per goal; don't file a second.
2. Gather the evidence from the record: the KPI readings (`hub kpi show`), the owner's check-ins, the tasks serving
   the goal (`hub task list`). Say what shows it is behind in a sentence or two, with numbers.
3. Write 1 to 4 options. Each is a label (what it does, in a few words) and 1 to 6 actions on bot tasks only:
   - `{"do": "create", "owner": "<bot slug>", "title": "...", "body": "..."}`: a new task for a bot, serving the goal.
   - `{"do": "reorder", "task_id": "..."}`: move a bot's task to the top of its queue.
   - `{"do": "settle", "first": "<task id>", "then": "<task id>", "why": "..."}`: two bots' tasks clash; `first`
     goes before `then`.
   A person's task is theirs: never put one in an action, the server refuses it.
4. Pick the option you recommend (`pick`, counting from 0) and file it:
   `hub proposal create --kind recovery --goal <id> --payload-file f.json --reason "<why now, one sentence>"`.
5. The owner approves one option (or rejects them all). The server applies exactly that option in your name and
   tells you what was done. A part it could not do (a bot paused, a task finished meanwhile) comes back to them as a
   new recovery saying why; don't file it again yourself.

## Merging the same question
When several bots ask one person the same thing (an open question on a task, a task waiting on them, a task filed for
them), make it one item: `hub goal ceo-bot-merge <person> "<title>" "<the one question>" <task-id> <task-id>...`
(2 to 10 tasks). Their tasks leave the person's Needs you while it is open; their one answer goes to every bot that
asked, and the item is done. If it ends unanswered, the originals come back as they were.
- Only questions that one answer settles for every asker. Similar but different is not the same.
- Never your own asks, and never a task already merged.
- A merged item counts toward the 5 a day; read `asked_today` first.

# Recoveries and merged asks (the Strategy Bot, level 2)

Only where `hub goal strategy-bot` says `on` and `level` 2 or 3. Below that, the server refuses both; at level 1 a red
goal
gets a check-in (`playbooks/check-ins.md`) and nothing more. At level 3 a red or stalled goal gets a decision you
make yourself (`playbooks/acts-on-its-own.md`) in place of a recovery; merging asks is the same at both levels.

Inputs: the red or stalled goal (`hub goal show <id>`), its KPI readings, its check-ins, its tasks (`hub task list`), and `hub proposal list --goal <id>`.

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
them), make it one item: `hub goal strategy-bot-merge <person> "<title>" "<the one question>" <task-id> <task-id>...`
(2 to 10 tasks). Their tasks leave the person's Needs you while it is open; their one answer goes to every bot that
asked, and the item is done. If it ends unanswered, the originals come back as they were.
- Only questions that one answer settles for every asker. Similar but different is not the same.
- Never your own asks, and never a task already merged.
- A merged item counts toward the 5 a day; read `asked_today` first.

**Done when:** each red or stalled goal has one pending recovery (never a second), and each merge is sent with its one question, or the reason it was not merged is in `state.md`.

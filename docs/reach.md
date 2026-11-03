# Reach (Rules › Internal)

Who each teammate may reach, how often, and the rules they follow. The Rules page's Internal tab is the map: the full
org chart, a panel for whoever is selected, and the lines between them.

## Parties

A party is `*` (Everyone), `group:<id>` (a team), `human:<id>` or `bot:<id>`, the same actor strings the hub uses.

## Settings

Each party may set, each field optional (`null` = take it from above):

| field | values | default |
|---|---|---|
| `mode` | `open` (can reach anyone), `nearby` (its part of the chart) or `closed` (only who's listed) | `nearby`; a person's own: `open` |
| `chats_per_day` | 0–100000 | 999 |
| `tasks_per_day` | 0–100000 | 999 |

A person or bot takes each unset field from its team, then the teams above it (nearest first), then Everyone, then the
default. A team takes it from the teams above it, then Everyone. Everyone, teams and bots default to `nearby`; a person
whose chain sets no mode shows `open`. Only bots are checked: a person's settings are stored and shown, and nothing a
person does is refused. Owners and admins set Everyone, a team or a bot back to `open` on the panel.

**Nearby** is a bot's part of the chart:

- its manager (`bot_config.reports_to`, a bot or a person);
- its direct reports (bots and people whose manager it is);
- its peers (teammates with the same manager);
- the members of the team it is directly in (bots and people; not the teams above or below).

A bot with no manager has no peers: it reaches its reports and its team only. The owner is reached like anyone else:
only a bot that reports to them, or is near them, or has a line to them, reaches them.

## Lines

A line is `sender → receiver`, either side any party. It has:

- `per_day`: `null` for no count, `0` to block the pair, `N` for at most N contacts a day. A contact is a task the sender
  sends or hands the receiver, or a chat message from the sender that reaches the receiver. A team side counts every
  member.
- `rule`: an optional sentence. It is stored as an internal org rule (Who = sender, With = receiver, If broken = warn)
  and checked like any rule; the line keeps its id.

While the sender is **open**, its lines are exceptions: only `per_day` limits. While it is **nearby**, a line adds a
receiver beyond its part of the chart, and `per_day` still limits. While it is **closed**, its lines are who it may
reach: anyone not on a line from the sender (or a team it is in, or Everyone) to the receiver (or a team the receiver is
in) is refused. The nearest line applies: the sender's own first, then its teams nearest first, then Everyone; on each,
the receiver's own first.

## What counts and when it is checked

`backend/reach.py` `decide` is the one decision: pure over the chart, settings, lines and the write's facts.

- **Tasks a day:** tasks the bot creates for someone else or hands to them since local midnight (the sends task caps
  record, `task_cap_log`). Own work is never counted.
- **Chats a day:** messages the bot posts that reach another teammate (a delivery), outside task threads and its own
  Home.
- **A line's count:** tasks, deliveries and notes from the line's sender side to its receiver side since midnight.
- Checks run on the write, before automations, wakes and the LLM rule checks (a precheck runs ahead of the preflight,
  so a refused write spends no judgment). Nothing is written when one is refused.

A bot's write is checked on:

- creating a task for someone else (`POST /api/v2/tasks`, MCP and CLI included), and the person a task question or
  approval asks (`asked`), unless they share the parent task;
- handing a task to someone or setting who it waits on (`POST /api/v2/tasks/{id}` with `owner` or `waiting_on`);
- chat: a message (`POST /api/v2/messages`, bot to bot included: the one it is sent to first, then everyone else it was
  delivered to, its @mentions and a DM's members, in the same write), a note (`POST /api/v2/notes`), and a thread post
  or new thread or DM (`/api/v2/threads...`): the one it is addressed to, those it @mentions who can read the thread, a
  DM's other members and the bot whose Home it is;
- an edit of its own message (`PATCH /api/v2/messages/{id}`): those it @mentions now and did not before. A message
  that is not its own, or one it cannot read, gets the edit's own 403 or 404 first, never a refusal.

`hub_task_dry_run` reports the refusal a task would get.

Never refused:

- a person's write, the owner's included; only a bot is ever checked;
- a bot writing to itself;
- a solicited reply: a reply to the receiver's message (`in_reply_to`, or the message a sub-thread hangs from), an
  answer to a teammate who gave the bot an open task, or to one who wrote to it (addressed or @mentioning it) in the
  last 7 days, in that thread (or the thread above a sub-thread) when it names one. In a DM, anything the receiver
  wrote there, and any person's message there (a person brought its members together), counts. It still adds to the
  bot's chats a day;
- anything on a task the bot and the receiver are both part of: requester, owner (now or at any earlier step), the one
  it waits on, its guest, or a poster in or member of the task's thread. A message in a task's thread is on that task.
  Handing a task back to its requester passes; handing it to someone new is checked;
- system hand-offs: step flow and the allocator, check pools, routines and automations, rule wakes and the keeper. They
  write in the hub, not through a bot's route;
- the hub's own work in a bot's name: a runner's reply to the message that woke it, an approval's decider, comments
  on a task;
- a held write (a message, note or task) a person chose to Send anyway: it passed reach before it was held;
- built-in helpers off the chart, as sender or receiver: BotOps, Task Manager, Goal Manager, Librarian, the Assistant
  (`TICO_ASSISTANT_BOT`, `coo` by default), the Bookkeeper, Counsel (`tico-counsel`) and the support helper. Anyone
  reaches them. The Health Monitor is a built-in bot too, but it is checked like any bot: its tasks to people far from
  it are what nearby reach is for.

The refusal is a 409 the bot reads once and acts on, never a retry:

- `reach_closed`: the receiver is outside its reach. The detail names who it reaches and ends "Send it to <manager>, who
  can pass it on." (with no manager, the teammates it does reach, or that it needs a manager);
- `reach_line`: a line blocks the pair (`per_day` 0) or its count for today is used;
- `reach_cap`: its chats or tasks a day are used; the count starts again at midnight.

Each says nothing was sent and not to retry, and carries `receiver`, `via` (the manager to send it to, or `null`) and
`can_reach` (up to 20 parties) beside `code` and `detail`. A teammate the bot may not See is never named in the detail,
`via` or `can_reach`.

`scripts/reach_dry_run.py --db HUB.db --days N` replays the last N days of bot task and chat writes through the same
`decide`, read-only, and reports what nearby would refuse: counts by sender → receiver, how many land on people, and
totals by kind. Run it on a copy (`python -m backend.devcopy`) before an install takes the default.

## Who changes what

- The owner and admins: every party, Everyone included.
- A team lead: their teams and the people and bots in them.
- A bot's managers (`Auth.bot_manager`): that bot.
- A line belongs to its sender. No one changes their own reach.
- Every teammate reads every setting and line, except lines and settings naming a teammate they may not See, which are
  left out. Every change writes an audit row and an event.

## Task caps

Task caps (`/api/v2/task-caps`, `backend/task_caps.py`) are still their own table, checked on the same task writes just
before reach: a task passes both. They are to become lines: each cap row moves to a line with its `per_day`, and the
routes and the `task_cap` approval read and write lines.

## API

- `GET /api/v2/reach` → `{defaults: {mode, chats_per_day, tasks_per_day, person_mode}, settings: [{party, mode, chats_per_day, tasks_per_day}], effective: {party: {mode,
  chats_per_day, tasks_per_day, from: {field: party}}}, lines: [{id, sender, receiver, per_day, rule: {id, text} | null,
  may_edit}], may_edit: [party]}`
- `PUT /api/v2/reach/settings/{party}` with any of `mode`, `chats_per_day`, `tasks_per_day` (`null` clears).
- `POST /api/v2/reach/lines` `{sender, receiver, per_day, rule}`; `POST /api/v2/reach/lines/{id}` to change one;
  `DELETE /api/v2/reach/lines/{id}`. One line per sender and receiver.

Details of the API as built:

- `GET` also returns `owner` (the owner's party, never in `may_edit`), each setting's `updated_at` and `updated_by`, and
  each line's `created_at` and `updated_at`. `effective` has every party: Everyone, every team, person and bot that is
  not archived. A `from` field is `null` when the value is the default. Parties are stored as ids: a bot by its id
  (a slug is accepted on the way in), a person as `human:<id>` (`person:<id>` is accepted).
- `PUT .../settings/{party}` returns `{setting, effective}`, the stored row (all `null` once nothing is set, and the row
  is gone) and the party's effective values. A field left out of the body is left as it is.
- `POST /api/v2/reach/lines` returns `{line}` (409 for a pair that already has a line, 422 for sender = receiver).
  Either side may be `*`; a rule with receiver `*` has an empty With.
- In POST bodies `rule` is a plain string or `null`; `null` or `""` retires the line's org rule. GET returns
  `rule: {id, text} | null`.
- `POST /api/v2/reach/lines/{id}` takes any of `sender`, `receiver`, `per_day` and `rule` (the full body is fine; only
  what differs changes) and returns `{line}`. A changed sender or receiver (a flipped direction) moves the line and its
  rule's Who and With; the caller must be able to change the new sender too, and a pair that already has a line is a
  409.
- `DELETE` returns `{id, removed: true}` and retires the line's rule.
- Refusals: 403 for one's own party or a party the caller may not change, 422 for the owner's party, 404 for an
  unknown party or line.
- Audit objects are `reach_setting:<party>` and `reach_line:<id>`; events are `reach.setting_changed`,
  `reach.line_added`, `reach.line_changed`, `reach.line_removed`.

## The panel

- Header: the party's name.
- Can reach anyone / Nearby / Only who's listed. Up to [N] chats a day. Up to [N] tasks a day.
- **Rules**, with `+ Add rule`. Company rules and the team's rules first, collapsed and always on; then the party's own
  internal rules (Who names it). Tapping a rule opens it.
- **Exceptions**, with `+ Add exception`. Rows read `To X` or `From X`, the count (`0 a day`, `1 a day`) and the rule.
  Adding searches teams, people and bots; the editor sets the direction, `Up to N a day` and the rule.
- Each line is drawn on the chart with a chip: `N a day · rule…`.

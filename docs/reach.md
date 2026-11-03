# Reach (Rules › Internal)

Who each teammate may reach, how often, and the rules they follow. The Rules page's Internal tab is the map: the full
org chart, a panel for whoever is selected, and the lines between them.

## Parties

A party is `*` (Everyone), `group:<id>` (a team), `human:<id>` or `bot:<id>`, the same actor strings the hub uses.

## Settings

Each party may set, each field optional (`null` = take it from above):

| field | values | default |
|---|---|---|
| `mode` | `open` (can reach anyone) or `closed` (only who's listed) | `open` |
| `chats_per_day` | 0–100000 | 999 |
| `tasks_per_day` | 0–100000 | 999 |

A person or bot takes each unset field from its team, then the teams above it (nearest first), then Everyone, then the
default. A team takes it from the teams above it, then Everyone. The company owner is never limited.

## Lines

A line is `sender → receiver`, either side any party. It has:

- `per_day`: `null` for no count, `0` to block the pair, `N` for at most N contacts a day. A contact is a task the sender
  sends or hands the receiver, or a chat message from the sender that reaches the receiver. A team side counts every
  member.
- `rule`: an optional sentence. It is stored as an internal org rule (Who = sender, With = receiver, If broken = warn)
  and checked like any rule; the line keeps its id.

While the sender is **open**, its lines are exceptions: only `per_day` limits. While it is **closed**, its lines are who
it may reach: anyone not on a line from the sender (or a team it is in, or Everyone) to the receiver (or a team the
receiver is in) is refused.

## What counts and when it is checked

Not enforced yet.

- **Tasks a day:** tasks the party creates for someone else or hands to them since local midnight (the sends task caps
  already record). Own work is never counted.
- **Chats a day:** messages the party posts that reach another internal teammate (a delivery), outside task threads and
  its own Home.
- A reply to a teammate who wrote to the party first (a solicited reply) skips the closed check and line counts. It still
  counts toward chats a day.
- Checks run on the write, before automations, wakes and the LLM rule checks. Over a limit, the write is refused with a
  409 (`reach_closed`, `reach_line` or `reach_cap`) that names the limit and who can change it. Nothing is written.

## Who changes what

- The owner and admins: every party, Everyone included.
- A team lead: their teams and the people and bots in them.
- A bot's managers (`Auth.bot_manager`): that bot.
- A line belongs to its sender. No one changes their own reach.
- Every teammate reads every setting and line, except lines and settings naming a teammate they may not See, which are
  left out. Every change writes an audit row and an event.

## Task caps

Not enforced yet.

Task caps (`/api/v2/task-caps`) become lines: each cap row moves to a line with its `per_day`, and the routes and the
`task_cap` approval read and write lines.

## API

- `GET /api/v2/reach` → `{defaults, settings: [{party, mode, chats_per_day, tasks_per_day}], effective: {party: {mode,
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
- Can reach anyone / Only who's listed. Up to [N] chats a day. Up to [N] tasks a day.
- **Rules**, with `+ Add rule`. Company rules and the team's rules first, collapsed and always on; then the party's own
  internal rules (Who names it). Tapping a rule opens it. For one party (on a bot's page, in the Reach panel or on the graph),
  **+ Add rule** shows only the rule text; **Kind**, **Followed by**, **With** and **If broken** sit behind **More ›**. The
  Rules page's **+ Add rule** is unchanged.
- **Exceptions**, with `+ Add exception`. Rows read `To X` or `From X`, the count (`0 a day`, `1 a day`) and the rule.
  Adding searches teams, people and bots; the editor sets the direction, `Up to N a day` and the rule.
- Each line is drawn on the chart with a chip: `N a day · rule…`.

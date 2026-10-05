# Querying Tico with SQL

Read-only SQL over the Tico database (`/data/hub.sqlite` in the Docker install; other installs use their configured database path), for humans, bots and
scripts alike. One `SELECT` per call; what it may see is decided in the database layer, not by
the caller and not by prompt text. 

## Three ways in

- **The SQL page** (`#/sql`, under your email next to Credentials; the owner and admins, and the owner may limit it to
  the owner in Settings > Humans). Type, press
  ⌘↩, read the rows. The last query is remembered in the browser.
- **`hub sql "<select>"`** from a bot run or from a computer. Prints an aligned table and a
  trailing `N rows (M ms)` line; `--json` for the API response, `--csv` for CSV, `--max-rows N`
  to ask for fewer rows, `--param key=value` to bind `:key` (numbers bind as numbers). Inside a
  run it is the bot. Outside a run, `hub sql` alone falls back to the computer's runner credential
  (`~/.config/tico/runner.json`) and queries as the human who registered the computer, so a
  script or an agent on Ana's Mac needs no browser session. From any other computer, a
  personal API token (Settings, Computers, API tokens; any human, unless the owner limits it to admins) makes
  `hub` you: `export HUB_API_URL=https://hub.acme.example HUB_TOKEN=tico_pt_...` and no
  `HUB_BOT` ([How Tico works](how-it-works.md), "Calling the API from a script").
- **`POST /api/v2/sql`** with `{"sql": "...", "params": [...] | {...}, "max_rows": N}` →
  `{"columns": [...], "rows": [[...]], "row_count": N, "truncated": bool, "ms": N}`. Errors are
  the usual `{"error": {"code", "detail"}}` with SQLite's own message (`no such column: x`,
  `access to credentials.id is prohibited`). `BLOB` values come back base64-encoded and the
  response says so in `note`.

Limits: 500 rows and 5 seconds per query (the owner: 5,000 rows and 20 seconds). A query that
hits the row cap returns `truncated: true`; one that hits the clock fails with `timeout`. Every
call is written to `events` as `sql.query` with the statement, the row count, the time and any
error, so the log answers "who looked at what".

## What you may read

The rules are the same ones the JSON API applies (`backend/auth.py`), built into the connection
that runs your query (`backend/sql.py`): each guarded table is replaced, for that one request,
by a view that already carries your visibility, and SQLite's authorizer refuses everything
else. `SELECT * FROM messages` therefore means "the messages you may read". In plain words:

- **The owner** (Ana) sees everything that is not a credential, with one exception: another
  human's private Tico room (`conversations.scope = 'personal'`) and its messages stay
  private to that human.
- **A human on the roster** sees the team: every bot they may read ([Who can see, read and write to a
  bot](permissions.md); a bot they may only see or write to is in the API but not here), every task except
  those a bot they cannot read owns or requested, unless it is theirs, their own private Tico room, the shared rooms they are a member of, and
  the direct conversations they take part in. Their own typed and voice notes, files and
  tool snapshots; nobody else's. Mail copies (`mail_messages`, `mail_mailboxes`,
  `mail_fts`) are owner-only in SQL; humans browse their visible mail on the Message bots page.
  Meetings are the exception: every team meeting is
  theirs to read, and a private one only if the invite names them
  ([Meetings](meetings.md), Who can do what).
- **A bot** sees itself and every other bot it may read; its own tasks (owner or requester) and
  tasks delegated to it while the delegation lasts; the conversation of the run it is running
  in and the conversations of its tasks, with their messages, jobs, runs and output; its own
  audit events and refusals; humans as `humans(id, name)` and no email addresses. Never another
  human's private room, never the tasks, runs or messages of a bot it may not read, never the roster file.
- **A computer's runner credential** queries as the human who registered the computer, on this
  endpoint only.
- **A personal API token** is the human it belongs to, here and on every other endpoint,
  with that human's visibility. The one thing it cannot do is make or revoke tokens.

Everyone may read `sqlite_master` (the schema) and use `json_each`/`json_tree` on JSON
columns. Nobody may read the tables below, nor `attempts.token_hash`, `bots.token_hash`,
or `service_jobs.token_hash`; the columns simply do not exist in
the view (`no such column`). Writes, `PRAGMA`, `ATTACH`, transactions, `EXPLAIN` (plain) and
multiple statements are refused; `EXPLAIN QUERY PLAN` is allowed.

Reserved, never readable through SQL: `credentials`, `credential_keys`, `credential_grants`,
`idempotency` (stored request and response bodies), `runners` (credential hashes),
`human_tokens` (personal API token hashes), `service_keys` and `service_key_tasks` (service keys,
[service-keys.md](service-keys.md)), `enrollments`, `session_epochs`, `settings_changes`, `backup_verified_blobs`, every
`_litestream_*` table and every `sqlite_*` internal other than `sqlite_master`.

## The useful tables

Timestamps are ISO-8601 UTC text (`2026-09-15T21:40:12.931675Z`); compare them with
`strftime('%Y-%m-%dT%H:%M:%S', 'now', '-7 days')`. Actors are `human:<id>` or `bot:<slug>`.

| Table | Columns worth knowing | Notes |
|---|---|---|
| `bots` | `slug, display_name, runtime, model, effort, state, created, last_turn_at` | `state`: active, paused, planned, quarantined |
| `bot_config` | `bot, team, operator, description, reports_to, repo, thread_mode, revision` | the team chart |
| `bot_status` | `bot, state, focus, task_id, since, last_turn_at, last_result, next_due, open_tasks, needs_human` | live status; history in `bot_status_history(bot, state, focus, since, until, reason, by)` |
| `humans` | `id, name, email, slack_id, teams_json` | bots do not get `email` |
| `conversations` | `id, kind, scope, subject, task_id, participants_json, owner_actor, room_key, created, last_message_at, closed_at` | `scope`: direct, personal, shared, task |
| `messages` | `id, conversation_id, from_actor, to_actor, kind, body, refs_json, in_reply_to, created, delivered_at, read_at, wait_s` | `kind`: say, ask, answer, notice, steer |
| `tasks` | `id, title, body, requester, owner, status, due, goal_id, conversation_id, created, updated, done_at, closed_at, closed_by, note, version, acceptance_json, type_id, step_id, number, step_rank, waiting_on` | `status`: open, doing, waiting, done, closed, declined; `waiting_on` the person a waiting task waits on; `number` is the team-wide ticket number a numbered type gives (NULL until one does), `step_rank` the task's place within its step, lower first ([Tasks](tasks.md)) |
| `task_relations` | `from_task, to_task, kind, created_by, created` | every task-to-task relationship, read only when both tasks are; `kind`: `parent` (from is a subtask of to), `blocks` (from blocks to), `related` (stored once, lower id first), `duplicate_of`, `follow_up` (from was split off or followed up from to). It replaces the old `tasks.parent_id` and `tasks.blocked_by`: a task's parent is `SELECT to_task FROM task_relations WHERE from_task=:task AND kind='parent'`, its subtasks `SELECT from_task ... WHERE to_task=:task AND kind='parent'`, its blockers `SELECT from_task ... WHERE to_task=:task AND kind='blocks'` |
| `task_types` | `id, name, numbered, created, updated` | types and, in `task_steps(id, type_id, name, position, status)`, their steps; `numbered` is 1 when the type numbers its tasks |
| `task_events` | `task_id, ts, actor, field, old, new, note` | every change to a task |
| `goals` | `id, title, owner, parent_id, body, status, status_note, status_by, status_at, status_source, suggest_status, suggest_note, suggest_at, rank, last_read_at, last_read_by, created, created_by, updated` | what every human and bot is for; `status`: red, yellow, green, gray (no data), done, dropped, or NULL while proposed or not yet scored; `status_source`: `auto` (the Goal Manager's arithmetic) or `person` (set by hand, sticks until handed back); `suggest_*` is what the arithmetic says over a human's colour; `parent_id` is the goal it supports (optional); `owner` is `bot:x`, `human:x` or `company`; `tasks.goal_id` names the goal a task serves |
| `goal_events` | `goal_id, ts, actor, field, old, new, note, status_by, status_source` | every change to a goal; a `status` row says who set it and how |
| `kpis` | `id, slug, name, definition, unit, direction, cadence, owner, source_note, definition_version, created, created_by, updated` (`goal_id` and `target` are kept for the rows that had them) | a measure that stands on its own; `direction`: up, down, range; `cadence`: daily, weekly, monthly |
| `goal_kpis` | `goal_id, kpi_id, kind, baseline, baseline_at, target, deadline, min, max, created, created_by, updated, updated_by` | the link between a goal and a KPI, holding the target: `kind` is `improve` (baseline, target, deadline), `maintain` (min, max) or `none`; `kpi_id` may be `auto:<bot>:<metric>` |
| `kpi_readings` | `kpi_id, ts, value, actor, source, note, created, period_start, period_end, collected_at, evidence, quality, definition_version, supersedes` | appended, never edited; `period_start` and `period_end` are the business time the value describes (`ts` equals `period_end`), `collected_at` when it was collected; `quality`: measured, estimate, partial; a correction is a new row whose `supersedes` names the old one |
| `kpi_definitions` | `kpi_id, version, ts, actor, name, definition, unit, direction, cadence, source_note` | every version of a KPI's definition |
| `goal_checkins` | `goal_id, kpi_id, ts, author, source_actor, body, signal` | the owner's own words on how a goal is going; `signal`: on_track, at_risk, off_track |
| `goal_proposals` | `id, kind, goal_id, kpi_id, payload_json, reason, proposed_by, proposed_at, status, decided_by, decided_at` | a change waiting for its owner to confirm |
| `task_delegations` | `task_id, delegate, requested_by, message_id, expires` | |
| `approvals` | `id, kind, task_id, message_id, payload_json, requested_by, decided_by, decision, decided_at, consumed_at, created` | `kind`: send, spend, publish, merge |
| `jobs` | `id, message_id, bot, state, created, attempt_id` | one per message a bot has to act on; `state`: queued, leased, running, input, completed, failed, uncertain, cancelled |
| `attempts` | `id, job_id, bot, runner_id, state, lease_until, created, started, finished, result_json, final_text` | a run (`state`: leased, running, input, completed, failed, interrupted, expired); its streamed output is `attempt_events(attempt_id, seq, kind, payload_json, created)` |
| `turns` | `id, bot, started, finished, trigger, message_id, task_id, exit, tokens_in, tokens_out, cost, summary, input_tokens, cached_tokens, output_tokens, model, provider, est_cost_usd, billing` | one per run (`id` = the run's attempt id); `exit` is null while running |
| `schedules` | `id, bot, routine_key, title, cron, event_name, playbook, timezone and enabled (schedule_config), last_fired, next_due, deleted_at` | routines (`docs/routines.md`); `schedule_occurrences(schedule_id, occurrence, task_id, outcome)` says what each firing did |
| `events` | `ts, actor, action, target, detail_json` | the audit log; `action` such as `task.create`, `sql.query` |
| `slack_posts` | `message_id, bot, state, attempts, next_attempt, slack_ts, created, updated` | Owner-only outbound queue status for messages the Owner may read; message text, destination and raw errors are hidden. Join `messages` and filter `json_extract(messages.refs_json, '$.task')` for one task. Other callers receive no rows. |
| `refusals` | `ts, actor, rule, detail_json, severity` | the rule a bot broke |
| `meetings` | `id, title, owner, recorded_by, transcript_readable, notes, created, updated` | your notes; every team meeting |
| `meeting_items` | `id, meeting_id, section, text, detail_json, quote, at_ms, status, created_by, updated_by, pushed_at, result_ref, created, updated` | a meeting's action items; `section`: doc, task, feature (older rows may say decision or question); `status`: proposed, pushed, dismissed; `result_ref` is the Tico task a push made. Visible exactly where its meeting is ([Meetings](meetings.md), What a meeting turns into) |
| `meeting_comments` | `id, meeting_id, author, text, at_ms, created` | the thread beside a meeting: anyone who can open it can add to it ([Meetings](meetings.md)). Visible exactly where its meeting is |
| `import_refs` | `source, external_id, meeting_id, runner_id, created` | which outside record a meeting was imported from — one row per Close call imported before the transcript-only worker ([Meetings](meetings.md), Close). Visible exactly where its meeting is |
| `docs` | `id, path, title, body, locked, version, created_by, created, updated_by, updated` | internal Docs, readable by teammates |
| `doc_versions` | `doc_id, version, title, path, body, actor, created, note` | versions of visible internal Docs |
| `linked_docs` | `id, title, url, kind, description, added_by, created, updated` | linked Docs |
| `bot_files` | `id, bot, scope, identity, title, kind, locator, current_version, task_id, conversation_id` | Files, with the same task, conversation or bot visibility as their APIs |
| `bot_file_versions`, `bot_file_activity` | `file_id, version` or `file_id, action, actor, created` | versions and activity of visible Files |
| `documents`, `document_versions` | legacy mirror fields | compatibility tables for the older docs mirror |
| `learnings` | `id, integration, actor, text, created, deleted_at` | Tools: what bots and humans learned about a tool (`integration` is the stored column; `integrations/`, `hub tool learn`) |

`sqlite_master` lists the rest (`SELECT name, sql FROM sqlite_master WHERE type='table'`).

## Examples

Queued work, oldest first:

```sql
SELECT bot, count(*) AS queued, min(created) AS oldest
FROM jobs WHERE state='queued' GROUP BY bot ORDER BY queued DESC
```

My open tasks (a bot's own; a human swaps in `human:<id>`):

```sql
SELECT id, status, title, requester, due, updated
FROM tasks WHERE owner=:me AND status IN ('open','doing','waiting') ORDER BY due, updated
```

What is waiting on a human, and for how long:

```sql
SELECT t.owner, t.title, t.updated, substr(m.body, 1, 100) AS question
FROM tasks t JOIN messages m ON m.conversation_id=t.conversation_id AND m.kind='ask'
WHERE t.status='waiting' ORDER BY t.updated
```

The last run of every bot, with the week's count and failures:

```sql
SELECT bot, max(started) AS last_turn, count(*) AS turns,
       sum(exit IN ('failed', 'expired')) AS failed
FROM turns WHERE started > strftime('%Y-%m-%dT%H:%M:%S', 'now', '-7 days')
GROUP BY bot ORDER BY last_turn DESC
```

Successful runs finish with `completed`. `failed` and `expired` are terminal errors;
a bot reporting blocked work can still complete its run successfully. Health counts failed and
expired attempts in the last 24 hours. Runs includes those attempts even when no turn was recorded,
with the attempt ID and reason. Query `attempts` when counting all execution attempts.

Who asked what today:

```sql
SELECT created, from_actor, to_actor, substr(body, 1, 90) AS ask
FROM messages WHERE kind='ask' AND created >= strftime('%Y-%m-%d', 'now') ORDER BY created DESC
```

Routine outcomes, most recent first:

```sql
SELECT s.bot, s.title, o.occurrence, o.outcome, t.status
FROM schedule_occurrences o JOIN schedules s ON s.id=o.schedule_id
LEFT JOIN tasks t ON t.id=o.task_id ORDER BY o.occurrence DESC LIMIT 50
```

A task's history in order:

```sql
SELECT ts, actor, field, old, new, note FROM task_events WHERE task_id=:task ORDER BY ts
```

What a run said (its streamed output, in order):

```sql
SELECT seq, kind, json_extract(payload_json, '$.text') AS text
FROM attempt_events WHERE attempt_id=:attempt ORDER BY seq
```

Bots that have not finished a run in two days:

```sql
SELECT b.slug, b.state, s.state AS live, s.focus, s.last_turn_at
FROM bots b LEFT JOIN bot_status s ON s.bot=b.slug
WHERE b.state='active' AND coalesce(s.last_turn_at, '') < strftime('%Y-%m-%dT%H:%M:%S', 'now', '-2 days')
ORDER BY s.last_turn_at
```

Refusals by rule this week:

```sql
SELECT actor, rule, count(*) AS n FROM refusals
WHERE ts > strftime('%Y-%m-%dT%H:%M:%S', 'now', '-7 days') GROUP BY actor, rule ORDER BY n DESC
```

Who queried what (the audit of this very feature):

```sql
SELECT ts, actor, json_extract(detail_json, '$.rows') AS rows, json_extract(detail_json, '$.sql') AS sql
FROM events WHERE action='sql.query' ORDER BY ts DESC LIMIT 20
```

From a bot: `hub sql "SELECT ..." --param me=bot:seo`. From Ana's Mac: `scripts/hub sql "SELECT ..."`.

For a task's Slack notification status (Owner only):

```sql
SELECT p.message_id, p.state, p.created
FROM slack_posts p JOIN messages m ON m.id = p.message_id
WHERE json_extract(m.refs_json, '$.task') = :task_id;
```

No rows means no visible queued post for that task. `slack_events` and the other gateway
internals remain unavailable through SQL; use the Slack history tools to read messages.

## Tags on tasks

`tags` is readable by signed-in teammates. `task_tags` contains only associations with tasks
you may read, including when joining the tables:

```sql
SELECT tasks.id, tasks.title, tags.key, tags.label, tags.metadata_json
FROM tasks
JOIN task_tags ON task_tags.task_id = tasks.id
JOIN tags ON tags.id = task_tags.tag_id
WHERE tags.key = 'release-2026-10-02';
```

Use these tables instead of the retained `tasks.labels_json` column.

## Task pipelines

`task_types(id, name, created, updated)` and
`task_steps(id, type_id, name, position, status)` describe the team's pipelines. Every task carries
`type_id` and `step_id`; `status` stays the contract. Both definition tables are readable by every
SQL caller. Joining to `tasks` still returns only tasks that caller may read.

```sql
SELECT t.id, t.title, t.status, ty.name AS type, s.name AS step
FROM tasks t
JOIN task_types ty ON ty.id = t.type_id
LEFT JOIN task_steps s ON s.id = t.step_id
ORDER BY t.updated DESC
```

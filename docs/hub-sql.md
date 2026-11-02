# Querying Tico with SQL

Read-only SQL over the Tico database (`/data/hub.sqlite` in the Docker install; other installs use their configured database path), for humans, bots and
scripts alike. One `SELECT` per call; what it may see is decided in the database layer, not by
the caller and not by prompt text. 

## Three ways in

- **The SQL page** (`#/sql`, under your email next to Credentials; the owner and admins, and the owner may limit it to
  the owner in Settings > People). Type, press
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
columns. Nobody may read the tables below, nor `attempts.token_hash`, `bots.token_hash`, `bots.cwd`,
`bots.thread_id`, `task_requesters.token_hash` or `service_jobs.token_hash`; the columns simply do not exist in
the view (`no such column`). `bot_control.claim_task`, the one task a bot's claims are limited to, reads
`''` when you may not read that task: you learn a limit is on, not which task. Writes, `PRAGMA`, `ATTACH`, transactions, `EXPLAIN` (plain) and
multiple statements are refused; `EXPLAIN QUERY PLAN` is allowed.

Reserved, never readable through SQL: `credentials`, `credential_keys`, `credential_grants`,
`idempotency` (stored request and response bodies), `runners` (credential hashes),
`human_tokens` (personal API token hashes), `service_keys` and `service_key_tasks` (service keys,
[service-keys.md](service-keys.md)), `enrollments`, `session_epochs`, `bot_session_epochs`, `settings_changes`, `backup_verified_blobs`, every
`_litestream_*` table and every `sqlite_*` internal other than `sqlite_master`.

## Stable and unstable tables

Twenty-three tables are stable. A change to one of their names or columns is announced in the release notes, with
what to do about it. Every other table on this page is unstable: it can be renamed, merged or dropped in any release,
so a query against one may need a change after an upgrade.

The stable tables are `tasks`, `messages`, `conversations`, `bots`, `humans`, `principals`, `jobs`, `attempts`,
`automations`, `automation_runs`, `goals`, `goal_kpis`, `kpis`, `kpi_readings`, `bot_files`, `pages`, `meetings`,
`approvals`, `cards`, `docs`, `task_types`, `task_steps` and `runners`. `runners` is in the set, but SQL never reads it:
its rows hold credential hashes ([Reserved](#what-you-may-read), above).

### Naming

- Tables are plural nouns (`tasks`, `goal_kpis`). The key is `id`. A column that points at another row is named for
  that row: `task_id`, `kpi_id`.
- A moment is `created_at`, `updated_at` or `<verb>_at` (`done_at`, `closed_at`, `last_seen_at`). A calendar date is
  `<noun>_on` (`due_on`, `renewal_on`). Times are ISO-8601 UTC text, as below.
- `status` is a lifecycle a person sees and filters by (`tasks.status`, `goals.status`). `state` is a step the machinery
  is in, set by code (`jobs.state`, `attempts.state`). A machine step on a table that already has a `status` is named
  `<process>_state`. `bots.state` is the bot's lifecycle and keeps its name. Other value lists take their own name
  (`approvals.decision`, `goals.status_source`). Each list has one spelling per meaning (`canceled`, not `cancelled`).
  [Status and state columns](status-and-state.md) gives every list and how the database enforces it.
- A yes-or-no column reads as a question (`enabled`, `locked`), with no `is_` prefix. Soft deletion is `deleted_at`.
- JSON is stored as text in a column named for what it holds (`refs`, `labels`, `payload`). There is no `_json`
  suffix: read it with `json_extract(refs, '$.task')` or `json_each(labels)`.

A few older time columns predate these rules and keep their names: `events.ts`, `kpi_readings.ts`, `tasks.due` and
`automations.next_due`. `automation_runs.state` holds JSON (the run's saved working data), not a step.

### Actor columns

Each column in the stable set that records who did something, under its current name. An actor string is `human:<id>`,
`bot:<id>` or a system name such as `keeper` or `system:company`; join it to `principals.id`. A bot id or a human id
is the bare id, without the prefix.

| Table | Columns | Holds |
|---|---|---|
| `tasks` | `requester, owner, closed_by, checkpoint_by, snoozed_by` | actor string |
| `messages` | `from_actor, to_actor, answered_by` | actor string |
| `conversations` | `owner_actor` | actor string |
| `goals` | `owner, status_by, last_read_by, why_by, created_by` | actor string (`owner` can also be `company`) |
| `goal_kpis` | `created_by, updated_by` | actor string |
| `kpis` | `owner, created_by, archived_by` | actor string |
| `kpi_readings` | `actor` | actor string |
| `approvals` | `requested_by, decided_by, decision_owner` | actor string |
| `automations` | `owner` | `person:<id>`, `bot:<id>` or `group:<name>` |
| `automations` | `created_by` | actor string |
| `cards` | `made_by, updated_by` | actor string |
| `docs` | `created_by, updated_by` | actor string |
| `meetings` | `owner` | actor string |
| `meetings` | `recorded_by, uploaded_by` | an email address or a runner id |
| `jobs`, `attempts`, `bot_files` | `bot` | bot id |
| `task_types` | `manager` | bot id |
| `runners` | `operator` | human id |

## The useful tables

Timestamps are ISO-8601 UTC text (`2026-09-15T21:40:12.931675Z`); compare them with
`strftime('%Y-%m-%dT%H:%M:%S', 'now', '-7 days')`. Actors are `human:<id>`, `bot:<id>` or a system name
([Actor columns](#actor-columns)).

| Table | Columns worth knowing | Notes |
|---|---|---|
| `bots` | `id, slug, display_name, runtime, model, effort, state, created_at, last_turn_at` | `id` is the bot's permanent id, which actor strings and foreign keys use; `slug` is its name and can change. `state`: active, paused, planned, quarantined, archived |
| `bot_config` | `bot, team, operator, description, reports_to, repo, thread_mode, revision` | the team chart |
| `bot_status` | `bot, state, focus, task_id, since, last_turn_at, last_result, next_due, open_tasks, needs_human` | live status; history in `bot_status_history(bot, state, focus, since, until, reason, by)` |
| `humans` | `id, name, email, slack_id, teams` | bots do not get `email` |
| `principals` | `id, kind, deleted_at` | one row per human, bot and known system actor: `id` is the actor string, `kind` is human, bot, agent, service or system. `deleted_at` is set when the person or bot is removed; the row stays so history still names it |
| `seats` | `id, title, description, reports_to, team, created_at, created_by, updated_at, updated_by, closed_at, closed_by` | Recruiting seats, readable by everyone like the chart; open ones have `closed_at` NULL; `reports_to` is a bot slug, `human:<id>` or `seat:<id>` |
| `conversations` | `id, kind, scope, subject, task_id, participants, owner_actor, room_key, created_at, last_message_at, closed_at` | `scope`: direct, personal, shared, task |
| `messages` | `id, conversation_id, from_actor, to_actor, kind, body, refs, in_reply_to, created_at, delivered_at, read_at, wait_s` | `kind`: say, ask, answer, notice, steer |
| `tasks` | `id, title, body, requester, owner, status, due, goal_id, conversation_id, created_at, updated_at, done_at, closed_at, closed_by, note, version, acceptance, type_id, step_id, number, step_rank, waiting_on` | `status`: open, doing, waiting, review, ready, done, closed, declined; `waiting_on` the person a waiting task waits on; `number` is the team-wide ticket number a numbered type gives (NULL until one does), `step_rank` the task's place within its step, lower first ([Tasks](tasks.md)) |
| `task_relations` | `from_task, to_task, kind, created_by, created_at` | every task-to-task relationship, read only when both tasks are; `kind`: `parent` (from is a subtask of to), `blocks` (from blocks to), `related` (stored once, lower id first), `duplicate_of`, `follow_up` (from was split off or followed up from to). It replaces the old `tasks.parent_id` and `tasks.blocked_by`: a task's parent is `SELECT to_task FROM task_relations WHERE from_task=:task AND kind='parent'`, its subtasks `SELECT from_task ... WHERE to_task=:task AND kind='parent'`, its blockers `SELECT from_task ... WHERE to_task=:task AND kind='blocks'` |
| `task_types` | `id, name, numbered, created_at, updated_at` | types and, in `task_steps(id, type_id, name, position, status)`, their steps; `numbered` is 1 when the type numbers its tasks |
| `task_events` | `task_id, ts, actor, field, old, new, note` | every change to a task |
| `task_roles` | `task_id, role, actor, added_by, created_at` | who is on a task, by role (a short name the team chooses: `developer`, `reviewer`, `qa` ...); one actor may hold several roles on a task |
| `goals` | `id, title, owner, parent_id, body, status, status_note, status_by, status_at, status_source, suggest_status, suggest_note, suggest_at, rank, last_read_at, last_read_by, created_at, created_by, updated_at` | what every human and bot is for; `status`: red, yellow, green, gray (no data), done, dropped, or NULL while not yet scored; `status_source`: `auto` (the Strategy Bot's arithmetic) or `person` (set by hand, sticks until handed back); `suggest_*` is what the arithmetic says over a human's colour; `parent_id` is the goal it supports (optional); `owner` is `bot:x`, `human:x` or `company`; `tasks.goal_id` names the goal a task serves |
| `goal_events` | `goal_id, ts, actor, field, old, new, note, status_by, status_source` | every change to a goal; a `status` row says who set it and how |
| `kpis` | `id, slug, name, definition, unit, direction, cadence, owner, source_note, definition_version, created_at, created_by, updated_at` (`goal_id` and `target` are kept for the rows that had them) | a measure that stands on its own; `direction`: up, down, range; `cadence`: daily, weekly, monthly |
| `goal_kpis` | `goal_id, kpi_id, kind, baseline, baseline_at, target, deadline, min, max, created_at, created_by, updated_at, updated_by` | the link between a goal and a KPI, holding the target: `kind` is `improve` (baseline, target, deadline), `maintain` (min, max) or `none`; `kpi_id` may be `auto:<bot>:<metric>` |
| `kpi_readings` | `kpi_id, ts, value, actor, source, note, created_at, period_start, period_end, collected_at, evidence, quality, definition_version, supersedes` | appended, never edited; `period_start` and `period_end` are the business time the value describes (`ts` equals `period_end`), `collected_at` when it was collected; `quality`: measured, estimate, partial; a correction is a new row whose `supersedes` names the old one |
| `kpi_definitions` | `kpi_id, version, ts, actor, name, definition, unit, direction, cadence, source_note` | every version of a KPI's definition |
| `goal_checkins` | `goal_id, kpi_id, ts, author, source_actor, body, signal` | the owner's own words on how a goal is going; `signal`: on_track, at_risk, off_track |
| `goal_proposals` | `id, kind, goal_id, kpi_id, payload, reason, proposed_by, proposed_at, status, decided_by, decided_at` | a change waiting for its owner to confirm |
| `goal_asks` | `kind, ref, owner, task_id, created_at` | the Strategy Bot's task asking a person about a KPI or proposal; `kind`: kpi_red, kpi_stale, proposal; `ref`: the KPI or proposal id |
| `task_delegations` | `task_id, delegate, requested_by, message_id, expires_at` | |
| `approvals` | `id, kind, task_id, message_id, payload, requested_by, decided_by, decision, decided_at, consumed_at, created_at` | `kind`: send, spend, publish, merge |
| `jobs` | `id, message_id, bot, state, created_at, attempt_id` | one per message a bot has to act on; `state`: queued, leased, running, input, completed, failed, uncertain, canceled |
| `attempts` | `id, job_id, bot, runner_id, state, lease_until, created_at, started_at, finished_at, result, final_text` | a run (`state`: leased, running, input, completed, failed, interrupted, expired); its streamed output is `attempt_events(attempt_id, seq, kind, payload, created_at)` |
| `turns` | `id, bot, started_at, finished_at, trigger, message_id, task_id, exit, summary, input_tokens, cached_tokens, output_tokens, model, provider, est_cost_usd, usage_reason, billing, trigger_kind, trigger_source, purpose, writes, outcome, did_nothing, rotated, session_kind, session_why, rule_id` | one per run (`id` = the run's attempt id); `exit` is null while running. **Sum `input_tokens`, `cached_tokens`, `output_tokens` and `est_cost_usd`** (input = `input_tokens` + `cached_tokens`); every run finished from this change on, failed, interrupted or usage-limited, has them set (older runs keep what they had, and some have none). A run whose runner counted nothing reads 0 tokens and $0, and `usage_reason` says why (`usage_unreported`, `usage_limited`, `no_result`); `cost_unpriced` means tokens were counted but the model has no list price, so `est_cost_usd` is null. On a run finished from this change on, a null `usage_reason` means usage was measured; an older run has a null reason whether or not it was. The older one-number `tokens_in`, `tokens_out` and `cost` columns are gone: their values moved into `input_tokens`, `output_tokens` and `est_cost_usd` where those were empty (`backend/migrations/0009_drop_turns_legacy_usage.py`). `trigger_kind`, `writes` and `outcome` are explained under [What woke each run](#what-woke-each-run-and-what-it-did) |
| `run_weekly` | `week, bot, trigger, purpose, runs, input_tokens, cached_input_tokens, output_tokens, acted, no_change, nothing_to_do, unknown, did_nothing, rotated, acted_pct, no_change_pct, nothing_to_do_pct, unknown_pct` | a read-only view: runs per week, bot, trigger and purpose, every attempt counted ([below](#what-woke-each-run-and-what-it-did)) |
| `rule_weekly` | `week, bot, rule, runs, input_tokens, cached_input_tokens, output_tokens, acted, did_nothing, did_nothing_pct` | a read-only view: runs per week, bot and [wake rule](how-it-works.md#what-happens-when) (`turns.rule_id`, the rule that woke the run; `unknown` for a run before rules), with tokens and the share that did nothing |
| `schedules` | `id, bot, routine_key, title, cron, event_name, playbook, last_fired_at, next_due, deleted_at` (`timezone` and `enabled` are in `schedule_config(schedule_id, timezone, enabled)`) | routines from before Automations, kept but no longer fired (`docs/routines.md`); `schedule_occurrences(schedule_id, occurrence, task_id, outcome)` says what each firing did |
| `automations` | `id, name, owner, trigger_type, enabled, builtin, flow, version, created_at, created_by, updated_at, deleted_at` | a routine or automation. `trigger_type` says what starts it; `flow` holds its steps as JSON; `version` is the current version in `automation_versions` |
| `automation_runs` | `id, automation_id, version, trigger_ref, started_at, finished_at, status, cost, state, claimed_at, wake_at, chain` | one run of an automation. `status` is its outcome ([Status and state columns](status-and-state.md)); `state` is its saved JSON working data, not a step; `version` is the automation version it ran |
| `events` | `ts, actor, action, target, detail` | the audit log; `action` such as `task.create`, `sql.query` |
| `slack_posts` | `message_id, bot, state, attempts, next_attempt_at, slack_ts, created_at, updated_at` | Owner-only outbound queue status for messages the Owner may read; message text, destination and raw errors are hidden. Join `messages` and filter `json_extract(messages.refs, '$.task')` for one task. Other callers receive no rows. |
| `refusals` | `ts, actor, rule, detail, severity` | the rule a bot broke |
| `meetings` | `id, title, owner, recorded_by, transcript_readable, notes, created_at, updated_at` | your notes; every team meeting |
| `meeting_items` | `id, meeting_id, section, text, detail, quote, at_ms, status, created_by, updated_by, pushed_at, result_ref, created_at, updated_at` | a meeting's action items; `section`: doc, task, feature (older rows may say decision or question); `status`: proposed, pushed, dismissed; `result_ref` is the Tico task a push made. Visible exactly where its meeting is ([Meetings](meetings.md), What a meeting turns into) |
| `meeting_comments` | `id, meeting_id, author, text, at_ms, created_at` | the thread beside a meeting: anyone who can open it can add to it ([Meetings](meetings.md)). Visible exactly where its meeting is |
| `import_refs` | `source, external_id, meeting_id, runner_id, created_at` | which outside record a meeting was imported from — one row per Close call imported before the transcript-only worker ([Meetings](meetings.md), Close). Visible exactly where its meeting is |
| `docs` | `id, path, title, body, locked, version, created_by, created_at, updated_by, updated_at` | internal Docs, readable by teammates |
| `doc_versions` | `doc_id, version, title, path, body, actor, created_at, note` | versions of visible internal Docs |
| `linked_docs` | `id, title, url, kind, description, added_by, created_at, updated_at` | linked Docs |
| `bot_files` | `id, bot, scope, identity, title, kind, locator, current_version, task_id, conversation_id` | Files, with the same task, conversation or bot visibility as their APIs |
| `bot_file_versions`, `bot_file_activity` | `file_id, version` or `file_id, action, actor, created_at` | versions and activity of visible Files |
| `pages` | `file_id, cadence, as_of, replaced_by, archived, created_at, updated_at` | a bot's File published as a page: `file_id` is `bot_files.id`; `cadence`: none, daily, weekly, monthly; `as_of` is the date its data is from; `replaced_by` names the newer page, if any ([Pages](pages.md)) |
| `cards` | `id, template, settings, title, made_by, created_at, updated_at, updated_by` | a built-in card: `template` is one of Tico's own templates and `settings` its checked settings. A card's numbers are worked out when it is read, for the reader, and are not stored ([Overview](overview.md)) |
| `learnings` | `id, integration, actor, text, created_at, deleted_at` | Tools: what bots and humans learned about a tool (`integration` is the stored column; `integrations/`, `hub tool learn`) |
| `assistant_action_weekly`, `mcp_call_weekly` | `week, proposer, status, actions, share_pct` and `week, bot, tool, calls, errors, error_pct, avg_ms, max_ms` | read-only totals for the owner and a bot the owner grants them (API only: `PUT /api/v2/outcome-stats/readers/<slug>`; revoke with `read: false`): how Assistant and BotOps proposals settled (`status`: done, failed, cancelled, expired ...) and how bot MCP tool calls went, per week. No row, text, id or person is in them; the underlying `assistant_actions` stays unreadable to a bot. The same bot may call `hub mcp stats` (no `--via`) |

Only the signed-in owner can grant or revoke access; personal API tokens and delegated Assistant or BotOps turns cannot.
Delegated turns also cannot read these totals through the owner's role. Revocation removes the bot's access on its next
request. Buckets are not suppressed: a count of one is visible, without the underlying proposal or person's identity.
People's MCP calls are excluded from `mcp_call_weekly` and a granted bot's `hub mcp stats`. Assistant proposal
totals combine all owners by week, proposer and status; action routes and methods are not exposed.
Tool names in the MCP totals and a granted bot's `hub mcp stats` are registered hub tool names; unknown names count as `other`.

`sqlite_master` lists the rest (`SELECT name, sql FROM sqlite_master WHERE type='table'`).

## Examples

Queued work, oldest first:

```sql
SELECT bot, count(*) AS queued, min(created_at) AS oldest
FROM jobs WHERE state='queued' GROUP BY bot ORDER BY queued DESC
```

My open tasks (a bot's own; a human swaps in `human:<id>`):

```sql
SELECT id, status, title, requester, due, updated_at
FROM tasks WHERE owner=:me AND status IN ('open','doing','waiting') ORDER BY due, updated_at
```

What is waiting on a human, and for how long:

```sql
SELECT t.owner, t.title, t.updated_at, substr(m.body, 1, 100) AS question
FROM tasks t JOIN messages m ON m.conversation_id=t.conversation_id AND m.kind='ask'
WHERE t.status='waiting' ORDER BY t.updated_at
```

The last run of every bot, with the week's count and failures:

```sql
SELECT bot, max(started_at) AS last_turn, count(*) AS turns,
       sum(exit IN ('failed', 'expired')) AS failed
FROM turns WHERE started_at > strftime('%Y-%m-%dT%H:%M:%S', 'now', '-7 days')
GROUP BY bot ORDER BY last_turn DESC
```

Successful runs finish with `completed`. `failed` and `expired` are terminal errors;
a bot reporting blocked work can still complete its run successfully. Health counts failed and
expired attempts in the last 24 hours. Runs includes those attempts even when no turn was recorded,
with the attempt ID and reason. Query `attempts` when counting all execution attempts.

### What woke each run, and what it did

Every run records what woke it (`turns.trigger_kind`), its tokens, and whether it changed anything
(`turns.outcome`). All three come from plain code, not from asking a model:

- **`trigger_kind`**: `comment` (a bot wrote to it: a task comment or a message), `person_message` (a person
  wrote to it, or pressed Run now), `status_or_relation` (a task of its moved: assigned, done, unblocked, a
  subtask finished, reopened, snoozed by someone), `pr_checks` (its pull request's checks, review comments,
  merge conflict, merge or release), `stall_check` (Tico's status check or stall wake on a quiet task, a task
  not starting, a stale ask, a worktree left idle), `schedule` (a routine firing, a due date, a snooze ending,
  a next-run task, an update request), `background_done` (background commands an earlier turn left running
  when the runner ended it on its wait have ended) or `other` (anything else: a Slack digest, a meeting turn, a worktree
  becoming ready). `trigger_source` names the event behind it where there is one: the message itself for
  `comment` and `person_message`, the subtask, the routine, the worktree.
- **`writes`**: the hub writes the run made with its own run token (a task change, comment, link, message,
  file, approval request), plus one for a reply Tico posted for it (to a person, or an answer to a bot's ask). Reads and bookkeeping are not
  writes: `hub sql`, opening a conversation, marking messages read, its own status line, refusals and lint.
  A git push from a worktree of the run's own task counts one per push: its runner reports them with the run's
  result. A commit or pull request counts once Tico hears of it (`hub task link`, a task note, a push); a local
  commit Tico never sees cannot be counted.
- **`outcome`**: `acted` when `writes` is above zero; otherwise `no_change` when the run's task is still open
  as it ends (it looked, and nothing had moved) and `nothing_to_do` when there was no open task to act on (no
  task, or one already done, closed or declined).
- **What became of it**: when a bot's task sits at `done` or `review` and someone other than the bot or the keeper
  moves it on, the run that delivered it gets one `turn.outcome` event (`target` the turn id, `detail`
  `{task_id, outcome, from, to}`): `accepted` (closed, or `review` moved to `done`), `edited` (its title or body
  rewritten) or `overridden` (sent back to work, declined or handed to another owner). The first is the run's outcome;
  later moves add none. `SELECT json_extract(detail,'$.outcome') o, count(*) FROM events WHERE
  action='turn.outcome' GROUP BY o`. A caller's own decision calls get the same from `hub decision outcome`
  (`decision.outcome` events, target the `judge.call` event id).
- **`did_nothing`**: 1 when `writes` is 0, else 0: the exact flag to measure against. It sees every write that
  reaches Tico with the run's token, and every push from its task's worktrees; a push from anywhere else (the
  bot's own checkout, another task's worktree) is not counted.

- **`rotated`**: older runners only. `tokens` or `turns` when the run started a fresh AI session instead of resuming
  the bot's last one, because that one had grown past a size limit (`rotate_at`, `rotate_after_turns`, now removed;
  the default was 400,000 input tokens); NULL otherwise, and always NULL from a current runner, which lets
  auto-compaction manage a session's size. `run_weekly` counts these runs as `rotated`.
- **`session_kind`, `session_why`**: `resumed` or `new`, and why. A `new` session reads one of `none_saved` (no
  session was saved yet), `not_found` (the old one wasn't on this computer), `resume_failed`, `compact_failed`,
  `interrupted`, `fallback_side` (a fallback model keeps a session of its own), or `prompt_too_long_after_compact`
  (the run failed on a prompt too large to send even after compacting, so Tico cleared the session for the next
  run); `resumed` may carry `compacted`. NULL from an older runner. A new session for any reason but `none_saved` is
  also a line in its task's history (`task_events.field = 'session'`). Unusual: `SELECT bot, count(*) FROM turns
  WHERE session_kind = 'new' AND session_why <> 'none_saved' AND started_at > date('now', '-1 day') GROUP BY bot`.

`purpose` is a short label for what the run was for (`review PR`, `answer person message`): the place that wakes the bot may name it, otherwise it is the default for that bot and trigger, and runs from before it existed were filled the same way. A wake the map does not know reads `unknown`. Group by it: `SELECT purpose, sum(runs), sum(input_tokens) FROM run_weekly GROUP BY purpose`.

A run from before this was recorded has none of these, and reads `unknown`; so does an attempt that never got
a `turns` row (one that failed or expired before it started), counted with no tokens and no outcome. `run_weekly` adds them up per week
(starting Monday, UTC), bot, trigger and purpose, over exactly the runs you may read in `turns` and `attempts`: a run on a
private task, in someone else's room, or one you no longer have access to is left out of your totals. Its `input_tokens` is the run's measured usage (`input_tokens` + `cached_tokens`, every
fallback segment included); a run with no usage recorded reads 0.
Last week's numbers, biggest input first:

```sql
SELECT * FROM run_weekly WHERE week = date('now', '-13 days', 'weekday 1') ORDER BY input_tokens DESC
```

Per bot over about the last four weeks, with the share of runs that changed nothing:

```sql
SELECT bot, sum(runs) AS runs, sum(input_tokens) AS input_tokens,
       round(100.0 * sum(no_change + nothing_to_do) / sum(runs), 1) AS idle_pct
FROM run_weekly WHERE week >= date('now', '-27 days', 'weekday 1') GROUP BY bot ORDER BY input_tokens DESC
```

Which wake rule woke the runs, and how many of them did nothing (`rule_weekly`; the rules are in the **What wakes**
automations on the Automations page). A run from before rules reads `unknown`:

```sql
SELECT rule, sum(runs) AS runs, sum(did_nothing) AS did_nothing,
       round(100.0 * sum(did_nothing) / sum(runs), 1) AS did_nothing_pct, sum(input_tokens) AS input_tokens
FROM rule_weekly WHERE week >= date('now', '-27 days', 'weekday 1') GROUP BY rule ORDER BY input_tokens DESC
```

Wakes that waited (token saving 2/9, `backend/wake_rules.py`): each is one `wake.skipped` event naming the bot, the
task, the held message (`target`; for a bot's own comment on its task, that comment), its trigger and the rule
(`parked`, `own_write` or `same_state`, token saving 3/9: a stall or status check on an idle task that stands as at
its owner's last checkpoint). Leased or running task sessions keep their recovery checks. Overlapping turns of the
same bot leave no settled comparison state because their writes cannot be attributed to one turn. A sample of 50
from last week for the audit:

```sql
SELECT ts, json_extract(detail, '$.bot') AS bot, json_extract(detail, '$.task') AS task,
       json_extract(detail, '$.rule') AS rule, json_extract(detail, '$.trigger') AS trigger, target AS message
FROM events WHERE action = 'wake.skipped' AND ts >= date('now', '-13 days', 'weekday 1')
  AND ts < date('now', '-6 days', 'weekday 1') ORDER BY random() LIMIT 50
```

Replies Tico did not post: a turn woken by Tico on a task already finished that changed nothing leaves one
`reply.withheld` event (`target` is the run; `bot`, `task`, `to`); its text stays on the run (`attempts.final_text`):

```sql
SELECT ts, json_extract(detail, '$.bot') AS bot, json_extract(detail, '$.task') AS task,
       json_extract(detail, '$.to') AS to_person, target AS run
FROM events WHERE action = 'reply.withheld' ORDER BY ts DESC LIMIT 50
```

Who asked what today:

```sql
SELECT created_at, from_actor, to_actor, substr(body, 1, 90) AS ask
FROM messages WHERE kind='ask' AND created_at >= strftime('%Y-%m-%d', 'now') ORDER BY created_at DESC
```

Routine outcomes from before Automations, most recent first:

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
SELECT seq, kind, json_extract(payload, '$.text') AS text
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
SELECT ts, actor, json_extract(detail, '$.rows') AS rows, json_extract(detail, '$.sql') AS sql
FROM events WHERE action='sql.query' ORDER BY ts DESC LIMIT 20
```

From a bot: `hub sql "SELECT ..." --param me=bot:seo`. From Ana's Mac: `scripts/hub sql "SELECT ..."`.

For a task's Slack notification status (Owner only):

```sql
SELECT p.message_id, p.state, p.created_at
FROM slack_posts p JOIN messages m ON m.id = p.message_id
WHERE json_extract(m.refs, '$.task') = :task_id;
```

No rows means no visible queued post for that task. `slack_events` and the other gateway
internals remain unavailable through SQL; use the Slack history tools to read messages.

## Tags on tasks

`tags` is readable by signed-in teammates. `task_tags` contains only associations with tasks
you may read, including when joining the tables:

```sql
SELECT tasks.id, tasks.title, tags.key, tags.label, tags.metadata
FROM tasks
JOIN task_tags ON task_tags.task_id = tasks.id
JOIN tags ON tags.id = task_tags.tag_id
WHERE tags.key = 'release-2026-10-02';
```

Use these tables instead of the retained `tasks.labels` column.

## Task pipelines

`task_types(id, name, created_at, updated_at)` and
`task_steps(id, type_id, name, position, status)` describe the team's pipelines. Every task carries
`type_id` and `step_id`; `status` stays the contract. Both definition tables are readable by every
SQL caller. Joining to `tasks` still returns only tasks that caller may read.

```sql
SELECT t.id, t.title, t.status, ty.name AS type, s.name AS step
FROM tasks t
JOIN task_types ty ON ty.id = t.type_id
LEFT JOIN task_steps s ON s.id = t.step_id
ORDER BY t.updated_at DESC
```

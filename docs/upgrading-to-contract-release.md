# Upgrading to the contract release

This release gives each thing one name. Times end in `_at`, JSON fields and columns lose the `_json` suffix, a run is
called a run (it was an attempt), and a bot has an id that never changes next to a slug that can. Clients written
against the old names need the changes below. There are no aliases: the old names stop working in this release.

Use this page with the spec (`docs/openapi/v2.json`), the [SQL guide](hub-sql.md) and the
[status and state columns](status-and-state.md). If a name here and the spec disagree, the spec is right.

## Before you upgrade

- A database at schema version 70 or older is refused. The message is: "This database is at schema version N, below 71,
  the oldest this server upgrades. Start it once with Tico v0.5.x (the release that writes version 71), then upgrade to
  this release." Start the old database once with v0.5.x, then upgrade.
- Migrations run on the first start. Migrations `0013_rails_renames_wave1`, `0014_rails_renames_wave2`,
  `0031_rails_renames_wave3`, `0032_one_spelling_canceled` (two note columns) and `0033_rails_renames_wave5` rename the
  columns. A database that has both the old and the new name of a column is refused, so do not rename columns by hand.

## Field names

| Old | New | Where |
|---|---|---|
| `<name>_json` | `<name>` | Every JSON field and column. The stored content is the same text. |
| `created`, `updated` | `created_at`, `updated_at` | Objects and tables. |
| `started`, `finished`, `last_seen`, `expires`, `last_checked`, `last_sync`, `next_attempt` | `started_at`, `finished_at`, `last_seen_at`, `expires_at`, `last_checked_at`, `last_synced_at`, `next_attempt_at` | Objects and tables. |

Exceptions, where the new name is not the old name with the suffix dropped:

| Old | New | Table or object |
|---|---|---|
| `start_json`, `end_json` | `at_start`, `at_end` | attempt load snapshots |
| `when_json` | `condition` | rules, finance rules |
| `to_json` | `to_addresses` | mail messages |
| `with_json` | `with_scope` | org rules |

Inside a response, a field that holds JSON in storage comes back parsed: `refs` is an object, `participants` is a list,
and a task's acceptance criteria are `acceptance_criteria`, a list.

## Runs

- `attempt_id` is `run_id`, and an `attempt` object is a `run` object. This covers the `run` on a message, `run_id` on
  an execution, and `run` on each entry of a task's run list. The task's list is `runs` (it was `attempts`).
- The routes under `/api/v2/attempts/...` keep their paths, for example `POST /api/v2/attempts/{aid}/renew`.
- The runner wire keeps its `attempt` keys, so runners on older releases keep working.
- Event names in the audit log keep `attempt`, for example `attempt.input.moved`.

## Holds

A task no longer has `snoozed_until`, `snoozed_by` or `snoozed_note` fields. Its `holds` list carries those facts and
the blocked ones. Each hold has `kind` (`blocked` or `snoozed`), `reason`, `since`, `until`, `by` and `cause`. The snooze
and unsnooze endpoints keep working as before.

```json
{"id": "t_8f2", "title": "Send the Q4 invoice", "created_at": "2026-10-02T09:12:00Z",
 "holds": [{"kind": "snoozed", "reason": "waiting on the vendor", "since": "2026-10-02T09:12:00Z",
            "until": "2026-10-09T09:00:00Z", "by": "human:sam"}]}
```

The example is abridged. The old response for the same task had `"created"`, `"snoozed_until"` and `"snoozed_by"`
at the top level, and no `holds`.

## Needs you

Each needs-you item has `id`, `kind`, `title`, `body`, `subject`, `created_at` and `actions`. It had `created`.

## Status values

- `cancelled` is now `canceled` everywhere Tico writes it, including `jobs.state`, `webhook_sends.state`,
  `fin_payments.status`, `fin_transfers.status`, `fin_cards.status`, `assistant_actions.status`,
  `bot_tool_requests.state`, `bot_transitions.state`, `credential_requests.status`, `model_logins.state`,
  `seats.request_state`, `service_jobs.state`, `slack_posts.state`, `task_reconnects.ended` and `worktree_waits.state`.
  Migration `0032_one_spelling_canceled` converts stored rows. A seat being canceled is `canceling`.
- The same spelling applies to names: `notes.cancelled_at` and `cancelled_by` are `canceled_at` and `canceled_by`, a
  deferred message's `cancelled_at` is `canceled_at`, the answer to deleting a webhook has `canceled`, a live meeting
  bot removal has `canceled_turns`, and the spec's `SeatCancelled` is `SeatCanceled`.
- Four audit events are renamed, stored rows included: `assistant.action.canceled`, `bot.tool_request_canceled`,
  `bot.transition_canceled` and `seat.request_canceled`.
- Values from an outside system keep that system's spelling, such as Mercury's `provider_status` on a feed row.
- Bot onboarding `needs_onboarding` is now `needs_setup`. Readers still accept `needs_onboarding` for one release, but
  new values are `needs_setup` or `onboarded`.
- A status column refuses a value outside its list. On a trigger column the error is `<table>.<column>: invalid value`.
  On a CHECK column the error is SQLite's own `CHECK constraint failed`.

## Bots

- Every bot has an `id` and a `slug`. The `id` never changes. The `slug` is a label, and it can be renamed.
- A bot made before this release has `id` equal to its old slug, so `bot:acme-ops` in stored history still names it. A
  bot made in this release gets a UUID as its `id`.
- Use the `id` for actor strings (`bot:<id>`), references and lookups. Use the `slug` to display a bot. A route, field
  or command that takes a bot accepts either.
- Rename a bot with `hub bot update acme-ops --new-slug acme-support` (CLI), the `new_slug` argument of `hub_bot_update`
  (MCP), or the `slug` field of `POST /api/v2/bots/{bot}/definition`. The `id`, the history and the folder stay.
- A bot's checkout folder is named when the bot is made: `bot-<slug>`, or `bot-<slug>-<part of its id>` when that name
  is taken. A rename does not change it. Clients find a bot's files by `folder` from `GET /api/v2/bots`, not by slug.
  The spec does not list `folder` yet.
- Placing a bot on a computer where another bot already uses its folder fails with `409 folder_taken`.
- Groups list bot ids in `bots`. For bots made before this release those are the old slugs, so nothing changes for them.
- Restoring a deleted task that holds data this version cannot place fails with `409 unplaced`. The task stays in
  Deleted tasks.

## SQL

Use the new column names in every query. The same change, before and after, for a message that cites a task:

```sql
-- before
SELECT id, created FROM messages WHERE json_extract(refs_json, '$.task') = :task;
-- after
SELECT id, created_at FROM messages WHERE json_extract(refs, '$.task') = :task;
```

- `principals` is new. It has one row per human, bot and known system actor. Its `id` is the actor string, so an actor
  column joins to `principals.id`.
- Gone: `documents` and `document_versions` (the market notes are in `market_pages`), `ceo_actions`, `ceo_merges`,
  `redesign_migrations`, `redesign_migration_accepts`, `task_cap_sends`, `fin_vendor_legacy`, the `strategy_*_to_old`
  mirror triggers, and three task indexes nothing read.
- `turns.tokens_in`, `turns.tokens_out` and `turns.cost` are gone. Read `input_tokens`, `output_tokens` and
  `est_cost_usd`.
- `attempt_events.payload_json` is `payload`, and `attempt_events.created` is `created_at`.
- The stable tables are listed in [Querying Tico with SQL](hub-sql.md#stable-and-unstable-tables). Other tables can change
  in any release.

## Unchanged on purpose

- The runner wire, and the JSON keys inside stored blobs and in payloads Tico stores as sent.
- External payloads: Stripe, QuickBooks, Xero, and inbound webhooks.
- HTTP headers and `TICO_*` environment variables.
- MCP tool names and CLI command names. An input that takes a bot, a person or an owner keeps that argument name, because
  it accepts either an id or a name.
- Route paths, and audit-log event names other than the four under Status values.

## Actor and time columns

An actor column ends in `_id` and holds the same actor string as before. A time column names its moment. These renames
apply to the tables named, not to every column with the old name: `tasks.owner` is `owner_id`, `meetings.owner` stays.

| Old | New | Tables |
|---|---|---|
| `owner` | `owner_id` | `tasks`, `goals`, `kpis`, `automations` |
| `owner_actor` | `owner_id` | `conversations` |
| `operator` | `owner_id` | `runners`, `bot_config`, `enrollments` |
| `requester` | `requester_id` | `tasks` |
| `from_actor`, `to_actor` | `sender_id`, `recipient_id` | `messages` |
| `source_actor` | `sender_id` | `goal_checkins` |
| `actor` | `actor_id` | `kpi_readings` |
| `bot` | `bot_id` | `jobs`, `attempts`, `bot_files` |
| `created_by`, `updated_by`, `closed_by`, `decided_by`, `requested_by` and other `*_by` | `*_by_id` | `tasks`, `goals`, `kpis`, `goal_kpis`, `approvals`, `cards`, `docs`, `messages` and others |
| `grp`, `dept` | `group_id`, `department` | `automations`; the task manager tables |
| `ts` | `created_at` (`measured_at` on `kpi_readings`) | `events`, `task_events`, `goal_events` and other logs |
| `at`, `since`, `until` | a name for the moment, such as `created_at`, `changed_at`, `started_at`, `ended_at` | `clients/renames.json` has each table |
| `due`, `next_due` | `due_at`, `next_due_at` | `tasks`, `task_reminders`, `deferrals`; `automations`, `schedules`, `bot_status` |
| `state` | `saved_state` | `automation_runs` (it was `state_json` before that) |

`clients/renames.json` holds the full list, with every table each name moved on and the tables that keep it.

These response fields keep their old names in this release, and the spec lists them: `created` on
`BotFileResult`, `PagePublished` and `InboundTaskResult`; `updated` on `UpdateArchiveResult`; and `blocked_by` on
`UsageLimitView`. Inputs keep their names too: `owner` and `due` on `TaskCreate` and `TaskUpdate`, `from_actor` on a goal
check-in, and `at` on a KPI reading, which is the old name of `period_end`.

Some response fields were renamed in this release and the spec now says so:

- Granola's status, sign-in and sync answers: `last_sync` is `last_synced_at`, the time of the last sync. The Granola
  document Tico stores keeps `last_sync`, so nothing stored changes.
- A goal's `why_by` is `why_by_id`: the actor who wrote the goal's why.
- A goal check-in's `source_actor` is `sender_id`, and `source_actor_name` is `sender_name`: whose words the check-in is.
  `author` is who recorded it. Migration 0033 renames the column.
- A KPI reading's `ts` is `measured_at`: the moment the reading is about, the end of its period. Its `actor` is
  `actor_id`, the person who recorded it.
- A message's display names are `sender_name` and `recipient_name`.

## Check a bot's repository

`hub bot check-names <slug>` lists the old column, field and command names a bot's checkout still uses, and runs on the
computer without the server. It reads SQL in `hub sql`, SQL-looking strings, keys read from API answers, and `hub`
commands. A name renamed on some tables only is reported with those tables: `SELECT owner FROM tasks` is flagged and
`SELECT owner FROM meetings` is not.

`hub bot check-names <slug> --fix` rewrites a name only when it has one new spelling everywhere and no table keeps it.
It leaves everything else for you to change, including `owner`, `state`, `from_actor`, a bot's `memory/` and SQL outside
a `hub sql` call, which may be the bot's own database.

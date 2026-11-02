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
- Migrations run on the first start. Migrations `0013_rails_renames_wave1` and `0014_rails_renames_wave2` rename the
  columns. A database that has both the old and the new name of a column is refused, so do not rename columns by hand.

## Field names

| Old | New | Where |
|---|---|---|
| `<name>_json` | `<name>` | Every JSON field and column. The stored content is the same text. |
| `created`, `updated` | `created_at`, `updated_at` | Objects and tables. |
| `started`, `finished`, `last_seen`, `expires`, `last_checked`, `last_sync`, `next_attempt` | `started_at`, `finished_at`, `last_seen_at`, `expires_at`, `last_checked_at`, `last_synced_at`, `next_attempt_at` | Objects and tables. The names not yet changed are under [Still to rename](#todo-still-to-rename). |

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

- `cancelled` is now `canceled` in `jobs.state`, `webhook_sends.state`, `fin_payments.status` and
  `fin_transfers.status`.
- Finance cards (`fin_cards.status`) and Assistant actions keep `cancelled` in this release.
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
- Route paths, and audit-log event names.

## TODO: still to rename

> **TODO, not done in this release.** Actor columns still have role names: `owner`, `requester`, `closed_by`,
> `from_actor`, `to_actor`, `created_by`, `updated_by`, `decided_by`, `requested_by`, `bot` (in SQL and in the API),
> `operator` (bots and runners), `grp` (automations) and `dept`. The plan is `<role>_id` holding the same actor string.
> The time names `ts`, `at`, `since`, `due` and `next_due` also keep their old names. The spec still has these
> fields with their old names: `created` on `BotFileResult`, `PagePublished` and `InboundTaskResult`; `updated` on
> `UpdateArchiveResult`; `last_sync` on the Granola objects; `due` on `Task`, `TaskCreate` and `TaskUpdate`; `next_due`
> on `Automation` and `AutomationHeld`; `since` on automation holds; `ts` on `KpiReadingView`, `GoalCheckinView` and the
> company history events; `at` on `KpiReading`; and `blocked_by` on `UsageLimitView`.
>
> When this lands, replace this note with the old-to-new table for these names.

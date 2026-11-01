# Status and state columns

Every stored status or state column in the hub database: what each allowed value means, the transitions the code makes,
and how the database enforces the list. The lists themselves live in `backend/status_values.py`, which is what the
database enforces. If this page and that file disagree, the file is right and this page needs fixing.

Schema facts are from a fresh database built by `Store.initialize` on this branch. Transitions are from the code that
writes each column (`UPDATE <table> SET <column>=...`, `INSERT`, or the helper that calls it). File names are given
without line numbers so they stay greppable.

## How the lists are enforced

Three kinds of enforcement:

- **trigger**: `BEFORE INSERT` and `BEFORE UPDATE OF <column>` triggers named `status_values_<table>_<column>_ins` and
  `_upd`. They `RAISE(ABORT, '<table>.<column>: invalid value')`. Used on the hot and growing tables (tasks, jobs,
  attempts, automation_runs, webhook_sends), where a CHECK would mean rebuilding a large table.
- **check**: a named table constraint `CONSTRAINT status_values_<column> CHECK (<column> IN (...))`, installed by
  rebuilding the small table. A rebuild renames the old table aside, copies every row (mapping retired spellings), checks
  the row count, then drops the old table. Foreign keys are off for the rebuild, so child rows are not cascaded.
- **none**: no constraint. Listed below where the column is in the stable set but deliberately left alone.

Changing a list is a code change in `backend/status_values.py`. `Store.initialize` calls `enforce` on every start: it
replaces a trigger whose text differs and rebuilds a CHECK whose list differs. Adding a value needs nothing else.

Removing a value is not free. Only migration 0002 counts stored values against the list. After that, a removed value that
is still stored is left alone on trigger columns (old rows keep it, new writes are refused), and makes the CHECK rebuild
fail on a CHECK column. A removal therefore needs its own migration that maps or refuses those rows first.

## Retired spellings and the upgrade

Migration `backend/migrations/0002_status_values.py` runs `backend/status_values.py` `upgrade`, with foreign keys off
(`FOREIGN_KEYS_OFF`) so a CHECK rebuild does not cascade:

1. Counts every stored value outside the list, plus the retired spellings it maps. If any remain, it stops before
   changing anything and raises `StatusValuesError` listing each one as `table.column = 'value' (n rows)`.
2. Rewrites retired spellings on trigger columns with `UPDATE`, and maps them on CHECK columns while the table is copied.
3. Installs the triggers and CHECKs.

Retired spellings on this branch:

| Retired | Current | Columns |
|---|---|---|
| `cancelled` | `canceled` | `jobs.state`, `webhook_sends.state`, `fin_payments.status`, `fin_transfers.status` |
| `needs_onboarding` | `needs_setup` | `bot_config.onboarding_state` (migration 45 rewrote stored rows; this maps any stale row) |

Readers of `bot_config.onboarding_state` still accept `needs_onboarding` for one release (`backend/statuses.py`).
That is unchanged here. New writes can only be `needs_setup` or `onboarded`.

`complete` is not stored in any stable-set column. Two live-meeting tables still write it (see "Outside the stable set").

On a fresh file the migration runs before the module schema code creates most tables, so `Store.initialize` calls
`enforce` last, on every start: a new database has the constraints and no retired spelling.

## Same word, different meanings (kept apart)

- `attempts.state` `expired` (the lease ran out, nothing reported) and `interrupted` (the runner reported it stopped
  partway, and it can resume). `execution.py` treats them differently.
- `tasks.status` `done` (finished work) and `closed` (filed under Closed). `declined` is a third meaning (will not be done).
- Finance `unknown` (a payment or transfer whose send got no answer; reconciliation pending) and `withheld`
  (`fin_outbox.status`: a rehearsal copy did not send). `withheld` is not in the payment or transfer lists.
- `approvals.decision` `declined` (a person refused an approval request) and `goal_proposals.status` `rejected` (a
  person refused a proposed goal). Different records, so not merged. The integration owner may unify them later.

## Summary of the stable set

| Column | Kind | Enforcement | NULL | Growing or user-defined set? |
|---|---|---|---|---|
| `tasks.status` | status | trigger | allowed (no default) | no, fixed in code |
| `tasks.blocked_from_status` | saved status | trigger | allowed (NULL when not held) | no, same list as `tasks.status` |
| `jobs.state` | state | trigger | NOT NULL, default `queued` | no |
| `attempts.state` | state | trigger | NOT NULL | no |
| `automation_runs.status` | status | trigger | NOT NULL | no |
| `webhook_sends.state` | state | trigger | NOT NULL, default `ready` | no |
| `bots.state` | state | check | allowed (default `active`) | no |
| `bot_status.state` | state | check | allowed | no |
| `bot_config.onboarding_state` | state | check | allowed (NULL: not a starter bot) | no |
| `automation_versions.status` | status | check | NOT NULL, default `active` | no |
| `goals.status` | status | check | allowed (NULL: never scored) | no |
| `goals.status_source` | source | check | allowed | no |
| `goals.suggest_status` | suggestion | check | allowed | no |
| `goal_proposals.status` | status | check | NOT NULL, default `pending` | no |
| `bot_files.state` | state | check | NOT NULL, default `ready` | no |
| `approvals.decision` | decision | check | allowed (NULL = open) | no |
| `doc_sources.status` | status | check | NOT NULL, default `link_only` | no |
| `model_subscriptions.status` | status | check | NOT NULL, default `new` | no |
| `strategy_actions.state` | state | check | NOT NULL | no |
| `google_connects.status` | status | check | NOT NULL | no |
| `fin_payments.status` | status | check | NOT NULL, default `queued` | no |
| `fin_transfers.status` | status | check | NOT NULL, default `queued` | no |
| `task_steps.status` | status | check (already in the schema) | NOT NULL | no, see below |
| `meetings.review_state` | state | check (already in the schema) | NOT NULL, default `live` | no |
| `bot_status_history.state` | state (history) | none | allowed | no, see below |

No column in the stable set holds a user-defined value. Team-defined task steps (`task_steps.status`) pick from
`TASK_STATUSES`; a team can add a step with any of those statuses, never a new one. The Growth and Finance tables that a
user shapes (channels, budgets, rules) have their own columns, listed under "Outside the stable set".

## Detail

### tasks.status (trigger)

Values (`TASK_STATUSES`):

- `open`: not started. Owners may set it.
- `doing`: being worked on.
- `waiting`: parked on something else (`waiting_on`). The task stays in its working step and shows a Waiting tag.
- `review`: work is finished and waiting for review or QA. Owners may set it.
- `ready`: the hub has learned the work is ready, for example its pull request merged. Set by movers and the keeper, never by an owning bot.
- `done`: finished work. Shown under Done.
- `closed`: filed under Closed. Not the same as done.
- `declined`: will not be done. It goes to the type's closed step.

Transitions:

- Set on insert by the task creators. The column has no default.
- `open` to `doing`: a bot starts the task (`backend/auto_tasks.py`).
- Any to `open`: reopening (`auto_tasks.py`, `milestone_waits.py`).
- `open` or `doing` to `waiting`, and `waiting` to `open`: `backend/reconnects.py`.
- Any to `ready`: a merge or release milestone lands (`backend/github.py`, `backend/app.py`, `hubdb.py` `task_milestone_waits`).
- Owners set `open`, `doing`, `waiting`, `review`, `done`, `declined` (`hubdb.py` `OWNER_STATUSES`). Movers and the keeper may set any value.
- `closed`: movers and the keeper; a bot may close only a task it requested (`hubdb.py` task close rule).
- Holds: `backend/task_blocked.py` writes the status in force to `blocked_from_status` when a hold starts, and restores it when the hold clears.

Enforcement: trigger on insert and on update of `status`. NULL is accepted because the column is nullable. Whether NULL
should be refused is an open decision (see "Decisions").

### tasks.blocked_from_status (trigger)

The status a task returns to when its hold clears. Same list as `tasks.status`. NULL when the task is not held. Written
by `backend/task_blocked.py`, read by `hubdb.py` when a hold clears.

### jobs.state (trigger)

One job per message a bot must answer.

Values (`JOB_STATES`):

- `queued`: waiting for a runner to claim it.
- `leased`: claimed by a runner for an attempt; the attempt holds a lease.
- `running`: the attempt is executing it.
- `input`: the attempt is waiting for a person's or another bot's answer (an ask).
- `completed`: answered.
- `failed`: the attempt failed.
- `uncertain`: the outcome cannot be known (the runner was lost, or the hub restarted mid-run). Looked at before any retry.
- `canceled`: stopped before it finished (a person stopped it, the task closed or was declined, a duplicate wake
  replaced it, or the owner's rule dropped it). Spelled `cancelled` before migration 0002.

Transitions:

- Insert: `queued`.
- `queued` to `leased`: claim (`backend/execution.py`, `UPDATE jobs SET state='leased',attempt_id=...`).
- `leased` to `running`: the attempt starts (`execution.py`).
- `running` or `queued` to `input`: an ask is sent and the attempt waits (`execution.py`).
- `input` to `running`: the answer arrives and the attempt resumes (`execution.py`).
- `leased`, `running` or `input` to `queued` or `uncertain`: the attempt's lease expires or the attempt ends without a result. `queued` when nothing was done yet, `uncertain` otherwise (`execution.py` expiry and end-of-attempt paths).
- On an attempt's end, each job takes the attempt's outcome state, or `canceled` for jobs a stop cancelled (`execution.py`).
- `running` or `input` to `uncertain`, and `leased` to `queued`: hub restart (`backend/backup.py`).
- `queued` to `completed`: a person answers before any bot runs (`backend/app.py`, `UPDATE jobs SET state='completed' WHERE ... AND state='queued'`).
- Any open state to `canceled`: stop, task closed or declined, duplicate wake, quota or wake presets (`app_delivery.py`, `delivery.py`, `demo.py`, `chat_goals.py`, `execution.py`).

Enforcement: trigger on insert and update of `state`. NOT NULL, default `queued`.

### attempts.state (trigger)

One attempt is one run of a job on a runner.

Values (`ATTEMPT_STATES`):

- `leased`: claimed, and the runner holds the lease.
- `running`: the runner has started.
- `input`: in the list, but no code path writes it on an attempt. The job goes to `input`, not the attempt. Kept for now; see "Decisions".
- `completed`: finished with a result.
- `failed`: finished with an error.
- `interrupted`: the runner reported that it stopped partway. It can resume (`execution.py` treats it as resumable).
- `expired`: the lease ran out with nothing reported, or the hub restarted mid-run. A timeout, not a report.

Transitions:

- Insert `leased` on claim (`execution.py`).
- `leased` to `running`, and again when resumed (`execution.py`).
- `leased` or `running` to `expired`: lease timeout (`execution.py` expiry), or hub restart (`backup.py`).
- `leased` or `running` to `failed`: a recorded error on the attempt (`execution.py`).
- `running` to `completed`, `failed` or `interrupted`: the runner's outcome, from its result body (`execution.py`).

Enforcement: trigger on insert and update of `state`. NOT NULL.

### Runs

API v2, the live events, webhooks, the MCP tools, the hub CLI and the web app call an attempt a run until Release 2
moves the table: a field is `run_id` (the attempt's id), an object is `run`, a list is `runs`. `run.state` comes from
`attempts.state` by one fixed map (`backend/runs.py`, `STATE`):

| `attempts.state` | `run.state` | Meaning |
|---|---|---|
| `leased` | `leased` | a runner holds the lease and has not started the harness |
| `running` | `running` | the harness is working |
| `input` | `input` | paused for an answer it asked for |
| `completed` | `completed` | finished with a result |
| `failed` | `failed` | finished with an error it reported |
| `interrupted` | `interrupted` | the runner reported that it stopped partway; it can resume |
| `expired` | `expired` | the lease ran out with nothing reported, or the hub restarted mid-run |

`expired` and `interrupted` stay apart: one is a report from the runner, the other a timeout that says nothing about
what the run did. `leased`, `running` and `input` are live (`runs.LIVE`). A run with no attempt row yet has
`state: null`. The runner's own wire (`/api/v2/runners/...`, `/api/v2/attempts/...`, `contracts/runner`) keeps the
storage names.

### automation_runs.status (trigger)

One run of an automation.

Values (`RUN_STATUSES`):

- `queued`: waiting to run.
- `held`: queued but held by an active guard hold on the automation (`automation_guard_holds`).
- `running`: claimed by a worker.
- `waiting`: parked until `wake_at` (a wait step in the flow).
- `ok`: finished successfully.
- `failed`: finished with an error.
- `empty`: finished with nothing to do.
- `skipped`: not run, because the automation was paused or a newer run replaced it. `finished_at` is set.

Transitions (`backend/automations.py`):

- Insert `queued`.
- `queued` to `held` when a guard hold is placed; `held` to `queued` when it clears.
- `queued` or `held` to `skipped`.
- `queued` to `running` on claim.
- `running` to `waiting` when a wait step sets `wake_at`.
- `waiting` to `queued` or `held` when `wake_at` passes (`RUNNABLE` picks `held` if a guard hold is still active, else `queued`).
- `running` to `ok`, `failed` or `empty` when the flow finishes; any state to `failed` on a fatal error (`automations.py`).

Enforcement: trigger on insert and update of `status`. NOT NULL, no default.

### webhook_sends.state (trigger)

One delivery of one event to one endpoint.

Values (`WEBHOOK_SEND_STATES`):

- `ready`: waiting to be sent, or a retry is scheduled (`next_attempt_at`).
- `sending`: claimed by a delivery worker.
- `delivered`: the endpoint answered with a 2xx.
- `failed`: retries are used up, or the endpoint refused it for good.
- `canceled`: the endpoint asked not to be sent this message again, or the endpoint was deleted. Spelled `cancelled` before migration 0002.
- `withheld`: a rehearsal copy did not send it. Nothing is sent from a copy.

Transitions (`backend/webhook_delivery.py`, `backend/webhooks.py`):

- Insert `ready` (`webhooks.py` on each event).
- `ready` to `sending`: claimed.
- `sending` to `delivered`: 2xx.
- `sending` to `canceled`: the receiver's abort.
- `sending` to `failed`: retry budget used up (`retry_delay` returns nothing).
- `sending` to `ready`: a retry is scheduled, or the claim is released.
- `ready` to `withheld`: rehearsal withhold (`withhold`).
- `ready` or `sending` to `canceled`: endpoint deleted (`webhooks.py`).
- `failed`, `canceled` or `delivered` to `ready`: a person retries the send by hand (`webhooks.py` refuses `ready`, `sending` and `withheld`).

Enforcement: trigger on insert and update of `state`. NOT NULL, default `ready`.

### bots.state (check)

Values (`BOT_STATES`):

- `active`: running normally.
- `paused`: stopped by a person. Takes no new work.
- `planned`: created but not yet set up or activated.
- `quarantined`: stopped by the safety check for review. Only a person (or the lift rules in `hubdb.py` `status_set`) takes it out.
- `archived`: retired, kept for history.

Transitions:

- `planned` to `active`: setup finishes (`backend/agents.py`, `shared_bots.py`, `settings_admin.py`).
- `active` and `paused`: `app.py` pause and resume actions.
- `active` to `quarantined`: `hubdb.py` `quarantine`.
- `quarantined` to `active`: `hubdb.py` `status_set(state="active")` by a person, or by the keeper or BotOps under its rule.
- `active` or `paused` to `archived`: `shared_bots.py`, `settings_admin.py`.
- `archived` to the previous state: restore (`shared_bots.py`, `settings_admin.py`).

Enforcement: CHECK. Nullable, default `active`.

### bot_status.state (check)

What a bot is doing now. One live row per bot.

Values (`hubdb.STATUS_STATES`, which is `BOT_STATUS_STATES` in `status_values.py`):

- `idle`: no current work.
- `running`: working on a turn.
- `waiting_human`: waiting for a person.
- `waiting_bot`: waiting for another bot.
- `blocked`: cannot continue until a blocker is cleared.
- `limited`: a usage or quota limit; cools off (`execution.py`, `health.py`).
- `crashed`: the run failed unexpectedly. Listed; no writer found on this base beyond the list itself.
- `paused`: mirrors `bots.state` `paused`.
- `quarantined`: mirrors `bots.state` `quarantined`.

Transitions: every change goes through `hubdb.py` `status_set`, which copies the old row to `bot_status_history` and
writes the new one. `active` passed to `status_set` is stored as `idle` (it clears a quarantine; `bots.state` becomes
`active`). `settle_status(..., 'idle')` in `execution.py` returns a bot to `idle` after a stopped or reviewed run.
`limited` is set by `execution.py` on quota hits.

Enforcement: CHECK. Nullable, because the base schema allows it; every writer sets a value.

### bot_config.onboarding_state (check)

Values:

- `needs_setup`: a starter bot that has not finished its setup. Parked: it claims no work and answers only its setup.
- `onboarded`: setup finished.
- NULL: a bot with no setup state (not a starter).

Transitions: a starter bot is written `needs_setup` when created (`backend/onboarding.py`, `backend/recruiting.py`), and `onboarded` when its setup finishes (`onboarding.py`).

`needs_onboarding` is retired (see above); readers in `statuses.py` still accept it for one release.

Enforcement: CHECK with a retired mapping for `needs_onboarding`. Nullable.

### automation_versions.status (check)

Values: `active` (the version the automation runs), `pending` (saved, not yet live). Default `active`.

Transitions: a new edit writes a `pending` version (`backend/automations.py`); publishing it sets `active`.

### goals.status (check)

Values:

- `red`, `yellow`, `green`: a colour.
- `gray`: the automatic check has no reading to score. A goal with no status stays unscored rather than taking `gray`.
- `done`: a person marked it done.
- `dropped`: a person dropped it.

Transitions (`backend/goals.py`):

- A person sets any value (`status_source` becomes `person`), and clears `suggest_status`.
- The automatic check writes `red`, `yellow`, `green` or `gray` (`status_source` becomes `auto`). It never writes `done` or `dropped`.
- `status_restore` puts back the prior value, recorded in the goal's events.

Enforcement: CHECK. Nullable (NULL: never scored).

### goals.status_source (check)

Values: `person`, `auto`. Who last set `status`. Written with `status`. Nullable.

### goals.suggest_status (check)

Values: `red`, `yellow`, `green`. The automatic check's suggestion for a goal a person set. It is never set on an automatic goal.
Cleared when a person sets or accepts a status and when an automatic write lands (`goals.py`). Nullable.

### goal_proposals.status (check)

Values: `pending` (default, awaiting a decision), `confirmed` (accepted), `rejected` (declined).

Transitions: `pending` to `confirmed` or `rejected` only (`backend/goals.py`, `backend/company.py`). Not reversible.

### bot_files.state (check)

Values:

- `ready` (default): the current file.
- `not_synced`: the source changed, so the copy on a bot's computer is out of date (`backend/files.py`).
- `deleted`: an archived tombstone (`backend/file_library.py` sets `deleted` and `archived=1`).

Transitions: `ready` to `not_synced` (`files.py`); any to `deleted` (`file_library.py`). No writer back to `ready` was found
on this base after `not_synced`, so a re-synced file stays `not_synced` until it is replaced. Flagged in "Decisions".

Enforcement: CHECK. NOT NULL, default `ready`.

### approvals.decision (check)

Values: NULL (open, no decision yet), `approved`, `declined`, `withdrawn`.

Transitions (`backend/hubdb.py`):

- NULL to `approved` or `declined`: a person or a bot with the right decides (`decided_by`, `decided_via`).
- NULL to `withdrawn`: an undecided approval nobody needs any more, after the 14-day expiry or when its task ended.
- Any to NULL: reopen, which clears `decided_by` and `decided_at`.

Enforcement: CHECK (installed by this branch; the base schema had none). Nullable, because NULL is the open state.

### doc_sources.status (check)

Values:

- `link_only` (default): linked; nothing fetched.
- `queued`: the first fetch is waiting.
- `preparing`: the first fetch is running.
- `updating`: a refresh is running and a snapshot is still in use.
- `ready`: the current snapshot is in use.
- `empty`: fetched, with nothing usable.
- `failed`: the last fetch failed; `error_code` says why, and it is retried later.
- `access_needed`: the fetch failed for lack of access; the owner must grant it.

Transitions (`backend/doc_sources.py`): `link_only` to `queued` on request; `queued` to `preparing` when a worker starts;
`ready` or `updating` to `updating` on refresh; `preparing` to `ready` or `empty` on success; `updating` to `ready` on
success; any to `failed` or `access_needed` on error.

Enforcement: CHECK. NOT NULL, default `link_only`.

### model_subscriptions.status (check)

Values:

- `new`: saved, not yet checked.
- `working`: the last check passed.
- `needs_reconnect`: the check failed or the sign-in lapsed. Use is refused until the owner reconnects.

Transitions (`backend/model_subscriptions.py`): any to `new` when the key is saved again (`revision` goes up);
`new` to `working` or `needs_reconnect` from a check (`_status`); `needs_reconnect` blocks use until reconnected.

Enforcement: CHECK. NOT NULL, default `new`.

### strategy_actions.state (check)

Values: `proposed` (suggested, not yet acted on), `done` (carried out), `undone` (reversed).

Transitions (`backend/strategy_acts.py`): insert `proposed`; `proposed` to `done`; `done` to `undone` (an undo records `undone_at` and `undone_by`).

Enforcement: CHECK. NOT NULL.

### google_connects.status (check)

Values:

- `signing_in`: the sign-in started and waits for its callback. `oauth_state` holds the one-time nonce.
- `ready`: the callback arrived, the code is stored, and the connection waits to be finished.
- `finishing`: tokens are being exchanged and saved (a claim with a time limit).
- `connected`: working.
- `revoking`: the grant is being revoked at the provider (a claim).
- `disconnecting`: a local disconnect was asked for, waiting to be claimed.
- `disconnected`: removed.
- `failed`: the step failed.

Transitions (`backend/google_connect.py`): (none) to `signing_in` on start; `signing_in` to `ready` on callback (clears
`oauth_state`); `ready` to `finishing`; `finishing` to `connected` or `failed`; `connected` or `ready` to `disconnecting`;
`disconnecting` to `revoking`; `revoking` to `disconnected` or `failed`. A row stuck in `signing_in`, `ready`, `finishing`
or `revoking` past its time limit is reset by a sweep.

Enforcement: CHECK. NOT NULL, no default.

The column `google_connects.state` was renamed to `oauth_state` on this branch (the only column rename here). `ensure_schema`
renames it on an existing database and recreates its index as `google_connects_oauth_state`.

### fin_payments.status (check)

Values: `queued` (default), `sending`, `awaiting_bank_approval` (sent, the bank needs its own approval), `submitted`
(accepted by the bank), `settled` (money moved), `returned`, `failed` (refused or the send failed), `canceled` (stopped
before it moved; spelled `cancelled` before migration 0002), `unknown` (the send got no answer, so the outcome is not known
and reconciliation is pending). `unknown` is not `withheld`.

Transitions (`backend/finance/payments.py`, `backend/finance/store/banking.py`): `queued` to `sending` when the send
starts (`banking.py`); a send that gets no answer goes to `unknown` (`payments.py`); the bank's answer moves it on to
`submitted`, `awaiting_bank_approval`, `settled`, `returned` or `failed`; a rule, an owner or a bank rejection moves it to
`canceled`.

Enforcement: CHECK (the retired `cancelled` is mapped during the table copy). NOT NULL, default `queued`.

The OpenAPI v2 enum (`docs/openapi/v2.json`) uses the same list, so the public payment status changes from `cancelled` to `canceled`.

### fin_transfers.status (check)

Values: `queued` (default), `submitted` (sent to the bank, feed ids cleared), `unknown` (send got no answer; reconciliation
pending), `failed`, `posted` (in the books), `canceled` (spelled `cancelled` before migration 0002).

Transitions (`backend/finance/transfers.py`, `backend/finance/store/banking.py`): `queued` to `submitted` on send
(`banking.py`); the bank's answer or an error moves it to `posted`, `unknown` or `failed` (`transfers.py`); `unknown` is
retried or reconciled; a stopped transfer goes to `canceled`.

Enforcement: CHECK (as for payments). NOT NULL, default `queued`.

### task_steps.status (check, already in the schema)

Same list as `tasks.status`. Already constrained in the base schema, left as it was.

### meetings.review_state (check, already in the schema)

Values: `pending`, `live`, `dismissed`. Already constrained in the base schema, left as it was.

### bot_status_history.state (none)

The status a bot had before each change, copied from `bot_status` by `status_set`. Values are the `bot_status` list, so a
CHECK would refuse any older row written by an earlier version of the code. Left unconstrained on purpose. Flagged in "Decisions".

## Outside the stable set

Columns found by a scan of the fresh schema whose names mean status, state, decision, stage, outcome or review. These are
not in this branch's lists and are not changed here, except where a spelling is noted.

Already constrained by a CHECK in the base schema (values unchanged on this branch):

- `chat_goals.status`: active, paused, met, stopped, cleared.
- `live_meeting_chat_routes.status`, `live_meeting_windows.status`: deciding, complete (and interrupted for windows). **Still `complete`.** Not in the stable set, so not renamed; see "Decisions".
- `live_meeting_turns.status`: pending, claimed, replied, skipped.
- `live_meetings.state`: live, paused, ended.
- `meeting_items.status`: proposed, pushed, dismissed.
- `org_rules.status`: suggested, active, retired. `org_rule_holds.state`: held, sent, kept.
- `growth_channels.stage`, `growth_ideas.status`: the Growth module's stages and statuses. `growth_segments.status`: serving, testing, next, dropped.
- `fin_cards.status`: active, frozen, **cancelled**. Left as is (cards are not in the stable set).
- `fin_actions.status`, `fin_bills.status`, `fin_invoices.status`, `fin_entries.status`, `fin_holds.status`, `fin_outbox.status`
  (pending, sent, failed, withheld), `fin_feed_txns.status`, `fin_periods.status`, `fin_reconciliations.status`,
  `fin_recipients.status`, `fin_view_grants.status`, `fin_money_accounts.kind`, and others in the Finance schema.
- `strategy_bets.status`, `strategy_versions.status`, `tico_storage.state`, `storage_moves.state`,
  `update_archive_overrides.state`, `invite_sends.state`, `blob_media.media_state`, `bot_file_versions.media_state`.
- `task_links.review_state` and others with a CHECK in the base schema.

Without a CHECK (not constrained here; values are whatever the code writes):

- `tasks.checkpoint_state` (a fingerprint, not a status), `tasks.verdict`, `tasks.verdict_head`.
- `bot_tool_requests.state`, `credential_requests.status`, `credential_imports.state`, `bot_transitions.state`,
  `bot_transition_checkpoints.state`, `model_logins.state`, `oauth_grants.state`, `cloud_machines.state`, `computer_keys.state`,
  `seats.request_state`, `seat_requests.state`, `service_jobs.state`, `slack_events.state`, `slack_posts.state`,
  `worktree_waits.state`, `check_waits.state`, `calendar_actions.status`, `intake_items.status`, `support_tickets.status`,
  `rule_suggestions.status`, `runner_versions.update_state`, `runner_harness_actions.state`, `update_queue.state`,
  `mail_items.state`, `mail_items.status`, `market_*.status`, `memory_tidy_cards.status`, `inbound_inboxes.state`, `bills_inbound.state`,
  `learning_runs.status`, `lens_runs.status`, `listen_runs.status`, `flight_slow.status`, `redesign_migrations.state`,
  `task_trash.status`, `assistant_actions.status`, `automation_mail_actions.state`, `ask_ingest_items.outcome`,
  `batches.state`, `turns.outcome`, `schedule_occurrences.outcome`, `stale_suggestions.verdict`, `stall_triage.outcome`,
  `trigger_bodies.outcome`, `trigger_deliveries.outcome`, `webhook_attempts.status`, `org_rule_holds.reviewer`, `kpis.stage`
  (the funnel stage from `backend/growth.py`), `goal_events.status_source` and `goal_events.status_by` (event history).

Spellings still outside the stable set:

- `cancelled` in `fin_cards.status`, `assistant_actions.status`, `automation_mail_actions.state`, `slack_posts.state`,
  `bot_tool_requests.state`, `credential_requests.status`, `model_logins.state`, `service_jobs.state`, `worktree_waits.state`,
  `bot_transitions.state` (the lifecycle constant), `reconnects.ended`, and in the finance provider's own status strings and
  feed rows. Each means the same thing as `canceled`; none is changed.
- `complete` in `live_meeting_chat_routes.status` and `live_meeting_windows.status`. Same meaning as `completed`; not changed.

## Tables named in the plan without a status column on this base

- `principals`: no such table.
- `pages`, `humans`, `runners`, `cards`, `docs`, `task_types`, `automations`: no status or state column (`automations`
  keeps its state in `automation_versions.status` and `automation_runs.status`).
- `conversations.kind` (chat, ask, task, notice) and `messages.kind` (say, ask, answer, notice, steer): types, not statuses.
- `kpis.stage`, `goal_kpis.kind`, `kpi_readings`: the KPI tree's stage and kind; not statuses, not constrained here.
- `turns`: the unstable run table (`attempts` is the stable one).

## Decisions

These are open for the integration owner.

1. **`tasks.status` NULL.** The column is nullable and the trigger allows NULL, which matches the base schema. Refusing NULL would need a backfill of any NULL rows first.
2. **`attempts.state` `input`.** Listed, but no code path writes it. Keep it (harmless, matches the list the upgrade accepts) or drop it in a later change with a migration.
3. **`bot_status_history.state` unconstrained.** Old history rows may hold values from older code. A CHECK would make the upgrade refuse them.
4. **`bot_files.state`.** No code moves a file from `not_synced` back to `ready`. Confirm whether that is intended before a sync feature relies on it.
5. **`cancelled` and `complete` outside the stable set.** The same meanings are spelled both ways across roughly a dozen tables, and the live-meeting tables use `complete`. Unify in a follow-up, with a migration per table.
6. **`approvals.declined` and `goal_proposals.rejected`.** Kept apart on purpose (different records). Unify only if the product wants one word for "refused".
7. **Public API.** `docs/openapi/v2.json` changes the `cancelled` enum values for webhook sends, payments and transfers to `canceled`. Any client that matches the old spelling breaks.
8. **`needs_onboarding` readers.** `backend/statuses.py` still accepts it for one release. Remove that acceptance in a later release, after this migration has run everywhere.
9. **Removing a list value later.** Needs a migration that maps or refuses stored rows first (see "How the lists are enforced").

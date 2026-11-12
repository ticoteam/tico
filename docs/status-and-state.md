# Status and state columns

Every stored status or state column in the hub database: what each allowed value means, the transitions the code makes,
what guards them, and how the database enforces the list. The lists themselves live in `backend/status_values.py`,
which is what the database enforces. The value table below is generated from that file
(`python3 scripts/build_status_docs.py`), and `--check` fails when the table, or a column's bullets, drift from it.

A list only says which labels a column may hold. It does not say which change is allowed, or which run, worker or
sign-in owns the change: that is what the transitions, guards and the operation-identity table below are for. Schema
facts are from a fresh database built by `Store.initialize`. Transitions are from the code that writes each column
(`UPDATE <table> SET <column>=...`, `INSERT`, or the helper that calls it). File names are given without line numbers so
they stay greppable.

## Value lists

<!-- status-values:start -->
| Column | Enforced by | NULL | Values | Retired spelling (mapped to) |
|---|---|---|---|---|
| [`tasks.status`](#tasksstatus) | trigger | refused | `open`, `done`, `closed` |  |
| [`jobs.state`](#jobsstate) | trigger | refused | `queued`, `leased`, `running`, `input`, `completed`, `failed`, `uncertain`, `canceled` | `cancelled` (`canceled`), `done` (`completed`) |
| [`attempts.state`](#attemptsstate) | trigger | allowed | `leased`, `running`, `input`, `completed`, `failed`, `interrupted`, `expired` |  |
| [`automation_runs.status`](#automation_runsstatus) | trigger | refused | `queued`, `running`, `waiting`, `ok`, `failed`, `empty`, `skipped`, `held` |  |
| [`webhook_sends.state`](#webhook_sendsstate) | trigger | refused | `ready`, `sending`, `delivered`, `failed`, `canceled`, `withheld` | `cancelled` (`canceled`) |
| [`bots.state`](#botsstate) | check | allowed | `active`, `paused`, `planned`, `quarantined`, `archived` |  |
| [`bot_config.onboarding_state`](#bot_configonboarding_state) | check | allowed | `needs_setup`, `onboarded` | `needs_onboarding` (`needs_setup`) |
| [`bot_status.state`](#bot_statusstate) | check | allowed | `idle`, `running`, `waiting_human`, `waiting_bot`, `blocked`, `limited`, `crashed`, `paused`, `quarantined` |  |
| [`automation_versions.status`](#automation_versionsstatus) | check | refused | `active`, `pending` |  |
| [`goals.status`](#goalsstatus) | check | allowed | `red`, `yellow`, `green`, `gray`, `done`, `dropped` |  |
| [`goals.status_source`](#goalsstatus_source) | check | allowed | `person`, `auto` |  |
| [`goals.suggest_status`](#goalssuggest_status) | check | allowed | `red`, `yellow`, `green` |  |
| [`goal_proposals.status`](#goal_proposalsstatus) | check | refused | `pending`, `confirmed`, `rejected` |  |
| [`bot_files.state`](#bot_filesstate) | check | refused | `ready`, `deleted`, `not_synced` |  |
| [`doc_sources.status`](#doc_sourcesstatus) | check | refused | `link_only`, `queued`, `preparing`, `updating`, `ready`, `empty`, `failed`, `access_needed` |  |
| [`model_subscriptions.status`](#model_subscriptionsstatus) | check | refused | `new`, `working`, `needs_reconnect` |  |
| [`strategy_actions.state`](#strategy_actionsstate) | check | refused | `proposed`, `done`, `undone` |  |
| [`google_connects.status`](#google_connectsstatus) | check | refused | `signing_in`, `ready`, `finishing`, `revoking`, `disconnecting`, `connected`, `disconnected`, `failed` |  |
| [`google_workspaces.status`](#google_workspacesstatus) | check | refused | `pending`, `connected`, `failing` |  |
| [`fin_cards.status`](#fin_cardsstatus) | check | refused | `active`, `frozen`, `canceled` | `cancelled` (`canceled`) |
| [`fin_payments.status`](#fin_paymentsstatus) | check | refused | `queued`, `sending`, `awaiting_bank_approval`, `submitted`, `settled`, `returned`, `failed`, `canceled`, `unknown` | `cancelled` (`canceled`) |
| [`fin_transfers.status`](#fin_transfersstatus) | check | refused | `queued`, `submitted`, `unknown`, `failed`, `posted`, `canceled` | `cancelled` (`canceled`) |
<!-- status-values:end -->

## How the lists are enforced

Two kinds of enforcement, both refusing a new write outside the list:

- **trigger**: `BEFORE INSERT` and `BEFORE UPDATE OF <column>` triggers named `status_values_<table>_<column>_ins` and
  `_upd`. They `RAISE(ABORT, '<table>.<column>: invalid value')`. Used on the hot and growing tables (tasks, jobs,
  attempts, automation_runs, webhook_sends), where a CHECK would mean rebuilding a large table. They check new writes
  only; rows already stored are not read.
- **check**: a named table constraint `CONSTRAINT status_values_<column> CHECK (<column> IN (...))`, installed by
  rebuilding the small table. A rebuild renames the old table aside, copies every row (mapping retired spellings), checks
  the row count, then drops the old table. Foreign keys are off for the rebuild, so child rows are not cascaded.

Changing a list is a code change in `backend/status_values.py`. `Store.initialize` calls `enforce` on every start: it
replaces a trigger whose text differs and rebuilds a CHECK whose list differs. Adding a value needs nothing else.

### A stored value outside its list

Nothing stops the server for a value outside a list. Migration 0002 (`upgrade`) and every later start (`enforce`) do
this instead:

| Column kind | Rows that hold the value | New writes | What is logged |
|---|---|---|---|
| trigger | kept as they are | refused by the new trigger | a warning, `status values outside their lists, kept as they are`, naming `table.column = 'value' (n rows)` |
| check | kept as they are | the CHECK is **not** installed or rebuilt for that column, so the column keeps whatever it had (no CHECK, or the old list's) until its rows are fixed | the same warning, on every start until the rows are fixed |

So removing a value from a CHECK column's list while rows still hold it leaves that column enforcing the old list, and
says so in the log. Remediation: read the rows with `status_values.unknown_values(conn)` (or the warning), write a
migration that maps or refuses them, and the next start's `enforce` installs the CHECK. Tests:
`test_status_values.py::test_the_upgrade_keeps_values_it_cannot_map_logs_them_and_still_guards_new_writes`,
`::test_a_check_column_with_an_unknown_value_keeps_its_rows_and_gets_no_check` and
`::test_a_later_start_logs_a_check_column_it_cannot_enforce`.

## Retired spellings and the upgrade

Migration `backend/migrations/0002_status_values.py` runs `backend/status_values.py` `upgrade`, with foreign keys off
(`FOREIGN_KEYS_OFF`) so a CHECK rebuild does not cascade:

1. Counts every stored value outside the list (retired spellings are accepted, they are mapped next) and logs what it
   finds (see above). It never stops for them.
2. Rewrites retired spellings on trigger columns with `UPDATE`, and maps them on CHECK columns while the table is copied.
3. Installs the triggers, and the CHECKs for every column whose rows are all in its list.

The retired spellings are the last column of the value table. `cancelled` was also moved to `canceled` outside the
stable set by migration 0032: `assistant_actions.status`, `automation_mail_actions.state`, `bot_tool_requests.state`,
`bot_transitions.state`, `bot_transition_checkpoints.state`, `credential_requests.status`, `fin_cards.provider_status`,
`model_logins.state`, `seats.request_state` (with `cancelling` to `canceling`), `service_jobs.state`, `slack_posts.state`,
`task_reconnects.ended`, `worktree_waits.state`. `jobs.state` `done` is the spelling older hubs stored for a finished
job.

Readers of `bot_config.onboarding_state` still accept `needs_onboarding` for one release (`backend/statuses.py`).
New writes can only be `needs_setup` or `onboarded`.

On a fresh file the migration runs before the module schema code creates most tables, so `Store.initialize` calls
`enforce` last, on every start: a new database has the constraints and no retired spelling.

## Same word, different meanings (kept apart)

- `attempts.state` `expired` (the lease ran out, nothing reported) and `interrupted` (the runner reported it stopped
  partway, and it can resume). `execution.py` treats them differently.
- `tasks.status` `done` (finished work) and `closed` (will not be done, with a why).
- Finance `unknown` (a payment or transfer whose send got no answer; reconciliation pending) and `withheld`
  (`fin_outbox.status`: a rehearsal copy did not send). `withheld` is not in the payment or transfer lists.

## Which attempt owns a change

A state machine whose writes come from more than one run, worker, callback or sign-in needs more than its list: each
write must name the attempt that owns it (a token, a generation, a revision or a snapshot), so a late or reclaimed one
cannot settle what a newer one owns. Where that is missing, the fix adds the identity to the existing machine; it does
not add states.

| Machine | Owner (writer) | Guard: which attempt owns the change | Recovery | Example test |
|---|---|---|---|---|
| `jobs.state` / `attempts.state` | `execution.py` | the runner's assignment `generation` and the job's `attempt_id` | lease expiry; `restore` within `STALL_MAX`; a late result settles only when `settleable` or `settles_late`, otherwise it is filed | `test_execution_review.py::test_a_late_result_never_overrides_the_attempt_that_took_the_work_over` |
| `doc_sources.status` | `doc_sources.py` | the source's `generation` and the job's `lease` | a failed refresh keeps the last good snapshot | `test_doc_sources.py::test_failed_refresh_keeps_the_last_good_copy` |
| `google_workspaces.status` | `google_workspace.py` | the key revision a check started on | `tick` checks a pending or failing key again | `test_google_workspace.py::test_a_result_for_a_key_that_changed_meanwhile_is_dropped` |
| `bot_files.state` | `files.py` | none needed: each report is the file's latest, and the newest wins | the next successful publish writes `ready` | `test_files.py::test_a_file_that_failed_to_sync_is_ready_again_once_it_publishes` |
| `idempotency.state` bindings | `store.py` (`Store.mutate`), `invites.py`, `github_app.py` | one key per operation, `pending` while its outcome is unknown | a repeat answers the stored result, and is refused while pending | `test_idempotency_one_table.py` |

The state-machines audit (SM-01 to SM-09) found the machines below guarded by their state alone. Each row names what its
fix adds; its race test is the example to follow.

| Fix | Machine | Owner (writer) | Guard today | What the fix adds | Race test |
|---|---|---|---|---|---|
| SM-01 | statement upload preview and agent pairing (UI) | `ui/app` | none: the latest reply wins | a phase and a request revision; only the current revision may submit | pending (SM-01) |
| SM-02 | merge-queue act and undo | `source_actions.py` | the item's state | the undo checks the current visit's intent before it dequeues | pending (SM-02) |
| SM-03 | `google_connects.status` | `google_connect.py` | the person's row | an operation id per connect or disconnect, required at exchange and result | pending (SM-03) |
| SM-04 | `automation_runs.status`, `webhook_sends.state` | `automations.py`, `webhook_delivery.py` | the state (`running`, `sending`) | a claim token; a reclaimed worker cannot settle | pending (SM-04) |
| SM-05 | direct meeting-turn claims (`live_meeting_turns.status`) | `live_meetings.py` | the claim | a recoverable claim: returned on reconnect, or abandoned | pending (SM-05) |
| SM-07 | AI Gateway spending cap | `finance/gateway_sync.py` | the cap record | the key and destination the cap was set for | pending (SM-07) |
| SM-08 | desktop confirm before delete or revoke | desktop bridge, `ui/app` | none: the Promise reads as yes | an awaited `true` before every write | pending (SM-08) |
| SM-09 | GitHub PR facts | `item_sources/github.py` | `updated_at` | an equal-time conflict is reread before it is final; only merged is final | pending (SM-09) |

## Detail

### tasks.status

Trigger. NULL refused.

- `open`: in a stage of its pipeline. What it waits on is read, never stored (`backend/waiting_on.py`).
- `done`: it left the last stage. Only Done counts as output.
- `closed`: it will not be done, with a why.

Transitions: only the engine writes it (`backend/task_engine.py` `move`), inside the guard the `tasks_move_only` trigger
reads. A task is inserted `open` and entered in the first stage of its pipeline in the same transaction. Done, Close and
Reopen are moves. See [Tasks](tasks.md#done-closed-and-reopen) and [the spec](pipelines-v3.md#how-a-task-moves).

### jobs.state

Trigger. NOT NULL, default `queued`. One job per message a bot must answer.

- `queued`: waiting for a runner to claim it.
- `leased`: claimed by a runner for an attempt; the attempt holds a lease.
- `running`: the attempt is executing it.
- `input`: the attempt is waiting for a person's or another bot's answer (an ask).
- `completed`: answered.
- `failed`: the attempt failed.
- `uncertain`: the outcome cannot be known (the runner was lost, or the hub restarted mid-run). Looked at before any retry.
  A run whose harness process was killed or crashed resumes once by itself; a second in a row waits for its operator
  or BotOps. Until someone chooses, its task's wait line, Needs you and Health say it waits on a person, and **Run
  now** on the task (by its operator or BotOps) runs it again.
- `canceled`: stopped before it finished (a person stopped it, the task closed, a duplicate wake
  replaced it, or the owner's rule dropped it).

Transitions:

| From | To | Writer | Guard |
|---|---|---|---|
| (insert) | `queued` | delivery | |
| `queued` | `leased` | claim (`execution.py`) | |
| `leased` | `running` | the attempt starts (`execution.py`) | |
| `running`, `queued` | `input` | an ask is sent (`execution.py`) | |
| `input` | `running` | the answer arrives (`execution.py`) | |
| `leased`, `running`, `input` | `queued` or `uncertain` | lease expiry or an attempt that ends without a result: `queued` when nothing was done yet (`execution.py`) | |
| `running`, `input` | `uncertain` | hub restart (`backend/backup.py`) | |
| `leased` | `queued` | hub restart (`backend/backup.py`) | |
| `uncertain` | `running` | `restore`: the runner reports in within `STALL_MAX` | job still `uncertain` and on this attempt, no Stop, no review, lane free |
| open | the attempt's outcome, or `canceled` | the attempt ends (`execution.py`) | job still on this attempt |
| `uncertain`, `canceled`, `queued` | `completed` | a late result (`settleable`, `settles_late`) | see the operation-identity table |
| `queued` | `completed` | a person answers before any bot runs (`bot_status_routes.py`) | `AND state='queued'` |
| open | `canceled` | stop, task closed, duplicate wake, quota or wake presets (`app_delivery.py`, `delivery.py`, `demo.py`, `chat_goals.py`, `execution.py`) | |

### attempts.state

Trigger. NULL allowed. One attempt is one run of a job on a runner.

- `leased`: claimed, and the runner holds the lease.
- `running`: the runner has started.
- `input`: in the list, but no code path writes it on an attempt. The job goes to `input`, not the attempt. Kept for now; see "Decisions".
- `completed`: finished with a result.
- `failed`: finished with an error.
- `interrupted`: the runner reported that it stopped partway. It can resume (`execution.py` treats it as resumable).
- `expired`: the lease ran out with nothing reported, the runner handed back a lease it did not use, or the hub
  restarted mid-run. A timeout, not a report.

NULL is a run recorded with no lease: the demo seed, and the old `turns` rows migration
`0072_turns_into_attempts.py` moved onto `attempts`. These are history; no code moves them. Every leased run has a
state.

Transitions:

| From | To | Writer (`execution.py` unless named) | Guard |
|---|---|---|---|
| (insert) | `leased` | claim | |
| `leased` | `running` | start | a start reservation |
| `leased` | `expired` | `hand_back`: the runner returns an unused lease | |
| `leased` | `failed` | a start refused for good | the job still `leased` on this attempt |
| `leased`, `running` | `expired` | lease expiry; hub restart (`backup.py`) | |
| `running` | `completed`, `failed`, `interrupted` | the runner's result | the reporting runner holds the bot's current assignment (`current_owner`: runner and generation) |
| `expired` | `running` | `restore`: the runner renews within `STALL_MAX` | the job is still `uncertain` and on this attempt, no Stop, no cap, no review (`job_recovery`), no other live attempt in the lane |
| `expired` | `completed`, `failed`, `interrupted` | a late result that `settleable` or `settles_late` allows | as for `restore`, without the clock; `settles_late` only for a completed result on a job let go meanwhile |
| `expired` | (stays) | a late result that may not settle: `file_result` records its text and tokens on the attempt | |

`expired` is therefore not final: a lapsed lease is a guess about the runner, and the runner's own report can still win
while nothing newer owns the job. Tests: `test_execution_review.py` (`test_a_run_that_only_read_waits_out_a_lapsed_lease_for_the_runner_to_restore_it`,
`test_a_late_result_never_overrides_the_attempt_that_took_the_work_over`,
`test_a_late_result_that_did_not_finish_after_a_dismissal_is_only_filed`).

### Runs

API v2, the live events, webhooks, the MCP tools, the tico CLI and the web app call an attempt a run until Release 2
moves the table: a field is `run_id` (the attempt's id), an object is `run`, a list is `runs`. `run.state` is
`attempts.state` under the same name (`backend/runs.py`, `STATE`), one value for one value. `expired` and `interrupted`
stay apart: one is a timeout that says nothing about what the run did, the other a report from the runner. `leased`,
`running` and `input` are live (`runs.LIVE`). A run with no attempt row yet, or a NULL state, has `state: null`. The
runner's own wire (`/api/v2/runners/...`, `/api/v2/attempts/...`, `contracts/runner`) keeps the storage names.

### automation_runs.status

Trigger. NOT NULL, no default. One run of an automation.

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
- `running` to `ok`, `failed` or `empty` when the flow finishes; any state to `failed` on a fatal error.
- A `running` row whose worker stopped is reclaimed by recovery. SM-04: the settle that ends the run checks the state
  only, so the worker that lost the row can still settle it.

### webhook_sends.state

Trigger. NOT NULL, default `ready`. One delivery of one event to one endpoint.

- `ready`: waiting to be sent, or a retry is scheduled (`next_attempt_at`).
- `sending`: claimed by a delivery worker.
- `delivered`: the endpoint answered with a 2xx.
- `failed`: retries are used up, or the endpoint refused it for good.
- `canceled`: the endpoint asked not to be sent this message again, or the endpoint was deleted.
- `withheld`: a rehearsal copy did not send it. Nothing is sent from a copy.

Transitions (`backend/webhook_delivery.py`, `backend/webhooks.py`):

- Insert `ready` (`webhooks.py` on each event).
- `ready` to `sending`: claimed.
- `sending` to `delivered`: 2xx.
- `sending` to `canceled`: the receiver's abort.
- `sending` to `failed`: retry budget used up (`retry_delay` returns nothing).
- `sending` to `ready`: a retry is scheduled, or the claim is released or reclaimed. SM-04: as for automation runs, the
  settle checks the state only.
- `ready` to `withheld`: rehearsal withhold (`withhold`).
- `ready` or `sending` to `canceled`: endpoint deleted (`webhooks.py`).
- `failed`, `canceled` or `delivered` to `ready`: a person retries the send by hand (`webhooks.py` refuses `ready`, `sending` and `withheld`).

### bots.state

CHECK. NULL allowed, default `active`.

- `active`: running normally.
- `paused`: stopped by a person. Takes no new work.
- `planned`: created but not yet set up or activated.
- `quarantined`: stopped by the safety check for review. Only a person (or the lift rules in `hubdb.py` `status_set`) takes it out.
- `archived`: retired, kept for history.

Transitions:

- `planned` to `active`: setup finishes (`backend/agents.py`, `shared_bots.py`, `settings_admin.py`).
- `active` and `paused`: `app.py` pause and resume actions.
- `active` to `quarantined`: `hubdb.py` `quarantine`.
- `quarantined` to `active`: `hubdb.py` `status_set(state="active")` by a person, or by Tico's own upkeep or BotOps
  under its rule.
- `active` or `paused` to `archived`: `shared_bots.py`, `settings_admin.py`.
- `archived` to the previous state: restore (`shared_bots.py`, `settings_admin.py`).

### bot_status.state

CHECK. NULL allowed by the base schema; every writer sets a value. What a bot is doing now, one live row per bot
(`hubdb.STATUS_STATES`, which is `BOT_STATUS_STATES` in `status_values.py`).

- `idle`: no current work.
- `running`: working on a turn.
- `waiting_human`: waiting for a person.
- `waiting_bot`: waiting for another bot.
- `blocked`: cannot continue until a blocker is cleared.
- `limited`: a usage or quota limit; cools off (`execution.py`, `health.py`).
- `crashed`: the run failed unexpectedly. Listed; no writer found beyond the list itself.
- `paused`: mirrors `bots.state` `paused`.
- `quarantined`: mirrors `bots.state` `quarantined`.

Transitions: every change goes through `hubdb.py` `status_set`, which copies the old row to `bot_status_history` and
writes the new one. `active` passed to `status_set` is stored as `idle` (it clears a quarantine; `bots.state` becomes
`active`). `settle_status(..., 'idle')` in `execution.py` returns a bot to `idle` after a stopped or reviewed run.
`limited` is set by `execution.py` on quota hits.

### bot_config.onboarding_state

CHECK, with `needs_onboarding` mapped to `needs_setup`. NULL is a bot with no setup state (not a starter).

- `needs_setup`: a starter bot that has not finished its setup. Parked: it claims no work and answers only its setup.
- `onboarded`: setup finished.

Transitions: a starter bot is written `needs_setup` when created (`backend/onboarding.py`, `backend/recruiting.py`), and
`onboarded` when its setup finishes (`onboarding.py`).

### automation_versions.status

CHECK. NOT NULL, default `active`.

- `active`: the version the automation runs.
- `pending`: saved, not yet live.

Transitions: a new edit writes a `pending` version (`backend/automations.py`); publishing it sets `active`.

### goals.status

CHECK. NULL: never scored.

- `red`, `yellow`, `green`: a colour.
- `gray`: the automatic check has no reading to score. A goal with no status stays unscored rather than taking `gray`.
- `done`: a person marked it done.
- `dropped`: a person dropped it.

Transitions (`backend/goals.py`):

- A person sets any value (`status_source` becomes `person`), and clears `suggest_status`.
- The automatic check writes `red`, `yellow`, `green` or `gray` (`status_source` becomes `auto`). It never writes `done` or `dropped`.
- `status_restore` puts back the prior value, recorded in the goal's events.

### goals.status_source

CHECK. NULL allowed. Who last set `status`, written with it.

- `person`: a person set it.
- `auto`: the automatic check set it.

### goals.suggest_status

CHECK. NULL allowed. The automatic check's suggestion for a goal a person set; never set on an automatic goal.

- `red`, `yellow`, `green`: the suggested colour.

Cleared when a person sets or accepts a status and when an automatic write lands (`goals.py`).

### goal_proposals.status

CHECK. NOT NULL, default `pending`.

- `pending`: awaiting a decision.
- `confirmed`: accepted.
- `rejected`: declined.

Transitions: `pending` to `confirmed` or `rejected` only (`backend/goals.py`, `backend/company.py`). Not reversible.

### bot_files.state

CHECK. NOT NULL, default `ready`.

- `ready`: the file's current version (or link) is stored on Tico and opens.
- `not_synced`: the bot's runner could not upload the file's bytes. After its retries it reports the failure
  (`runner-rs/src/files_publish.rs` `report_not_synced`, an upload with `sync=failed`), so the file's page never shows a
  link that opens nothing. No version is added.
- `deleted`: an archived tombstone (`backend/file_library.py` sets `deleted` and `archived=1`).

Transitions:

| From | To | Writer | Guard |
|---|---|---|---|
| (insert), `ready` | `not_synced` | an upload with `sync=failed` (`files.py` `publish`) | |
| (insert), `not_synced`, `ready` | `ready` | any successful upload (`files.py` `add_version`), changed or not | |
| (insert), `not_synced`, `ready` | `ready` | a link is saved (`files.py`, `files/links`) | |
| any | `deleted` | delete (`file_library.py`) | |

Recovery from `not_synced` is the next successful publish of the same file, which the runner tries again on its own
next sync. Test: `test_files.py::test_a_file_that_failed_to_sync_is_ready_again_once_it_publishes`.

### doc_sources.status

CHECK. NOT NULL, default `link_only`.

- `link_only`: linked; nothing fetched.
- `queued`: the first fetch is waiting.
- `preparing`: the first fetch is running.
- `updating`: a refresh is running and a snapshot is still in use.
- `ready`: the current snapshot is in use.
- `empty`: fetched, with nothing usable.
- `failed`: the last fetch failed; `error_code` says why, and it is retried later.
- `access_needed`: the fetch failed for lack of access; the owner must grant it.

Transitions (`backend/doc_sources.py`): `link_only` to `queued` on request; `queued` to `preparing` when a worker starts;
`ready` or `updating` to `updating` on refresh; `preparing` to `ready` or `empty` on success; `updating` to `ready` on
success; any to `failed` or `access_needed` on error. A worker settles only for the `generation` and `lease` it took.

### model_subscriptions.status

CHECK. NOT NULL, default `new`.

- `new`: saved, not yet checked.
- `working`: the last check passed.
- `needs_reconnect`: the check failed or the sign-in lapsed. Use is refused until the owner reconnects.

Transitions (`backend/model_subscriptions.py`): any to `new` when the key is saved again (`revision` goes up);
`new` to `working` or `needs_reconnect` from a check (`_status`); `needs_reconnect` blocks use until reconnected.

### strategy_actions.state

CHECK. NOT NULL.

- `proposed`: suggested, not yet acted on.
- `done`: carried out.
- `undone`: reversed (an undo records `undone_at` and `undone_by`).

Transitions (`backend/strategy_acts.py`): insert `proposed`; `proposed` to `done`; `done` to `undone`.

### google_connects.status

CHECK. NOT NULL, no default.

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
or `revoking` past its time limit is reset by a sweep. Each start (connect or disconnect) sets a new `operation_id` and
clears the pending grant; the claim, `/exchange` and `/result` name that operation, and every write after the claim is
conditional on it, so an answer for an earlier operation changes nothing. `answered` records the computer's result for
the current operation, and a repeat of it is answered the same way.

The column `google_connects.state` was renamed to `oauth_state`. `ensure_schema` renames it on an existing database and
recreates its index as `google_connects_oauth_state`.

### google_workspaces.status

CHECK. NOT NULL. The company's Google Workspace service-account key.

- `pending`: an uploaded key whose first check has not passed. The active key keeps working meanwhile.
- `connected`: the key's last check, or the last token a computer asked for, worked.
- `failing`: a check or a computer's token failed; `last_error` says why.

Transitions (`backend/google_workspace.py`): an upload writes `pending` and deletes any older pending key
(`_store_pending`); a passing check promotes it to `connected` and deletes the key it replaces (`_settle`); a failing
check or token turns it `failing` (`_settle`, `record_mint`); a later success turns it `connected` again. `tick` checks a
pending or failing key again for a while. A result is recorded only for the key revision it started on.

### fin_cards.status

CHECK, with `cancelled` mapped. NOT NULL.

- `active`: usable.
- `frozen`: paused at the provider.
- `canceled`: closed for good.

### fin_payments.status

CHECK, with `cancelled` mapped. NOT NULL, default `queued`.

- `queued`: waiting to be sent.
- `sending`: the send started.
- `awaiting_bank_approval`: sent; the bank needs its own approval.
- `submitted`: accepted by the bank.
- `settled`: money moved.
- `returned`: the money came back.
- `failed`: refused, or the send failed.
- `canceled`: stopped before it moved.
- `unknown`: the send got no answer, so the outcome is not known and reconciliation is pending. Not `withheld`.

Transitions (`backend/finance/payments.py`, `backend/finance/store/banking.py`): `queued` to `sending` when the send
starts (`banking.py`); a send that gets no answer goes to `unknown` (`payments.py`); the bank's answer moves it on to
`submitted`, `awaiting_bank_approval`, `settled`, `returned` or `failed`; a rule, an owner or a bank rejection moves it to
`canceled`. The OpenAPI v2 enum uses the same list.

### fin_transfers.status

CHECK, with `cancelled` mapped. NOT NULL, default `queued`.

- `queued`: waiting to be sent.
- `submitted`: sent to the bank, feed ids cleared.
- `unknown`: the send got no answer; reconciliation pending.
- `failed`: refused, or the send failed.
- `posted`: in the books.
- `canceled`: stopped before it moved.

Transitions (`backend/finance/transfers.py`, `backend/finance/store/banking.py`): `queued` to `submitted` on send
(`banking.py`); the bank's answer or an error moves it to `posted`, `unknown` or `failed` (`transfers.py`); `unknown` is
retried or reconciled; a stopped transfer goes to `canceled`.

## Outside the stable set

`meetings.review_state` (`pending`, `live`, `dismissed`) has a CHECK in the base schema, not from `status_values.py`.
`bot_status_history.state` is left unconstrained on purpose: it copies `bot_status` rows, and a CHECK would refuse an
older row written by an earlier version of the code.

Columns found by a scan of the fresh schema whose names mean status, state, decision, stage, outcome or review, and
that `status_values.py` does not list:

Already constrained by a CHECK in the base schema:

- `chat_goals.status`: active, paused, met, stopped, cleared.
- `live_meeting_chat_routes.status`, `live_meeting_windows.status`: deciding, complete (and interrupted for windows). **Still `complete`**; see "Decisions".
- `live_meeting_turns.status`: pending, claimed, replied, skipped.
- `live_meetings.state`: live, paused, ended.
- `meeting_items.status`: proposed, pushed, dismissed.
- `org_rules.status`: suggested, active, retired. `org_rule_holds.state`: held, sent, kept.
- `growth_channels.stage`: the Growth module's channel stages.
- `fin_actions.status`, `fin_bills.status`, `fin_invoices.status`, `fin_entries.status`, `fin_holds.status`, `fin_outbox.status`
  (pending, sent, failed, withheld), `fin_feed_txns.status`, `fin_periods.status`, `fin_reconciliations.status`,
  `fin_recipients.status`, `fin_view_grants.status`, `fin_money_accounts.kind`, and others in the Finance schema.
- `strategy_bets.status`, `strategy_versions.status`, `tico_storage.state`, `storage_moves.state`,
  `update_archive_overrides.state`, `blob_media.media_state`, `bot_file_versions.media_state`.
- `idempotency.state`: pending, completed, refused. A stored answer is always completed and is swept after
  48 hours. An operation binding (`binding` set: `invites`, `github_product_repo`, `github_review`) is never swept: it is
  pending while its outcome is unknown (a repeat is refused with no second external call), then completed or refused.
  Invites map `sending` to pending, `sent` to completed and `refused` to refused; a product repository's `rejected` is
  refused; a review with no response yet is pending. An access invite's binding keeps the time it recorded as
  `response` {"invited_at"}, so a repeat of that request answers its own time.
- `task_links.review_state` and others with a CHECK in the base schema.

Without a CHECK (values are whatever the code writes):

- `tasks.checkpoint_state` (a fingerprint, not a status), `tasks.verdict`, `tasks.verdict_head`.
- `bot_tool_requests.state`, `credential_requests.status`, `credential_imports.state`, `bot_transitions.state`,
  `bot_transition_checkpoints.state`, `model_logins.state`, `oauth_grants.state`, `cloud_machines.state`, `computer_keys.state`,
  `seats.request_state`, `seat_requests.state`, `service_jobs.state`, `slack_events.state`, `slack_posts.state`,
  `worktree_waits.state`, `check_waits.state`, `calendar_actions.status`, `listening_items.status`, `support_tickets.status`,
  `rule_suggestions.status`, `runner_versions.update_state`, `runner_harness_actions.state`, `update_queue.state`,
  `mail_items.state`, `mail_items.status`, `market_*.status`, `inbound_inboxes.state`, `bills_inbound.state`,
  `learning_runs.status`, `lens_runs.status`, `listening_sources.status`, `flight_slow.status`, `redesign_migrations.state`,
  `task_trash.status`, `assistant_actions.status`, `automation_mail_actions.state`, `ask_ingest_items.outcome`,
  `batches.state`, `attempts.outcome`, `schedule_occurrences.outcome`, `stale_suggestions.verdict`, `stall_triage.outcome`,
  `trigger_bodies.outcome`, `trigger_deliveries.outcome`, `webhook_attempts.status`, `org_rule_holds.reviewer`, `kpis.stage`
  (the funnel stage from `backend/growth.py`), `status_source` and `status_by` in a goal's `history_events.data` (event history).

Spellings still outside the stable set:

- `cancelled` in `fin_feed_txns.provider_status` and Mercury's other status strings and feed rows: Mercury's spelling, kept as
  it sent them. Tico's own lists are all `canceled` since migration 0032.
- `complete` in `live_meeting_chat_routes.status` and `live_meeting_windows.status`. Same meaning as `completed`; not changed.

Tables without a status column: `principals` (its `kind` is a type, enforced by a CHECK), `pages`, `humans`, `runners`,
`cards`, `docs`, `automations` (its state is in `automation_versions.status` and `automation_runs.status`).
`conversations.kind`, `messages.kind`, `kpis.stage`, `goal_kpis.kind` and `kpi_readings` are types, not statuses.

## Decisions

These are open for the integration owner.

1. **`attempts.state` `input`.** Listed, but no code path writes it. Keep it (harmless) or drop it in a later change
   with a migration.
2. **`bot_status_history.state` unconstrained.** Old history rows may hold values from older code.
3. **`complete` outside the stable set.** The live-meeting tables use `complete` where other lists say `completed`.
   Unify in a follow-up, with a migration.
4. **`needs_onboarding` readers.** `backend/statuses.py` still accepts it for one release. Remove that acceptance in a
   later release, after this migration has run everywhere.
5. **Removing a list value later.** Needs a migration that maps or refuses stored rows first (see "A stored value outside
   its list").

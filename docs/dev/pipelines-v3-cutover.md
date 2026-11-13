# Pipelines v3: the cut-over

How the old task engine is replaced. Read with [pipelines-v3.md](../pipelines-v3.md) (the spec) and
[pipelines-v3-build.md](pipelines-v3-build.md) (the engine). Where this file and the build file disagree about the
cut-over, this file wins.

## Decided

- **No migration.** No task is carried over, and none is deleted in place. A database that still has tasks was made
  by 0.5: 0.6 does not open it, and says so with nothing changed, so it still runs on 0.5. Every company starts over on
  a new database. No backfill, no status mapping.
- **One branch**, `chris/pipelines-v3-cutover`. It is not green until the end. It merges to main once, through the queue.
- A track works in its own worktree and branch, cut from the cut-over branch. It edits **only the files it owns** (the
  table at the end). It needs a change in a file it does not own: it says so in its report and does not make it.
- Each track keeps the tests of its own files green and deletes tests whose subject is gone. It does not port a test
  of deleted behaviour.
- 0.6.0-rc.1 has one pipeline, General. Product and Engineering come after (spec phases 2 and 3). Code that only
  serves the old Engineering pipeline is deleted now, not kept for later.
- The public roadmap is off in 0.6.0-rc.1: it was built on task types and steps. It comes back with the Product
  pipeline, on its stages.
- Growth ideas are General tasks with labels in 0.6.0-rc.1. A Growth pipeline comes with Product in phase 2, as a third
  preset: Ideas (by hand), On deck, Running, Measuring. A result question (Succeeded, Failed, Inconclusive, with one
  line of evidence) must be answered to leave Measuring. A person OKs an idea before Running only when it spends
  money or publishes. A dropped idea is Closed.

## What a task is after the cut-over

- `tasks.status` is `open`, `done` or `closed`. Nothing else.
- Every task has `pipeline_id`, `stage_id` and exactly one open visit. A task is made by `task_create`, which puts it
  in the first stage of its pipeline (General when none is named) in the same transaction.
- `owner_id` is who has the task now. `assignee_id` is who it was given to. Only the engine writes status, stage,
  owner and pipeline.
- What it waits on is read, never stored: `waiting_on`.
- A task's parent is the task that filed it. It is set when the task is filed (the engine's `links`) and never changed.
  `tasks.parent_id` is written once, at create, and always equals the link. Changing a parent is gone.

What the old words become:

| Old | Now |
|---|---|
| doing | open, with a live run |
| waiting, blocked, hold | open; `waiting_on` says on what |
| review, ready | a stage, or "everything is met" |
| declined | closed, with a why |
| type, step | pipeline, stage |
| question task, approval object | a requirement that names a person (`answer` or `ok`) |
| check back, stale, stuck, stranded, loop | the never-stuck check |

## The Python calls

Everything outside the engine uses these and nothing else. They live in `backend/hubdb.py` (`H.`) so today's call sites
keep their names. `ver` below is `seen_version=None`.

```
H.task_create(conn, actor, title, body, owner, due=None, parent_id=None, *, pipeline=None, link="sub_task", ...)
    # Makes the row and enters it, in one savepoint: a refused enter leaves no row.
    # `owner` becomes the assignee. `pipeline` is an id; None is General.
    # Gone: type, step, status, mover-only landing, enter_gate, next_run, quiet_notice, review_prs, change_paths, qa,
    # qa_not_checkable_live, receipt_owner, check_back_at, check_back_note. A task for a bot always wakes it.
H.task_done(conn, actor, task_id, note="", ver)
    # "The work of this stage is finished." It records completion and never pushes past anything.
    # Where the stage asks "owner says done" and it is not met: the owner, the pipeline's manager, a person or SYSTEM
    # answers it, and the engine moves the task on when everything else is met (in the last stage, to Done).
    # Otherwise (the stage does not ask for it, or it is met): a forward move with no reason, which is refused with
    # what is missing while anything is. To go on anyway is task_move(NEXT, why=...), said as such.
H.task_move(conn, actor, task_id, to, why=None, ver)
    # `to` is a stage id or name, or task_engine.NEXT, DONE, CLOSED, REOPEN. A bot does not reopen what a person closed.
H.task_close(conn, actor, task_id, note="", ver)                  # task_move(CLOSED, why=note)
H.task_reassign(conn, actor, task_id, owner, why=None, ver)
H.task_update(conn, actor, task_id, *, title, body, due, labels, rank, private, goal, note, ..., ver)
    # Field edits only. For the mass of old call sites it still takes status= ("done" -> task_done, "closed" ->
    # task_close, "open" on an ended task -> task_move(REOPEN)) and owner= (-> task_reassign). Any other status is a
    # ValueError. Gone: step, type, lane, mover, strict, waiting_on, skip_step_wake, override_requirements.
H.task_ask(conn, actor, task_id, person, line, *, kind="answer", choices=None, shows=None, ver)
H.task_withdraw_ask(conn, actor, requirement_id, why=None)
H.task_answer(conn, actor, task_id, requirement_id, value, note=None, ver)
H.task_give(conn, actor, task_id, requirement_id, person, ver)
H.task_answer_undo(conn, actor, answer_id)
H.task_thumbs(conn, actor, task_id, requirement_id, up, note=None)
H.task_snooze(conn, actor, task_id, why=None, until=None, ver)
H.task_unsnooze(conn, actor, task_id, why=None)
H.task_try_again(conn, actor, task_id, why=None)
H.attempt(conn, call, *args, **kwargs) -> row | None
    # For code that acts for nobody and may be refused. Runs the call in a savepoint. A refusal rolls back that call
    # alone and returns None; the caller's other writes stand. This replaces try_auto_move and strict=False.
H.task(conn, task_id)                    # the row, as today, without the dead columns
H.task_view(conn, task_id, viewer=None)  # the engine's fields under "The task as JSON", for one viewer
H.task_waits(conn, task_ids)             # {task_id: [wait, ...]}, one batched read
```

The calls that work a task also take `mover=`: a route saying this person may move any task (the company's owner).
They live in `backend/task_calls.py`; hubdb re-exports them.

Rules for callers:

- **Who may.** Every call: the actor is on the roster and may read the task. A call that works the task (done, move,
  close, reassign, ask, snooze, try again) also runs the check a field edit runs today, plus the pipeline's manager
  and owner. An answer, a give, an undo and a thumbs are for whoever the requirement is asked of: the engine checks
  that, so someone named on an OK answers it without being on the task. The engine then applies its own rules, C8
  among them: a bot cannot close a task a person asked for; it asks that person.
- **Version.** `tasks.version` says "this row changed since you looked", nothing more. The engine bumps it on a move
  and a reassign; `task_update` bumps it once for a field edit. Nothing else writes it, and it is not progress: the
  never-stuck check does not read it.
  - A bot's own command carries the version the bot was shown. Without one it is refused (`task_changed`).
  - Code that acts for a bot inside one transaction passes the version it just read.
  - A person may leave it out of everything but a field edit.
- Code that acts for nobody uses `hubdb_core.KEEPER` as the actor (import it as `SYSTEM`; never write the word keeper
  in new code or copy) and always gives a why. When it moves a task because of what someone did (the owner said done),
  the effects below are attributed to that someone, not to SYSTEM.
- A refusal is `H.Refused` with a `task_*` rule and `extra` (`missing`, `next`, `version`). There is no `strict=`.

## What a move still does

The engine calls `backend/task_effects.py` (new) at the end of every move and reassign, inside the transaction. No
caller does these, and none may be skipped:

| On | Effect |
|---|---|
| any move | one `history_events` row; cancel wakes queued for bots that no longer have the turn; wake the new owner |
| a new stage | `task.step_changed` to the task trigger rules, the outgoing webhooks and the automations |
| done, closed, reopened | `task.done`, `task.closed`, `task.reopened` to the same three; `reconnects.moved` |
| done, closed, reopened | `rule_due.on_task_status`, with the actor whose act caused it |
| done, closed | `RT.turn_result` (the run view) |
| done, closed | `delivery.task_closed` (deferred deliveries for the task are dropped) |
| done, closed | a bot that asked for the task is woken once with the result, whether or not it filed it from a task |
| done | the task that filed it is told (`linked_done` may now be met) |
| closed | close its open sub-tasks (`cascade`); stop work still running on it (`stop_ended_work`) |
| reassign, before | the checks today's owner change runs: may this owner take tasks, same department for a task manager, `strategy_acts.gate` |
| reassign, after | `task.assigned` to the same three; `reconnects.moved`; the new owner's private default (and every task under it); drop temporary delegations; demote the old owner's sessions; move the worktree link; `task_run_models.moved`; `task_followers.owner_moved`; clear the task goal; rank at the end of the new owner's queue; `reconnects.access_changed` |

Done does not finish sub-tasks. No chat, email or push goes to a person about a task: a person learns from Needs you.

Two more Paused causes join `task_wakes`: a sign-in the bot needs is missing (`reconnects`), and the task's worktree is
not ready (`worktree_waits`, for that task only). Each stops the tries and shows with its fix.

## HTTP

| Route | Body | Does |
|---|---|---|
| `POST /api/v2/tasks` | `title, body, owner, pipeline?, parent?, due?, labels?, private?` | create |
| `GET /api/v2/tasks/{id}` | | the task, full |
| `GET /api/v2/tasks` | | brief rows |
| `POST /api/v2/tasks/{id}` | `version` + fields | field edits only |
| `POST /api/v2/tasks/{id}/done` | `note?, version` | the work of this stage is finished |
| `POST /api/v2/tasks/{id}/move` | `to, why?, version` | forward, push past, back, done, close, reopen |
| `POST /api/v2/tasks/{id}/owner` | `owner, why?, version` | reassign |
| `POST /api/v2/tasks/{id}/snooze` | `until?, why?, version` | |
| `POST /api/v2/tasks/{id}/unsnooze` | `why?` | |
| `POST /api/v2/tasks/{id}/try-again` | `why?` | a person hands a task back to its owner, in their own session: the Assistant, BotOps and tokens get `task_needs_person` |
| `POST /api/v2/tasks/{id}/asks` | `to, line, kind?, choices?, show?, version` | ask a person (C19) |
| `DELETE /api/v2/tasks/{id}/asks/{requirement_id}` | `why?` | withdraw |
| `POST /api/v2/tasks/{id}/asks/{requirement_id}/answer` | `value, note?, version` | OK, send back (`no` with a note), look again, or an answer |
| `POST /api/v2/tasks/{id}/asks/{requirement_id}/give` | `to, version` | give it to another person |
| `POST /api/v2/tasks/{id}/asks/{requirement_id}/thumbs` | `up, note?` | was this worth asking |
| `POST /api/v2/answers/{id}/undo` | | |
| `POST /api/v2/task-bulk` | `ids, action: move / owner / close / tags, ...` | people only |
| `GET /api/v2/needs-you` | | see below |
| `GET /api/v2/pipelines`, `GET/POST /api/v2/pipelines/{id}`, `POST /api/v2/pipelines/{id}/retire` | the settings record | `pipeline_settings` |

`to` is `next`, `done`, `closed`, `reopen`, or a stage id or name. A requirement of the stage is answered through the
same `asks/{requirement_id}` routes as one added to the task.

A refusal is 409 with `{"error": <rule>, "detail": <one line>, "missing": [<requirement line>, ...], "next": [<command>,
...], "version": <current>}`. `missing` and `next` are there for `task_not_met`, `task_no_push_past`,
`task_needs_person` and `task_stage_full`; `version` for `task_changed`. `not-found` is 404. The store's replay of a
write carries the same fields.

**Needs you** (`GET /api/v2/needs-you`) is one list for a person, made of:

- `waiting_on.needs(person)`: every requirement with their name, every task they own, every Paused cause they can fix;
- every @ of them by a person, on a task or in a channel, until they reply (C28). A bot's @ only marks the task unread.
  A task never waits on an @.

Each item has `kind` (`task`, `ask`, `paused`, `mention`), the brief task, and for an ask the fields of `to_leave`
below. An ask's `id` is `<visit>:<requirement>`: a stage's requirement is on every task in the stage, so each task's
ask is its own item. `requirement_id` answers it.

Routes that go: approvals (all), task questions, check-back, later/defer, qa-waiver, waits, change parent, pipeline
review actions, stage-requirement routes, task types, stuck, flow, and `status`, `step`, `type`, `owner`, `waiting_on`,
`close`, `override_requirements` on `POST /api/v2/tasks/{id}`.

## The task as JSON

Full (`GET /api/v2/tasks/{id}`): today's plain fields (id, number, title, body, due, labels, private, parent,
requester, created, updated) with what `H.task_view` returns laid over them:

```
status          "open" | "done" | "closed"
version         int
pipeline        {"id", "name"}
stage           {"id", "name", "kind": "work" | "done" | "closed", "by_hand": bool}
owner_id        actor | null            who has it now (the route shows it as it shows any actor)
assignee_id     actor | null
turn            the headline wait: {"state", "who", "line", "since", "until", "requirement_id", "ask"} | null
waits           [wait, ...]             every wait, headline first
to_leave        [requirement, ...]      every requirement of the stage and every one added to this visit
next            {"id", "name"} | null   the stage a forward move goes to
late            null | {"since", "level": "told" | "person"}
snooze          null | {"until", "by", "why"}
filed_by        task id | null
filed           [{"id", "kind", "status", "title"}, ...]    title is null for a task the viewer may not read
```

A requirement in `to_leave`:

```
requirement_id, kind, line, met: bool
standing        one sentence: how it stands ("OK'd", "Sent back: ...", "No task filed yet")
who             actor or task id | null   who or what it waits on
asked_by        actor | null              set when it was added to this task
choices         [str, ...] | null
shows           [ref, ...]                what to look at ("page:12", "file:...")
answer          null | {"id", "value", "note", "by", "at"}
may             ["done", "ok", "no", "again", "answer", "give", "undo", "withdraw", "thumbs"]   for this viewer; a hint
```

Brief (`GET /api/v2/tasks`, lists, Needs you): `status, version, pipeline_id, stage_id, owner, assignee, turn, late,
snoozed, stage_since` (when it entered its stage: its open visit, else when it was filed; a board card's age, task
#220). `turn.state` is one of the nine in `waiting_on.STATES`. The fields `type_id, step_id, step, step_rank,
waiting_on, blocked, blocked_reason, check_back_at, hold` are gone from both. A list builds `turn` for all its rows
with one `H.task_waits(ids)`, never one read per row. A snoozed `turn.line` is clipped to 200 UTF-8 bytes in a
brief row; the task's own read keeps the snooze's why whole.

## Bot commands

As spec S16. MCP tools mirror them one for one (`tico task move` is `tico_task_move`).

```
tico task show <id>
tico task done <id> -m "what was done" --version N
tico task move <id> next|<stage> -m "why" --version N
tico task close <id> -m "why" --version N
tico task reassign <id> <owner> -m "why" --version N
tico task ask <id> --to <person> "line" [--choices a,b] [--show page:12] --version N
tico task ask <id> --withdraw
tico task answer <id> <requirement> ok|no|<answer> [-m "note"] --version N      (a bot named on an OK or a question)
tico task give <id> <requirement> --to <person> --version N
tico task file --parent <id> [--pipeline general] "title"
tico task snooze <id> [--until date] -m "why" --version N
tico task create, list, comment, label, link, follow        (kept; no --status, --step, --type)
```

- `--version` is the version the bot was last shown: `show`, a list row and every wake print it ("version 12"). The
  tools and MCP are stateless: the bot passes it. A tool never fetches a version just before it posts. Without one:
  "Add --version N: the version you were last shown (`tico task show <id>` prints it, as do a list row and your wake)."
  The one exception is `label`: it only adds or takes off the labels named, so it reads the labels and, given no
  version, writes at the version it read (a change in between is refused, never lost).
- `show` prints: version; pipeline · stage · owner; Turn; To leave (each requirement, met or not); Next; Filed by.
- A refusal prints what is missing and the commands that are allowed next.
- Gone: `update --status/--step/--waiting-on`, `bulk`, `check-back`, `later`, `unsnooze` for bots, `waive-qa`, `wait`,
  `question`, every `approval` command, `pipeline` review commands, task types. Skills, templates and prompt text that
  name them change in the same track.

## The database change

One migration, `20261110054000_pipelines_v3_cutover.py`, with `FOREIGN_KEYS_OFF = True`:

1. A database with any task is refused: the migration stops, nothing is changed, and the message says to run 0.5 on it
   or set up a new company.
2. `tasks` is made again without the dead columns and their foreign keys: `type_id`, `step_id`, `step_rank`,
   `check_back_at`, `check_back_note`, `questions`, `question_topic`, `if_no_answer`, `review`, `spend`, `answer`,
   `asked_at`, `next_run`, `carried_by`, `resume`, `verdict`, `verdict_head`, `building_started_at`,
   `building_seconds`, `blocker_key`, `stale_asked_at`. Its indexes and triggers go with the old table; the start-up
   code makes them again, so any that names a dead column or an old status must be changed by its owner.
3. `tasks_move_only` loses its first `WHEN` line, so it holds for every task. `tasks_enter_only` is its `BEFORE INSERT`
   twin: a row may be inserted only with `pipeline_id` and `stage_id` NULL and `status = 'open'`.
4. The old tables are dropped: `task_types`, `task_steps`, `stage_requirements`, `stage_requirement_versions`,
   `stage_visits`, `stage_decisions`, `task_holds`, `approvals`, `pipeline_links`, `pipeline_members`,
   `pipeline_handoffs`, `pipeline_check_runs`, `pipeline_check_cursor`, `pipeline_manager_actions`,
   `pipeline_manager_runs`, `review_claims`, `requirement_attachments`, `stuck_alarms`, `stuck_spells`,
   `task_check_back_wakes`, `task_milestone_waits`, `milestones`, `check_waits`, `stale_judged`, `stale_suggestions`.
5. The trash is emptied.

Every file that still names a dropped column or table is listed, with line numbers, in the session scratchpad file
`dead_columns.md`. A statement that names one fails at run time; a trigger or index that names one fails at start.

A next-run task is gone with its columns: a task for a bot wakes it.

Schema code that runs after the migrations must not bring the old tables back. hubdb's startup column adds for
`approvals` (and any other dropped table) go. A cloud step in `Store.initialize` that creates or alters a dropped
table, or gives `tasks` a foreign key to one (step 48 among them), is skipped. One test starts a fresh database, one
at today's main and one some releases behind, each through the whole of `Store.initialize`, and checks: none of the
dropped tables exists, `tasks` has no dead column and no foreign key to one, a second start changes nothing. A
database with a task is refused untouched.

After migrations, every start calls `pipeline_settings.ensure_presets`, which makes General with its requirement when
it is missing. The test snapshot is taken after it.

An applied migration's file is never edited (its checksum is recorded). A shipped migration that imports a deleted
module goes in `migrations.SUPERSEDED`, and the runner skips a superseded file **before** importing it. Known:
`20261107140000_blocked_reason_recompute` (imports `task_holds`) and `20261107210000_factory_output_stage` (imports
`pipeline_setup`). A test imports every migration with the deleted modules gone.

The never-stuck check's `rules_hold` gains: no task without a pipeline, a stage and one open visit.

The trash: delete takes the task off as today and closes its open snooze. Restore, in order: insert the bare row
(`status = 'open'`, no pipeline, no stage), restore its conversation and backlinks, then `task_engine.restore` places
it with a fresh visit and a history note: an open task in the stage it was in while that stage is still in its
pipeline, has room and may take it (`shut_out`), else in the first stage; a done or closed one as it ended. A private
task neither stage may take is refused (`task_not_shared`) and stays in the trash. Old asks and wakes are not
replayed.

## What is deleted

Modules (spec "What gets deleted"): `pipeline_engine`, `pipeline_review`, `pipeline_links`, `check_pools`,
`verdict_moves`, `step_flow`, `review_gate`, `merge_arm`, `release_flow`, `move_retry`, `roadmap_flow`, `stuck_cards`,
`step_age`, `step_backlog`, `task_blocked`, `task_holds`, `task_check_backs`, `stale_review`, `stranded_resume`,
`task_loops`, `task_allocator`, `auto_tasks`, `task_roundup`, `milestone_waits`, `question_tasks`, `ask_deadlines`,
`approval_expiry`, `blocker_asks`, `ticket_notices`, `still_wanted`, the old `task_snooze`.

Also, decided here: `check_blocked`, `still_needed`, `approvals_routes`, `ask_refusals`, `stuck_alarm`,
`pipeline_exceptions`, `repo_waits`, `task_review`, `settings_asks`, `qa_defaults`, `qa_waivers`, `task_types`,
`pipeline_requirements`, `pipeline_setup`, `pipeline_manager`, `pipeline_overview`, `pipeline_factories`,
`pipeline_github_checks` (back in phase 3 as a requirement kind), `requirement_attachments` (back in phase 2),
`quiet_work`'s defer, the parent-changing calls and the `blocks` relation of `task_relations` (a task waits on no
relation: `add_blocker`, `remove_blocker`, `set_parent`, `live_blocker` and the rest are gone), `evals/factory` (the rig replaces it), and in
`hubdb.py`: `_set_status`, `try_auto_move`, the approval functions, the type and step functions, the old status sets.

`pipeline_stats` goes, but two things in it are kept and move to their one user first: the bot page's queue counts
(`bot_rows`) and the queue classification Health uses.

Kept and rewired, not deleted: `reconnects`, `worktree_waits`, `wake_rules`, `strategy_merge`, `goal_asks`, `rules`,
`automations`, `task_relations` (relate and link only). `pipeline_judges` keeps how Jev judges; its dispatcher over the
old stage tables is removed and nothing calls it until the Jev requirement kind (phase 2).

A deleted module's tests go with it. A kept test that imports a deleted module is cut down to what still means
something, or deleted.

## Approvals and questions

Anything that asked a person through an approval object or a question task uses a task and adds a requirement:

- Question: `H.task_ask(..., kind="answer", choices=[...])`.
- Approval of something that leaves the company or spends (send this email, publish this edition, spend this, raise
  this cap): an OK that is bound to exactly what was shown. `backend/task_asks.py` (new, `# pyright: strict`):

```
ask_ok(conn, by, person, line, *, shows, subject, content, task_id=None, title=None) -> {"task_id", "requirement_id"}
    # Adds an OK naming `person` (always a person, never "anyone") to the task the work belongs to. Records a grant:
    # the requirement, who asked, `subject` (what will be done, e.g. "email_send:<id>") and the hash of `content`.
    # `by` is a bot, a person or SYSTEM. With no task it files one in General first, asked for by `by`: owned by `by`
    # when that is a bot or a person (an edition's author), by `person` when it is SYSTEM (a payment run).
give_ok(conn, person, requirement_id, shown_hash, *, own_click) -> None
    # The only way a bound OK is given. Passes when `person` is the one named, may still OK this (today's `can_ok`),
    # saw exactly this content (`shown_hash` is the hash of the preview they were shown) and clicked it themselves in
    # their own session: an Assistant or a token acting for them does not count. It then answers the requirement
    # `ok`. A plain task_answer of `ok` on a bound requirement is refused: "OK this from its preview."
standing(conn, requirement_id) -> "waiting" | "ok" | "sent_back" | "withdrawn" | "used"
use(conn, requirement_id, subject, content, by) -> None
    # The one gate before the act. Passes once, and only when the OK was given through give_ok, `by` is who asked,
    # and the subject and the content are exactly what was OK'd. A push past never passes it. A change to the content
    # after the OK needs a new OK. Marks the grant used in the same transaction as the act's own write. Otherwise
    # H.Refused.
```

  The grant is one new table, `ok_grants(requirement_id, asked_by_id, subject, content_hash, ok_at, ok_by_id,
  used_at, used_by_id)`. There is no approval that expires: Late handles an OK nobody gives.
- A task that `ask_ok` filed ends with the ask: whoever does the act finishes it with what was done; a sent back or
  withdrawn one is closed with that as the why.

## After the cut-over merge

Before 0.6.0-rc.1 (phase 1 is not done without them): the rule check on a bot's new ask (C21, scenarios 48 and 49);
screens S1 to S6 and S13 to S17 beyond what the readers need to work; the batched read that makes one never-stuck pass
fast; the fault rig green on 10,000 runs.

Later phases: the other requirement kinds; Product and Engineering.

## Who edits what

| Track | Owns |
|---|---|
| core | `hubdb.py`, `hubdb_core.py`, `hubdb_text.py`, `task_calls.py`, the engine (`task_engine`, `task_effects`, `task_requirements`, `waiting_on`, `task_wakes`, `never_stuck`, `answers`, `pipeline_settings`, `stage_effects`), the cut-over migration and `migrations/__init__.py`, `status_values.py`, `task_delete.py`, `task_relations.py`, `rows.py`, `sql.py`, the start-up and refusal parts of `store.py`, `conftest.py`, `tests/helpers/` |
| routes | `app.py`, `models.py`, `openapi_v2.py`, `task_*_routes.py`, `answers_routes.py`, `views.py`, `auth.py`, `task_reads.py`, `task_privacy.py`, `privacy_index.py`, `contracts/`, and the live changes: `events.py`, `read_deps.py`, `row_clock.py`, `provenance_clock.py`, `roster_clock.py` |
| upkeep | `scheduler.py`, `heartbeat.py`, `health.py`, `maintenance_health.py`, `store.py` (after core's part), `execution.py`, `enqueue.py`, `run_view.py`, `run_triggers.py`, `wake_rules.py`, `delivery.py`, `github.py`, `invariants.py`, `maintenance_steps/`, `system_routines.py`, `sleep_idle.py`, `worktrees.py`, `worktree_waits.py`, `reconnects.py`, `profiles.py`, `bot_rows.py`, `assigned_start.py`, `completion_notes.py`, `chat_goals.py`, and the deletion of every module under "What is deleted" |
| asks | `task_asks.py`, `email_sending.py`, `letters.py`, `company_page/`, `task_caps.py`, `webhooks.py`, `support.py`, `file_reviews.py`, `proposals.py`, `batch.py`, `media.py`, `bot_task_tabs.py`, `live_blocks.py`, `personal_alerts.py`, `reach.py`, `slack_gateway.py`, `strategy_merge.py`, `goal_asks.py`, `assistant.py`, `assistant_changes.py` |
| callers | every other backend module, in five groups (rules and automations; strategy, goals, market and roadmaps; task management and settings; demo data; the rest), each with its tests |
| bot tools | `clients/` but `agent_skill.py`, `backend/mcp.py`, `e2e/cli/`, `e2e/mcp/`, `test_botops_parity.py` |
| bot words | `templates/`, `skills/`, `clients/agent_skill.py`, `backend/dream.py` |
| screens | `ui/`, `backend/ui_bundle.py` |
| last | `e2e/goldens`, `e2e/tests`, `docs/`, `evals/`, `scripts/` (a script that inserts tasks by hand makes them through `task_create`; one that builds an old release's database keeps that release's shape) |

A test file belongs to the track that owns the module it is named after.

The order: core first. Bot tools and bot words start from this file. Routes, upkeep, asks and callers start when core
is on the branch. Screens start when the routes are. Last runs when the rest is merged.

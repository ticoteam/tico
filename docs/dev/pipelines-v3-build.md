# Pipelines v3: build contract

The spec is `docs/pipelines-v3.md`. This file is what the spec leaves to the build: table and column names, function
signatures, refusal names, and the order things land in. Code follows this file. A change to a name or a signature changes
this file in the same PR.

## How it lands

1. **Additive.** New tables and new modules go into main in small PRs. Nothing on main calls them except their tests and
   the fault rig. The old engine keeps running.
2. **Cut-over.** One change points every caller at the engine, turns the trigger on for every task, and deletes the old
   movers and the upkeep files. After it, Product and Engineering pipelines do not run on main until phases 2 and 3.

Until the cut-over a task is on the new engine when `tasks.pipeline_id IS NOT NULL`. Old tasks have it NULL and are not
touched by anything in this file.

## Names

- Actors are the strings the hub already uses: `bot:<id>`, `human:<id>`. The engine's own actor is `hubdb_core.KEEPER`,
  imported as `SYSTEM` in `task_engine`.
- Stage owner: an actor string, the word `assignee`, or NULL (the manager bot is woken to pick one, C25).
- Module names are the spec's, except `requirements.py`, which is `task_requirements.py` (the backend folder already has
  `requirements.txt`).

## Tables

All in `backend/migrations/<timestamp>_pipelines_v3.py`. Times are `H.now()` text. Ids are `H.new_id()` unless said.

```
pipelines     id, name, goal, prefix, manager_id, owner_id, private (0|1), factory_id, preset,
              settings_version, created_at, updated_at, retired_at
              unique name and unique prefix among rows not retired
stages        id, pipeline_id, position, kind (work|done|closed), name, purpose, owner, by_hand (0|1),
              task_limit, expected_minutes, expected_bots_only (0|1), on_enter (json), created_at,
              updated_at, retired_at
              every pipeline has exactly one done stage and one closed stage, after its work stages
requirements  id, stage_id | visit_id (exactly one), kind, settings (json), can_push_past (0|1),
              who_oks (none|people_only|people_with_exceptions), person_id, added_by_id, by_bot (0|1),
              line, shows (json), choices (json), position, created_at, withdrawn_at
              kind: owner_done | ok | page | pr_open | checks_pass | pr_merged | in_release |
                    linked_done | answer
              answer is never on a stage. person_id only on ok, page, answer (C29).
              unique (visit_id, added_by_id) where by_bot = 1 and withdrawn_at is null (C15)
visits        id, task_id, stage_id, entered_at, entered_how (new|met|pushed_past|back|reopened),
              left_at, how (met|pushed_past|back|closed|reopened), by_id, why,
              tries, level (owner|manager|person), person_id, next_try_at, progress_mark,
              last_change_at, late_told_at, late_person_at, bot_snoozes
              one open visit per task (left_at is null), always, Done and Closed included
answers       id, visit_id, requirement_id, by_id, answer, notes, page_version, created_at, undone_at
snoozes       id, task_id, visit_id, by_id, why, until_at (null = no end), created_at, ended_at,
              ended_by_id        one open snooze per task
links         id, from_task_id, to_task_id, visit_id, kind (sub_task|handoff), created_at
              a task has at most one row as to_task_id
effects       id, visit_id, kind (queue_pr|dequeue_pr), key (unique), state (owed|done|failed|moot),
              tries, last_error, created_at, updated_at
task_move_guards   id = 1, open (0|1)        the guard the trigger reads
tasks         + pipeline_id, stage_id, assignee_id      (status, owner_id, version exist)
messages      + wake_key, unique where not null
```

`pr_facts` and `releases` arrive with phase 3.

`tasks.status` on the engine is `open`, `done` or `closed`, and always equals the kind of the task's stage (`work` is
`open`).

`answers.answer` values: `done` (owner_done), `ok` and `no` (ok, and a page's person OK), the chosen choice (answer),
`pushed_past` (any kind, with the reason in `notes`). A requirement is met for a visit by its newest answer that is not
undone.

### The trigger

```sql
CREATE TRIGGER tasks_move_only BEFORE UPDATE OF status, stage_id, owner_id, pipeline_id ON tasks
WHEN (OLD.pipeline_id IS NOT NULL OR NEW.pipeline_id IS NOT NULL)
 AND (SELECT open FROM task_move_guards WHERE id = 1) IS NOT 1
BEGIN SELECT RAISE(ABORT, 'tasks: stage, owner and status change only through move()'); END;
```

The guard row is set to 1 and back to 0 inside the write transaction, in one place (`task_engine._write`). No other
connection ever sees it open. At the cut-over the `WHEN` loses its first line and a `BEFORE INSERT` twin is added.

## task_engine.py

The only writer of `tasks.status`, `stage_id`, `owner_id`, `pipeline_id`, of `visits`, of `snoozes`, and of requirements
added to a task. Every function takes the caller's open write transaction `conn` and never commits.

```python
NEXT, DONE, CLOSED = "next", "done", "closed"        # values for `to`, besides a stage id

def enter(conn, task_id, pipeline_id, by, *, assignee=None) -> dict
    # New. The row exists with pipeline_id NULL. Puts it in the first stage, opens the visit, wakes the owner.
def move(conn, task_id, to, by, why=None, seen_version=None) -> dict
    # Forward, push past, back, done, close, reopen. `to` is a stage id, NEXT, DONE or CLOSED.
def reassign(conn, task_id, owner, by, why=None, seen_version=None, *, assignee=False) -> dict
    # Same writer. Same visit. Resets tries and level. Drops the old owner's queued wakes for the task.
    # assignee=True also sets tasks.assignee_id (a person handing the whole task over).
def snooze(conn, task_id, by, why=None, until=None, seen_version=None) -> dict
def unsnooze(conn, task_id, by) -> dict
def add_requirement(conn, task_id, by, kind, person, line, *, choices=None, shows=None, seen_version=None) -> dict
    # kind is ok or answer (C30). A bot's second one on the visit replaces its first (C15).
def withdraw_requirement(conn, requirement_id, by, why=None) -> dict
def task(conn, task_id) -> dict       # the row plus its open visit, stage and pipeline, as the engine sees it
def fill_open_slots(conn, stage_ids=None) -> list   # oldest waiting task first; called after a move and by the check
```

Each returns the task as `task()` gives it after the write. Each raises `hubdb.Refused` through `hubdb.refuse` with one of
these rules, and nothing else:

```
task_changed         changed since you looked               (also: a bot gave no seen_version)
task_not_met         not met: <requirement line>            (forward with no why)
task_no_push_past    <requirement> can't be pushed past. Move back or close.
task_needs_person    <requirement> needs a person
task_stage_full      no room in <stage>
task_why             say why                                (back, close, push past, a bot's snooze; a note on reopen)
task_no_such_stage   no such stage in this pipeline
task_ended           the task is Done / Closed. Reopen it first.
task_too_deep        sub-tasks nest at most 5 deep
task_not_yours       only someone working the task can do this
```

None of these counts toward quarantining a bot. `hubdb._escalate` skips every rule that starts with `task_`.

What `move()` does, in order, in one transaction: check the version; work out the kind; for forward, check requirements
through `task_requirements.unmet`, then room; for back and close, `stage_effects.undo(visit)`; close the visit (its task
requirements are withdrawn with it); open the guard, write `stage_id, owner_id, status, version + 1, updated_at`, close the
guard; open the new visit; run the stage's on-enter through `stage_effects.enter(visit)`; write the owner's wake through
`task_wakes.add`; write one `history_events` row (`kind` = `move`, `old` and `new` are stage ids, `note` is the why,
`data` has the move kind, both owners and anything pushed past). After the caller commits, the route calls
`fill_open_slots` and pushes to screens.

Closing a task closes its open sub-tasks (links from visits of this task, to tasks still open) with the same actor and
the note "Closed with <task>". Done does not.

## task_requirements.py

```python
KINDS: dict[str, Kind]        # one object per kind
def for_visit(conn, visit) -> list[dict]          # the stage's, then the ones added to this visit, not withdrawn
def standing(conn, task, requirement) -> Standing # met | waiting | not_met
def unmet(conn, task) -> list[Standing]
```

`Standing` has `state` (`met`, `waiting`, `not_met`), `on` (who or what it waits on: an actor, `jev`, `github`, a task
id), `line` (what is missing, one sentence), `requirement`. A kind reads tables only. It never calls GitHub, Jev or a
computer.

Phase 1 kinds: `owner_done`, `ok`, `answer`, `linked_done`. The rest refuse to be saved until their phase.

## waiting_on.py

```python
def waiting_on(conn, task_id) -> list[Wait]
def waiting_on_many(conn, task_ids) -> dict[str, list[Wait]]     # one set of queries for the whole list
def needs(conn, person) -> list[dict]                            # Needs you for a person: a read, nothing stored
```

`Wait` has `state`, `who`, `line`, `requirement_id`, `since`. `state` is one of, in headline order: `snoozed`, `person`,
`paused`, `bot`, `jev`, `github`, `tasks`, `room`, `by_hand`. The list is never empty for an open task. A Done or Closed
task returns `[]`.

## never_stuck.py

```python
def check(conn_factory, now=None) -> dict     # one pass: effects, facts, snoozes that ended, every open task, slots
def rules_hold(conn) -> list[str]             # the "rules that hold at all times"; empty means all hold
```

Registered as one Heartbeat step, `never_stuck`, every beat. Ladder times: 2 min, 15 min, 1 h. Three tries at `owner`,
three at `manager`, then `person`. It is the only caller of `move()` with `by=SYSTEM`.

## task_wakes.py

```python
def add(conn, task, target, visit, body, *, level="owner", try_n=0) -> str | None   # None when the key exists
def queued(conn, task_id, actor) -> bool        # a wake for this task is waiting for this bot, held ones included
def live_run(conn, task_id, actor) -> bool      # a run on this task whose computer checked in within 90 s
def drop(conn, task_id, actor) -> int           # reassign: the old owner's queued wakes for the task
```

`wake_key` is `<task id>:<visit id>:<level>:<try>`. The body lists what is missing and the five ways a run may end.

## answers.py, stage_effects.py, pipeline_settings.py

```python
answers.answer(conn, requirement_id, by, answer, notes=None, *, seen_version=None) -> dict
answers.give_to(conn, requirement_id, by, person) -> dict
answers.thumbs_down(conn, requirement_id, by, reason, rule=None) -> dict      # writes a feedbacks row

stage_effects.enter(conn, visit) -> None      # file a linked task; add a queue_pr effect row
stage_effects.undo(conn, visit) -> None       # close tasks filed in the visit; add dequeue_pr rows
stage_effects.run(conn_factory) -> int        # the GitHub calls still owed (phase 3)

pipeline_settings.read(conn, pipeline_id) -> dict           # one record: pipeline, stages, requirements
pipeline_settings.save(conn, by, record, seen_version) -> dict
pipeline_settings.PRESETS: dict[str, dict]                  # general (phase 1), product, engineering
pipeline_settings.ensure_presets(conn) -> None              # boot: a new org gets them
```

General's ids are fixed: pipeline `general`, stages `general-todo`, `general-done`, `general-closed`.

## Tests and the rig

- `backend/tests/helpers/engine.py` makes rows for tests: `pipeline(c, ...)`, `stage(...)`, `new_task(c, by, title,
  pipeline_id="general", assignee=None)`. Until the cut-over `new_task` inserts the row itself and calls `enter()`.
  After it, it calls `H.task_create`.
- The fault rig lives in `evals/never_stuck/`. It drives the real modules on a real SQLite file with a fake clock, fake
  bots and seeded faults, and runs `never_stuck.rules_hold` after every step.
- A lint test, `backend/tests/test_one_task_writer.py`, reads the source and fails on any `UPDATE tasks` or
  `INSERT INTO tasks` that sets `status`, `stage_id`, `owner_id` or `pipeline_id` outside `task_engine.py`. Until the
  cut-over it has an allow list of today's writers, and the list may only shrink.

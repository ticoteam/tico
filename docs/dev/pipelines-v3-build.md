# Pipelines v3: build contract

The spec is `docs/pipelines-v3.md`. This file is what the spec leaves to the build: table and column names, function
signatures, refusal names, and the order things land in. Code follows this file. A change to a name or a signature changes
this file in the same PR.

## How it lands

1. **Additive.** New tables and new modules go into main in small PRs. Nothing on main calls them except their tests and
   the fault rig. The old engine keeps running.
2. **Cut-over.** One change points every caller at the engine, turns the trigger on for every task, and deletes the old
   movers and the upkeep files. After it, Product and Engineering pipelines do not run on main until phases 2 and 3. The
   steps before it and what it must carry are under [The cut-over](#the-cut-over).

Until the cut-over a task is on the new engine when `tasks.pipeline_id IS NOT NULL`. Old tasks have it NULL and are not
touched by anything in this file.

## Names

- Actors are the strings the hub already uses: `bot:<id>`, `human:<id>`. The engine's own actor is `hubdb_core.KEEPER`,
  imported as `SYSTEM` in `task_engine`.
- Stage owner: an actor string, the word `assignee`, or NULL (the manager bot is woken to pick one, C25).
- Module names are the spec's, except `requirements.py`, which is `task_requirements.py` (the backend folder already has
  `requirements.txt`).

## Rules for every new module

- `# pyright: strict` in the first 10 lines. `scripts/check_pyright_ratchet.py` refuses a new backend module without it.
- A new table gets a row shape in `backend/rows.py`, a line in `backend/tests/test_rows_schema.py` and a class in
  `backend/devcopy.py` (keep, history or empty).
- No foreign key from a new table to `tasks`.
- Every function takes the caller's open write transaction `conn` and never commits, unless it says otherwise.

## Tables

All in `backend/migrations/20261110010000_pipelines_v3.py`. Times are `H.now()` text. Ids are `H.new_id()` unless said.

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
visits        id, task_id, stage_id, entered_at,
              entered_how (new|met|pushed_past|back|closed|reopened),
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

`tasks.version` goes up by one on every move and every reassign, and on nothing else the engine does. A snooze, an
answer and an added requirement leave it alone.

`answers.answer` values: `done` (owner_done), `ok` and `no` (ok, and a page's person OK), `again` (the owner asks the
person to look again after a `no`), the chosen choice (answer), `pushed_past` (any kind, with the reason in `notes`). A
requirement is met for a visit by its newest answer that is not undone.

### The trigger

```sql
CREATE TRIGGER tasks_move_only BEFORE UPDATE OF status, stage_id, owner_id, pipeline_id ON tasks
WHEN (OLD.pipeline_id IS NOT NULL OR NEW.pipeline_id IS NOT NULL)
 AND (SELECT open FROM task_move_guards WHERE id = 1) IS NOT 1
BEGIN SELECT RAISE(ABORT, 'tasks: stage, owner and status change only through move()'); END;
```

The guard row is set to 1 and back to 0 inside the write transaction, in one place (`task_engine._write`), around one
statement, with the close in a `finally`. No other connection ever sees it open. At the cut-over the `WHEN` loses its
first line and a `BEFORE INSERT` twin is added.

`_write` takes only the columns in `task_engine.WRITES` (`status`, `stage_id`, `owner_id`, `pipeline_id`, `assignee_id`,
`version`, `updated_at`, `done_at`, `closed_at`, `closed_by_id`) and raises `ValueError` for any other. It holds the only
`UPDATE tasks SET` in the file.

## task_engine.py

The only writer of `tasks.status`, `stage_id`, `owner_id`, `pipeline_id`, of `visits`, of `snoozes`, and of requirements
added to a task.

```python
NEXT, DONE, CLOSED, REOPEN = "next", "done", "closed", "reopen"     # values for `to`, besides a stage
MAX_DEPTH = 5          # sub-tasks nest at most this deep (C20)
BOT_SNOOZES = 3        # a bot's snoozes in one stage before the manager bot gets the task

def enter(conn, task_id, pipeline_id, by, *, assignee=None, parent=None, link="sub_task") -> dict
    # New. The row exists with pipeline_id NULL. Puts it in the first stage, opens the visit, wakes the owner.
    # `parent` links it to the task that filed it, on that task's open visit. `link` is sub_task or handoff.
def move(conn, task_id, to, by, why=None, seen_version=None, *, cascade=False) -> dict
    # Forward, push past, back, done, close, reopen. `to` is a stage id or a stage name, or NEXT, DONE, CLOSED, REOPEN.
    # `cascade` is the engine closing a task with the one that filed it.
def advance(conn, task_id) -> dict | None
    # Forward, by the engine: moves the task on if everything is met and there is room. None when it stays.
def fill_open_slots(conn, stage_ids=None) -> list[str]
    # Every task that is ready and has room, oldest first. Returns the ids it moved.
def reassign(conn, task_id, owner, by, why=None, seen_version=None) -> dict
    # Same writer. Same visit. Resets tries and level. Drops the old owner's queued wakes for the task.
    # In a stage owned by `assignee`, the task's assignee follows.
def snooze(conn, task_id, by, why=None, until=None, seen_version=None) -> dict
def unsnooze(conn, task_id, by, why=None) -> dict
def add_requirement(conn, task_id, by, kind, person, line, *, choices=None, shows=None, seen_version=None) -> dict
    # kind is ok or answer (C30). A bot's second one on the visit replaces its first (C15).
    # The result carries `requirement_id`.
def withdraw_requirement(conn, requirement_id, by, why=None) -> dict | None

def task(conn, task_id) -> dict | None
    # The row plus `visit` (the open one), `stage`, `pipeline` and `snooze` (the open one or None).
    # None for a task that is not on the engine.
def pipeline(conn, pipeline_id), stages(conn, pipeline_id), stage(conn, stage_id)
def next_stage(conn, task_row) -> dict | None      # the next work stage, or Done after the last
def has_room(conn, stage_row, extra=0) -> bool     # snoozed tasks hold no slot
def owner_for(stage_row, assignee, current=None)   # who owns a task in a stage
def depth(conn, task_id) -> int
```

Each write returns the task as `task()` gives it after the write. Each raises `hubdb.Refused` through `hubdb.refuse` with
one of these rules, or `not-found`, and nothing else:

```
task_changed         changed since you looked               (also: a bot gave no seen_version)
task_not_met         not met: <requirement line>            (forward with no why)
task_no_push_past    <requirement> can't be pushed past. Move back or close.
task_needs_person    <requirement> needs a person
task_stage_full      no room in <stage>
task_why             say why
task_no_such_stage   no such stage in this pipeline
task_ended           the task is Done / Closed. Reopen it first.
task_too_deep        sub-tasks nest at most 5 deep
task_requirement     a requirement that cannot be added or withdrawn as asked
```

None of these counts toward quarantining a bot. `hubdb._escalate` skips every rule that starts with `task_`.

Who must say why:

- Push past: everyone. The why is the reason on each requirement pushed past.
- Back and Close: a bot. A person may leave it out.
- Reopen: everyone gives a note.
- Snooze: a bot.

A bot's fourth snooze in one stage is not applied. The visit goes to level `manager`, the manager bot is woken, and the
result carries `note`.

What `move()` does, in order, in one transaction: check the version; work out the kind; for forward, check requirements
through `task_requirements.unmet`, then room; for back and close, `stage_effects.undo`; close the visit (its task
requirements are withdrawn with it); open the guard, write `stage_id, owner_id, status, version + 1, updated_at`, close the
guard; open the new visit; run the stage's on-enter through `stage_effects.enter`; write the owner's wake through
`task_wakes.add`; write one `history_events` row (`kind` = `move`, `old` and `new` are stage ids, `note` is the why,
`data` has the move kind, both owners and anything pushed past). After the caller commits, the route calls
`fill_open_slots` and pushes to screens.

A stage with no requirements passes a task straight on when the next stage has room, unless the stage is by hand.

Closing a task closes every open task under it, however deep, with the same actor and the note "Closed with <task>". It
walks through tasks that already ended to reach open ones below them. Done does not close anything.

## task_requirements.py

```python
MET, WAITING, NOT_MET = "met", "waiting", "not_met"
JEV, GITHUB = "jev", "github"                     # what Standing.on is when it is not an actor or a task
PHASE_ONE = ("owner_done", "ok", "answer", "linked_done")
NEVER_PUSHED_PAST = ("pr_merged", "in_release")

def on_stage(conn, stage_id) -> list[dict]
def on_visit(conn, visit_id) -> list[dict]
def for_visit(conn, visit) -> list[dict]          # the stage's, then the ones added to this visit, not withdrawn
def get(conn, requirement_id) -> dict | None
def newest_answers(conn, visit_id) -> dict[str, dict]
def standing(conn, task, requirement, answer=None) -> Standing
def standings(conn, task) -> list[Standing]
def unmet(conn, task) -> list[Standing]
def name_of(requirement) -> str
def can_push_past(requirement) -> bool
def people_only(requirement) -> bool
```

`Standing` has `state` (`met`, `waiting`, `not_met`), `on` (who or what it waits on: an actor, `jev`, `github`, a task
id), `line` (what is missing, one sentence), `name`, `requirement`, and `met`. A kind reads tables only. It never calls
GitHub, Jev or a computer.

Phase 1 kinds: `owner_done`, `ok`, `answer`, `linked_done`. The rest refuse to be saved until their phase.

## waiting_on.py

```python
STATES = ("snoozed", "person", "paused", "bot", "jev", "github", "tasks", "room", "by_hand")   # headline order

def waiting_on(conn, task_id) -> list[Wait]
def waiting_on_many(conn, task_ids) -> dict[str, list[Wait]]
def headline(conn, task_id) -> Wait | None                       # the first wait: what a row shows
def all_met(waits) -> bool                                       # it only waits for a slot
def needs(conn, person) -> list[dict]                            # Needs you for a person: a read, nothing stored
def person_for(conn, row) -> str | None                          # the person a task falls to
def holder(conn, row) -> str | None                              # who has the task's own work now
```

`Wait` has `state`, `who`, `line`, `requirement_id`, `since`, `until`. `who` is an actor for `person`, `paused` and `bot`,
a task id for `tasks`, the stage id for `room` and `by_hand`, whoever snoozed it for `snoozed`, and None for `jev` and
`github`. `until` is a snooze's end. The list is never empty for an open task. A Done or Closed task returns `[]`.

- `room`: everything is met. The line is "No room in <stage>" when the next stage is full, and "Moving to <stage>" when
  it has room and the engine has not passed yet.
- `by_hand`: in a by-hand stage nobody is chased for a requirement that is not met. People named on a requirement are
  still listed.
- `holder`: the owner; at level `manager`, or with no owner, the pipeline's manager; at level `person`, `person_for`.
- `person_for`, first that exists: the person the ladder already chose, the pipeline's owner or manager where that is a
  person, the owner of its manager bot, the owner of the bot that has the task, whoever asked for the task, the company
  owner.
- `needs` leaves out snoozed tasks and returns `task_id`, `number`, `title`, `pipeline_id`, `stage_id`,
  `requirement_id`, `line`, `since`, oldest first.

## task_wakes.py

```python
def key(task_id, visit_id, level="owner", try_n=0) -> str        # <task id>:<visit id>:<level>:<try>
def body(conn, task, lead="") -> str                             # the stage, what is missing, the five endings
def add(conn, task, target, visit, text, *, level="owner", try_n=0, wake_key=None, quiet=False) -> str | None
    # None when the key exists or there is nobody to tell
def cannot_run(conn, actor) -> str | None       # why this bot cannot run now, in a few words; None when it can
def queued(conn, task_id, actor) -> bool        # a wake for this task is waiting for this bot, held ones included
def live_run(conn, task_id, actor) -> bool      # a run on this task whose computer checked in within 90 s
def drop(conn, task_id, actor) -> int           # reassign: the old owner's queued wakes for the task
```

The wake is a message from the engine in the task's conversation, with `refs.wake = "task_turn"` and the task, visit,
level, try and version beside it. `task_turn` is a trigger in `run_triggers.TRIGGERS`, an always-wake event in
`wake_rules.ALWAYS_WAKE` and a floor event in `rules`: no rule holds it. `add` writes the message itself and does not go
through `hubdb._wake`, whose holds belong to the old engine.

`cannot_run` today reads `bots.state`. `never_stuck` extends it with the rest of the spec's Paused causes: no computer,
out of credit, over a limit, a login to reconnect. `live_run` arrives with `never_stuck`.

## never_stuck.py

```python
def check(conn_factory, now=None) -> dict     # one pass: effects, facts, snoozes that ended, every open task, slots
def rules_hold(conn) -> list[str]             # the "rules that hold at all times"; empty means all hold
```

Registered as one Heartbeat step, `never_stuck`, every beat. Ladder times: 2 min, 15 min, 1 h. Three tries at `owner`,
three at `manager`, then `person`. It is the only caller of `move()` with `by=SYSTEM`.

## answers.py, stage_effects.py, pipeline_settings.py

```python
answers.answer(conn, requirement_id, by, answer, notes=None, *, seen_version=None) -> dict
answers.give_to(conn, requirement_id, by, person) -> dict
answers.thumbs_down(conn, requirement_id, by, reason, rule=None) -> dict      # writes a feedbacks row

stage_effects.enter(conn, task, by) -> None        # file a linked task; add a queue_pr effect row
stage_effects.undo(conn, task, by, note) -> None   # close tasks filed in the visit; add dequeue_pr rows
stage_effects.filed_open(conn, visit_id) -> list[str]
stage_effects.run(conn_factory) -> int             # the GitHub calls still owed (phase 3)

pipeline_settings.read(conn, pipeline_id) -> dict           # one record: pipeline, stages, requirements
pipeline_settings.save(conn, by, record, seen_version) -> dict
pipeline_settings.PRESETS: dict[str, dict]                  # general (phase 1), product, engineering
pipeline_settings.ensure_presets(conn) -> None              # boot: a new org gets them
```

`answers.answer` writes the answer row and then calls `task_engine.advance`.

General's ids are fixed: pipeline `general`, stages `general-todo`, `general-done`, `general-closed`.

## Tests and the rig

- Run them with a Python 3.12 environment built from `backend/requirements-dev.txt`:
  `python -m pytest -q -n 2 backend/tests/test_task_engine.py backend/tests/test_waiting_on.py`.
- `backend/tests/helpers/engine.py` makes rows for tests: `bot(c, slug)`, `pipeline(c, pid, name, stages, manager)`,
  `requirement(c, stage_id, kind, **fields)`, `new_task(c, by, title, pipeline_id="general", assignee=None,
  parent=None)`, `say_done`, `answer`, `wakes`, `settled`, `guard_closed`. Until the cut-over `new_task` inserts the row
  itself and calls `enter()`. After it, it calls `H.task_create`. Once `pipeline_settings.PRESETS` exists the helper
  builds General from it, so tests use the fixed ids.
- The fault rig lives in `evals/never_stuck/`. It drives the real modules on a real SQLite file with a fake clock, fake
  bots and seeded faults, and runs `never_stuck.rules_hold` after every step.
- A lint test, `backend/tests/test_one_task_writer.py`, reads the source and fails on any `UPDATE tasks` that sets
  `status`, `stage_id`, `owner_id` or `pipeline_id` outside `task_engine.py`, and on a second `UPDATE tasks SET` inside
  it. Until the cut-over it has an allow list of today's writers (`OLD_WRITERS`), and the list may only shrink.

## The cut-over

In order. Each step before the last keeps the suite green.

1. **Finish the additive build**: `never_stuck`, `answers`, `pipeline_settings` with General, the fault rig.
2. **Hollow out the old engine.** Make the top-level imports of the modules to be deleted lazy. Turn the direct writers
   in `OLD_WRITERS` and in `scripts/` into calls. Move code nobody imports out first.
3. **Readers.** Point the screens, the bot tools, the skills and the templates at "open, done or closed, plus
   `waiting_on`". The old statuses still exist, so tests stay green. Prompt text and a tool's list of values change in the
   same PR.
4. **Approvals.** Sending email, page editions and spend use approval objects today. They move to OK requirements before
   the approvals code is deleted.
5. **The cut-over itself**, one change:
   - The trigger loses its first `WHEN` line and gets its `BEFORE INSERT` twin.
   - Create, update, close, bulk and restore go through `enter`, `move` and `reassign`.
   - **Every task that exists is kept.** Open ones go to `general-todo`, done ones to `general-done`, closed ones to
     `general-closed`, each with one open visit, `entered_how = 'new'`. Old pipelines, stages and their settings are not
     carried over. The migration opens the guard itself.
   - The old statuses collapse to `open`, `done`, `closed` before the trigger goes on.
   - The old movers, the upkeep files and their tests are deleted.
6. **Drop the old tables** one release later.

What the old funnel does today that the engine must still do at the cut-over, each in the move transaction or right
after its commit:

- tell a routine that its task ended (`rule_due.on_task_status`)
- record the turn's result for the run view
- deliver a closed task to whoever asked
- wake the requester when a task is Done
- stop work that is still running on a task that ended
- publish GitHub checks for tasks a move affects (phase 3)

The version check must be real for bots: the bot tool reads the task, the bot decides, and the write carries the version
the bot read. A tool that fetches the version just before it posts checks nothing.

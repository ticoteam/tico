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
              requests_move_on (0|1, by-hand stages only), task_limit, expected_minutes, expected_bots_only (0|1),
              on_enter (json), created_at, updated_at, retired_at
              every pipeline has exactly one done stage and one closed stage, after its work stages
requirements  id, stage_id | visit_id (exactly one), kind, source, condition, settings (json), can_push_past (0|1),
              who_oks (none|people_only|people_with_exceptions), person_id, added_by_id, by_bot (0|1),
              line, shows (json), choices (json), position, created_at, withdrawn_at
              kind: owner_done | ok | page | item | linked_done | answer
              source and condition on an item only (github: pr_open | checks_pass | pr_merged | in_release)
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
effects       id, visit_id, kind (act|undo), source, action, item_type, item_key (nocase), pinned,
              key (unique), state (owed|done|failed|moot),
              tries, last_error, created_at, updated_at
task_move_guards   id = 1, open (0|1), task_id      the guard the trigger reads: open for one task
tasks         + pipeline_id, stage_id, assignee_id      (status, owner_id, version exist)
messages      + wake_key, unique where not null
```

The Product and Engineering tables are in `backend/migrations/20261110213830_product_engineering_facts.py`; see
"Product and Engineering" below.

`tasks.status` on the engine is `open`, `done` or `closed`, and always equals the kind of the task's stage (`work` is
`open`).

`tasks.version` goes up by one on every move and every reassign, and on nothing else the engine does. A snooze, an
answer and an added requirement leave it alone. After the cut-over a field edit (a new title, a label) raises it too.
It says "this row changed since you looked", not progress: `progress_mark` does not read it.

`answers.answer` values: `done` (owner_done), `ok` and `no` (ok, and a page's person OK), `again` (the owner asks the
person to look again after a `no`), the chosen choice (answer), `give` (a stage's OK given to the person in `notes`),
`pushed_past` (any kind, with the reason in `notes`). `no`, `again` and `give` steer only an `ok` or a `page`
(`task_requirements.steers`); on a question they are an answer like any other. A requirement is met for a visit by its
newest answer that is not undone. It waits on the person of its newest `give` that is not undone, else on its
`person_id`, skipping anyone who has left or was removed (`task_requirements.person_of`). With nobody left it names
nobody, so any person may answer it.

### The trigger

```sql
CREATE TRIGGER tasks_move_only BEFORE UPDATE OF status, stage_id, owner_id, pipeline_id ON tasks
WHEN (OLD.pipeline_id IS NOT NULL OR NEW.pipeline_id IS NOT NULL)
 AND (SELECT task_id FROM task_move_guards WHERE id = 1 AND open = 1) IS NOT NEW.id
BEGIN SELECT RAISE(ABORT, 'tasks: stage, owner and status change only through move()'); END;
```

The guard row is opened for one task and closed again inside the write transaction, in one place
(`task_engine._write`), around one statement, with the close in a `finally`. No other connection ever sees it open, and
a trigger that fires during that statement cannot change another task. At the cut-over the `WHEN` loses its
first line and a `BEFORE INSERT` twin is added.

`_write` takes only the columns in `task_engine.WRITES` (`status`, `stage_id`, `owner_id`, `pipeline_id`, `assignee_id`,
`version`, `updated_at`, `done_at`, `closed_at`, `closed_by_id`) and raises `ValueError` for any other. It holds the only
`UPDATE tasks SET` in the file.

## task_engine.py

The only writer of `tasks.status`, `stage_id`, `owner_id`, `pipeline_id`, of `visits`, of `snoozes`, and of requirements
added to a task, except the person on one, which `answers.give_to` changes.

```python
NEXT, DONE, CLOSED, REOPEN = "next", "done", "closed", "reopen"     # values for `to`, besides a stage
MAX_DEPTH = 5          # sub-tasks nest at most this deep (C20)
BOT_SNOOZES = 3        # a bot's snoozes in one stage before the manager bot gets the task
FIRST_TRY_S = 120      # seconds a bot is left alone after a wake before the never-stuck check looks at it

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
    # `owner` is a person who is here, a bot that exists, or nobody; anyone else is refused task_requirement.
    # In a stage owned by `assignee`, the task's assignee follows.
def snooze(conn, task_id, by, why=None, until=None, seen_version=None) -> dict
def unsnooze(conn, task_id, by, why=None) -> dict
def on_delete(conn, task_ids, by) -> int
    # The delete path (task_delete.delete_tasks), before the rows go: each task's open snooze ends. Its visit,
    # requirements and links stay for a restore; every read skips a task that is gone.
def add_requirement(conn, task_id, by, kind, person, line, *, choices=None, shows=None, seen_version=None) -> dict
    # kind is ok or answer (C30). A bot's second one on the visit replaces its first (C15).
    # `person` is a person who is here (not left or removed) or a bot that exists. The result carries `requirement_id`.
def withdraw_requirement(conn, requirement_id, by, why=None) -> dict | None
def try_again(conn, task_id, by, why=None) -> dict
    # Hands a task the ladder or Late brought to a person back to its owner, count at zero. The owner is woken once.
    # People only: a bot is refused task_needs_person, told it can reassign, ask a person, snooze or close.
def changed(conn, task_id, by) -> None
    # Something the task waits on changed outside a move (an answer came or was taken back). The ladder starts again.
    # Whoever calls it also wakes the bot it hands the turn to.
def progress_mark(conn, visit_id, standings=None) -> str
    # What tells a task that changed from one that did not: this visit's answers, added requirements, filed tasks and
    # snoozes, and where each requirement stands. A comment is not a change, nor the task's version (a field edit
    # raises it); a move and a reassign start the ladder again themselves.

def task(conn, task_id) -> dict | None
    # The row plus `visit` (the open one), `stage`, `pipeline` and `snooze` (the open one or None).
    # None for a task that is not on the engine.
def pipeline(conn, pipeline_id), stages(conn, pipeline_id), stage(conn, stage_id)
def next_stage(conn, task_row) -> dict | None      # the next work stage, or Done after the last
def has_room(conn, stage_row, extra=0, task_row=None) -> bool  # snoozed tasks hold no slot; a sub-task rides its filer's
def owner_for(stage_row, assignee, current=None)   # who owns a task in a stage
def depth(conn, task_id) -> int
def check_version(conn, by, task_row, seen_version, cascade=False) -> None   # task_changed; a bot must send one
```

Each write returns the task as `task()` gives it after the write. Each raises `hubdb.Refused` through `hubdb.refuse` with
one of these rules, or `not-found`, and nothing else:

```
task_changed         changed since you looked               (also: a bot gave no seen_version)
task_not_met         not met: <requirement line>            (forward with no why)
task_no_push_past    <requirement> can't be pushed past. Move back or close.
task_needs_person    <requirement> needs a person; try again needs a person; a bot closing a person's request
task_stage_full      no room in <stage>
task_not_shared      <who> owns / is asked in <stage> and cannot read this private task (`cause`, `fix`)
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

A bot can't close a person's request (C8): `move(..., CLOSED)` by a bot is refused `task_needs_person` when the task's
requester is a person and no task filed it, and the detail tells it to ask that person, in one sentence, why it can't be
done (they close it or reassign it). A person closes anything. A bot closes what a bot asked for and what a task filed.
The engine closing filed tasks with the task above them (`cascade`) is not a bot.

A bot sends `seen_version` with every command that takes one: move, reassign, snooze and add a requirement. A person
may leave it out.

A bot's fourth snooze in one stage is not applied. The visit goes to level `manager` with its next try due at once, so
the never-stuck check wakes the manager bot on its next pass. Wakes still queued for a bot that has no turn now are
canceled; a bot named on an OK or an answer that still waits keeps its own. The result carries `note`.

A snooze sets a task aside in one stage: any move ends it. A snooze and its end are not a change: the ladder's count and
level are kept, so setting a task aside earns no runs. When a snooze ends no wake is written: the visit's next try is due
at once (no gap after the bot's last run), and the never-stuck check wakes whoever has the turn on its next pass, as
the next try. At level `person` nobody is woken. A person's Try again is the way to start the count over.

A move and a snooze cancel every wake still queued for the task, whoever it was for. Every other change (a reassign, an
ask added or withdrawn, an answer, a filed task ending, try again, `changed`) and every hand-over cancels the wakes still
queued for a bot that has no turn on the task now, and keeps those of a bot that still has one (`waiting_on.turns`).
A bot has at most one wake queued per task (`task_wakes.add` folds a second into it).

Only whoever has the turn is woken. Entering a stage, a reassign, try again and a filed task ending wake the one bot
the ladder would wake (`waiting_on.turn`): the bot with the task's own work, before a bot named on an OK or an answer;
the manager bot when the task has no owner. News meant for a bot with no turn (an answer to its ask, a send back, a
thumbs-down) goes to whoever has the turn, naming the bot it was for.

A filed task that was deleted counts as closed, not done, for Linked work done.

What `move()` does, in order, in one transaction: check the version; work out the kind; for forward, check requirements
through `task_requirements.unmet`, then room; for back and close, `stage_effects.undo`; close the visit (its task
requirements are withdrawn with it); open the guard, write `stage_id, owner_id, status, version + 1, updated_at`, close the
guard; open the new visit; run the stage's on-enter through `stage_effects.enter`; write the wake of whoever has the
turn (`waiting_on.turn`) through `task_wakes.add`; write one `history_events` row (`kind` = `move`, `old` and `new` are stage ids, `note` is the why,
`data` has the move kind, both owners and anything pushed past). After the caller commits, the route calls
`fill_open_slots` and pushes to screens.

A stage with no requirements passes a task straight on when the next stage has room, unless the stage is by hand. The
never-stuck check does it, on its next pass. A by-hand stage holds every task, unless it has `requests_move_on` (Product's
Ideas): then a person's request on its first visit (`task_engine.not_held_by_hand`) is moved on by the check when the
next stage has room, oldest first (`never_stuck._requests`). C10: nothing is written about room, for any by-hand
stage in any pipeline; the moved task's owner is woken by its turn in the next stage, as after any move. Every other
task in a by-hand stage rests until someone moves it. (The "has room" wake, keyed `room:<stage>:<freed by>`, was
removed Nov 12; migration `drop_queued_room_notices` canceled the ones still queued.)

The ladder columns of a visit (`tries`, `level`, `person_id`, `next_try_at`, `progress_mark`, `last_change_at`,
`late_told_at`, `late_person_at`) are written by `task_engine` and `never_stuck` only. Every engine write that changes
a task without moving it (reassign, add or withdraw a requirement, try again, a filed task ending, `changed`) starts
the ladder again: tries 0, level `owner`, next look in `FIRST_TRY_S`. One exception: a task that Late
brought to a person stays with that person until a person acts.

A requirement that names a bot wakes that bot once, when it is added. That bot is on the task's count from then on.

Closing a task closes every open task under it, however deep, with the same actor and the note "Closed with <task>". It
walks through tasks that already ended to reach open ones below them. Done does not close anything.

## task_requirements.py

```python
MET, WAITING, NOT_MET = "met", "waiting", "not_met"
JEV, GITHUB = "jev", "github"                     # what Standing.on is when it is not an actor or a task
NEVER_PUSHED_PAST = ("pr_merged", "in_release")

def on_stage(conn, stage_id) -> list[dict]
def on_visit(conn, visit_id) -> list[dict]
def for_visit(conn, visit) -> list[dict]          # the stage's, then the ones added to this visit, not withdrawn
def get(conn, requirement_id) -> dict | None
def newest_answers(conn, visit_id) -> dict[str, dict]
def person_of(conn, requirement, visit_id) -> str | None   # who it waits on: the newest give, else person_id; never
                                                           # a person who left
def active_person(conn, actor) -> bool            # a person who is here and can act: in humans, not left or hidden
                                                  # on the roster, sign-in not turned off (`sign_in: false`)
def steers(kind, value) -> bool                   # no / again / give on an ok or a page; never on a question
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

The six kinds: `owner_done`, `ok`, `answer`, `linked_done`, and Product and Engineering's `page` and `item`. An
`item` names a source and one of its conditions (`backend/item_sources/`: GitHub's `pr_open`, `checks_pass`,
`pr_merged`, `in_release`). A condition gives its name, settings and whether it is never pushed past. `SETTINGS` lists the settings keys each kind takes; `pipeline_settings` refuses
any other key.

## waiting_on.py

```python
STATES = ("snoozed", "person", "paused", "bot", "jev", "source", "tasks", "room", "by_hand")   # headline order

def waiting_on(conn, task_id) -> list[Wait]
def waiting_on_many(conn, task_ids) -> dict[str, list[Wait]]
def headline(conn, task_id) -> Wait | None                       # the first wait: what a row shows
def all_met(waits) -> bool                                       # it only waits for a slot
def needs(conn, person) -> list[dict]                            # Needs you for a person: a read, nothing stored
def of(conn, row, seen=None, standings=None) -> list[Wait]       # the same, for a task already read with task()
def person_for(conn, row) -> str | None                          # the person a task falls to
def company_person(conn) -> str | None                           # the company owner, else whoever has been here longest
def holder(conn, row) -> str | None                              # who has the task's own work now
def turns(conn, row, waits=None) -> set[str]                     # the bots whose turn it is (Paused ones too, and the
                                                                 # manager bot at level manager); none at level person
def turn(conn, row, waits=None, *, paused=False) -> str | None   # the one bot the ladder wakes now
```

`Wait` has `state`, `who`, `line`, `requirement_id`, `since`, `until`, `ask`. `who` is an actor for `person`, `paused` and `bot`,
a task id for `tasks`, the stage id for `room` and `by_hand`, whoever snoozed it for `snoozed`, and None for `jev` and
`github`. `until` is a snooze's end. `ask` is set when a requirement names who it waits on (an OK or an answer): a
person, or a bot named to approve. Without it the wait is the task's own work. The list is never empty for an open task. A Done or Closed task returns `[]`.

- `room`: everything is met. The line is "No room in <stage>" when the next stage is full, and "Moving to <stage>" when
  it has room and the engine has not passed yet.
- `by_hand`: in a by-hand stage nobody is chased for a requirement that is not met. People named on a requirement are
  still listed.
- `holder`: the owner; at level `manager`, or with no owner, the pipeline's manager; at level `person`, `person_for`.
  A person's own task stays theirs at every level. An owner or manager who is a person who left gives `person_for`.
- `person_for`, first that is a person who is here (not left or removed): the person the ladder already chose, the
  task's owner where that is a person, the pipeline's owner or manager where that is a person, the owner of its manager
  bot, the owner of the bot that has the task, whoever asked for the task. Else the company owner, else the person who
  has been here longest (first on the roster). Anyone who left, was removed or cannot sign in is skipped, so it is None
  only when nobody can sign in.
- A cause that pauses a bot is in the list of the bot's owner, else the company owner when that owner left
  (`company_person`: whoever has been here longest when the owner cannot act).
- A bot that has the task may leave it waiting on something it started: a question or an OK it added, or a task it
  filed. Until that ends, the rest of what is missing is nobody's turn: the task waits on the person, or on the filed
  task, and the bot is not woken. A person's own task stays in their list.
- `paused`: the line is the cause as a sentence, "Builder has no computer". Nobody is woken, and the task carries on
  when the cause is fixed.
- At level `person` the first wait is the person's, and its line says why: "Late in <stage>" or "Stuck in <stage>".
- `needs` leaves out snoozed tasks and returns `kind`, `task_id`, `number`, `title`, `pipeline_id`, `stage_id`,
  `requirement_id`, `line`, `missing`, `since`, oldest first. A task whose work is the person's is one entry (`kind`
  `task`, `missing` lists every line), however many requirements are missing. Each OK or answer that names them is its
  own entry (`kind` `ask`). Paused tasks are one entry per cause (`kind` `paused`, with `line`, `bots`, `tasks`,
  `since`), in the list of the person who can fix it: the company owner for a cause that stops the whole company, else
  the bot's owner.

## task_wakes.py

```python
def key(task_id, visit_id, level="owner", try_n=0) -> str        # <task id>:<visit id>:<level>:<try>
def body(conn, task, lead="") -> str                             # the stage, what is missing, the five endings
def add(conn, task, target, visit, text, *, level="owner", try_n=0, wake_key=None, quiet=False) -> str | None
    # None when the key exists or there is nobody to tell. A wake still queued for that bot and task is folded in:
    # its words first, then these, and it is canceled, so one run starts. A quiet note folds nothing.
def cannot_run(conn, actor, seen=None) -> str | None   # why this bot cannot run now; None when it can
def cause_line(conn, actor, cause) -> str       # the cause as a sentence: "Builder has no computer"
def queued(conn, task_id, actor) -> bool        # a wake for this task is waiting for this bot, held ones included
def live_run(conn, task, actor, now=None) -> bool      # a run on this task whose lease is still renewed
def last_run(conn, task, actor) -> dict | None  # the bot's newest run on the task: `state`, `finished_at`
def runs_since(conn, task, since) -> int        # bot runs started on the task since then, whoever ran
def lost(conn, task, actor, since) -> dict | None      # its newest wake in this visit since then queued a run (refs.job),
                                                       # the job is gone or canceled and no run of it started: id, body
def woken_since(conn, task_id, actor, since) -> bool   # a wake about the task was written for them since then
def drop(conn, task_id, keep=None) -> int       # cancel every wake still queued for the task, but `keep`'s (one bot
                                                # or several)
def cancel(conn, wake_key) -> int                # cancel one wake by its key, while no run has taken it
```

The wake is a message from the engine in the task's conversation, with `refs.wake = "task_turn"` and the task, visit,
level, try and version beside it. `task_turn` is a trigger in `run_triggers.TRIGGERS`, an always-wake event in
`wake_rules.ALWAYS_WAKE` and a floor event in `rules`: no rule holds it. `add` writes the message itself and does not go
through `hubdb._wake`, whose holds belong to the old engine.

`cannot_run` reads saved state only, and asks no computer. Its causes, first that applies: the bot was removed; its
state is not active ("is paused", "is quarantined", "has not started yet"); "The company is paused"; "Tico Cloud is
locked"; "Tico Cloud is out of credit" (a bot on Tico's model only); "has no computer" (no assignment, or a revoked
computer; a computer that is offline is not a cause); "keeps failing its runs" (held by `run_backoff`). The three whole
sentences stop every bot they touch and are one Needs-you row for the company owner. Still to add: a login to
reconnect, and over a limit.

## never_stuck.py

```python
GAPS = (120, 900, 3000)   # seconds a bot is left alone before try 1, 2 and 3
TRIES = 3
OWNER_RUNS = 4            # the owner's start and 3 tries
RUNS = 7                  # then the manager bot's 3 tries: bot runs one visit may start between two changes
LATE_PERSON = 3           # times a stage's expected time before a late task lands in a person's list

def check(conn, now=None, failures=None) -> dict[str, int]
    # One pass, in the caller's write transaction: snoozes that ended, then every open task in a work stage that is
    # not by hand and not snoozed, oldest visit first. Returns snoozes_ended, moved, woken, to_manager, to_person, late.
    # Each task is done in its own savepoint. One that raises is rolled back and added to `failures` as
    # (task id, error), and the pass goes on to the next; with no list the error is raised. A row that names a task
    # that was deleted (an open snooze, a visit, a queued wake, a requirement) is passed by: no read raises on it.
def rules_hold(conn) -> list[str]
    # The seven rules that hold at all times. One sentence per break, starting with the rule's number. Empty: all hold.
```

Registered as one Heartbeat step, `never_stuck`, every beat (`scheduler.step_never_stuck`). A task it could not check
is reported like any failed row, and the step fails with "N tasks could not be checked" while the rest of the pass is
kept.

What a pass does with one task:

- Everything is met and there is room: `advance`, and on through any stage that needs nothing.
- The progress mark differs from the one kept on the visit and nothing announced it: `task_engine.changed`. No try is
  counted, and while no run has started since, the bot is woken again after the first gap with "This task is not
  finished. Carry on."
- A bot's turn (the bot with the task's own work first, then a bot named on an OK or an answer): nothing while it has
  a live run or a wake queued for the task. A lost wake is not a try: when its newest wake since the last change never
  started a run and its job is gone (`task_wakes.lost`), the same words are written again on this pass, keyed
  `<task>:<visit>:lost:<message id>`, and nothing is counted. A wake that never queued a run (a bot that reads its own
  inbox) is not lost. When no run of any bot has started since the last change, the next wake is not a try either (the
  "Carry on" below). A try is counted only after a run that started since the last change ended without one.
  Otherwise, once `next_try_at` has passed and the gap has passed since its
  last run ended, try `n` of 3 with a wake keyed `<task>:<visit>:<level>:<n>:<last change>`. The gap after try 3 is
  2 minutes, then the task goes up: to the pipeline's manager bot, if it is a bot that can run and is not the one that
  just tried, else to a person. The manager bot's first try is sent in the same pass and says who tried. A try is
  counted even when its wake could not be written, so the ladder always climbs. Going up cancels the wakes no run has
  taken yet: for a bot with no turn when it goes to the manager bot (a bot named on an OK or an answer that still
  waits keeps its wake), every one when it goes to a person. A run already going is left alone.
- Seven runs: every bot run that started on the task since the visit's last change counts (`task_wakes.runs_since`),
  whoever it was for and whatever woke it. With 4 the owner gets no more tries and the task goes up; with 7 the manager
  bot gets none and it goes to a person.
- Level `person`: nothing more is sent. The task is in that person's Needs you until a person acts (`try_again`,
  reassign, an answer, a move).
- Late: when the time in this visit, less the time snoozed, passes the stage's expected time, `late_told_at` is set,
  once per visit. A bot's turn (even beside a person's ask) goes to the manager bot on the same 3 tries, at level
  `owner` only: never a second set, never down from a person. A task where no bot has a turn, that waits on a person,
  on other tasks or on the outside, gets one wake to the manager bot, keyed `<task>:<visit>:late`, within the 7 runs. A
  paused task gets nothing:
  its cause is already in a person's list. At 3 times the expected time `late_person_at` is set and the task goes to a
  person, unless it is already a person's turn or paused.

History events it writes: `woken_again` (data: level, try), `ladder` (old and new level), `late`.

It is the only caller of `move()` with `by=SYSTEM`, through `advance`.

What `rules_hold` reads for each rule:

1. Every open task has at least one wait, and no `tasks` wait names a sub-task whose `room` wait is for the full stage
   the filer is in.
2. Every bot's turn on a task that is not snoozed has a live run, a queued wake, a next try time, or a level above
   `owner`.
3. Every person's turn is in that person's `needs` exactly once: one `task` entry for the task's own work, and one
   `ask` entry per requirement that names them. A person's turn that names nobody is a break while anyone can sign in.
4. The trigger exists, the guard is closed, and every task on the engine has a status that fits its stage, a stage of
   its own pipeline, and exactly one open visit, in that stage.
5. At most one handoff per visit; no handed-off task still open after its visit was left by Back or Close; no
   `queue_pr` effect of such a visit without its `dequeue_pr`.
6. In a stage with a limit, the open tasks that came in by a forward move since the stage last changed, and were never
   snoozed there, number at most the limit. A sub-task filed while its filer was in the stage is not counted.
7. No wake is queued for a task that is snoozed, Done or Closed.

## answers.py

The only writer of `answers` rows apart from the `pushed_past` rows `move()` writes. A stage's requirement applies to
every task in the stage, so each call names the task.

```python
answers.answer(conn, task_id, requirement_id, by, value, notes=None, *, seen_version=None) -> dict
    # done | ok | no (notes required) | again (the owner, after a no) | the choice or any text (a question).
    # On a question every value is just the answer: "no" needs no notes and sends nothing back.
    # Then task_engine.advance: the last thing missing moves the task in the same step. The same answer twice is one.
answers.give_to(conn, task_id, requirement_id, by, person) -> dict
    # A requirement added to one task: its person_id. A stage's OK: a `give` answer. owner_done: task_engine.reassign.
    # `person` is a person who is here; anyone else is refused task_requirement.
answers.undo(conn, answer_id, by) -> dict
    # Still in that visit: undone_at. Moved on by it, and not since: task_engine.move back to that stage.
answers.thumbs_up(conn, task_id, requirement_id, by) -> dict
answers.thumbs_down(conn, task_id, requirement_id, by, reason, *, send_back=True) -> dict
    # A bot's ask on one task: withdrawn, the bot woken once with the words. A stage's OK: answer(..., "no", reason).
answers.thumbs_down_count(conn, bot, days=30) -> int      # the third in 30 days opens the rule sheet (C21)
answers.ask_subject_id(visit_id, requirement_id) -> str   # "<visit id>:<requirement id>"
answers.ask_of(conn, subject_id) -> dict | None           # {task_id, visit, requirement, bot}
```

Who answers:

- `done`: the task's owner, the pipeline's manager, or any person.
- `ok` and `no`: whoever the requirement waits on. A stage's OK that names nobody, or only people who left, is any
  person's. A bot answers an OK or a question only when it is named. `again`: the task's owner, or whoever holds it now.
- A question: the person it waits on, with a choice or any text that is not empty.
- `pushed_past` is never accepted. Kinds other than `owner_done`, `ok` and `answer` are not answered by hand.
- A bot sends `seen_version` with every answer; a person may leave it out. `undo`: whoever answered, or any person.
  Thumbs: people only, on an `ok` or `answer` whose bot is the one that added it (one task) or owns the task (a stage).

Refusals: `task_requirement` (cannot be answered as asked: wrong person, wrong value, withdrawn, not an ask),
`task_why` (a `no` on an OK, or a send back, with no words), `task_changed` (a stale version, or a stage the task
has left), `task_ended`, `not-found`.

Wakes, once each, never about a snoozed task, written even when the bot can't run yet: after a `no` on an OK that left
the task where it was, to whoever holds the task, with the notes word for word; after a question or an OK that was
added to the task is answered, to the bot that asked, with the ask and the answer (key
`<task>:<visit>:answer:<answer id>`); after a thumbs-down send back, to the bot that asked (key
`<task>:<visit>:thumbs_down:<feedback id>`). Undo cancels its answer's wake if no run took it.

An answer, a give and an undo that leave the task where it is call `task_engine.changed` before any wake: the
never-stuck ladder starts again.

Thumbs are `feedbacks` rows written through `feedback.rate`, the writer `feedback.give` also uses. Their subject type
is `ask` (migration 20261110020000 adds it to the table's CHECK) and their subject id `<visit id>:<requirement id>`
(`answers.ask_subject_id`; `answers.ask_of` reads one back). `POST /api/v2/feedback` refuses an `ask`: it is rated
only here. The feedback list, markers, removal and rule draft read an ask as its task: shown to whoever reads the task,
left out for everyone else. The rule made from a 👎, and the rule check on new asks, come later.

No answer, give or thumb changes `tasks.version`. Giving the owner's own `done` away is a reassign, which does.

## stage_effects.py, pipeline_settings.py

```python
stage_effects.enter(conn, task, by) -> None        # file a linked task; add a queue_pr effect row. Neither is built
                                                   # yet: a stage that has one (saved before the refusal) does
                                                   # nothing on enter, and never raises into the move
stage_effects.undo(conn, task, by, note) -> None   # close tasks filed in the visit; add dequeue_pr rows
stage_effects.filed_open(conn, visit_id) -> list[str]
stage_effects.run(conn_factory) -> int             # the GitHub calls still owed (phase 3)

pipeline_settings.read(conn, pipeline_id) -> dict | None    # one record: pipeline, stages, requirements
pipeline_settings.save(conn, by, record, seen_version=None) -> dict   # create or change; returns read()
pipeline_settings.retire(conn, by, pipeline_id, seen_version=None) -> dict
pipeline_settings.check(conn, record) -> list[str]           # every problem, as sentences; empty = can be saved
pipeline_settings.may_change(conn, by, pipeline_row) -> bool # owner, Admins, the pipeline's owner and manager
pipeline_settings.PRESETS: dict[str, dict]                   # general (phase 1), product, engineering
pipeline_settings.preset(conn, key) -> dict                  # a preset's record for this org, manager filled in
pipeline_settings.ensure_presets(conn) -> list[str]          # boot: makes the presets the org lacks; the ids made;
                                                             # unedited wording follows the preset (EARLIER)
                                                             # an older Engineering gets QA once (_add_qa)
```

General's ids are fixed: pipeline `general`, stages `general-todo`, `general-done`, `general-closed`.

The record `read` gives and `save` takes. Keys are the column names. `settings_version`, `retired_at`, `done` and
`closed` are read only. Only work stages are in `stages`, in order; Done and Closed are made and kept by `save`.

```
{"id", "name", "goal", "prefix", "manager_id", "owner_id", "private", "factory_id", "preset", "settings_version",
 "retired_at", "done": {"id", "name"}, "closed": {"id", "name"},
 "stages": [{"id", "name", "purpose", "owner", "by_hand", "task_limit", "expected_minutes", "expected_bots_only",
             "on_enter", "requirements": [{"id", "kind", "settings", "can_push_past", "who_oks", "person_id",
                                           "line"}]}]}
```

- `on_enter` is `{}`, `{"file_into": "<pipeline id>"}` (refused until phase 2: "Filing into a pipeline comes with the
  Product pipeline") or `{"act": "github.queue"}` (a source action).
- A row with an id is changed in place; without one it is made: a stage as `<pipeline id>-<slug of its name>` (`-2`,
  `-3` on a clash), a requirement as `H.new_id()`. A stage left out is retired (refused while it holds open tasks), a
  requirement left out is withdrawn. A requirement's kind does not change in place. A requirement id is listed once in
  the whole record, across every stage.
- A stage's owner applies when a task enters the stage: changing it does not reassign the tasks already there.
- An OK on a stage names someone who is here: a person who has not left and can sign in, or a bot that exists (a
  named bot makes it that bot's turn). The pipeline's manager and owner, and a stage's owner, are the same.
- `save` refuses `task_settings` with the first problem `check` finds, or who may not change it, and `task_changed`
  when `seen_version` is not `settings_version` (a bot must give it to change a pipeline). Each save raises
  `settings_version` by one and writes one `events` row, `pipeline.save`, with `version` and `changes`
  (`{path: [old, new]}`). `retire` writes `pipeline.retire`. A save that removes a requirement, raises or lifts a limit,
  or turns off by hand calls `task_engine.fill_open_slots` on the pipeline's stages.

## Product and Engineering

Release 0.6. The shared base is on `chris/pe-base`; the full contract (owners, tracks, choices) is
`reports/2026-11-09-pipelines-v3/resume/pe-contract.md` in the planning repo. Tables
(`20261110213830_product_engineering_facts.py`):

```
item_metadata      id, source, item_type, item_key (nocase), title, fields (json: a PR's state (open|closed|merged), draft, head_sha, head_seen_at,
                   checks_state (none|pending|success|failure), checks (json), checks_started_at,
                   merged_sha, merged_at, queue_state (none|queued|auto_merge|removed), queued_at,
                   removed_at, removed_reason, failing_check, failing_check_url,
                   ...), metadata_version (ms), read_at, due_at, read_error, refused_at, gone_at, final_at,
                   created_at, updated_at
                   unique (source, item_type, item_key)
releases           id, repo, tag, commit_sha, prerelease, published_at, metadata_version, read_at, created_at
                   unique (repo, tag)
release_reads      repo (pk), read_at, metadata_version     a read happened, even with no releases (C31)
release_commits    release_id, commit_sha, included, checked_at              pk (release_id, commit_sha); index (commit_sha)
github_deliveries  id (X-GitHub-Delivery), event, received_at               kept 7 days
requirement_pages  id, visit_id, requirement_id, file_id, version, heads (json), attached_by_id, created_at,
                   replaced_at            one not replaced per (visit_id, requirement_id)
page_verdicts      id, file_id, version, rubric_hash, verdict (null|met|not_met|unsure|failed), p, reasons,
                   model, cost_usd, tries, last_error, next_try_at, created_at, updated_at
                   unique (file_id, version, rubric_hash)
effects            + next_try_at (the item and its pinned head are on every effect)
```

Modules: `item_sources/github.py` (the GitHub source: metadata, the `GitHub` protocol, the queue action),
`item_metadata.py` (every source's saved metadata), `source_actions.py` (act, undo, retries), `page_verdicts.py` (Jev's verdict store and step),
`task_pages.py` (attach), `pipeline_seats.py` (roles to bots, the OK
person). Tests and the rig use `backend/tests/helpers/fake_github.py`.

### Standings of the Product and Engineering kinds

`owner` is the task's owner (for Merge, the stage owner: Release Owner). A requirement never calls GitHub or Jev.

```
page          no page attached                          not_met  owner   No page attached
              at_head and a linked PR's head moved      not_met  owner   New commits since the review
              verdict owed                               waiting  jev     Jev is checking
              verdict failed (3 tries), a bot holds it,  waiting  manager Decide if it meets the check: OK it or send it
                manager bot set (not the holder)                          back, and say why. Jev couldn't check this:
                                                                          <error>
              not_met (not a Spec page a person OKs)     not_met  owner   Jev sent it back: <the missing parts, one line
                                                                          each, at most three>
              not_met, Spec page a person OKs            waiting  person  [Version <n>, after the send back: "<notes>". ]
                                                                          Jev: <the missing parts>
              unsure or failed, a person OKs it anyway   waiting  person  [Version <n>, after the send back: "<notes>". ]
                (people only or naming one: every version)               OK <name> (version <n>)  (nothing from Jev)
              unsure, a bot holds it, manager bot set    waiting  manager Decide if it meets the check: OK it or send it
                (not the holder; woken once per version)                  back, and say why. Jev is unsure: <the parts
                                                                          it can't tell>
              unsure or failed otherwise (a person       waiting  person  Jev is unsure: <parts> (once per visit, then
                holds it, no manager bot, the manager                     not_met on the owner)
                holds it)
              met, person_id set, no OK for this version waiting  person  [Version <n>, after the send back: "<notes>". ]
                                                                          OK <name> (version <n>)
              person said no for this version            not_met  owner   Sent back: <notes>[ Attach the new version; Tico
                                                                          asks the person.] (one end mark, never two)
              met and OK'd for this version              met              OK'd
              met, no person                             met              Jev passed it
```

A page a person OKs that is not `at_head` (the QA page) is not asked of the person while a linked PR's
newest commit is one no bot reviewed: no met Review page (`at_head`, Jev passed or OK'd) was attached at it, and no
bot OK'd it as below (`task_requirements._new_heads`). Once Jev has looked, it waits on the bot that attached the
newest met Review page (`delta_reviewer`; never the task's owner now): "Review the new commits since the reviews (now
at <sha>): read only them, against what the reviews found and the spec. OK them or send them back, and say what you
read." That bot answers on the page (`tico task answer <id> <requirement> ok|no -m ...`, notes required either way):
the answer keeps the commits it was given at (`answers.heads`). Its OK makes those commits reviewed, and the person's
ask then starts "<bot> reviewed the commits since the reviews: <notes>." Its no is `not_met` on the owner: "<bot>
sent back the new commits: <notes>". A round is one set of commits it answered at; after `DELTA_ROUNDS` (2) rounds in
a visit, or with no such bot, the person is asked with "New commits since the reviews (now at <sha>): not reviewed by
a bot." (Tico keeps no commit count). A person's OK on that page also keeps its commits (`task_requirements._after_ok`,
C22). After a new commit, `item_sources.github.same_changes` compares what each moved PR changes (`github_pr_diffs`,
read once per commit by the re-read's `_refresh_diffs` through `GitHub.pr_diff`: GitHub's compare `<base>...<sha>`,
hashed by `diff_id` from the added and removed lines of each file); until both are read the page waits on GitHub
("Reading what the commits since <name>'s OK change"). The same lines: met, "OK'd by <name>; the commits since change
the same lines". Other lines: the delta review above, its rounds counted from the OK; the reviewer's OK at the newest
commits is met ("OK'd by <name>; <bot> read the commits since"). After its no, with no such bot, or with the rounds
spent, the OK is gone: the ask starts "New commits since <name>'s OK." and goes to that person. Back from Merge with no
page attached on the visit (`page_for`), the OK and page of the QA visit before Merge count; when that OK is gone,
the Builder is asked to attach the page again. In Merge a bot's `requeue` of a moved head needs the same lines
(`COMPARING` while unread, else `SENT_BACK_FOR_NEW`). Answers from before `heads` was kept stand. The Builder's wake says "not
reviewed by a bot" only when no other bot will review them. A waiting page's `since` is when that version was
asked (its attach, or Jev's verdict when later), not when the stage began. The manager bot asked to decide answers with
`tico task answer <id> <requirement> ok|no -m "<why>"`; its forward move with a reason is kept as that OK, not a push
past.

```
pr_open       no PR linked                               not_met  owner   No PR linked
              no metadata yet                            waiting  github  Reading the PR from GitHub
              every PR closed without merging            not_met  owner   <ref> was closed without merging
              an open or merged PR                       met              PR open
checks_pass   a check failed                             not_met  owner   Checks failed on <ref>: <names>
              running < 2 hours                          waiting  github  Checks running
              running >= 2 hours                         not_met  owner   Checks still running after 2 hours
              no checks, < 10 min since head seen        waiting  github  Waiting for checks
              no checks after 10 min (C13)               met              No checks on this repo
              all pass                                   met              Checks pass
pr_merged     effect owed                                waiting  github  Queuing the PR
              queued / auto-merge on                     waiting  github  In the merge queue
              GitHub refused the queue call 3 times      not_met  owner   GitHub refused the merge: <reason>
              removed from the queue                     not_met  owner   Dropped from the merge queue: <reason>
                                                                          (<failing check> when known)
              every PR merged                            met              Merged
in_release    a PR not merged                            waiting  github  Not merged yet
              releases never read                        waiting  github  Reading releases
              repo publishes none (C31)                  met              This repo doesn't publish releases
              merged, no release holds it yet            waiting  github  Waiting for the next release of <repo>
              in a counted release                       met              In <tag>
```

With many PRs the first unmet one, in link order, gives the line.

### Choices made while building

- **Room in Spec (C10)** wakes nobody. A person's request in Ideas takes the room by itself (G5, Nov 10); a bot's
  own idea waits for a person (Nov 12: the "has room" wake was removed).
- **The two sign-offs are people only** (Spec page, QA page). A bot that may change a pipeline (its
  manager or owner; BotOps may not) is refused, "This change loosens the person's OK on Spec. A person must make it.",
  when its save would remove a people-only OK or its stage, move it to another stage, change its kind, open it to
  bots, name a bot on it, put a later stage before it, add or change an on-enter action at or before it, or replace
  the stages wholesale (`pipeline_settings._loosens_people_only`); and when it retires a pipeline that has one. A
  forward move counts every jumped stage as not met, so no bot jumps one; a task never moves into another pipeline;
  and a bot never files a task straight into a stage past one (`task_engine.enter`'s `start`). The rubric, the push-
  past switch and the stages after it stay a bot's to change. An untouched preset made before is tightened by `_fill`.
- **Closing a Product task** closes the Engineering tasks it filed, except one whose PR merged: its work is in the code,
  so it stays open to reach a release or be closed by its owner (`stage_effects.merged_work`, scenario 24). Undoing a
  stage's filing skips it the same way.
- **A person is asked once per requirement per visit.** A page's ask offers OK and Send back only.
- **Queue again** (`tico task queue`) covers a PR GitHub refused to queue as well as one it dropped from the queue.
- **A drop from the merge queue is the owner's turn at once**, with the reason (one wake per drop). There is no separate
  "after 3 drops" step: the owner already has the turn.
- **GitHub down for days:** the metadata waits, the stage turns Late after its expected time and the manager bot looks once,
  then a person's list at 3 times the expected time (rig case `github-down-for-days`).
- **The Release Owner** is the bot the release schedule names, else the first active bot made from `release-owner`.
  `release-notes-writer` is the Release Notes Writer and never stands in.

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

[pipelines-v3-cutover.md](pipelines-v3-cutover.md) is the plan, and it wins where this file says otherwise. In short:

- No task is carried over. Every task is deleted, the old tables are dropped, and every company starts on General.
- One branch, built by tracks that each own their files, merged once.
- Everything outside the engine uses the task calls (`backend/task_calls.py`, re-exported by hubdb). The engine calls
  `backend/task_effects.py` at the end of every move and reassignment, for what a move must still cause.
- The trigger loses its first `WHEN` line and gets its `BEFORE INSERT` twin.

The version check must be real for bots: the bot tool reads the task, the bot decides, and the write carries the version
the bot read. A tool that fetches the version just before it posts checks nothing.

# pyright: strict
"""The never-stuck check (docs/pipelines-v3.md, "The never-stuck check").

    check(conn, now=None) -> dict[str, int]      one pass over every open task, on the Heartbeat every 10 seconds
    rules_hold(conn) -> list[str]                the seven rules that hold at all times; empty when they do (each
                                                 sentence a `Break`, carrying the ids of the tasks it is about)

`check` reads tables only: no GitHub, no Jev, no computer, no model. It looks at state, not events, so a lost wake or a
crash only delays things until the next pass. For every open task it asks what is missing, who has it, and whether they
are on it:

- Everything is met and there is room: the task moves.
- A bot's turn: nothing while the bot has a live run or a wake queued for the task. Otherwise the ladder: the bot is
  woken again 2 minutes after it stopped, then 15 minutes, then 1 hour. After 3 tries with no change the pipeline's
  manager bot gets the task on the same 3 tries, then a person. If the task changed, the count goes back to zero.
- Paused, a person's turn, other tasks, room, by-hand stages and snoozed tasks: nothing. `waiting_on` shows them.
- Late: past the stage's expected time the manager bot gets the task once per visit; at 3 times the expected time it
  lands in one person's list, once.
- A snooze whose date came ends, and the owner is woken once.

A pass is one write transaction, opened by the caller. Each task is done on its own inside it: a task the check cannot
work out is left as it was and named in `failures`, and the pass goes on to the next one. Doing a pass twice is
harmless: every wake has a key made from the task's visit, the level, the try and when the task last changed.

Names and signatures are in docs/dev/pipelines-v3-build.md.
"""

from collections.abc import Callable, Iterable
from typing import TYPE_CHECKING, Any

from . import batch_work, hubdb_core, task_wakes
from . import task_engine as E
from . import task_requirements as R
from . import waiting_on as W

if TYPE_CHECKING:
    import sqlite3

Row = dict[str, Any]

# hubdb_core is untyped: its actor helpers get their types here.
_core: Any = hubdb_core
is_bot: Callable[[object], bool] = _core.is_bot
_batch: Any = batch_work
storage_error: Callable[[BaseException], bool] = _batch.storage_error
SYSTEM: str = E.SYSTEM

# Seconds a bot is left alone after it stopped, before try 1, try 2 and try 3. After try 3 the task goes up a level
# once the first gap has passed.
GAPS = (E.FIRST_TRY_S, 900, 3600)
TRIES = len(GAPS)
# Bot runs one visit may start between two changes: the owner's start and 3 tries, then the manager bot's 3 tries.
# Every run on the task counts, whoever it was for and whatever woke it (R1). The ladder holds to them, and the claim
# of a job asks `task_wakes.may_start`, which holds the 7 at the moment a run starts.
OWNER_RUNS = 1 + TRIES
RUNS = task_wakes.RUNS
assert RUNS == 1 + 2 * TRIES
HOPS = 20  # stages one task is taken through in one pass, at most
LATE_PERSON = 3  # times a stage's expected time before a late task lands in a person's list (C5)
CUT_OFF = ("expired", "failed", "interrupted")
MANAGER_HAS_IT = "It is yours now: do it, reassign it, ask a person, snooze it or close it."

_OPEN = (
    "SELECT t.id FROM tasks t JOIN visits v ON v.task_id = t.id AND v.left_at IS NULL "
    "JOIN stages s ON s.id = t.stage_id "
    "WHERE t.pipeline_id IS NOT NULL AND t.status='open' AND s.kind='work' AND s.by_hand=0 "
    "AND NOT EXISTS (SELECT 1 FROM snoozes z WHERE z.task_id = t.id AND z.ended_at IS NULL) "
    "ORDER BY v.entered_at, v.id"
)


def _h() -> Any:
    from . import hubdb

    return hubdb


def _part(row: Row, key: str) -> Row:
    found: Row | None = row.get(key)
    return found or {}


def _name(conn: "sqlite3.Connection", actor: str | None) -> str:
    return str(_h().actor_name(conn, actor)) if actor else "Nobody"


def _event(conn: "sqlite3.Connection", task_id: str, kind: str, old: str | None, new: str | None, data: Row) -> None:
    _h().history_event(conn, "task", task_id, SYSTEM, kind, old, new, None, data)


def _usable(conn: "sqlite3.Connection", bot: str | None, seen: dict[str, str | None]) -> bool:
    """A bot that can be handed a task now."""
    return is_bot(bot) and task_wakes.cannot_run(conn, bot, seen) is None


def _reads(conn: "sqlite3.Connection", actor: str | None, row: Row) -> bool:
    """Whether `actor` may read this task already. The engine never adds a reader (docs/pipelines-v3.md: private
    tasks, "Who can see them doesn't change"), so on a private task the ladder skips a manager bot that cannot read it,
    and a bot that cannot read it is never woken: the task goes to a person who may."""
    return bool(actor) and bool(_h().task_private_readable(conn, actor, row))


def _manager(conn: "sqlite3.Connection", row: Row, seen: dict[str, str | None]) -> str | None:
    """The pipeline's manager bot when it can take this task now: it can run, and it reads the task."""
    manager: str | None = _part(row, "pipeline").get("manager_id")
    return manager if _usable(conn, manager, seen) and _reads(conn, manager, row) else None


# ----------------------------------------------------------------------------- the pass
def check(
    conn: "sqlite3.Connection", now: str | None = None, failures: list[tuple[str, Exception]] | None = None
) -> dict[str, int]:
    """One pass. Returns what it did: snoozes ended, tasks moved, bots woken, tasks handed to a manager bot, tasks
    handed to a person, and tasks that became late. A task that raised is rolled back on its own and added to
    `failures` with its error; without the list, the error is raised."""
    H = _h()
    at: str = now or H.now()
    done = {"snoozes_ended": 0, "moved": 0, "woken": 0, "to_manager": 0, "to_person": 0, "late": 0}
    seen: dict[str, str | None] = {}
    live = task_wakes.live_bots(conn, at)  # a pass starts no run: read once
    # A snooze of a task that was deleted, or is not on the engine, is no task's: it is left alone.
    due = conn.execute(
        "SELECT z.task_id FROM snoozes z JOIN tasks t ON t.id = z.task_id AND t.pipeline_id IS NOT NULL "
        "WHERE z.ended_at IS NULL AND z.until_at IS NOT NULL AND z.until_at <= ? ORDER BY z.until_at, z.id",
        (at,),
    ).fetchall()
    todo = [(str(found[0]), True) for found in due]
    todo += [(str(found[0]), False) for found in conn.execute(_OPEN).fetchall()]
    for task_id, wake_up in todo:
        conn.execute("SAVEPOINT never_stuck_task")
        try:
            if wake_up:
                E.unsnooze(conn, task_id, SYSTEM)
                done["snoozes_ended"] += 1
            else:
                _one(conn, task_id, at, done, seen, live)
        except Exception as error:
            # SQLite may already have ended the transaction (a full disk, an I/O error): that stops the pass.
            if failures is None or not conn.in_transaction or storage_error(error):
                raise
            conn.execute("ROLLBACK TO never_stuck_task")
            failures.append((task_id, error))
        finally:
            if conn.in_transaction:
                conn.execute("RELEASE never_stuck_task")
    return done


def _one(
    conn: "sqlite3.Connection",
    task_id: str,
    at: str,
    done: dict[str, int],
    seen: dict[str, str | None],
    live: set[str],
) -> None:
    row = E.task(conn, task_id)
    if not row or row["status"] != "open" or row.get("snooze") or row["stage"].get("by_hand"):
        return  # a move earlier in this pass took it
    standings = R.standings(conn, row)
    waits = W.of(conn, row, seen, standings)
    if W.all_met(waits) and E.advance(conn, task_id):
        done["moved"] += 1
        for _ in range(HOPS):  # stages that need nothing are passed in the same step
            if not E.advance(conn, task_id):
                break
        return
    _ladder(conn, row, waits, E.progress_mark(conn, row["visit"]["id"], standings), at, done, seen, live)
    _late(conn, row, at, done, seen)


# ----------------------------------------------------------------------------- the ladder
def _ladder(
    conn: "sqlite3.Connection",
    row: Row,
    waits: list[W.Wait],
    mark: str,
    at: str,
    done: dict[str, int],
    seen: dict[str, str | None],
    live: set[str],
) -> None:
    H = _h()
    visit = row["visit"]
    level: str = visit.get("level") or "owner"
    kept = visit.get("progress_mark")
    if kept is None:
        conn.execute("UPDATE visits SET progress_mark=? WHERE id=?", (mark, visit["id"]))
    elif kept != mark:
        # The task changed and nothing announced it: a bot did part of the work in a run. No try is counted, and the
        # check looks again after the first gap.
        E.changed(conn, row["id"], SYSTEM)
        return
    # Whoever has the task's own work, before a bot that was only asked for an OK or an answer; the manager bot at
    # level manager.
    target = W.turn(conn, row, waits)
    manager = _manager(conn, row, seen)
    if target and ((level == "manager" and not manager) or (is_bot(target) and not _reads(conn, target, row))):
        # The manager bot cannot take it any more, or the bot whose turn it is cannot read this private task: a person.
        _to_person(conn, row, level, done)
        return
    epoch: str = visit.get("last_change_at") or visit["entered_at"]
    tries = int(visit.get("tries") or 0)
    # Before the ladder tries anyone, every bot that has a turn gets back a wake it lost (R3).
    woken, spent_by = _recover(conn, row, target, waits, level, tries, epoch, at, done, live)
    if not target or target in woken:
        return
    if task_wakes.live_run(conn, row, target, at, live) or task_wakes.queued(conn, row["id"], target):
        return
    inbox = task_wakes.inbox(conn, row, target)
    if inbox and not inbox["read_at"]:
        # A bot that reads its own inbox has not read its wake yet: it is on its way, as a queued one is. Nothing more
        # is written; if it never reads, Late takes the task on.
        return
    read: str | None = inbox["read_at"] if inbox else None  # it took its wake then, and nothing ran on the engine
    spent = target in spent_by  # its wake for this try was lost twice: the try is spent
    gap = GAPS[tries] if tries < TRIES else GAPS[0]
    due: str | None = visit.get("next_try_at")
    if not due:
        conn.execute("UPDATE visits SET next_try_at=? WHERE id=?", (H.shift(at, seconds=gap), visit["id"]))
        return
    last = task_wakes.last_run(conn, row, target)
    stopped: str | None = (last or {}).get("finished_at") or read
    if stopped and not _snoozed_since(conn, visit, stopped):
        due = max(due, str(H.shift(stopped, seconds=gap)))  # the gap runs from when the bot stopped, or read its wake
    if at < due:
        return
    runs = task_wakes.runs_since(conn, row)
    cut_off = bool(last) and (last or {}).get("state") in CUT_OFF
    if level == "owner" and tries == 0 and not runs and not read and not spent:
        # Nothing has taken a wake since the task last changed (it changed during a run, and nothing announced it):
        # this wake starts it again and is not a try. A try is counted only after a run that started since the change
        # ended without one, or a wake the bot read in its own inbox (R3). The count of runs stays what really ran.
        lead = "Your last run on this task was cut off." if cut_off else "This task is not finished. Carry on."
        if _wake(conn, row, target, lead, level, 0, epoch):
            conn.execute("UPDATE visits SET next_try_at=? WHERE id=?", (H.shift(at, seconds=GAPS[0]), visit["id"]))
            done["woken"] += 1
            return
        # This change's free wake was written already and no run came of it: the try is spent, below.
    if tries < TRIES and runs < (OWNER_RUNS if level == "owner" else RUNS):
        lead = "Your last run on this task was cut off." if cut_off else "You stopped before this task was finished."
        if level == "manager" and tries == 0:
            lead = f"{_name(conn, row.get('owner_id'))} could not take this task further. {MANAGER_HAS_IT}"
        _try(conn, row, target, level, tries + 1, f"{lead} Try {tries + 1} of {TRIES}.", at, epoch, done)
    elif level == "owner" and manager and manager != target and runs < RUNS:
        _to_manager(conn, row, manager, f"{_name(conn, target)} stopped {TRIES} times without finishing it.", at, done)
    else:
        _to_person(conn, row, level, done)


def _recover(
    conn: "sqlite3.Connection",
    row: Row,
    target: str | None,
    waits: list[W.Wait],
    level: str,
    tries: int,
    epoch: str,
    at: str,
    done: dict[str, int],
    live: set[str],
) -> tuple[set[str], set[str]]:
    """Write again the lost wake of every bot that has a turn and is not on it: no live run, no queued wake, no wake
    unread in its own inbox. The same words, on this pass, and nothing is counted (R3). Once per bot, level, try and
    change: when that one is lost too, the bot's try is spent. A bot named on an ask that was never woken for it (the
    ask came while a person had the task) gets that wake now. Returns the bots woken, and those whose try is spent.

    For the bot the ladder tries (`target`) a wake is lost only since the last change: after a change the ladder wakes
    it anyway. Any other bot (one named on an OK or an answer) is woken by nothing else, so its newest wake in the
    whole visit counts: it gets a turn back only when the task leaves a person or a snooze ends."""
    visit = row["visit"]
    woken: set[str] = set()
    spent: set[str] = set()
    for bot in sorted(W.turns(conn, row, waits)):
        if task_wakes.live_run(conn, row, bot, at, live) or task_wakes.queued(conn, row["id"], bot):
            continue
        inbox = task_wakes.inbox(conn, row, bot)
        if inbox and not inbox["read_at"]:
            continue
        lost = task_wakes.lost(conn, row, bot, whole_visit=bot != target)
        if not lost:
            asks = [w.requirement_id for w in waits if w.who == bot and w.ask and w.requirement_id]
            if any([task_wakes.ask(conn, row, R.get(conn, rid), bot) for rid in asks]):
                done["woken"] += 1
                woken.add(bot)
            continue
        key = f"{row['id']}:{visit['id']}:lost:{_slug(bot)}:{level}:{tries}:{epoch}"
        if task_wakes.add(conn, row, bot, visit, lost["body"], level=level, try_n=tries, wake_key=key):
            done["woken"] += 1
            woken.add(bot)
        else:
            spent.add(bot)
    return woken, spent


def _slug(actor: str) -> str:
    """A bot's id without "bot:", as a wake key holds it."""
    return str(_h().actor_id(actor))


def _snoozed_since(conn: "sqlite3.Connection", visit: Row, since: str) -> bool:
    """A snooze of this visit ended after `since`: the task was set aside after the bot stopped, so its next try is due
    at once, with no gap."""
    found = conn.execute(
        "SELECT 1 FROM snoozes WHERE visit_id=? AND ended_at IS NOT NULL AND ended_at > ? LIMIT 1", (visit["id"], since)
    ).fetchone()
    return found is not None


def _wake(conn: "sqlite3.Connection", row: Row, target: str | None, lead: str, level: str, n: int, epoch: str) -> bool:
    key = f"{row['id']}:{row['visit']['id']}:{level}:{n}:{epoch}"
    text = task_wakes.body(conn, row, lead)
    return bool(task_wakes.add(conn, row, target, row["visit"], text, level=level, try_n=n, wake_key=key))


def _try(
    conn: "sqlite3.Connection",
    row: Row,
    target: str | None,
    level: str,
    n: int,
    lead: str,
    at: str,
    epoch: str,
    done: dict[str, int],
) -> None:
    """Try `n` of 3 at this level. It is counted whether or not the wake could be written, so the ladder always
    climbs."""
    H = _h()
    if _wake(conn, row, target, lead, level, n, epoch):
        done["woken"] += 1
    look = H.shift(at, seconds=GAPS[n]) if n < TRIES else at
    conn.execute("UPDATE visits SET tries=?, next_try_at=? WHERE id=?", (n, look, row["visit"]["id"]))
    _event(conn, row["id"], "woken_again", None, target, {"level": level, "try": n})


def _to_manager(
    conn: "sqlite3.Connection", row: Row, manager: str | None, why: str, at: str, done: dict[str, int]
) -> None:
    """Hand the task to the pipeline's manager bot, told who tried and what is missing: its first of 3 tries. A wake
    still queued for a bot that has no turn now is canceled: it would start a run about a turn that is over. A bot
    named on an OK or an answer that still waits keeps its wake (R2). A live run goes on. When the visit has used its
    7 runs since the last change, a person has it instead."""
    visit = row["visit"]
    epoch: str = visit.get("last_change_at") or visit["entered_at"]
    if task_wakes.runs_since(conn, row) >= RUNS:
        _to_person(conn, row, visit.get("level") or "owner", done)
        return
    conn.execute("UPDATE visits SET level='manager', tries=0, next_try_at=? WHERE id=?", (at, visit["id"]))
    handed = E.task(conn, row["id"]) or row
    task_wakes.drop(conn, row["id"], keep=W.turns(conn, handed) | {str(manager)})
    _event(conn, row["id"], "ladder", visit.get("level") or "owner", "manager", {"why": why})
    done["to_manager"] += 1
    lead = f"{why} {MANAGER_HAS_IT} Try 1 of {TRIES}."
    _try(conn, handed, manager, "manager", 1, lead, at, epoch, done)


def _to_person(conn: "sqlite3.Connection", row: Row, level: str, done: dict[str, int]) -> None:
    """Hand the task to a person. It shows in their Needs you, once, and nothing more runs until a person acts: every
    wake still queued for the task is canceled. A live run goes on."""
    visit = row["visit"]
    person = _person(conn, row)
    conn.execute("UPDATE visits SET level='person', person_id=?, next_try_at=NULL WHERE id=?", (person, visit["id"]))
    task_wakes.drop(conn, row["id"])
    _event(conn, row["id"], "ladder", level, "person", {"person": person})
    done["to_person"] += 1


def _person(conn: "sqlite3.Connection", row: Row) -> str | None:
    """The person a task falls to (`waiting_on.person_for`). On a private task, one who may read it already: the one the
    ladder chose, its owner where that is a person, else whoever asked for it, else a person on its reader list. Nobody
    is made a reader: with no such person, None."""
    H = _h()
    chosen = W.person_for(conn, row)  # on a private task, only a person who reads it
    if not H.task_private(conn, row):
        return chosen
    visit = _part(row, "visit")
    readers = [actor for actor in H.task_readers(row) if H.is_human(actor)]
    for person in (visit.get("person_id"), row.get("owner_id"), row.get("requester_id"), chosen, *readers):
        if R.active_person(conn, person) and _reads(conn, person, row):
            return person
    # Nobody who reads it can act. It goes to no person and takes no bot turns; Health counts it for the company's
    # owner and Admins without naming it, and the rules check reports it held for that cause.
    return None


# ----------------------------------------------------------------------------- late
def _spent(conn: "sqlite3.Connection", visit: Row, at: str) -> float:
    """Seconds the task has been in this visit, not counting the time it was snoozed."""
    H = _h()
    now, entered = H.parse_ts(at), H.parse_ts(visit["entered_at"])
    if not now or not entered:
        return 0.0
    spent = (now - entered).total_seconds()
    for created, ended in conn.execute(
        "SELECT created_at, ended_at FROM snoozes WHERE task_id=? AND visit_id=?", (visit["task_id"], visit["id"])
    ).fetchall():
        start, end = H.parse_ts(created), H.parse_ts(ended or at)
        if start and end:
            spent -= max((end - start).total_seconds(), 0.0)
    return spent


def _span(seconds: float) -> str:
    minutes = int(seconds // 60)
    if minutes >= 2880:
        return f"{minutes // 1440} days"
    if minutes >= 120:
        return f"{minutes // 60} hours"
    return f"{max(minutes, 1)} minutes"


def _late(conn: "sqlite3.Connection", row: Row, at: str, done: dict[str, int], seen: dict[str, str | None]) -> None:
    H = _h()
    stage = row["stage"]
    minutes = int(stage.get("expected_minutes") or 0)
    visit = row["visit"]
    if not minutes or (visit.get("late_told_at") and visit.get("late_person_at")):
        return
    if stage.get("expected_bots_only") and not is_bot(row.get("owner_id")):
        return
    now, entered = H.parse_ts(at), H.parse_ts(visit["entered_at"])
    if not now or not entered or (now - entered).total_seconds() < minutes * 60:
        return
    spent = _spent(conn, visit, at)
    if spent < minutes * 60:
        return
    manager = _manager(conn, row, seen)
    said = f"Late: {_span(spent)} in {stage['name']}, expected {_span(minutes * 60)}."
    if not visit.get("late_told_at"):
        conn.execute("UPDATE visits SET late_told_at=? WHERE id=?", (at, visit["id"]))
        _event(conn, row["id"], "late", None, None, {"expected_minutes": minutes})
        done["late"] += 1
        fresh = E.task(conn, row["id"]) or row
        waits = W.of(conn, fresh, seen)
        head = waits[0] if waits else None
        level = fresh["visit"].get("level") or "owner"
        target = W.turn(conn, fresh, waits)
        if target and level == "owner" and manager and manager != target:
            # A bot's turn: Late hands the task to the manager bot, once per visit, on the same 3 tries. Never a second
            # set, and never back down from a person.
            _to_manager(conn, fresh, manager, f"{said} {_name(conn, target)} has it.", at, done)
        elif head and _looks(conn, fresh, waits, manager, head.who):
            # No bot has a turn: it waits on a person, another task or the outside, and the manager bot looks once. A
            # bot that cannot run is one row for its cause in a person's list, and its tasks carry on when the cause is
            # fixed: nothing is sent for it.
            lead = (
                f"{said} It waits on: {head.line}. Look once: push past with a reason where that is allowed, "
                "reassign it, snooze it or close it."
            )
            key = f"{row['id']}:{visit['id']}:late"
            if task_wakes.add(conn, fresh, manager, fresh["visit"], task_wakes.body(conn, fresh, lead), wake_key=key):
                done["woken"] += 1
    elif not visit.get("late_person_at"):
        _late_look_lost(conn, row, manager, done, seen)
    if spent >= LATE_PERSON * minutes * 60 and not visit.get("late_person_at"):
        conn.execute("UPDATE visits SET late_person_at=? WHERE id=?", (at, visit["id"]))
        fresh = E.task(conn, row["id"]) or row
        waits = W.of(conn, fresh, seen)
        if not (waits and waits[0].state in (W.PERSON, W.PAUSED)):
            # Still there at 3 times the expected time: one person's list, once. Already a person's turn, or paused
            # with its cause in a person's list: nothing.
            _to_person(conn, fresh, fresh["visit"].get("level") or "owner", done)


def _looks(conn: "sqlite3.Connection", row: Row, waits: list[W.Wait], manager: str | None, head: str | None) -> bool:
    """The manager bot looks at a late task once: no bot has a turn, the task is still the owner's, the manager bot is
    not who it waits on, and the visit has runs left since its last change."""
    return bool(
        manager
        and not W.turns(conn, row, waits)
        and (row["visit"].get("level") or "owner") == "owner"
        and manager != head
        and task_wakes.runs_since(conn, row) < RUNS
    )


def _late_look_lost(
    conn: "sqlite3.Connection", row: Row, manager: str | None, done: dict[str, int], seen: dict[str, str | None]
) -> None:
    """Being told Late is not the look: when the manager bot's wake for it was lost (its job is gone and no run of the
    manager bot on the task started since), it is written again with the same words, once, while the look is still
    due, on the same budget, and only to a manager bot that reads the task (`_manager`, `task_wakes.add`)."""
    visit = row["visit"]
    key = f"{row['id']}:{visit['id']}:late"
    told = conn.execute("SELECT id FROM messages WHERE wake_key=?", (key,)).fetchone()
    if (
        not told
        or not manager
        or task_wakes.live_run(conn, row, manager)
        or task_wakes.queued(conn, row["id"], manager)
    ):
        return
    lost = task_wakes.lost(conn, row, manager, whole_visit=True)
    if not lost or lost["id"] != str(told[0]):
        return
    waits = W.of(conn, row, seen)
    if waits and _looks(conn, row, waits, manager, waits[0].who):
        if task_wakes.add(conn, row, manager, visit, lost["body"], wake_key=f"{key}:again"):
            done["woken"] += 1


# ----------------------------------------------------------------------------- the rules that hold at all times
class Break(str):
    """One rule break: its sentence, and the tasks it is about (`tasks`), tied to it when it is found. A reader shown
    only the breaks of tasks they may read is shown them by these ids, never by reading the words."""

    tasks: tuple[str, ...]
    held: bool

    def __new__(cls, text: str, tasks: Iterable[object] = (), held: bool = False) -> "Break":
        made = super().__new__(cls, text)
        made.tasks = tuple(str(task) for task in tasks if task)
        made.held = held  # not a broken rule: the task is held for a cause a person must fix (no one reads it)
        return made


def rules_hold(conn: "sqlite3.Connection") -> list[str]:
    """The seven rules (docs/pipelines-v3.md, "Rules that hold at all times"), checked against the database as it is.
    Returns one sentence per break; empty when they all hold. The fault rig runs it after every step."""
    H = _h()
    broken: list[str] = []
    seen: dict[str, str | None] = {}
    needs: dict[str, dict[str, list[Row]]] = {}  # each person's Needs you, read once and indexed by task
    someone: bool | None = None  # whether anyone can sign in: read once, when a turn names nobody
    now: str = H.now()
    live = task_wakes.live_bots(conn, now)
    open_ids = [
        str(found[0])
        for found in conn.execute(
            "SELECT id FROM tasks WHERE pipeline_id IS NOT NULL AND status='open' ORDER BY id"
        ).fetchall()
    ]
    for task_id in open_ids:
        row = E.task(conn, task_id)
        if not row or not row.get("visit") or not row.get("stage"):
            broken.append(Break(f"4: task {task_id} is open with no open visit or no stage", [task_id]))
            continue
        waits = W.of(conn, row, seen)
        if not waits:
            broken.append(Break(f"1: task {task_id} waits on nothing", [task_id]))
        if row.get("snooze"):
            continue
        visit = row["visit"]
        ladder = W.turn(conn, row, waits)  # the one bot a next try is for
        for wait in waits:
            if wait.state == W.BOT and not (
                (visit.get("level") or "owner") != "owner"
                or task_wakes.live_run(conn, row, wait.who, now, live)
                or task_wakes.queued(conn, task_id, wait.who)
                or (
                    bool(visit.get("next_try_at"))
                    if wait.who == ladder
                    # Any other bot with a turn (an OK or an answer it is named on) was told in this visit: its wake
                    # ran, or it was lost and is written again on the ladder's next try.
                    else _told(task_wakes.told_in_visit(conn, row, wait.who), visit)
                )
            ):
                broken.append(
                    Break(f"2: task {task_id} is {wait.who}'s turn with no run, no wake and no next try", [task_id])
                )
        private = H.task_private(conn, row)
        if any(wait.state == W.PERSON and not wait.who for wait in waits):
            if someone is None:
                someone = W.company_person(conn) is not None
            if private and someone:
                # No person who reads it can act: held for that cause, never handed to someone who cannot read it.
                said = f"3: private task {task_id} has no one who reads it to move it"
                broken.append(Break(said, [task_id], held=True))
            elif someone:
                broken.append(Break(f"3: task {task_id} is a person's turn that names nobody", [task_id]))
        people = {wait.who for wait in waits if wait.state == W.PERSON and wait.who}
        for person in sorted(people):
            if private and not _reads(conn, person, row):
                broken.append(Break(f"3: task {task_id} is {person}'s turn and they cannot read it", [task_id]))
            if person not in needs:
                needs[person] = {}
                for item in W.needs(conn, person):
                    needs[person].setdefault(str(item.get("task_id")), []).append(item)
            listed = needs[person].get(task_id, [])
            mine = [wait for wait in waits if wait.state == W.PERSON and wait.who == person]
            asks = [wait.requirement_id for wait in mine if wait.ask]
            want = len(asks) + (1 if any(not wait.ask for wait in mine) else 0)
            got_asks = sorted(str(item["requirement_id"]) for item in listed if item["kind"] == "ask")
            if len(listed) != want or got_asks != sorted(str(a) for a in asks):
                said = f"3: task {task_id} shows {len(listed)} times in {person}'s Needs you, not {want}"
                broken.append(Break(said, [task_id]))
    broken += _rule_4(conn)
    broken += _rule_5(conn)
    broken += _rule_6(conn)
    broken += _rule_7(conn)
    return broken


def _told(told: str | None, visit: Row) -> bool:
    return told == "told" or told == "lost" and bool(visit.get("next_try_at"))


def _lines(conn: "sqlite3.Connection", sql: str, text: str, tasks: tuple[int, ...] = ()) -> list[str]:
    """One `Break` per row: `text` filled from the row, about the task ids in the columns `tasks` names."""
    return [Break(text.format(*found), [found[i] for i in tasks]) for found in conn.execute(sql).fetchall()]


def _rule_4(conn: "sqlite3.Connection") -> list[str]:
    """Stage, owner and status change only through `move()`: the trigger is there, the guard is closed, and every task
    on the engine has a status that fits its stage, a stage of its own pipeline, and exactly one open visit, in that
    stage."""
    out: list[str] = []
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='trigger' AND name='tasks_move_only'").fetchone():
        out.append("4: the tasks_move_only trigger is missing")
    if conn.execute("SELECT 1 FROM task_move_guards WHERE open <> 0").fetchone():
        out.append("4: the move guard is open")
    out += _lines(
        conn,
        "SELECT t.id, t.status, COALESCE(s.kind, 'no stage') FROM tasks t LEFT JOIN stages s ON s.id = t.stage_id "
        "WHERE t.pipeline_id IS NOT NULL AND (s.id IS NULL OR s.pipeline_id <> t.pipeline_id OR "
        "t.status <> CASE s.kind WHEN 'work' THEN 'open' ELSE s.kind END)",
        "4: task {0} is {1} in a stage that is {2}",
        (0,),
    )
    out += _lines(
        conn,
        "SELECT t.id, COUNT(v.id) FROM tasks t LEFT JOIN visits v ON v.task_id = t.id AND v.left_at IS NULL "
        "WHERE t.pipeline_id IS NOT NULL GROUP BY t.id HAVING COUNT(v.id) <> 1 OR MAX(v.stage_id) <> t.stage_id",
        "4: task {0} has {1} open visits, or its open visit is in another stage",
        (0,),
    )
    return out


def _rule_5(conn: "sqlite3.Connection") -> list[str]:
    """An on-enter action happens once per visit and is undone by moving back or closing: one handoff per visit, and
    no task a visit handed off is still open after the visit was left by Back or Close."""
    out = _lines(
        conn,
        "SELECT l.visit_id, COUNT(*), MAX(v.task_id) FROM links l JOIN visits v ON v.id = l.visit_id "
        "WHERE l.kind='handoff' GROUP BY l.visit_id HAVING COUNT(*) > 1",
        "5: visit {0} handed off {1} tasks",
        (2,),
    )
    out += _lines(
        conn,
        "SELECT l.to_task_id, v.task_id FROM links l JOIN visits v ON v.id = l.visit_id "
        "JOIN tasks c ON c.id = l.to_task_id WHERE l.kind='handoff' AND v.how IN ('back', 'closed') "
        "AND c.status='open'",
        "5: task {0} is still open after {1} moved back or closed",
        (0, 1),
    )
    out += _lines(
        conn,
        "SELECT e.key, v.task_id FROM effects e JOIN visits v ON v.id = e.visit_id JOIN tasks t ON t.id = v.task_id "
        "WHERE e.kind='queue_pr' AND e.state IN ('owed', 'done') AND v.how IN ('back', 'closed') AND NOT EXISTS "
        "(SELECT 1 FROM effects u WHERE u.visit_id = e.visit_id AND u.kind='dequeue_pr')",
        "5: effect {0} of task {1} was not undone when it moved back or closed",
        (1,),
    )
    return out


def _rule_6(conn: "sqlite3.Connection") -> list[str]:
    """A forward move never overfills a stage. Counted: the open tasks that came into a limited stage by a forward move
    since the stage's settings last changed and were never snoozed there. A task that came back, was reopened or had a
    snooze end may sit one over."""
    return _lines(
        conn,
        "SELECT s.id, COUNT(*), s.task_limit FROM stages s JOIN tasks t ON t.stage_id = s.id AND t.status='open' "
        "AND t.pipeline_id IS NOT NULL JOIN visits v ON v.task_id = t.id AND v.left_at IS NULL "
        "WHERE s.kind='work' AND s.task_limit > 0 AND v.entered_how IN ('met', 'pushed_past') "
        "AND v.entered_at >= s.updated_at "
        "AND NOT EXISTS (SELECT 1 FROM snoozes z WHERE z.task_id = t.id AND z.visit_id = v.id) "
        "GROUP BY s.id HAVING COUNT(*) > s.task_limit",
        "6: stage {0} holds {1} tasks that moved forward into it, over its limit of {2}",
    )


def _rule_7(conn: "sqlite3.Connection") -> list[str]:
    """Nothing wakes a bot about a snoozed, Done or Closed task: no wake for one is still queued."""
    return _lines(
        conn,
        "SELECT m.wake_key, t.status, t.id FROM jobs j JOIN messages m ON m.id = j.message_id "
        "JOIN tasks t ON t.id = substr(m.wake_key, 1, instr(m.wake_key, ':') - 1) "
        "WHERE j.state='queued' AND m.wake_key IS NOT NULL AND (t.status <> 'open' OR EXISTS "
        "(SELECT 1 FROM snoozes z WHERE z.task_id = t.id AND z.ended_at IS NULL))",
        "7: wake {0} is queued for a task that is {1} or snoozed",
        (2,),
    )

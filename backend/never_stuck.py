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
  woken again 2 minutes after it stopped, then 15 minutes, then 50 minutes. After 3 tries with no change the pipeline's
  manager bot gets the task on the same 3 tries, then a person. If the task changed, the count goes back to zero.
- Paused, a person's turn, other tasks, room, by-hand stages and snoozed tasks: nothing. `waiting_on` shows them. A
  person's request is not held in a by-hand stage: it moves on when the next stage has room (`_requests`). Nobody
  is told that a stage has room.
- Late: past the stage's expected time the manager bot gets the task once per visit; at 3 times the expected time it
  lands in one person's list, once.
- A snooze whose date came ends, and the owner is woken once.
- A snooze the owner bot set on its own task ends when everything is met and there is room, and the task moves
  (`_ready_snoozes`).

A pass is one write transaction, opened by the caller. Each task is done on its own inside it: a task the check cannot
work out is left as it was and named in `failures`, and the pass goes on to the next one. Doing a pass twice is
harmless: every wake has a key made from the task's visit, the level, the try and when the task last changed.

Names and signatures are in docs/dev/pipelines-v3-build.md.
"""

from collections.abc import Callable, Iterable
from datetime import timedelta, timezone
from typing import TYPE_CHECKING, Any, cast

from . import batch_work, hubdb_core, merge_order, task_wakes
from . import task_engine as E
from . import task_requirements as R
from . import waiting_on as W
from .prompt_cache import CACHE_WARM_S

if TYPE_CHECKING:
    import sqlite3

Row = dict[str, Any]

# hubdb_core is untyped: its actor helpers get their types here.
_core: Any = hubdb_core
is_bot: Callable[[object], bool] = _core.is_bot
is_human: Callable[[object], bool] = _core.is_human
_batch: Any = batch_work
storage_error: Callable[[BaseException], bool] = _batch.storage_error
SYSTEM: str = E.SYSTEM

# Seconds a bot is left alone after it stopped, before try 1, try 2 and try 3. After try 3 the task goes up a level
# once the first gap has passed. The third comes back while the bot's session is still cached (backend/prompt_cache.py).
GAPS = (E.FIRST_TRY_S, 900, CACHE_WARM_S)
TRIES = len(GAPS)
# Bot runs one visit may start between two changes: the owner's start and 3 tries, then the manager bot's 3 tries.
# Every run on the task counts, whoever it was for and whatever woke it (R1). The ladder holds to them, and the claim
# of a job asks `task_wakes.may_start`, which holds the 7 at the moment a run starts.
OWNER_RUNS = 1 + TRIES
RUNS = task_wakes.RUNS
assert RUNS == 1 + 2 * TRIES
QUIET = 8  # tasks in a row that changed nothing before the pass reads ahead again (`check`)
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
    _ready_snoozes(conn, done, failures)
    # A snooze of a task that was deleted, or is not on the engine, is no task's: it is left alone.
    due = conn.execute(
        "SELECT z.task_id FROM snoozes z JOIN tasks t ON t.id = z.task_id AND t.pipeline_id IS NOT NULL "
        "WHERE z.ended_at IS NULL AND z.until_at IS NOT NULL AND z.until_at <= ? ORDER BY z.until_at, z.id",
        (at,),
    ).fetchall()
    todo = [(str(found[0]), True) for found in due]
    todo += [(str(found[0]), False) for found in conn.execute(_OPEN).fetchall()]
    ahead: Ahead | None = None
    quiet = 0  # tasks in a row that changed nothing
    for n, (task_id, wake_up) in enumerate(todo):
        read: tuple[Row, list[R.Standing], list[W.Wait]] | None = None
        if not wake_up:
            # Every task still to do is read together. A task that changed something (a move may make room in a
            # stage, a wake is a new row) makes what was read old. The tasks after it are read one at a time until
            # QUIET in a row changed nothing, then read ahead again, twice as many as went by quietly: a pass where
            # most tasks change something reads no more than one task at a time would.
            if ahead is None or ahead.changes != conn.total_changes or task_id not in ahead.asked:
                rest = [each for each, woke in todo[n:] if not woke]
                if ahead is None:
                    ahead = read_ahead(conn, rest, seen)
                elif quiet >= QUIET:
                    ahead = read_ahead(conn, rest[: 2 * quiet], seen)
            if ahead and ahead.changes == conn.total_changes:
                read = ahead.found.get(task_id)
        changes = conn.total_changes
        _keeping_reads(conn, "SAVEPOINT never_stuck_task")
        rolled_back = False
        try:
            if wake_up:
                E.unsnooze(conn, task_id, SYSTEM)
                done["snoozes_ended"] += 1
            else:
                _one(conn, task_id, at, done, seen, live, read)
        except Exception as error:
            # SQLite may already have ended the transaction (a full disk, an I/O error): that stops the pass.
            if failures is None or not conn.in_transaction or storage_error(error):
                raise
            conn.execute("ROLLBACK TO never_stuck_task")
            rolled_back = True
            failures.append((task_id, error))
        finally:
            if conn.in_transaction:
                if rolled_back:
                    conn.execute("RELEASE never_stuck_task")
                else:
                    _keeping_reads(conn, "RELEASE never_stuck_task")
        quiet = quiet + 1 if conn.total_changes == changes else 0
    _keeping_reads(conn, "SAVEPOINT never_stuck_room")
    try:
        done["moved"] += _requests(conn)
        _handoffs(conn, at, done, seen)
    except Exception as error:
        if failures is None or not conn.in_transaction or storage_error(error):
            raise
        conn.execute("ROLLBACK TO never_stuck_room")
        failures.append(("room", error))
    finally:
        if conn.in_transaction:
            conn.execute("RELEASE never_stuck_room")
    return done


# ----------------------------------------------------------------------------- a bot's snooze on a ready task
# Open tasks on the engine, in a stage that is not by hand, set aside by a snooze their owner bot set itself.
_OWN_SNOOZES = (
    "SELECT t.id FROM tasks t JOIN snoozes z ON z.task_id = t.id AND z.ended_at IS NULL AND z.by_id = t.owner_id "
    "JOIN stages s ON s.id = t.stage_id "
    "WHERE t.pipeline_id IS NOT NULL AND t.status='open' AND s.kind='work' AND s.by_hand=0 "
    "AND t.owner_id LIKE 'bot:%' ORDER BY z.created_at, z.id"
)


def _ready_snoozes(
    conn: "sqlite3.Connection", done: dict[str, int], failures: "list[tuple[str, Exception]] | None"
) -> None:
    """A snooze the owner bot set on its own task does not hold it once everything the stage asks is met and the next
    stage has room: the bot was waiting for that (its PR's checks), so the snooze ends and the task moves on
    (`task_engine.move_on_past_own_snooze`). This catches what no event ended: checks that finished before the snooze
    was written, a result that was lost. A person's snooze, another bot's, or a task still missing something stays.
    *Nov 12: Tico Team's Build tasks sat for hours, snoozed "until checks pass", with their checks passed.*"""
    for (task_id,) in conn.execute(_OWN_SNOOZES).fetchall():
        _keeping_reads(conn, "SAVEPOINT never_stuck_snoozed")
        try:
            if E.move_on_past_own_snooze(conn, str(task_id), "Everything this stage asks is met"):
                done["snoozes_ended"] += 1
                done["moved"] += 1
        except Exception as error:
            if failures is None or not conn.in_transaction or storage_error(error):
                raise
            conn.execute("ROLLBACK TO never_stuck_snoozed")
            failures.append((str(task_id), error))
        finally:
            if conn.in_transaction:
                conn.execute("RELEASE never_stuck_snoozed")


# ----------------------------------------------------------------------------- room after a by-hand stage
# A person's request still on its first visit to a by-hand stage that moves requests on (task_engine.not_held_by_hand),
# oldest first.
_REQUESTS = (
    "SELECT t.id FROM tasks t JOIN visits v ON v.task_id = t.id AND v.left_at IS NULL "
    "JOIN stages s ON s.id = t.stage_id "
    "WHERE t.pipeline_id IS NOT NULL AND t.status='open' AND s.kind='work' AND s.by_hand=1 AND s.retired_at IS NULL "
    "AND s.requests_move_on=1 "
    "AND v.entered_how='new' AND t.requester_id LIKE 'human:%' "
    "AND NOT EXISTS (SELECT 1 FROM snoozes z WHERE z.task_id = t.id AND z.ended_at IS NULL) "
    "ORDER BY v.entered_at, v.id"
)


def _requests(conn: "sqlite3.Connection") -> int:
    """A task a person files into a by-hand stage whose "a person's request moves on" setting is on (Product's Ideas)
    is not held there: the person already picked it. Once the stage's own requirements are met (Ideas has none) it moves
    into the next stage as soon as that has room, oldest first. Nobody is told about room (C10): the move is the whole
    of it, and the owner's turn in the next stage wakes them as any move does. Ideas a bot filed stay until a person
    picks them, and so does every task in a by-hand stage with the setting off (Growth's Ideas, Monitoring's Watching).
    Returns how many moved."""
    moved = 0
    for (task_id,) in conn.execute(_REQUESTS).fetchall():
        row = E.task(conn, str(task_id))
        if not row or not E.not_held_by_hand(conn, row) or R.unmet(conn, row):
            continue
        nxt = E.next_stage(conn, row)
        if not nxt or nxt["kind"] != "work" or not E.has_room(conn, nxt, task_row=row) or E.shut_out(conn, row, nxt):
            continue
        E.move(conn, row["id"], nxt["id"], SYSTEM, seen_version=row.get("version"))
        moved += 1
    return moved


# A task in a by-hand stage that moves requests on, owned by a bot that is not its requester, not snoozed: maybe a
# hand-off (task_engine.handed_over), oldest first.
_HANDOFFS = (
    "SELECT t.id FROM tasks t JOIN visits v ON v.task_id = t.id AND v.left_at IS NULL "
    "JOIN stages s ON s.id = t.stage_id "
    "WHERE t.pipeline_id IS NOT NULL AND t.status='open' AND s.kind='work' AND s.by_hand=1 AND s.retired_at IS NULL "
    "AND s.requests_move_on=1 AND t.owner_id LIKE 'bot:%' AND t.owner_id IS NOT t.requester_id "
    "AND NOT EXISTS (SELECT 1 FROM snoozes z WHERE z.task_id = t.id AND z.ended_at IS NULL) "
    "ORDER BY v.entered_at, v.id"
)


def _handoffs(conn: "sqlite3.Connection", at: str, done: dict[str, int], seen: dict[str, str | None]) -> None:
    """Work someone else handed to its owner bot in Product's Ideas (task_engine.handed_over) is the owner's turn,
    though the stage has no ladder: the owner gets the one wake its turn starts with (the same key as the one written
    when it entered, so it is written once), and once the turn is due (task_engine.handover_due) the task is Late and
    the pipeline's manager bot is woken once to look. Nothing else chases it: the owner still chooses."""
    for (task_id,) in conn.execute(_HANDOFFS).fetchall():
        row = E.task(conn, str(task_id))
        if not row or not E.handed_over(conn, row):
            continue
        if E.wake_turn(conn, row):
            done["woken"] += 1
        visit = row["visit"]
        if visit.get("late_told_at") or at < E.handover_due(conn, row):
            continue
        conn.execute("UPDATE visits SET late_told_at=? WHERE id=?", (at, visit["id"]))
        _event(conn, row["id"], "late", None, None, {"expected_minutes": E.HANDED_OVER_S // 60})
        done["late"] += 1
        manager = _manager(conn, row, seen)
        if not manager or manager == row.get("owner_id"):
            continue
        fresh = E.task(conn, row["id"]) or row
        stage = row["stage"]["name"]
        lead = (
            f"Late: handed to {_name(conn, row.get('owner_id'))} in {stage} {_span(E.HANDED_OVER_S)} ago, and not "
            "moved on or snoozed. Look once: move it on, reassign it, snooze it or close it."
        )
        key = f"{row['id']}:{visit['id']}:late"
        if task_wakes.add(
            conn, fresh, manager, fresh["visit"], task_wakes.body(conn, fresh, lead, to=manager), wake_key=key
        ):
            done["woken"] += 1


def _keeping_reads(conn: "sqlite3.Connection", statement: str) -> None:
    """SAVEPOINT or RELEASE inside the pass's transaction: neither changes a row nor starts a snapshot, but a pooled
    connection empties its read cache on both (store._pooled). What the pass read ahead stays: the cache is set aside
    while the statement runs. A ROLLBACK TO empties it as it should."""
    kept: dict[Any, Any] | None = getattr(conn, "read_cache", None)
    if kept is None or not conn.in_transaction:
        conn.execute(statement)
        return
    setattr(conn, "read_cache", {})  # noqa: B010 - the pooled connection's own attribute
    try:
        conn.execute(statement)
    finally:
        setattr(conn, "read_cache", kept)  # noqa: B010


class Ahead:
    """What a pass read ahead for many tasks (`read_ahead`, the ids `asked`): for each task id its row
    (`task_engine.task`), standings and waits. It holds while nothing was written since (`changes`, the connection's
    total changes then)."""

    def __init__(self, changes: int, asked: list[str]) -> None:
        self.changes = changes
        self.asked = set(asked)
        self.found: dict[str, tuple[Row, list[R.Standing], list[W.Wait]]] = {}


_PART_READS = (
    (
        "SELECT * FROM stages WHERE id=?",
        "SELECT stage_id FROM visits WHERE left_at IS NULL AND task_id IN (SELECT value FROM json_each(?))",
    ),
    (
        "SELECT * FROM pipelines WHERE id=?",
        "SELECT pipeline_id FROM tasks WHERE id IN (SELECT value FROM json_each(?))",
    ),
)
_SNOOZE_ENDED = "SELECT MAX(ended_at) FROM snoozes WHERE visit_id=? AND ended_at IS NOT NULL"


def read_ahead(conn: "sqlite3.Connection", task_ids: list[str], seen: dict[str, str | None]) -> Ahead:
    """Read what the pass needs for every one of these tasks in a fixed number of statements, however many tasks there
    are: each one-task read the check makes is made once for the list and kept in the snapshot's read cache
    (`read_cache`), where the one-task read finds it. Then each task's row, standings and waits are worked out from it.
    A connection with no read cache reads each task as before."""
    import json

    from . import read_cache

    RC: Any = read_cache
    ahead = Ahead(conn.total_changes, task_ids)
    if not task_ids or RC.cache(conn) is None:
        return ahead
    listed = json.dumps(task_ids)
    # Each task's stage and pipeline (task_engine.task reads them by id), then the tasks as task_engine.task reads each
    # one and what standings and waits read for each (waiting_on.prime).
    for sql, of_tasks in _PART_READS:
        parts = {(str(row["id"]),): row for row in conn.execute(sql.replace("id=?", f"id IN ({of_tasks})"), (listed,))}
        RC.prime(conn, sql, parts)
    W.prime(conn, task_ids)
    tasks = [row for row in (E.task(conn, each) for each in task_ids) if row and row.get("visit")]
    visits: list[Row] = [row["visit"] for row in tasks]
    visit_ids = json.dumps([str(visit["id"]) for visit in visits])
    marks = E.PROGRESS_SQL.replace("SELECT v.task_id,", "SELECT v.id, v.task_id,").replace(
        "v.id=?", "v.id IN (SELECT value FROM json_each(?))"
    )
    RC.prime(conn, E.PROGRESS_SQL, {(str(row[0]),): tuple(row[1:]) for row in conn.execute(marks, (visit_ids,))})
    ended = {
        str(row[0]): row[1]
        for row in conn.execute(
            "SELECT visit_id, MAX(ended_at) FROM snoozes WHERE visit_id IN (SELECT value FROM json_each(?)) "
            "AND ended_at IS NOT NULL GROUP BY visit_id",
            (visit_ids,),
        )
    }
    RC.prime(conn, _SNOOZE_ENDED, {(str(visit["id"]),): (ended.get(str(visit["id"])),) for visit in visits})
    # Whose wakes and runs the pass asks about: the bots with a turn on each task, and its pipeline's manager bot.
    bots: dict[str, set[str]] = {}
    # A task the check cannot work out is left to `_one`, which reports it: what was worked out before it still holds
    # (the savepoint undoes anything its read wrote), and the tasks from it on are read one at a time.
    _keeping_reads(conn, "SAVEPOINT never_stuck_ahead")
    for n, row in enumerate(tasks):
        task_id = str(row["id"])
        try:
            standings = R.standings(conn, row)
            waits = W.of(conn, row, seen, standings)
            turns = W.turns(conn, row, waits)
        except Exception as error:
            if not conn.in_transaction or storage_error(error):
                raise
            conn.execute("ROLLBACK TO never_stuck_ahead")
            conn.execute("RELEASE never_stuck_ahead")
            ahead.changes = conn.total_changes
            ahead.asked -= {str(each["id"]) for each in tasks[n:]}
            return ahead
        ahead.found[task_id] = (row, standings, waits)
        manager = _part(row, "pipeline").get("manager_id")
        bots[task_id] = turns | ({str(manager)} if is_bot(manager) else set())
    _keeping_reads(conn, "RELEASE never_stuck_ahead")
    task_wakes.prime(conn, tasks, bots)
    return ahead


def _one(
    conn: "sqlite3.Connection",
    task_id: str,
    at: str,
    done: dict[str, int],
    seen: dict[str, str | None],
    live: set[str],
    ahead: "tuple[Row, list[R.Standing], list[W.Wait]] | None" = None,
) -> None:
    row = ahead[0] if ahead else E.task(conn, task_id)
    if not row or row["status"] != "open" or row.get("snooze") or row["stage"].get("by_hand"):
        return  # a move earlier in this pass took it
    standings = ahead[1] if ahead else R.standings(conn, row)
    waits = ahead[2] if ahead else W.of(conn, row, seen, standings)
    if W.all_met(waits) and E.advance(conn, task_id):
        done["moved"] += 1
        for _ in range(HOPS):  # stages that need nothing are passed in the same step
            if not E.advance(conn, task_id):
                break
        return
    level: str = row["visit"].get("level") or "owner"
    if level != "person" and W.unreadable(conn, row, standings):
        # It waits on someone who may not open it (a stage edited to ask them, its readers narrowed): nothing they can
        # do and no time to wait out. A person who reads it has it now, told whom to add under Who can see.
        _to_person(conn, row, level, done)
        return
    _ladder(conn, row, waits, E.progress_mark(conn, row["visit"]["id"], standings), at, done, seen, live)
    _late(conn, row, at, done, seen, waits)


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
        # The lost wake's references ride on (an automation's run and step, what woke it), so the run on the new one
        # is still that work: its turn answers the automation and is charged to it.
        refs = lost["refs"]
        trigger = refs.get("trigger") if refs.get("trigger") != "task_turn" else None
        if task_wakes.add(
            conn,
            row,
            bot,
            visit,
            lost["body"],
            level=level,
            try_n=tries,
            wake_key=key,
            refs=refs,
            trigger=trigger,
            source=refs.get("trigger_source"),
        ):
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
    from . import read_cache

    found = cast("Any", read_cache).one(conn, _SNOOZE_ENDED, (visit["id"],))  # read for many ahead
    return bool(found and found[0] and str(found[0]) > since)


def _wake(conn: "sqlite3.Connection", row: Row, target: str | None, lead: str, level: str, n: int, epoch: str) -> bool:
    key = f"{row['id']}:{row['visit']['id']}:{level}:{n}:{epoch}"
    text = task_wakes.body(conn, row, lead, to=target)
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
    """The person a task falls to: `waiting_on.person_for`, the one selection the turn read and this check share. On a
    private task, one who may read it already (its reader list's groups by their members); nobody is made a reader.
    With no such person, None: it goes to no person and takes no bot turns; Health counts it for the company's owner
    and Admins without naming it, and the rules check reports it held for that cause."""
    return W.person_for(conn, row)


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


def _late(
    conn: "sqlite3.Connection",
    row: Row,
    at: str,
    done: dict[str, int],
    seen: dict[str, str | None],
    waits: "list[W.Wait] | None" = None,
) -> None:
    H = _h()
    stage = row["stage"]
    minutes = int(stage.get("expected_minutes") or 0)
    visit = row["visit"]
    if is_human(row.get("owner_id")):
        _late_by_due(conn, row, at, done)
        return
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
    waits = W.of(conn, row, seen) if waits is None else waits
    if waits and merge_order.is_order_wait(waits[0].requirement_id):
        return  # it goes after a task that has not merged: not Late, and nobody is woken (merge_order)
    if _next_release_only(conn, waits):
        _late_release(conn, row, at, spent, minutes, done)
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
            waits_on = W.unnamed(head, task_wakes.told(conn, fresh, manager)).line  # a sub-task it may not be told of
            lead = (
                f"{said} It waits on: {waits_on}. Look once: push past with a reason where that is allowed, "
                "reassign it, snooze it or close it."
            )
            if _asks_only(waits):
                # It waits only on people's answers. The manager bot's one move is to push past an ask it may push
                # past (docs/pipelines-v3.md "A task waits a day on you"); with none it is not woken. Never a snooze:
                # the ask stays in the person's list, and Late at 3 times adds nothing to a person's turn.
                lead = (
                    f"{said} It waits on: {waits_on}. It stays in {_name(conn, head.who)}'s list. Look once: if the "
                    "answer is not needed, push past it with a reason where that is allowed; otherwise leave the task "
                    "as it is."
                    if _pushable(conn, waits)
                    else ""
                )
            key = f"{row['id']}:{visit['id']}:late"
            if lead and task_wakes.add(
                conn, fresh, manager, fresh["visit"], task_wakes.body(conn, fresh, lead, to=manager), wake_key=key
            ):
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


def _next_release_only(conn: "sqlite3.Connection", waits: "list[W.Wait]") -> bool:
    """Whether all the task waits on is the next release of a repository Tico does not cut releases for (In a release
    with no Release with): a release a person cuts, or the repository's own train."""
    from .item_sources import github_release

    if not waits or any(wait.state != W.SOURCE or not wait.requirement_id for wait in waits):
        return False
    for wait in waits:
        requirement = R.get(conn, str(wait.requirement_id))
        if not requirement or not github_release.waits_for_next_release(requirement):
            return False
    return True


def _late_release(
    conn: "sqlite3.Connection", row: Row, at: str, spent: float, minutes: int, done: dict[str, int]
) -> None:
    """A task that waits only for the next release, which no bot can bring: no bot is told Late or woken. After a week
    (github_release.RELEASE_WAIT_S), or the stage's expected time when that is longer, it is Late and lands in one
    person's list, once."""
    from .item_sources import github_release

    visit = row["visit"]
    bound = max(minutes * 60, github_release.RELEASE_WAIT_S)
    if spent < bound or visit.get("late_person_at"):
        return
    conn.execute(
        "UPDATE visits SET late_told_at=COALESCE(late_told_at, ?), late_person_at=? WHERE id=?", (at, at, visit["id"])
    )
    _event(conn, row["id"], "late", None, None, {"expected_minutes": int(bound // 60)})
    done["late"] += 1
    fresh = E.task(conn, row["id"]) or row
    _to_person(conn, fresh, fresh["visit"].get("level") or "owner", done)


def _late_by_due(conn: "sqlite3.Connection", row: Row, at: str, done: dict[str, int]) -> None:
    """A person's own task is never chased (C6): it is Late only once its due date has passed, whatever the stage
    expects, and that is the label alone. Nobody is woken and nothing moves to anyone's list."""
    H = _h()
    visit = row["visit"]
    if visit.get("late_told_at") or not row.get("due_at"):
        return
    due, now = H.parse_ts(row["due_at"]), H.parse_ts(at)
    if due and len(str(row["due_at"])) == 10:
        due = due.replace(tzinfo=timezone.utc) + timedelta(days=1)  # a due date with no time: Late once the day is over
    if not due or not now or now < due:
        return
    conn.execute("UPDATE visits SET late_told_at=? WHERE id=?", (at, visit["id"]))
    _event(conn, row["id"], "late", None, None, {"due_at": row.get("due_at")})
    done["late"] += 1


def _asks_only(waits: list[W.Wait]) -> bool:
    """Whether all a task waits on is OKs and answers asked of people, and causes a person fixes first (`Wait.fix`,
    Connect GitHub)."""
    return bool(waits) and all((wait.state == W.PERSON and wait.ask) or wait.fix for wait in waits)


def _pushable(conn: "sqlite3.Connection", waits: list[W.Wait]) -> bool:
    """Whether a bot may push past any of these asks: its switch is on and it is not people only (task_engine
    ._may_push_past refuses the rest)."""
    for wait in waits:
        if wait.fix:
            continue  # pushing past it would not get round its cause: the next stage needs it as well
        requirement = R.get(conn, str(wait.requirement_id)) if wait.requirement_id else None
        if requirement and R.can_push_past(requirement) and not R.people_only(requirement):
            return True
    return False


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
    needs: dict[str, dict[str, list[Row]]] | None = None  # everyone's Needs you, read once and indexed by task
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
        for wait in waits:
            if wait.state == W.TASKS and wait.who and _waits_for_its_room(conn, row, str(wait.who), seen):
                said = f"1: task {task_id} waits on {wait.who}, which waits for room in the stage {task_id} holds"
                broken.append(Break(said, [task_id, wait.who]))
        if row.get("snooze"):
            # Set aside: nobody's turn is chased. A bot's snooze still hides no ask from the person it names (rule 3).
            waits = W.listed(waits, row)
        visit = row["visit"]
        ladder = W.turn(conn, row, waits)  # the one bot a next try is for
        for wait in [] if row.get("snooze") else waits:
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
            if needs is None:
                needs = {}
                for who, items in W.needs_by_person(conn).items():
                    for item in items:
                        needs.setdefault(who, {}).setdefault(str(item.get("task_id")), []).append(item)
            listed = needs.get(person, {}).get(task_id, [])
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


def _waits_for_its_room(conn: "sqlite3.Connection", row: Row, filed: str, seen: dict[str, str | None]) -> bool:
    """The task waits on a task it filed, and that one waits for room in the limited stage the task is in: neither can
    move until something else leaves. The engine lets a sub-task in on its filer's slot (`task_engine.rides_with_filer`),
    so this is a safety net."""
    child = E.task(conn, filed)
    if not child or child["status"] != "open" or child.get("snooze") or not child.get("stage"):
        return False
    ahead = E.next_stage(conn, child)
    if not ahead or ahead["id"] != row["stage_id"] or E.has_room(conn, ahead, task_row=child):
        return False
    return any(wait.state == W.ROOM for wait in W.of(conn, child, seen))


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
        "WHERE e.kind='act' AND e.state IN ('owed', 'done') AND v.how IN ('back', 'closed') AND NOT EXISTS "
        "(SELECT 1 FROM effects u WHERE u.visit_id = e.visit_id AND u.kind='undo' AND u.source = e.source "
        "AND u.action = e.action)",
        "5: effect {0} of task {1} was not undone when it moved back or closed",
        (1,),
    )
    return out


def _rule_6(conn: "sqlite3.Connection") -> list[str]:
    """A forward move never overfills a stage. Counted: the open tasks that came into a limited stage by a forward move
    since the stage's settings last changed, were never snoozed there and hold a slot now (`waiting_on.slots`: a task
    waiting only on a person's OK or answer holds none). A task that came back, was reopened or had a snooze end may sit
    one over. So may a sub-task filed by a task while it was in the stage: it comes in on its filer's slot
    (task_engine.rides_with_filer). So may one a person answered on this visit, or whose ask was withdrawn: its turn
    went back to a bot after it gave up its slot, and it keeps its place, as after Back. So may one whose every wait is a
    person's OK or answer, though its bot is Paused or its ladder reached a person (`_persons_turn`)."""
    found = conn.execute(
        "SELECT s.id, s.task_limit, t.id FROM stages s JOIN tasks t ON t.stage_id = s.id AND t.status='open' "
        "AND t.pipeline_id IS NOT NULL JOIN visits v ON v.task_id = t.id AND v.left_at IS NULL "
        "WHERE s.kind='work' AND s.task_limit > 0 AND v.entered_how IN ('met', 'pushed_past') "
        "AND v.entered_at >= s.updated_at "
        "AND NOT EXISTS (SELECT 1 FROM snoozes z WHERE z.task_id = t.id AND z.visit_id = v.id) "
        "AND NOT EXISTS (SELECT 1 FROM links l JOIN visits f ON f.id = l.visit_id "
        "WHERE l.to_task_id = t.id AND f.stage_id = s.id) "
        "AND NOT EXISTS (SELECT 1 FROM answers a WHERE a.visit_id = v.id AND a.by_id LIKE 'human:%') "
        "AND NOT EXISTS (SELECT 1 FROM requirements r WHERE r.visit_id = v.id AND r.withdrawn_at IS NOT NULL) "
        "ORDER BY s.id"
    ).fetchall()
    stages: dict[str, tuple[int, list[str]]] = {}
    for stage_id, limit, task_id in found:
        stages.setdefault(str(stage_id), (int(limit), []))[1].append(str(task_id))
    out: list[str] = []
    for stage_id, (limit, came) in stages.items():
        if len(came) <= limit:
            continue
        count = len([task_id for task_id in came if not _persons_turn(conn, task_id)])
        if count > limit:
            out.append(
                Break(
                    f"6: stage {stage_id} holds {count} tasks that moved forward into it, over its limit of {limit}", []
                )
            )
    return out


def _persons_turn(conn: "sqlite3.Connection", task_id: str) -> bool:
    """Every wait on the task is a person's OK or answer. Its slot went back when its turn became a person's; a Paused
    bot or a ladder at level person makes it hold one again without moving, so it may sit over the limit."""
    waits = W.waiting_on(conn, task_id)
    return bool(waits) and all(wait.state == W.PERSON and wait.ask for wait in waits)


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

# pyright: strict
"""The never-stuck check (docs/pipelines-v3.md, "The never-stuck check").

    check(conn, now=None) -> dict[str, int]      one pass over every open task, on the Heartbeat every 10 seconds
    rules_hold(conn) -> list[str]                the seven rules that hold at all times; empty when they do

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

from collections.abc import Callable
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
                _one(conn, task_id, at, done, seen)
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


def _one(conn: "sqlite3.Connection", task_id: str, at: str, done: dict[str, int], seen: dict[str, str | None]) -> None:
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
    _ladder(conn, row, waits, E.progress_mark(conn, row["visit"]["id"], standings), at, done, seen)
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
    bots = sorted((wait for wait in waits if wait.state == W.BOT), key=lambda wait: wait.ask)
    if not bots or level == "person":
        return
    manager: str | None = _part(row, "pipeline").get("manager_id")
    target = bots[0].who  # whoever has the task's own work, before a bot that was only asked for an OK or an answer
    if level == "manager":
        if not _usable(conn, manager, seen):
            _to_person(conn, row, level, done)
            return
        target = manager
    if task_wakes.live_run(conn, row, target, at) or task_wakes.queued(conn, row["id"], target):
        return
    tries = int(visit.get("tries") or 0)
    gap = GAPS[tries] if tries < TRIES else GAPS[0]
    due: str | None = visit.get("next_try_at")
    if not due:
        conn.execute("UPDATE visits SET next_try_at=? WHERE id=?", (H.shift(at, seconds=gap), visit["id"]))
        return
    last = task_wakes.last_run(conn, row, target)
    if last and last.get("finished_at"):
        due = max(due, str(H.shift(last["finished_at"], seconds=gap)))  # the gap runs from when the bot stopped
    if at < due:
        return
    epoch: str = visit.get("last_change_at") or visit["entered_at"]
    cut_off = bool(last) and (last or {}).get("state") in CUT_OFF
    if level == "owner" and tries == 0 and not task_wakes.woken_since(conn, row["id"], target, epoch):
        # Nothing has told this bot about the task as it stands now: it changed during a run, or a wake was lost.
        # This wake starts it again and is not a try.
        lead = "Your last run on this task was cut off." if cut_off else "This task is not finished. Carry on."
        if _wake(conn, row, target, lead, level, 0, epoch):
            conn.execute("UPDATE visits SET next_try_at=? WHERE id=?", (H.shift(at, seconds=GAPS[0]), visit["id"]))
            done["woken"] += 1
            return
    if tries < TRIES:
        lead = "Your last run on this task was cut off." if cut_off else "You stopped before this task was finished."
        if level == "manager" and tries == 0:
            lead = f"{_name(conn, row.get('owner_id'))} could not take this task further. {MANAGER_HAS_IT}"
        _try(conn, row, target, level, tries + 1, f"{lead} Try {tries + 1} of {TRIES}.", at, epoch, done)
    elif level == "owner" and manager != target and _usable(conn, manager, seen):
        _to_manager(conn, row, manager, f"{_name(conn, target)} stopped {TRIES} times without finishing it.", at, done)
    else:
        _to_person(conn, row, level, done)


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
    still queued for anyone else is canceled: it would start a run about a turn that is over. A live run goes on."""
    visit = row["visit"]
    conn.execute("UPDATE visits SET level='manager', tries=0, next_try_at=? WHERE id=?", (at, visit["id"]))
    task_wakes.drop(conn, row["id"], keep=manager)
    _event(conn, row["id"], "ladder", visit.get("level") or "owner", "manager", {"why": why})
    done["to_manager"] += 1
    handed = E.task(conn, row["id"]) or row
    lead = f"{why} {MANAGER_HAS_IT} Try 1 of {TRIES}."
    epoch: str = visit.get("last_change_at") or visit["entered_at"]
    _try(conn, handed, manager, "manager", 1, lead, at, epoch, done)


def _to_person(conn: "sqlite3.Connection", row: Row, level: str, done: dict[str, int]) -> None:
    """Hand the task to a person. It shows in their Needs you, once, and nothing more runs until a person acts: every
    wake still queued for the task is canceled. A live run goes on."""
    visit = row["visit"]
    person = W.person_for(conn, row)
    conn.execute("UPDATE visits SET level='person', person_id=?, next_try_at=NULL WHERE id=?", (person, visit["id"]))
    task_wakes.drop(conn, row["id"])
    _event(conn, row["id"], "ladder", level, "person", {"person": person})
    done["to_person"] += 1


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
    manager: str | None = _part(row, "pipeline").get("manager_id")
    said = f"Late: {_span(spent)} in {stage['name']}, expected {_span(minutes * 60)}."
    if not visit.get("late_told_at"):
        conn.execute("UPDATE visits SET late_told_at=? WHERE id=?", (at, visit["id"]))
        _event(conn, row["id"], "late", None, None, {"expected_minutes": minutes})
        done["late"] += 1
        fresh = E.task(conn, row["id"]) or row
        waits = W.of(conn, fresh, seen)
        head = waits[0] if waits else None
        level = fresh["visit"].get("level") or "owner"
        if head and head.state == W.BOT and level == "owner" and manager != head.who and _usable(conn, manager, seen):
            # Late hands the task to the manager bot, once per visit, on the same 3 tries.
            _to_manager(conn, fresh, manager, f"{said} {_name(conn, head.who)} has it.", at, done)
        elif head and head.state != W.PAUSED and is_bot(manager) and level == "owner" and manager != head.who:
            # It waits on a person, another task or the outside: the manager bot looks once. A bot that cannot run is
            # one row for its cause in a person's list, and its tasks carry on when the cause is fixed.
            lead = (
                f"{said} It waits on: {head.line}. Look once: push past with a reason where that is allowed, "
                "reassign it, snooze it or close it."
            )
            key = f"{row['id']}:{visit['id']}:late"
            if task_wakes.add(conn, fresh, manager, fresh["visit"], task_wakes.body(conn, fresh, lead), wake_key=key):
                done["woken"] += 1
    if spent >= LATE_PERSON * minutes * 60 and not visit.get("late_person_at"):
        conn.execute("UPDATE visits SET late_person_at=? WHERE id=?", (at, visit["id"]))
        fresh = E.task(conn, row["id"]) or row
        waits = W.of(conn, fresh, seen)
        if not (waits and waits[0].state in (W.PERSON, W.PAUSED)):
            # Still there at 3 times the expected time: one person's list, once. Already a person's turn, or paused
            # with its cause in a person's list: nothing.
            _to_person(conn, fresh, fresh["visit"].get("level") or "owner", done)


# ----------------------------------------------------------------------------- the rules that hold at all times
def rules_hold(conn: "sqlite3.Connection") -> list[str]:
    """The seven rules (docs/pipelines-v3.md, "Rules that hold at all times"), checked against the database as it is.
    Returns one sentence per break; empty when they all hold. The fault rig runs it after every step."""
    H = _h()
    broken: list[str] = []
    seen: dict[str, str | None] = {}
    needs: dict[str, list[Row]] = {}
    now: str = H.now()
    open_ids = [
        str(found[0])
        for found in conn.execute(
            "SELECT id FROM tasks WHERE pipeline_id IS NOT NULL AND status='open' ORDER BY id"
        ).fetchall()
    ]
    for task_id in open_ids:
        row = E.task(conn, task_id)
        if not row or not row.get("visit") or not row.get("stage"):
            broken.append(f"4: task {task_id} is open with no open visit or no stage")
            continue
        waits = W.of(conn, row, seen)
        if not waits:
            broken.append(f"1: task {task_id} waits on nothing")
        if row.get("snooze"):
            continue
        visit = row["visit"]
        for wait in waits:
            if wait.state == W.BOT and not (
                visit.get("next_try_at")
                or (visit.get("level") or "owner") != "owner"
                or task_wakes.live_run(conn, row, wait.who, now)
                or task_wakes.queued(conn, task_id, wait.who)
            ):
                broken.append(f"2: task {task_id} is {wait.who}'s turn with no run, no wake and no next try")
        people = {wait.who for wait in waits if wait.state == W.PERSON and wait.who}
        for person in sorted(people):
            if person not in needs:
                needs[person] = W.needs(conn, person)
            listed = [item for item in needs[person] if item.get("task_id") == task_id]
            mine = [wait for wait in waits if wait.state == W.PERSON and wait.who == person]
            asks = [wait.requirement_id for wait in mine if wait.ask]
            want = len(asks) + (1 if any(not wait.ask for wait in mine) else 0)
            got_asks = sorted(str(item["requirement_id"]) for item in listed if item["kind"] == "ask")
            if len(listed) != want or got_asks != sorted(str(a) for a in asks):
                broken.append(f"3: task {task_id} shows {len(listed)} times in {person}'s Needs you, not {want}")
    broken += _rule_4(conn)
    broken += _rule_5(conn)
    broken += _rule_6(conn)
    broken += _rule_7(conn)
    return broken


def _lines(conn: "sqlite3.Connection", sql: str, text: str) -> list[str]:
    return [text.format(*found) for found in conn.execute(sql).fetchall()]


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
    )
    out += _lines(
        conn,
        "SELECT t.id, COUNT(v.id) FROM tasks t LEFT JOIN visits v ON v.task_id = t.id AND v.left_at IS NULL "
        "WHERE t.pipeline_id IS NOT NULL GROUP BY t.id HAVING COUNT(v.id) <> 1 OR MAX(v.stage_id) <> t.stage_id",
        "4: task {0} has {1} open visits, or its open visit is in another stage",
    )
    return out


def _rule_5(conn: "sqlite3.Connection") -> list[str]:
    """An on-enter action happens once per visit and is undone by moving back or closing: one handoff per visit, and
    no task a visit handed off is still open after the visit was left by Back or Close."""
    out = _lines(
        conn,
        "SELECT visit_id, COUNT(*) FROM links WHERE kind='handoff' AND visit_id IS NOT NULL "
        "GROUP BY visit_id HAVING COUNT(*) > 1",
        "5: visit {0} handed off {1} tasks",
    )
    out += _lines(
        conn,
        "SELECT l.to_task_id, v.task_id FROM links l JOIN visits v ON v.id = l.visit_id "
        "JOIN tasks c ON c.id = l.to_task_id WHERE l.kind='handoff' AND v.how IN ('back', 'closed') "
        "AND c.status='open'",
        "5: task {0} is still open after {1} moved back or closed",
    )
    out += _lines(
        conn,
        "SELECT e.key, v.task_id FROM effects e JOIN visits v ON v.id = e.visit_id JOIN tasks t ON t.id = v.task_id "
        "WHERE e.kind='queue_pr' AND e.state IN ('owed', 'done') AND v.how IN ('back', 'closed') AND NOT EXISTS "
        "(SELECT 1 FROM effects u WHERE u.visit_id = e.visit_id AND u.kind='dequeue_pr')",
        "5: effect {0} of task {1} was not undone when it moved back or closed",
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
        "SELECT m.wake_key, t.status FROM jobs j JOIN messages m ON m.id = j.message_id "
        "JOIN tasks t ON t.id = substr(m.wake_key, 1, instr(m.wake_key, ':') - 1) "
        "WHERE j.state='queued' AND m.wake_key IS NOT NULL AND (t.status <> 'open' OR EXISTS "
        "(SELECT 1 FROM snoozes z WHERE z.task_id = t.id AND z.ended_at IS NULL))",
        "7: wake {0} is queued for a task that is {1} or snoozed",
    )

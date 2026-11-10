# pyright: strict
"""The task calls everything outside the engine uses (docs/dev/pipelines-v3-cutover.md, "The Python calls").

hubdb re-exports each one, so a caller writes `H.task_move(...)`. Each call checks who is asking, then hands the write
to the engine (`task_engine`, `answers`). Nothing here writes a task's status, stage or owner.

Who may act:
- Every call: the actor is on the roster and may read the task (a private task reads as "not found" to anyone else).
- A call that works the task (done, move, close, reassign, ask, snooze, try again): the actor can work it, the same
  rule a field edit uses, plus the pipeline's manager and owner. `mover` is the caller saying a person may move any
  task (the company's owner; a route knows).
- An answer, a give, an undo and a thumbs: whoever the engine says the requirement is asked of. No more is checked
  here, so a bot or a person named on an OK answers it without being on the task.
"""

from collections.abc import Callable, Iterable
from typing import TYPE_CHECKING, Any, NoReturn

from . import answers as A
from . import hubdb_core, task_effects
from . import task_engine as E
from . import task_requirements as R
from . import waiting_on as W

if TYPE_CHECKING:
    import sqlite3

Row = dict[str, Any]
_core: Any = hubdb_core
is_bot: Callable[[object], bool] = _core.is_bot
is_human: Callable[[object], bool] = _core.is_human
SYSTEM: str = _core.KEEPER

NEXT, DONE, CLOSED, REOPEN = E.NEXT, E.DONE, E.CLOSED, E.REOPEN


def _h() -> Any:
    from . import hubdb

    return hubdb


def _refuse(conn: "sqlite3.Connection", by: str, rule: str, detail: str, task_id: str | None = None) -> NoReturn:
    _h().refuse(conn, by, rule, detail, task=task_id)
    raise AssertionError("refuse() always raises")  # pragma: no cover


# ----------------------------------------------------------------------------- who may
def can_work(conn: "sqlite3.Connection", actor: str, row: Row, mover: bool | None = None) -> bool:
    """Whether `actor` can work this task: its owner, its requester, their managers and delegates, a party to a task
    above it, the Task Manager, the manager and the owner of its pipeline, or a person who moves tasks."""
    H = _h()
    if actor == SYSTEM:
        return True
    if not H.task_private_readable(conn, actor, row):
        return False
    if H._task_party(conn, actor, row) or H.task_manager_acts(conn, actor, row):
        return True
    pipe: Row = E.pipeline(conn, row["pipeline_id"]) or {} if row.get("pipeline_id") else {}
    if actor in (pipe.get("manager_id"), pipe.get("owner_id")):
        return True
    if mover is None:
        mover = is_human(actor) and bool(H.can_move(conn, actor))
    return bool(H.task_mover(conn, actor, row, mover))


def _open(conn: "sqlite3.Connection", actor: str, task_id: str, mover: bool | None = None, *, work: bool = True) -> Row:
    """The task's row, once the actor is known, may read it and (with `work`) can work it."""
    H = _h()
    H._writer(conn, actor, task_id=task_id)
    row: Row | None = H.task(conn, task_id)
    if not row:
        _refuse(conn, actor, "not-found", "No such task")
    if actor != SYSTEM and not H.task_private_readable(conn, actor, row):
        _refuse(conn, actor, "not-found", "No such task")
    if work and not can_work(conn, actor, row, mover):
        _refuse(
            conn,
            actor,
            "identity",
            "This task is worked by its owner, whoever asked for it, their managers, and its pipeline's manager. "
            "Comment on it, or file a new task.",
            task_id,
        )
    return row


# ----------------------------------------------------------------------------- moves
def task_done(
    conn: "sqlite3.Connection",
    actor: str,
    task_id: str,
    note: str = "",
    seen_version: int | str | None = None,
    *,
    mover: bool | None = None,
) -> Row:
    """The work of this stage is finished. It records completion and never pushes past anything. Where the stage asks
    "owner says done" and that is not met, whoever may say so (the owner, the pipeline's manager, a person, the
    engine) answers it, and the engine moves the task on once everything else is met: in the last stage, to Done.
    Otherwise it is a forward move with no reason, refused with what is missing while anything is. To go on anyway is
    `task_move(NEXT, why=...)`."""
    _open(conn, actor, task_id, mover)
    row = E.task(conn, task_id)
    if not row:
        _refuse(conn, actor, "not-found", "No such task")
    if row["status"] == "done":
        return row
    own = next((s for s in R.standings(conn, row) if s.requirement["kind"] == "owner_done" and not s.met), None)
    pipe: Row = row.get("pipeline") or {}
    says = is_human(actor) or actor in (row.get("owner_id"), pipe.get("manager_id"), SYSTEM)
    if own and row["status"] == "open" and says:
        with task_effects.because(actor):
            return A.answer(
                conn, task_id, own.requirement["id"], actor, "done", note or None, seen_version=seen_version
            )
    with task_effects.because(actor):
        return E.move(conn, task_id, NEXT, actor, None, seen_version)


def task_move(
    conn: "sqlite3.Connection",
    actor: str,
    task_id: str,
    to: str,
    why: str | None = None,
    seen_version: int | str | None = None,
    *,
    mover: bool | None = None,
) -> Row:
    """Move a task: forward, push past, back, done, close or reopen. `to` is a stage id or name, or NEXT, DONE, CLOSED
    or REOPEN. A bot does not reopen a task a person closed."""
    H = _h()
    row = _open(conn, actor, task_id, mover)
    closer = row.get("closed_by_id")
    if row.get("status") == "closed" and is_bot(actor) and is_human(closer) and str(to) not in (CLOSED,):
        _refuse(
            conn,
            actor,
            "task_needs_person",
            f"{H.actor_name(conn, closer)} closed this task, so it stays closed. Add anything new as a comment.",
            task_id,
        )
    with task_effects.because(actor):
        return E.move(conn, task_id, str(to), actor, why, seen_version)


def task_close(
    conn: "sqlite3.Connection",
    actor: str,
    task_id: str,
    note: str = "",
    seen_version: int | str | None = None,
    *,
    mover: bool | None = None,
) -> Row:
    """Close a task: it will not be done. Its open sub-tasks close with it. A bot gives a reason, and does not close
    what a person asked for (it asks that person)."""
    return task_move(conn, actor, task_id, CLOSED, note or None, seen_version, mover=mover)


def task_reassign(
    conn: "sqlite3.Connection",
    actor: str,
    task_id: str,
    owner: str | None,
    why: str | None = None,
    seen_version: int | str | None = None,
    *,
    mover: bool | None = None,
) -> Row:
    """Give the task to another owner for this stage. Checked as any hand-over is: the actor reaches the new owner,
    the new owner takes tasks, the Task Manager stays in the department, and a bot's hand-over to a person passes the
    strategy rules."""
    H = _h()
    row = _open(conn, actor, task_id, mover)
    target: str | None = None
    if owner:
        H._botops_only(conn, actor, H.resolve_actor(conn, owner))
        target = H._reach(conn, actor, owner)
    if target and target != row.get("owner_id"):
        if actor == H.TASK_MANAGER:
            from . import task_manager

            department: Any = task_manager
            if not department.same_department(conn, row.get("owner_id"), target):
                _refuse(
                    conn,
                    actor,
                    "identity",
                    "The Task Manager reassigns only within the owner's department. Leave a comment for the "
                    "department instead.",
                    task_id,
                )
        if is_human(target):
            from . import strategy_acts

            acts: Any = strategy_acts
            acts.gate(conn, actor, "person_task", f"give {acts.TASK} to {H.person_name(conn, target)}", task_id=task_id)
        from . import task_relations

        relations: Any = task_relations
        H._takes_tasks(conn, actor, target, row.get("title"), relations.parent_of(conn, task_id))
        H._learner(conn, actor)
        H._learning(conn, actor, target)
    return E.reassign(conn, task_id, target, actor, why, seen_version)


# ----------------------------------------------------------------------------- asks and answers
def task_ask(
    conn: "sqlite3.Connection",
    actor: str,
    task_id: str,
    person: str,
    line: str,
    *,
    kind: str = "answer",
    choices: list[str] | None = None,
    shows: object = None,
    seen_version: int | str | None = None,
    mover: bool | None = None,
) -> Row:
    """Ask someone on the task: an answer (`kind` "answer", with `choices` where there are some) or an OK (`kind`
    "ok"). The task stays in its stage until they answer, the ask is withdrawn, or someone pushes past it. The result
    carries `requirement_id`."""
    H = _h()
    _open(conn, actor, task_id, mover)
    target = H._reach(conn, actor, person)
    return E.add_requirement(
        conn, task_id, actor, kind, target, line, choices=choices, shows=shows, seen_version=seen_version
    )


def _task_of_requirement(conn: "sqlite3.Connection", actor: str, requirement_id: str) -> str:
    found = conn.execute(
        "SELECT v.task_id FROM requirements r JOIN visits v ON v.id = r.visit_id WHERE r.id=?", (requirement_id,)
    ).fetchone()
    if not found:
        _refuse(conn, actor, "not-found", "No such ask on a task")
    return str(found[0])


def task_withdraw_ask(
    conn: "sqlite3.Connection", actor: str, requirement_id: str, why: str | None = None, *, mover: bool | None = None
) -> Row | None:
    """Take back an ask that was added to a task. The task moves on if that was all it waited for."""
    _open(conn, actor, _task_of_requirement(conn, actor, requirement_id), mover)
    with task_effects.because(actor):
        return E.withdraw_requirement(conn, requirement_id, actor, why)


def task_answer(
    conn: "sqlite3.Connection",
    actor: str,
    task_id: str,
    requirement_id: str,
    value: str,
    note: str | None = None,
    seen_version: int | str | None = None,
) -> Row:
    """Answer what is asked: `ok`, `no` with a note (send back) or `again` (look again) on an OK; the choice or the
    text on a question; `done` on the owner's own work. If that was the last thing missing the task moves on. The
    result carries `answer_id`."""
    _open(conn, actor, task_id, work=False)
    with task_effects.because(actor):
        return A.answer(conn, task_id, requirement_id, actor, value, note, seen_version=seen_version)


def task_give(conn: "sqlite3.Connection", actor: str, task_id: str, requirement_id: str, person: str) -> Row:
    """Give an ask to another person: it gets their name and moves to their list."""
    H = _h()
    _open(conn, actor, task_id, work=False)
    return A.give_to(conn, task_id, requirement_id, actor, H.resolve_actor(conn, person) or person)


def task_answer_undo(conn: "sqlite3.Connection", actor: str, answer_id: str) -> Row:
    """Take an answer back: whoever gave it, or any person."""
    found = conn.execute(
        "SELECT v.task_id FROM answers a JOIN visits v ON v.id = a.visit_id WHERE a.id=?", (answer_id,)
    ).fetchone()
    if not found:
        _refuse(conn, actor, "not-found", "No such answer")
    _open(conn, actor, str(found[0]), work=False)
    with task_effects.because(actor):
        return A.undo(conn, answer_id, actor)


def task_thumbs(
    conn: "sqlite3.Connection", actor: str, task_id: str, requirement_id: str, up: bool, note: str | None = None
) -> Row:
    """Was this worth asking. A thumbs down says what was wrong, and sends an open ask back."""
    _open(conn, actor, task_id, work=False)
    if up:
        return A.thumbs_up(conn, task_id, requirement_id, actor)
    return A.thumbs_down(conn, task_id, requirement_id, actor, note or "")


# ----------------------------------------------------------------------------- snooze
def task_snooze(
    conn: "sqlite3.Connection",
    actor: str,
    task_id: str,
    why: str | None = None,
    until: str | None = None,
    seen_version: int | str | None = None,
    *,
    mover: bool | None = None,
) -> Row:
    """Set a task aside until a date, or with no end. A bot gives a reason."""
    _open(conn, actor, task_id, mover)
    return E.snooze(conn, task_id, actor, why, until, seen_version)


def task_unsnooze(conn: "sqlite3.Connection", actor: str, task_id: str, why: str | None = None) -> Row:
    """End a task's snooze. Anyone who may read the task."""
    _open(conn, actor, task_id, work=False)
    return E.unsnooze(conn, task_id, actor, why)


def task_try_again(
    conn: "sqlite3.Connection", actor: str, task_id: str, why: str | None = None, *, mover: bool | None = None
) -> Row:
    """A person hands a task that reached them back to its owner, with the count at zero."""
    _open(conn, actor, task_id, mover)
    return E.try_again(conn, task_id, actor, why)


# ----------------------------------------------------------------------------- for code that acts for nobody
def attempt(conn: "sqlite3.Connection", call: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    """Run one task call that may be refused, for code nobody is waiting on. A refusal undoes that call alone and
    returns None; whatever the caller wrote before stands. Any other error is raised."""
    H = _h()
    name = f"task_attempt_{H.new_id().replace('-', '')}"
    conn.execute(f"SAVEPOINT {name}")
    try:
        out = call(conn, *args, **kwargs)
    except H.Refused:
        conn.execute(f"ROLLBACK TO {name}")
        conn.execute(f"RELEASE {name}")
        return None
    except BaseException:
        conn.execute(f"ROLLBACK TO {name}")
        conn.execute(f"RELEASE {name}")
        raise
    conn.execute(f"RELEASE {name}")
    return out


# ----------------------------------------------------------------------------- reads
def task_waits(conn: "sqlite3.Connection", task_ids: Iterable[str]) -> dict[str, list[W.Wait]]:
    """What each task waits on, headline first, by task id."""
    return W.waiting_on_many(conn, task_ids)

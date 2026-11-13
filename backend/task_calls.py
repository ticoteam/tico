# pyright: strict
"""The task calls everything outside the engine uses (docs/dev/pipelines-v3-cutover.md, "The Python calls").

hubdb re-exports each one, so a caller writes `H.task_move(...)`. Each call checks who is asking, then hands the write
to the engine (`task_engine`, `answers`). Nothing here writes a task's status, stage or owner.

Who may act:
- Every call: the actor is on the roster and may read the task (a private task reads as "not found" to anyone else).
- A call that works the task (done, move, close, reassign, ask, snooze, try again): the actor can work it, the same
  rule a field edit uses, plus the pipeline's manager and owner. `mover` is the caller saying a person may move any
  task (the company's owner; a route knows).
- An answer and a thumbs up: whoever the engine says the requirement is asked of, so a bot or a person named on an OK
  answers it without being on the task.
- Giving an ask away or sending it back with a thumbs down: who it waits on, whoever asked it, or someone who can work
  the task. Taking an answer back: only who gave it (`may_handle_ask`).

The engine's modules are loaded when a call runs, not when this module loads: hubdb imports this module, and the
engine's modules reach back to hubdb and the store.
"""

import functools
import json
from collections.abc import Callable, Iterable
from typing import TYPE_CHECKING, Any, Concatenate, NoReturn, ParamSpec, TypeVar, cast

from . import hubdb_core, task_effects

if TYPE_CHECKING:
    import sqlite3

    from . import task_requirements as R
    from . import waiting_on as W

Row = dict[str, Any]
_core: Any = hubdb_core
is_bot: Callable[[object], bool] = _core.is_bot
is_human: Callable[[object], bool] = _core.is_human
SYSTEM: str = _core.KEEPER

# The engine's words for where a move goes (task_engine.NEXT and the rest), spelled out so that loading this module
# never depends on the engine being loaded first.
NEXT, DONE, CLOSED, REOPEN = "next", "done", "closed", "reopen"


def _h() -> Any:
    from . import hubdb

    return hubdb


def _refuse(conn: "sqlite3.Connection", by: str, rule: str, detail: str, task_id: str | None = None) -> NoReturn:
    _h().refuse(conn, by, rule, detail, task=task_id)
    raise AssertionError("refuse() always raises")  # pragma: no cover


P = ParamSpec("P")
T = TypeVar("T")


def _task_of(conn: "sqlite3.Connection", of: str, ident: str) -> str | None:
    """The task a call is about, from what it names: the task, an ask on it, or an answer on it."""
    if of == "task":
        return ident
    table = "requirements" if of == "requirement" else "answers"
    found = conn.execute(f"SELECT v.task_id FROM {table} x JOIN visits v ON v.id = x.visit_id WHERE x.id=?", (ident,))
    row = found.fetchone()
    return str(row[0]) if row else None


def _task_write(
    of: str = "task",
) -> Callable[
    [Callable[Concatenate["sqlite3.Connection", str, str, P], T]],
    Callable[Concatenate["sqlite3.Connection", str, str, P], T],
]:
    """Around every call that writes: a private task's refusal is recorded without its words (the audit says only
    that a private write was refused, as for any private task write), the refusal is counted against this task, and
    the task trigger rules the write queues are judged once it is complete. `of` says what the third argument names."""

    def wrap(
        fn: Callable[Concatenate["sqlite3.Connection", str, str, P], T],
    ) -> Callable[Concatenate["sqlite3.Connection", str, str, P], T]:
        @functools.wraps(fn)
        def run(conn: "sqlite3.Connection", actor: str, ident: str, *args: P.args, **kwargs: P.kwargs) -> T:
            from . import task_trigger_rules

            H = _h()
            triggers: Any = task_trigger_rules
            task_id = _task_of(conn, of, ident)
            row: Row | None = H.task(conn, task_id) if task_id else None
            private = bool(row and H.task_private(conn, row))
            token = H.PRIVATE_WRITE.set(H.PRIVATE_WRITE.get() or private)
            about = H.REFUSAL_TASK.set((row or {}).get("id") or H.REFUSAL_TASK.get())
            try:
                with triggers.pending(conn):
                    return fn(conn, actor, ident, *args, **kwargs)
            except H.Refused as exc:
                exc.private = H.PRIVATE_WRITE.get()
                raise
            finally:
                H.REFUSAL_TASK.reset(about)
                H.PRIVATE_WRITE.reset(token)

        return run

    return wrap


# ----------------------------------------------------------------------------- who may
def can_work(conn: "sqlite3.Connection", actor: str, row: Row, mover: bool | None = None) -> bool:
    """Whether `actor` can work this task: its owner, its requester, their managers and delegates, a party to a task
    above it, the General Manager, the manager and the owner of its pipeline, or a person who moves tasks."""
    from . import task_engine as E

    H = _h()
    if actor == SYSTEM:
        return True
    if not H.task_private_readable(conn, actor, row):
        return False
    if H._task_party(conn, actor, row) or H.general_manager_acts(conn, actor, row):
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
    if work:
        _may_work(conn, actor, row, mover)
    return row


def _may_work(conn: "sqlite3.Connection", actor: str, row: Row, mover: bool | None = None) -> None:
    if not can_work(conn, actor, row, mover):
        _refuse(
            conn,
            actor,
            "identity",
            "This task is worked by its owner, whoever asked for it, their managers, and its pipeline's manager. "
            "Comment on it, or file a new task.",
            row["id"],
        )


def _owners_done_is_work(conn: "sqlite3.Connection", actor: str, row: Row, requirement_id: str) -> None:
    """Answering the owner's own "done" finishes the stage, and giving it away hands the task over: /done and /owner
    by another road, so the same reader may do them, refused as those are. The owner's answer to a stage's question is
    their work too."""
    from . import task_requirements as R

    requirement = R.get(conn, requirement_id) or {}
    if requirement.get("kind") == "owner_done" or R.owners_answer(requirement):
        _may_work(conn, actor, row)


def _requirement_named(conn: "sqlite3.Connection", row: Row, requirement: str) -> str:
    """The requirement a caller names: its id, or the name of a stage's answer the task's open visit asks
    (`tico task answer E-12 confidence 80`). Anything else is passed on as it is, for the engine to refuse."""
    from . import task_engine as E
    from . import task_requirements as R

    visit: Row | None = (E.task(conn, row["id"]) or {}).get("visit")
    if not visit or R.get(conn, requirement):
        return requirement
    wanted = requirement.strip().lower()
    for found in R.for_visit(conn, visit):
        settings: Row = found.get("settings") or {}
        if found["kind"] == "answer" and str(settings.get("name") or "") == wanted:
            return str(found["id"])
    return requirement


def may_handle_ask(
    conn: "sqlite3.Connection",
    actor: str,
    row: Row,
    requirement: Row,
    *,
    recipient: str | None = None,
    answered_by: str | None = None,
) -> bool:
    """Who may move an ask other than by answering it. Giving it away or withdrawing it: the person it waits on now
    (`recipient`), whoever asked it, or someone who can work the task. Taking an answer back (`answered_by`): only who
    gave it, and the owner's own "done" only while they can still work the task. A plain reader does neither."""
    if actor == SYSTEM:
        return True
    if answered_by is not None:
        if str(actor) != str(answered_by):
            return False
        return requirement.get("kind") != "owner_done" or can_work(conn, actor, row)
    if actor in (recipient, requirement.get("added_by_id")):
        return True
    return can_work(conn, actor, row)


# ----------------------------------------------------------------------------- moves
@_task_write()
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
    from . import task_engine as E
    from . import answers as A
    from . import task_requirements as R

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


@_task_write()
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
    from . import task_engine as E

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


@_task_write()
def task_let_see(
    conn: "sqlite3.Connection",
    actor: str,
    task_id: str,
    whom: list[str],
    seen_version: int | str | None = None,
    *,
    mover: bool | None = None,
    check: Callable[[list[str]], None] | None = None,
) -> Row:
    """A person lets `whom` see private task `task_id` and moves it on, in one write: the stuck card's one fix (task
    #392). `whom` must be exactly who the task waits on to be let in now (`waiting_on.Wait.share`, as the card showed
    it: everyone the next stage shuts out, so one press always gets it past them): when that changed, nobody is added
    and the refusal names who it is now. Only they are added (one reader event each, "<person> let <them> see this
    task"); then, with nothing else missing, the task moves to the next stage. A bot never lets anyone in: it asks a
    person (task_engine.fix_for). `check(added)` may refuse a reader (task_privacy.require_readers). A refused move
    adds nobody: the whole write is undone."""
    from . import task_engine as E
    from . import task_requirements as R
    from . import waiting_on as W

    H = _h()
    if is_bot(actor):
        _refuse(conn, actor, "task_needs_person", "Only a person lets someone see a private task. Ask one.", task_id)
    row = _open(conn, actor, task_id, mover)
    full = E.task(conn, task_id)
    if not full:
        _refuse(conn, actor, "not-found", "No such task")
    E.check_version(conn, actor, full, seen_version)
    waits: list[Any] = W.of(conn, full)
    nobody: list[str] = []
    now: list[str] = next((list(wait.share) for wait in waits if wait.share), nobody)
    named: list[str] = [str(H.resolve_actor(conn, who) or who) for who in whom]
    if not named or sorted(set(named)) != sorted(now):
        said = (
            f"{W.names_of(conn, now)} can't see it now. Nobody was added."
            if now
            else "Nobody is waiting to see this task now. Nobody was added."
        )
        _refuse(conn, actor, "task_changed", said, task_id)
    H.set_task_readers(conn, actor, task_id, add=now, mover=mover, check=check)
    after = E.task(conn, task_id)
    if not after or R.unmet(conn, after):
        return after or row  # an ask in this stage waited on them: it is theirs now, and the task stays
    with task_effects.because(actor):
        return E.move(conn, task_id, NEXT, actor, None, after.get("version"))


@_task_write()
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


@_task_write()
def task_reassign(
    conn: "sqlite3.Connection",
    actor: str,
    task_id: str,
    owner: str | None,
    why: str | None = None,
    seen_version: int | str | None = None,
    *,
    mover: bool | None = None,
    set_anyway: bool = False,
) -> Row:
    """Give the task to another owner for this stage. Checked as any hand-over is: the actor reaches the new owner,
    the new owner takes tasks, the General Manager stays in the department, and a bot's hand-over to a person passes the
    strategy rules. `set_anyway`: a person overturns the stage rule that blocks this owner (E.reassign)."""
    from . import task_engine as E

    H = _h()
    row = _open(conn, actor, task_id, mover)
    target: str | None = None
    if owner:
        H._botops_only(conn, actor, H.resolve_actor(conn, owner))
        target = H._reach(conn, actor, owner)
    if target and target != row.get("owner_id"):
        if actor == H.GENERAL_MANAGER:
            from . import general_manager

            department: Any = general_manager
            if not department.same_department(conn, row.get("owner_id"), target):
                _refuse(
                    conn,
                    actor,
                    "identity",
                    "The General Manager reassigns only within the owner's department. Leave a comment for the "
                    "department instead.",
                    task_id,
                )
        if is_human(target):
            from . import strategy_acts

            H.review_finding_route(conn, actor, task_id, target)
            acts: Any = strategy_acts
            acts.gate(conn, actor, "person_task", f"give {acts.TASK} to {H.person_name(conn, target)}", task_id=task_id)
        from . import task_relations

        relations: Any = task_relations
        H._takes_tasks(conn, actor, target, row.get("title"), relations.parent_of(conn, task_id), task=task_id)
        H._learner(conn, actor)
        H._learning(conn, actor, target)
    return E.reassign(conn, task_id, target, actor, why, seen_version, set_anyway=set_anyway)


# ----------------------------------------------------------------------------- asks and answers
def bring_in(conn: "sqlite3.Connection", actor: str, row: Row, who: str) -> None:
    """Whoever an ask names must be able to read the task already. On a private task anyone else is refused, with the
    fix named (a person who can see it adds them under Who can see): nobody is asked about a task they cannot open,
    and an ask makes nobody a reader (task_engine.not_shared; the engine checks it again for every ask)."""
    from . import task_engine as E

    unshared = E.not_shared(conn, row, who)
    if unshared:
        _refuse(conn, actor, "private", E.told(conn, actor, row, unshared), row["id"])


@_task_write()
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
    for_action: bool = False,
    brief: Row | None = None,
) -> Row:
    """Ask someone on the task: an answer (`kind` "answer", with `choices` where there are some) or an OK (`kind`
    "ok"). `brief` is a question's why, its choices' lines and the one recommended (backend/ask_shape.py). The task stays in its stage until they answer, the ask is withdrawn, or someone pushes past it. The result
    carries `requirement_id`."""
    from . import task_engine as E

    H = _h()
    row = _open(conn, actor, task_id, mover)
    target = H._reach(conn, actor, person)
    if is_bot(actor):
        # An ask writes to the one it names, so a bot's ask keeps to its reach as its messages do (reach.check: the
        # person who handed it the task is always in it). Refused before anything is written.
        from types import SimpleNamespace

        from . import reach as reach_module

        reach: Any = reach_module
        reach.check(conn, SimpleNamespace(role="bot", actor=actor), target, "task", task_id=task_id)
    bring_in(conn, actor, row, target)
    if is_bot(actor) and is_human(target):
        # What a bot puts in front of a person is plain: one or two short sentences, no ids or paths (rule 7).
        H.require_person_ask(conn, actor, target, line, "ask")
    return E.add_requirement(
        conn,
        task_id,
        actor,
        kind,
        target,
        line,
        choices=choices,
        shows=shows,
        seen_version=seen_version,
        for_action=for_action,
        brief=brief,
    )


def _task_of_requirement(conn: "sqlite3.Connection", actor: str, requirement_id: str) -> str:
    found = conn.execute(
        "SELECT v.task_id FROM requirements r JOIN visits v ON v.id = r.visit_id WHERE r.id=?", (requirement_id,)
    ).fetchone()
    if not found:
        _refuse(conn, actor, "not-found", "No such ask on a task")
    return str(found[0])


@_task_write("requirement")
def task_withdraw_ask(
    conn: "sqlite3.Connection", actor: str, requirement_id: str, why: str | None = None, *, mover: bool | None = None
) -> Row | None:
    """Take back an ask that was added to a task. The task moves on if that was all it waited for."""
    from . import task_engine as E

    _open(conn, actor, _task_of_requirement(conn, actor, requirement_id), mover)
    with task_effects.because(actor):
        return E.withdraw_requirement(conn, requirement_id, actor, why)


@_task_write()
def task_answer(
    conn: "sqlite3.Connection",
    actor: str,
    task_id: str,
    requirement_id: str,
    value: str,
    note: str | None = None,
    seen_version: int | str | None = None,
    *,
    own_click: bool = False,
    page_version: str | None = None,
) -> Row:
    """Answer what is asked: `ok`, `no` with a note (send back) or `again` (look again) on an OK; `ok` or `no` on a
    page, for the page shown (`page_version`, `<file id>@<version>`); the choice or the text on a question; `done` on
    the owner's own work. If that was the last thing missing the task moves on. The result carries `answer_id`. `own_click` says the
    request is the person's own click in their own session (task_asks.own_click): only that OKs an OK the person
    asked themself."""
    from . import answers as A

    row = _open(conn, actor, task_id, work=False)
    requirement_id = _requirement_named(conn, row, requirement_id)
    _owners_done_is_work(conn, actor, row, requirement_id)
    with task_effects.because(actor):
        return A.answer(
            conn,
            task_id,
            requirement_id,
            actor,
            value,
            note,
            seen_version=seen_version,
            own_click=own_click,
            page_version=page_version,
        )


@_task_write()
def task_requeue(conn: "sqlite3.Connection", actor: str, task_id: str, seen_version: int | None = None) -> list[Row]:
    """Queue again (item_sources.github.requeue), as a task write: a private task's refusal keeps none of its words."""
    from .item_sources import github as merge_queue

    return merge_queue.requeue(conn, task_id, actor, seen_version=seen_version)


@_task_write()
def task_release(
    conn: "sqlite3.Connection", actor: str, task_id: str, tag: str | None = None, seen_version: int | None = None
) -> list[Row]:
    """Cut the release the task waits for (item_sources.github_release.cut), as a task write: a private task's refusal
    keeps none of its words."""
    from .item_sources import github_release

    return github_release.cut(conn, task_id, actor, tag, seen_version=seen_version)


@_task_write()
def task_page_attach(
    conn: "sqlite3.Connection",
    actor: str,
    task_id: str,
    requirement: str,
    ref: str,
    seen_version: int | None = None,
    *,
    note: str | None = None,
    changed: list[str] | None = None,
    answered: list[str] | None = None,
) -> Row:
    """Attach a page (task_pages.attach), as a task write: a private task's refusal keeps none of its words."""
    from . import task_pages

    return task_pages.attach(
        conn,
        task_id,
        requirement,
        ref,
        actor,
        seen_version=seen_version,
        note=note,
        changed=changed,
        answered=answered,
    )


@_task_write()
def task_give(
    conn: "sqlite3.Connection",
    actor: str,
    task_id: str,
    requirement_id: str,
    person: str,
    seen_version: int | str | None = None,
) -> Row:
    """Give an ask to another person: it gets their name and moves to their list."""
    from . import answers as A

    H = _h()
    row = _open(conn, actor, task_id, work=False)
    _owners_done_is_work(conn, actor, row, requirement_id)
    bring_in(conn, actor, row, H.resolve_actor(conn, person) or person)
    with task_effects.because(actor):
        return A.give_to(
            conn, task_id, requirement_id, actor, H.resolve_actor(conn, person) or person, seen_version=seen_version
        )


@_task_write("answer")
def task_answer_undo(conn: "sqlite3.Connection", actor: str, answer_id: str) -> Row:
    """Take an answer back: only whoever gave it (may_handle_ask)."""
    from . import answers as A

    found = conn.execute(
        "SELECT v.task_id FROM answers a JOIN visits v ON v.id = a.visit_id WHERE a.id=?", (answer_id,)
    ).fetchone()
    if not found:
        _refuse(conn, actor, "not-found", "No such answer")
    _open(conn, actor, str(found[0]), work=False)
    with task_effects.because(actor):
        return A.undo(conn, answer_id, actor)


@_task_write()
def task_thumbs(
    conn: "sqlite3.Connection", actor: str, task_id: str, requirement_id: str, up: bool, note: str | None = None
) -> Row:
    """Was this worth asking. A thumbs down says what was wrong, and sends an open ask back. A move it causes is the
    actor's."""
    from . import answers as A

    _open(conn, actor, task_id, work=False)
    with task_effects.because(actor):
        if up:
            return A.thumbs_up(conn, task_id, requirement_id, actor)
        return A.thumbs_down(conn, task_id, requirement_id, actor, note or "")


# ----------------------------------------------------------------------------- snooze
@_task_write()
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
    from . import task_engine as E

    _open(conn, actor, task_id, mover)
    return E.snooze(conn, task_id, actor, why, until, seen_version)


@_task_write()
def task_unsnooze(conn: "sqlite3.Connection", actor: str, task_id: str, why: str | None = None) -> Row:
    """End a task's snooze. Anyone who may read the task."""
    from . import task_engine as E

    _open(conn, actor, task_id, work=False)
    return E.unsnooze(conn, task_id, actor, why)


@_task_write()
def task_try_again(
    conn: "sqlite3.Connection", actor: str, task_id: str, why: str | None = None, *, mover: bool | None = None
) -> Row:
    """A person hands a task that reached them back to its owner, with the count at zero."""
    from . import task_engine as E

    _open(conn, actor, task_id, mover)
    return E.try_again(conn, task_id, actor, why)


# ----------------------------------------------------------------------------- for code that acts for nobody
def attempt(conn: "sqlite3.Connection", call: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    """Run one task call that may be refused, for code nobody is waiting on. A refusal undoes that call alone, with
    the task trigger events it queued, and returns None; whatever the caller wrote before stands. Any other error is
    raised, undone the same way."""
    from . import task_trigger_rules

    H = _h()
    triggers: Any = task_trigger_rules
    try:
        with triggers.savepoint(conn):
            return call(conn, *args, **kwargs)
    except H.Refused:
        return None


# ----------------------------------------------------------------------------- reads
def task_waits(conn: "sqlite3.Connection", task_ids: Iterable[str]) -> "dict[str, list[W.Wait]]":
    """What each task waits on, headline first, by task id."""
    from . import waiting_on as W

    return W.waiting_on_many(conn, task_ids)


def _may(
    conn: "sqlite3.Connection",
    row: Row,
    standing: Any,
    answer: Row | None,
    viewer: str,
    holder: str | None,
    works: bool,
) -> list[str]:
    """What `viewer` may do with one requirement, as names a screen turns into buttons. A hint: the engine decides
    when the call is made."""
    from . import task_requirements as R

    requirement: Row = standing.requirement
    kind = requirement["kind"]
    human = is_human(viewer)
    owner = row.get("owner_id")
    out: list[str] = []
    asked = False
    if kind in ("ok", "answer"):
        person = R.person_of(conn, requirement, row["visit"]["id"])
        asked = viewer == person or (person is None and human)
    if kind == "owner_done" and not standing.met and works:
        out += ["done", "give"]
    if kind == "ok" and not standing.met:
        if answer and answer["answer"] == "no":
            if viewer in (owner, holder):
                out.append("again")
        elif asked and standing.state == R.WAITING and not R.outside(standing.on):
            out += ["ok", "no"]  # not while it is held on Jev or back with the owner (validate_gate.held)
    owners = R.owners_answer(requirement)
    if kind == "answer" and not standing.met and (works if owners else asked):
        out.append("answer")
    # A page is answered once Jev has looked: by whom it waits on, or any person when it names nobody (answers).
    if kind == "page" and standing.state == R.WAITING and not R.outside(standing.on):
        if viewer == standing.on or (standing.on is None and human):
            out += ["ok", "no"]
    # Give: whoever it waits on, whoever asked it, or someone who works the task (may_handle_ask); the owner's own work
    # is given by someone who works the task, which reassigns it.
    on_stage_answer = kind == "answer" and bool(requirement.get("stage_id"))
    if kind in ("ok", "answer") and standing.state == R.WAITING and not on_stage_answer:
        if works or viewer in (standing.on, requirement.get("added_by_id")):
            out.append("give")
    if answer and answer["by_id"] == viewer and (kind != "owner_done" or works):
        out.append("undo")
    if requirement.get("visit_id") and (works or viewer == requirement.get("added_by_id")):
        out.append("withdraw")
    # A thumb rates a bot's ask, as answers._ask reads it: one added to the task is its author's, a stage's is the
    # owner's.
    asker = requirement.get("added_by_id") if requirement.get("visit_id") else owner
    if kind in ("ok", "answer") and human and is_bot(asker) and not on_stage_answer:
        out.append("thumbs")
    return out


def _page_of(conn: "sqlite3.Connection", visit_id: str, requirement: Row) -> Row | None:
    """The page attached to a page requirement on this visit: {file_id, version, ref}, or None."""
    from . import task_pages

    found = task_pages.current(conn, visit_id, requirement["id"])
    if not found:
        return None
    ref = f"page:{found['file_id']}@{found['version']}"
    return {"file_id": found["file_id"], "version": int(found["version"]), "ref": ref}


# The viewer of what is kept on record (the audit of a refusal, a stored response): anyone may come to read it, so a
# private task it names is hidden as from someone who may not read that task.
ON_RECORD = "*"


def _readable(conn: "sqlite3.Connection", viewer: str | None, task_id: object, visible_sql: str | None = None) -> bool:
    """Whether `viewer` may read another task this one names. With no viewer nothing is hidden (the caller is the
    engine's own code); ON_RECORD reads only what is not private. `visible_sql` is the reader's whole rule
    (auth.task_sql: private audience, the bots they may read, a delegated identity's limits), the one a direct read
    of that task uses; without it only privacy is checked."""
    H = _h()
    if viewer is None:
        return True
    other: Row | None = H.task(conn, str(task_id)) if task_id else None
    if viewer == ON_RECORD:
        return bool(other) and not H.task_private(conn, other)
    if not other or not H.task_private_readable(conn, viewer, other):
        return False
    if visible_sql is None:
        return True
    return conn.execute(f"SELECT 1 FROM tasks WHERE id=? AND ({visible_sql})", (other["id"],)).fetchone() is not None


def _readable_rows(
    conn: "sqlite3.Connection", viewer: str | None, task_ids: Iterable[str], visible_sql: str | None = None
) -> dict[str, Row]:
    """`_readable` for many tasks at once, two statements however many: {id: its row} of those `viewer` may read."""
    H = _h()
    ids = sorted({str(task_id) for task_id in task_ids if task_id})
    if not ids:
        return {}
    listed = (json.dumps(ids),)
    rows: dict[str, Row] = {
        str(found["id"]): found
        for found in H._rows(conn.execute("SELECT * FROM tasks WHERE id IN (SELECT value FROM json_each(?))", listed))
    }
    if viewer is None:
        return rows
    if viewer == ON_RECORD:
        return {task_id: found for task_id, found in rows.items() if not H.task_private(conn, found)}
    rows = {task_id: found for task_id, found in rows.items() if H.task_private_readable(conn, viewer, found)}
    if visible_sql is None or not rows:
        return rows
    seen = {
        str(found[0])
        for found in conn.execute(
            f"SELECT id FROM tasks WHERE id IN (SELECT value FROM json_each(?)) AND ({visible_sql})",
            (json.dumps(sorted(rows)),),
        )
    }
    return {task_id: found for task_id, found in rows.items() if task_id in seen}


# The links and asks a task's read names, keyed by their first column, each read once in a snapshot (or for many
# tasks together by `prime_views`), so a list of tasks' views costs what one task's does.
_FILED_ON_VISIT = "SELECT visit_id, to_task_id FROM links WHERE visit_id IN (%s) ORDER BY rowid"
_FILED_BY = "SELECT to_task_id, from_task_id FROM links WHERE to_task_id IN (%s) ORDER BY rowid"
_FILED = "SELECT from_task_id, to_task_id, kind FROM links WHERE from_task_id IN (%s) ORDER BY created_at, id"
_DECIDED = (
    "SELECT r.visit_id, r.id FROM requirements r WHERE r.visit_id IN (%s) AND r.by_bot=1 "
    "AND r.withdrawn_at IS NOT NULL AND EXISTS (SELECT 1 FROM history_events e WHERE e.subject_type='task' "
    "AND e.subject_id=(SELECT v.task_id FROM visits v WHERE v.id=r.visit_id) AND e.kind='requirement_withdrawn' "
    "AND e.old=r.id AND e.note='Asked again') ORDER BY r.created_at, r.id"
)


def _keyed(conn: "sqlite3.Connection", sql: str, keys: list[str]) -> dict[str, list[tuple[Any, ...]]]:
    """{key: the rest of each row} of `sql` over many keys, one statement (the keys go in as one JSON list)."""
    out: dict[str, list[tuple[Any, ...]]] = {key: [] for key in keys}
    if keys:
        for found in conn.execute(sql % "SELECT value FROM json_each(?)", (json.dumps(keys),)).fetchall():
            out[str(found[0])].append(tuple(found)[1:])
    return out


def _cached(conn: "sqlite3.Connection", sql: str, key: str) -> list[tuple[Any, ...]]:
    from . import read_cache

    return read_cache.value(conn, (sql, key), lambda: _keyed(conn, sql, [key])[key], shared=True)


def prime_views(conn: "sqlite3.Connection", task_ids: Iterable[str], viewer: str | None = None) -> None:
    """Read what `task_view` reads of each of many tasks together, a few statements in all, for the views that follow
    in this snapshot (Needs you reads one for each ask on it). Without a read snapshot it reads nothing: each view
    then reads for itself. A write discards it all (read_cache)."""
    from . import read_cache
    from . import task_engine as E
    from . import task_relations
    from . import task_requirements as R
    from . import waiting_on as W

    TR: Any = task_relations
    values = read_cache.cache(conn)
    if values is None:
        return
    rows = W.prime(conn, task_ids)
    ids = sorted(rows)
    visits = sorted({str(row["visit"]["id"]) for row in rows.values() if row.get("visit")})
    for sql, keys in ((_FILED_ON_VISIT, visits), (_DECIDED, visits), (_FILED_BY, ids), (_FILED, ids)):
        unread = [key for key in keys if (sql, key) not in values]
        for key, found in _keyed(conn, sql, unread).items():
            values.setdefault((sql, key), found)
    E.prime_stages(conn, [str(row["pipeline_id"]) for row in rows.values() if row.get("pipeline_id")])
    R.prime_values(conn, ids)
    TR.prime_ancestry(conn, ids)
    if viewer:
        _h().prime_task_delegated(conn, ids, viewer)


def hidden_filed(
    conn: "sqlite3.Connection", row: Row, viewer: str | None, visible_sql: str | None = None, *, kept: bool = False
) -> list[str]:
    """The tasks the open visit of `row` (task_engine.task's shape) filed that `viewer` may not read (`_readable`).
    Their titles and ids are not theirs to see: not in what the task waits on, not in a requirement's standing, not
    in a refusal (waiting_on.unnamed says what they are told instead).

    `kept`: the words are a wake or a note kept in the task's thread, read later by whoever reads the task, not only by
    `viewer`. A private task filed there is hidden too unless its words may reach every reader of this one
    (H.subtask_flows_up)."""
    visit: Row = row.get("visit") or {}
    if row.get("status") != "open" or not visit:
        return []
    filed = [str(found[0]) for found in _cached(conn, _FILED_ON_VISIT, str(visit["id"]))]
    if viewer is None or not filed:
        return []
    readable = _readable_rows(conn, viewer, filed, visible_sql)
    H = _h()
    return [
        other
        for other in filed
        if other not in readable
        or kept
        and H.task_private(conn, readable[other])
        and not H.subtask_flows_up(conn, readable[other], row)
    ]


def standing_line(standing: "R.Standing", hidden: list[str]) -> str:
    """How a requirement stands, said to someone who may not read the tasks in `hidden` (`hidden_filed`)."""
    from . import waiting_on as W

    return W.unnamed(standing, lambda task_id: task_id not in hidden).line


def _answer_view(conn: "sqlite3.Connection", task_id: str, answer: Row, private: bool) -> Row:
    """One answer as the task shows it. `via` is who gave it for the person, when that was the Assistant or BotOps
    acting with the person's rights (H.VIA, kept on the answer's history row): "assistant" or "botops", else None."""
    found = conn.execute(
        "SELECT via FROM history_events WHERE subject_type='task' AND subject_id=? AND kind='answer' "
        "AND json_extract(data, '$.answer_id')=? LIMIT 1",
        (task_id, answer["id"]),
    ).fetchone()
    return {
        "id": answer["id"],
        "value": answer["answer"],
        "note": None if private else answer.get("notes"),
        "by": answer["by_id"],
        "via": (found[0] if found else None) or None,
        "at": answer["created_at"],
    }


def _typed_view(requirement: Row) -> Row | None:
    """A stage's answer as the task shows it: {name, type, min, max, question}. None for any other requirement."""
    settings: Row = requirement.get("settings") or {}
    if requirement.get("kind") != "answer" or not requirement.get("stage_id"):
        return None
    from . import task_requirements as R

    return {
        "name": settings.get("name"),
        "type": R.answer_type(requirement),
        "min": settings.get("min"),
        "max": settings.get("max"),
        "question": settings.get("question") or "",
    }


def _decided(conn: "sqlite3.Connection", task_id: str, visit_id: str, viewer: str | None) -> list[Row]:
    """A bot's asks on this visit that were answered (an OK given, a question answered) before its next ask replaced
    them (C15, task_engine._replace_open): what was decided, so it stays on the task, met, with its answer. Nothing
    can be done about one any more (`may` is empty)."""
    from . import task_asks
    from . import task_requirements as R

    out: list[Row] = []
    for (requirement_id,) in _cached(conn, _DECIDED, visit_id):
        requirement = R.get(conn, str(requirement_id)) or {}
        answer = R.newest_answers(conn, visit_id).get(str(requirement_id))
        if not answer or (requirement.get("kind") == "ok" and answer["answer"] != "ok"):
            continue  # it was still open when it was replaced: nothing was decided
        private = requirement["kind"] == "ok" and task_asks.hidden_from(conn, viewer, requirement["id"])
        out.append(
            {
                "requirement_id": requirement["id"],
                "kind": requirement["kind"],
                "line": task_asks.PRIVATE_LINE if private else R.name_of(requirement),
                "met": True,
                "replaced": True,
                "standing": "OK'd" if requirement["kind"] == "ok" else str(answer["answer"]),
                "who": None,
                "asked_by": None if private else requirement.get("added_by_id"),
                "choices": None if private else requirement.get("choices"),
                **({"brief": _brief_of(requirement)} if _brief_of(requirement) and not private else {}),
                "shows": [] if private else requirement.get("shows") or [],
                **({"source": requirement["shows"]} if requirement.get("shows") and not private else {}),
                "answer": _answer_view(conn, task_id, answer, private),
                "may": [],
            }
        )
    return out


def _brief_of(requirement: Row) -> Row | None:
    """A question's why, its choices' lines and the one recommended, as the bot asked it (backend/ask_shape.py), or
    None for an ask that carried none."""
    settings: Row = requirement.get("settings") or {}
    brief: Any = settings.get("brief")
    if not isinstance(brief, dict) or not cast("Row", brief).get("why"):
        return None
    found = cast("Row", brief)
    return {"why": found.get("why"), "details": found.get("details"), "recommend": found.get("recommend")}


def _leave_entry(
    conn: "sqlite3.Connection",
    row: Row,
    standing: Any,
    answers: dict[str, Row],
    viewer: str | None,
    hidden: Any,
    holder: Any,
    works: bool,
) -> Row:
    """One entry of a task's `to_leave` (task_view): a requirement of its open visit, where it stands, and what `viewer`
    may do about it. `hidden` is what `hidden_filed` keeps from them; `holder` and `works` are the task's."""
    from . import task_asks
    from . import task_requirements as R

    task_id = row["id"]
    requirement: Row = standing.requirement
    answer = answers.get(requirement["id"])
    # A bound OK's line, what it shows and a send back's note are its parties' (task_asks.party): anyone else who
    # reads the task learns only that it waits on a person.
    private = requirement["kind"] == "ok" and task_asks.hidden_from(conn, viewer, requirement["id"])
    # A page requirement shows the page attached now, at its pinned version: what an OK of it is for.
    page = _page_of(conn, row["visit"]["id"], requirement) if requirement["kind"] == "page" else None
    shows = [page["ref"]] if page else [] if private else requirement.get("shows") or []
    typed = _typed_view(requirement)
    return {
        "requirement_id": requirement["id"],
        "kind": requirement["kind"],
        "line": task_asks.PRIVATE_LINE if private else R.name_of(requirement),
        "met": standing.met,
        "standing": task_asks.PRIVATE_LINE if private and not standing.met else standing_line(standing, hidden),
        "who": None if standing.met or standing.on in hidden else standing.on,
        "asked_by": None if private else requirement.get("added_by_id") if requirement.get("visit_id") else None,
        "choices": None if private else requirement.get("choices"),
        **({"typed": typed} if typed else {}),
        **({"brief": _brief_of(requirement)} if _brief_of(requirement) and not private else {}),
        "shows": shows,
        # What it shows is where its words come from: a reader who may not open one of those pages reads
        # none of the ask, its choices or its answer (files.readable_page_refs drops the whole entry).
        **({"source": shows} if shows else {}),
        **({"page": page} if page else {}),
        "answer": _answer_view(conn, task_id, answer, private) if answer else None,
        "may": _may(conn, row, standing, answer, str(viewer), holder, works) if viewer else [],
    }


def ask_entry(conn: "sqlite3.Connection", task_id: str, requirement_id: str, viewer: str) -> Row | None:
    """The `to_leave` entry of one requirement of a task, as `task_view(conn, task_id, viewer)` gives it, without the
    rest of the view (its waits, next stage, filed tasks and values): Needs you shows each ask with this alone. None
    when the view has no such entry."""
    from . import task_engine as E
    from . import task_requirements as R
    from . import waiting_on as W

    row = E.task(conn, task_id)
    if not row:
        return None
    visit: Row = row.get("visit") or {}
    if not (row["status"] == "open" and visit):
        return None
    standings = R.standings(conn, row)
    found = next((s for s in standings if s.requirement["id"] == requirement_id), None)
    if found is None:
        # Not a live requirement: the view lists what was decided after the live ones.
        return next(
            (r for r in _decided(conn, task_id, visit["id"], viewer) if r["requirement_id"] == requirement_id), None
        )
    hidden = hidden_filed(conn, row, viewer, None)
    answers = R.newest_answers(conn, visit["id"])
    return _leave_entry(conn, row, found, answers, viewer, hidden, W.holder(conn, row), can_work(conn, viewer, row))


def task_view(
    conn: "sqlite3.Connection",
    task_id: str,
    viewer: str | None = None,
    visible_sql: str | None = None,
    *,
    inputs: bool = False,
) -> Row | None:
    """The engine's part of a task as JSON (docs/dev/pipelines-v3-cutover.md, "The task as JSON"): where it is, whose
    turn it is, everything it needs to leave its stage, and what `viewer` may do about each. None when there is no
    such task. A read: it writes nothing, and it does not check that `viewer` may read the task (the caller did).
    `inputs`: also what earlier stages handed on (stage_inputs), for the task's own read; a list of many tasks
    (Needs you) leaves it out."""
    from . import task_engine as E
    from . import task_requirements as R
    from . import waiting_on as W

    row = E.task(conn, task_id)
    if not row:
        return None
    visit: Row = row.get("visit") or {}
    stage: Row = row.get("stage") or {}
    pipe: Row = row.get("pipeline") or {}
    live = row["status"] == "open" and bool(visit)
    standings = R.standings(conn, row) if live else []
    # A task this one filed may be private to people who can read this one: its title is not theirs to see, here or
    # in the line that says what this task waits on, nor its id, nor why it was closed (waiting_on.unnamed).
    hidden = hidden_filed(conn, row, viewer, visible_sql)
    shown: Callable[[str], bool] = lambda task_id: task_id not in hidden
    waits = [W.view(wait, shown) for wait in W.of(conn, row, standings=standings)] if live else []
    answers = R.newest_answers(conn, visit["id"]) if live else {}
    holder = W.holder(conn, row) if live else None
    works = bool(viewer) and can_work(conn, str(viewer), row)
    to_leave = [_leave_entry(conn, row, standing, answers, viewer, hidden, holder, works) for standing in standings]
    if live:
        to_leave.extend(_decided(conn, task_id, visit["id"], viewer))
    following = E.next_stage(conn, row) if live else None
    ready = bool(waits) and all(wait["state"] == W.ROOM for wait in waits)
    held = E.shut_out(conn, row, following) if following and ready else None
    for wait in waits:
        if held and wait["state"] == W.ROOM:
            # Everything is met, and the next stage has someone who cannot read this private task: it waits there
            # until someone who can see it shares it (the never-stuck pass then moves it).
            wait["line"], wait["fix"] = held["cause"], E.fix_for(conn, viewer, row, held)
    # The task that filed this one, and the tasks this one filed, only as far as `viewer` may read them
    # (`_readable`): one they may not read is left out whole, its id, status and that it exists at all.
    above = _cached(conn, _FILED_BY, task_id)
    filed_by = above[0][0] if above and _readable(conn, viewer, above[0][0], visible_sql) else None
    links = _cached(conn, _FILED, task_id)
    children = _readable_rows(conn, viewer, [str(child_id) for child_id, _kind in links], visible_sql)
    filed: list[Row] = []
    for child_id, kind in links:
        child = children.get(str(child_id))
        if child:
            filed.append({"id": child_id, "kind": kind, "status": child["status"], "title": child["title"]})
    from . import stage_inputs

    # What earlier stages handed on (the Spec page, the PR, each Review page), as far as `viewer` may read the task
    # that filed this one and the tasks this one filed.
    handed = (
        stage_inputs.of(conn, row, shown, lambda filer: _readable(conn, viewer, filer["id"], visible_sql))
        if live and inputs
        else []
    )
    # The repositories the work is in (a filed Engineering task's from the start), for `tico task show`.
    repos = stage_inputs.repositories(conn, task_id, shown) if inputs else []
    snooze: Row | None = row.get("snooze")
    late = None
    if live and visit.get("late_told_at"):
        late = {"since": visit["late_told_at"], "level": "person" if visit.get("late_person_at") else "told"}
    return {
        "id": task_id,
        "status": row["status"],
        "version": int(row.get("version") or 0),
        "pipeline": {"id": pipe.get("id"), "name": pipe.get("name")},
        "stage": {
            "id": stage.get("id"),
            "name": stage.get("name"),
            "kind": stage.get("kind"),
            "by_hand": bool(stage.get("by_hand")),
        },
        "owner_id": row.get("owner_id"),
        "assignee_id": row.get("assignee_id"),
        "turn": waits[0] if waits else None,
        "waits": waits,
        "to_leave": to_leave,
        "inputs": handed,
        "repositories": repos,
        "next": {"id": following["id"], "name": following["name"]} if following else None,
        "late": late,
        "snooze": {"until": snooze.get("until_at"), "by": snooze.get("by_id"), "why": snooze.get("why")}
        if snooze
        else None,
        "filed_by": filed_by,
        "filed": filed,
        "values": R.values(conn, task_id),
    }

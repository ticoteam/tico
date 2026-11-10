# pyright: strict
"""Whose turn a task is (docs/pipelines-v3.md, "Whose turn it is: the nine states of an open task").

    waiting_on(conn, task_id) -> list[Wait]

One read. Every screen, every bot, the never-stuck check and Health call it; none of them works a task's state out for
itself. The list is in headline order, so the first entry is what a row shows and the task page lists the rest. It is
never empty for an open task, and empty for one that is Done or Closed. It reads tables only: no GitHub, no Jev, no
computer.

The nine states, in headline order:

    snoozed   someone set the task aside
    person    a requirement names them, a bot asked them, the task is theirs, or the ladder reached them
    paused    it is a bot's turn and the bot cannot run
    bot       a requirement is not met and a bot has it
    jev       Jev is checking a page
    github    checks are running, the PR is queued, or it merged and waits for a release
    tasks     tasks it filed are still open
    room      everything is met and the next stage is full
    by_hand   it sits in a stage that tasks leave by hand
"""

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from . import hubdb_core
from . import task_engine as E
from . import task_requirements as R
from . import task_wakes

if TYPE_CHECKING:
    import sqlite3

Row = dict[str, Any]

# hubdb_core is untyped: its actor helpers get their types here.
_core: Any = hubdb_core
actor_id: Callable[[object], str] = _core.actor_id
human_actor: Callable[[object], str] = _core.human_actor
is_bot: Callable[[object], bool] = _core.is_bot
is_human: Callable[[object], bool] = _core.is_human

SNOOZED, PERSON, PAUSED, BOT, JEV, GITHUB, TASKS, ROOM, BY_HAND = STATES = (
    "snoozed",
    "person",
    "paused",
    "bot",
    "jev",
    "github",
    "tasks",
    "room",
    "by_hand",
)
_ORDER = {state: at for at, state in enumerate(STATES)}


@dataclass(frozen=True)
class Wait:
    """One thing a task waits on. `who` is an actor for `person`, `paused` and `bot`, a task id for `tasks`, the stage
    id for `room` and `by_hand`, whoever snoozed it for `snoozed`, and None for `jev` and `github`. `line` is one
    sentence. `until` is a snooze's end, where it has one."""

    state: str
    who: str | None = None
    line: str = ""
    requirement_id: str | None = None
    since: str | None = None
    until: str | None = None


def person_for(conn: "sqlite3.Connection", row: Row) -> str | None:
    """The person a task falls to when no bot can take it further: the one the ladder already chose, else the
    pipeline's owner or manager where that is a person, else the owner of its manager bot, else the owner of the bot
    that has the task, else whoever asked for it, else the company owner. None only when the company has no people."""
    visit: Row = row.get("visit") or {}
    pipe: Row = row.get("pipeline") or {}
    for found in (visit.get("person_id"), pipe.get("owner_id"), pipe.get("manager_id")):
        if is_human(found):
            return found
    for bot in (pipe.get("manager_id"), row.get("owner_id")):
        if is_bot(bot):
            owner = conn.execute("SELECT owner_id FROM bot_config WHERE bot=?", (actor_id(bot),)).fetchone()
            if owner and owner[0]:
                return owner[0] if is_human(owner[0]) else human_actor(owner[0])
    if is_human(row.get("requester_id")):
        return row["requester_id"]
    from . import pipeline_rights

    return pipeline_rights.company_owner(conn) or None


def holder(conn: "sqlite3.Connection", row: Row) -> str | None:
    """Who has the task's own work now, as an actor: the owner, or whoever the ladder handed it to (the manager bot,
    then a person). A task with no owner is its pipeline manager's to hand out. Never None while the company has a
    person."""
    visit: Row = row.get("visit") or {}
    level = visit.get("level") or "owner"
    pipe: Row = row.get("pipeline") or {}
    manager = pipe.get("manager_id")
    if level == "person":
        return person_for(conn, row)
    if level == "manager" or not row.get("owner_id"):
        return manager if is_bot(manager) or is_human(manager) else person_for(conn, row)
    return row["owner_id"]


def _turn(
    conn: "sqlite3.Connection",
    row: Row,
    who: str | None,
    line: str,
    requirement_id: str | None = None,
    since: str | None = None,
) -> Wait:
    """The wait for work `who` has: a person's turn, a bot's turn, or Paused when that bot cannot run."""
    if not who or is_human(who):
        return Wait(PERSON, who, line, requirement_id, since)
    cause = task_wakes.cannot_run(conn, who)
    if cause:
        return Wait(PAUSED, who, cause, requirement_id, since)
    return Wait(BOT, who, line, requirement_id, since)


def _of(conn: "sqlite3.Connection", row: Row) -> list[Wait]:
    stage = row["stage"]
    if stage["kind"] != "work":
        return []
    visit = row["visit"]
    entered = visit["entered_at"]
    out: list[Wait] = []
    snooze = row.get("snooze")
    if snooze:
        out.append(
            Wait(SNOOZED, snooze["by_id"], snooze.get("why") or "", None, snooze["created_at"], snooze["until_at"])
        )
    by_hand = bool(stage.get("by_hand"))
    has: str | None = None
    unmet = [s for s in R.standings(conn, row) if not s.met]
    for standing in unmet:
        requirement = standing.requirement
        since = requirement["created_at"] if requirement.get("visit_id") else entered
        line = standing.line or standing.name
        if standing.state == R.WAITING and requirement["kind"] == "linked_done":
            out.append(Wait(TASKS, standing.on, line, requirement["id"], since))
        elif standing.state == R.WAITING and standing.on in (R.JEV, R.GITHUB):
            out.append(Wait(JEV if standing.on == R.JEV else GITHUB, None, line, requirement["id"], since))
        elif standing.state == R.WAITING:
            out.append(Wait(PERSON, standing.on or person_for(conn, row), line, requirement["id"], since))
        elif not by_hand:
            # Not met: whoever has the task's work has this too. In a by-hand stage nobody is chased for it.
            has = has or holder(conn, row)
            out.append(_turn(conn, row, has, line, requirement["id"], since))
    if by_hand:
        out.append(Wait(BY_HAND, stage["id"], f"Leaves {stage['name']} by hand", None, entered))
    elif not unmet:
        # Everything is met. It waits for a slot in the next stage; with room, the engine takes it on its next pass.
        ahead = E.next_stage(conn, row)
        full = bool(ahead) and not E.has_room(conn, ahead)
        name = ahead["name"] if ahead else "the next stage"
        out.append(
            Wait(
                ROOM,
                ahead["id"] if ahead else None,
                f"No room in {name}" if full else f"Moving to {name}",
                None,
                entered,
            )
        )
    return sorted(out, key=lambda wait: _ORDER[wait.state])


def waiting_on(conn: "sqlite3.Connection", task_id: str) -> list[Wait]:
    """What the task waits on, headline first. Never empty for an open task; empty for a Done or Closed one, and for a
    task that is not on the engine."""
    row = E.task(conn, task_id)
    return _of(conn, row) if row else []


def waiting_on_many(conn: "sqlite3.Connection", task_ids: Iterable[str]) -> dict[str, list[Wait]]:
    """`waiting_on` for each task of a list, by task id."""
    return {task_id: waiting_on(conn, task_id) for task_id in task_ids}


def headline(conn: "sqlite3.Connection", task_id: str) -> Wait | None:
    """The first wait: what a row shows. None for an ended task."""
    waits = waiting_on(conn, task_id)
    return waits[0] if waits else None


def all_met(waits: list[Wait]) -> bool:
    """Whether a task's waits say it is ready to move on: nothing is missing and it only waits for a slot."""
    return bool(waits) and all(wait.state == ROOM for wait in waits)


def needs(conn: "sqlite3.Connection", person: str) -> list[Row]:
    """Needs you, for one person: every wait on an open task that is theirs to act on, oldest first. A read: nothing is
    stored, so nothing can fall out of step with the tasks. A snoozed task is set aside and is not listed."""
    out: list[Row] = []
    rows = conn.execute(
        "SELECT t.id FROM tasks t WHERE t.pipeline_id IS NOT NULL AND t.status='open' "
        "AND NOT EXISTS (SELECT 1 FROM snoozes s WHERE s.task_id = t.id AND s.ended_at IS NULL) ORDER BY t.id"
    ).fetchall()
    for (task_id,) in rows:
        row = E.task(conn, task_id)
        if row is None:  # the query lists only tasks on the engine
            raise LookupError(f"task {task_id} is not on the engine")
        for wait in _of(conn, row):
            if wait.state == PERSON and wait.who == person:
                out.append(
                    {
                        "task_id": task_id,
                        "number": row.get("number"),
                        "title": row.get("title"),
                        "pipeline_id": row["pipeline_id"],
                        "stage_id": row["stage_id"],
                        "requirement_id": wait.requirement_id,
                        "line": wait.line,
                        "since": wait.since,
                    }
                )
    return sorted(out, key=lambda item: (item["since"] or "", item["task_id"]))

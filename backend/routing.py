# pyright: strict
"""Routing: who takes work that needs a person (docs/dev/pipelines-v3.md "Routing").

A person's availability is `on`, `async` or `off` (`humans.availability`). They set their own, an admin sets anyone's;
`availability_until` ends a set value, and past it the derived value applies again, computed at read with no timer.
Nothing set (or expired) is derived from their working hours (Calendar settings: `work_starts`, `work_ends`,
`work_days`, `time_zone`): on inside them, async outside; someone with no working hours saved is on.

`route` runs once, when work needs a person (a task filed with `route`), in the caller's transaction. It gives the task
to the first of:

1. the recent helper: the last person who worked the task, or another task of its relationship, in the last
   RECENT_HOURS (its history or its room), who is on;
2. the relationship's owner (`entities.owner_*` through `tasks.entity_id`), else the task's own person: on, or async
   when the task is not urgent;
3. anyone on in that owner's team, fewest open tasks first;
4. the inbox fallback: the pipeline's manager, else its owner, when a person, else the company's default person. So
   there is always a named owner.

Each candidate but the last must be an active person who may read the task. It sets both the task's owner and its
assignee. A task is urgent when it carries the label `urgent`.

Routed work with no pipeline goes on the company's Support pipeline (`support_pipeline`), made the first time it is
needed: one work stage held by the task's assignee and not by hand, so the stage timers (Late, stage rules) and the
pipeline manager's tells own every overdue wait. Its default stage rule tells the manager about an urgent task quiet
for an hour at once, not in the hourly batch (backend/stage_rule_timers.py).
"""

from __future__ import annotations

import json
from datetime import datetime, time, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

Row = dict[str, Any]

ON, ASYNC, OFF = "on", "async", "off"
STATES = (ON, ASYNC, OFF)
RECENT_HOURS = 48
URGENT = "urgent"
SUPPORT = "support"  # the Support pipeline's preset
SUPPORT_STAGE = "support-open"
DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


def _h() -> Any:
    from . import hubdb

    return hubdb


def _engine() -> Any:
    from . import task_engine

    return task_engine


# ----------------------------------------------------------------------------- availability
def now() -> datetime:
    return datetime.now(timezone.utc)


def _zone(name: object) -> ZoneInfo:
    try:
        return ZoneInfo(str(name)) if name else ZoneInfo("UTC")
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo("UTC")


def from_hours(person: Row, at: datetime) -> str:
    """On inside the person's working hours, async outside; on when they saved none."""
    if not (person.get("work_starts") or person.get("work_ends") or person.get("work_days")):
        return ON
    from .calendar_api import WORK_DAYS, WORK_ENDS, WORK_STARTS

    local = at.astimezone(_zone(person.get("time_zone")))
    starts = time.fromisoformat(str(person.get("work_starts") or WORK_STARTS))
    ends = time.fromisoformat(str(person.get("work_ends") or WORK_ENDS))
    days = set(str(person.get("work_days") or WORK_DAYS).split(","))
    return ON if DAYS[local.weekday()] in days and starts <= local.time() < ends else ASYNC


def _set_now(person: Row, at: datetime) -> str:
    """The value the person (or an admin) set, while it lasts; "" when none is set or it ended."""
    chosen = str(person.get("availability") or "")
    if chosen not in STATES:
        return ""
    until = _h().parse_ts(person.get("availability_until"))
    return chosen if until is None or until > at else ""


def effective(person: Row, at: datetime) -> str:
    """on, async or off now: the set value while it lasts, else from working hours."""
    return _set_now(person, at) or from_hours(person, at)


def view(person: Row, at: datetime) -> Row:
    """What the API answers for one person."""
    chosen = _set_now(person, at)
    return {
        "person": "human:" + str(person["id"]),
        "availability": chosen or from_hours(person, at),
        "set": chosen or None,
        "until": person.get("availability_until") if chosen else None,
        "from": "set" if chosen else "working_hours",
    }


def person_row(conn: Any, pid: str) -> Row | None:
    found = conn.execute(
        "SELECT id, time_zone, work_starts, work_ends, work_days, availability, availability_until "
        "FROM humans WHERE id=?",
        (pid,),
    ).fetchone()
    return dict(found) if found else None


def set_availability(conn: Any, by: str, pid: str, value: str | None, until: str | None, at: datetime) -> Row:
    """Store a person's availability (None clears it: working hours decide again). `until` is an ISO time with a zone
    in the future, and needs a value. Returns `view`."""
    from .store import Problem

    H = _h()
    if value is not None and value not in STATES:
        raise Problem("validation", "availability is on, async or off", 422)
    if until is not None:
        end = H.parse_ts(until)
        if value is None:
            raise Problem("validation", "until goes with an availability", 422)
        if end is None or end.tzinfo is None:
            raise Problem("validation", "until is an ISO-8601 time with a zone", 422)
        if end <= at:
            raise Problem("validation", "until is in the future", 422)
        until = end.astimezone(ZoneInfo("UTC")).strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"
    before = person_row(conn, pid)
    if before is None:
        raise Problem("not_found", "Person not found", 404)
    conn.execute("UPDATE humans SET availability=?, availability_until=? WHERE id=?", (value or "", until, pid))
    H.event(conn, by, "person.availability", "human:" + pid, {"availability": value, "until": until})
    after = person_row(conn, pid)
    assert after is not None  # the row updated above
    return view(after, at)


# ----------------------------------------------------------------------------- route
def _labels(row: Row) -> set[str]:
    raw: Any = row.get("labels_json") if row.get("labels_json") is not None else row.get("labels")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            raw = []
    items: list[Any] = list(raw) if isinstance(raw, list) else []  # type: ignore[arg-type]
    return {str(x).lower() for x in items}


def urgent(row: Row) -> bool:
    return URGENT in _labels(row)


def _columns(conn: Any, table: str) -> set[str]:
    return {str(r[1]) for r in conn.execute(f"PRAGMA table_info({table})")}


def relationship(conn: Any, row: Row) -> tuple[str | None, list[str]]:
    """The task's relationship, read defensively (tasks.entity_id may not exist yet): its owner as `human:<id>` when a
    person, and the ids of its other tasks. (None, []) for a task with none."""
    if "entity_id" not in _columns(conn, "tasks"):
        return None, []
    found = conn.execute("SELECT entity_id FROM tasks WHERE id=?", (row["id"],)).fetchone()
    entity = str(found[0] or "") if found else ""
    if not entity:
        return None, []
    tasks = [str(r[0]) for r in conn.execute("SELECT id FROM tasks WHERE entity_id=? AND id<>?", (entity, row["id"]))]
    owner = conn.execute("SELECT owner_type, owner_id FROM entities WHERE id=?", (entity,)).fetchone()
    person = None
    if owner and str(owner[0] or "") in ("human", "person") and owner[1]:
        person = "human:" + str(owner[1]).removeprefix("human:")
    return person, tasks


def _recent(conn: Any, row: Row, related: list[str], since: str) -> list[str]:
    """People who worked the task or its relationship's other tasks since `since`, newest first, not counting the
    task's own requester (who asked for it is not its helper)."""
    ids = [row["id"], *related]
    marks = ",".join("?" * len(ids))
    rooms = [str(r[0]) for r in conn.execute(f"SELECT conversation_id FROM tasks WHERE id IN ({marks})", ids) if r[0]]
    rows: list[Any] = list(
        conn.execute(
            f"SELECT actor_id, created_at FROM history_events WHERE subject_type='task' AND subject_id IN ({marks}) "
            "AND actor_id LIKE 'human:%' AND created_at >= ?",
            (*ids, since),
        )
    )
    if rooms:
        rows += list(
            conn.execute(
                f"SELECT sender_id, created_at FROM messages WHERE conversation_id IN ({','.join('?' * len(rooms))}) "
                "AND sender_id LIKE 'human:%' AND deleted_at IS NULL AND created_at >= ?",
                (*rooms, since),
            )
        )
    out: list[str] = []
    for actor, _ in sorted(rows, key=lambda r: str(r[1]), reverse=True):
        if actor != row.get("requester_id") and actor not in out:
            out.append(str(actor))
    return out


def _team(conn: Any, actor: str) -> list[str]:
    """The other people in `actor`'s team (their roster group), as `human:<id>`."""
    found = conn.execute("SELECT value FROM registry_metadata WHERE key='people'").fetchone()
    try:
        roster: Any = json.loads(found[0]) if found else {}
    except ValueError:
        roster = {}
    people: list[Row] = [p for p in (roster.get("people") or []) if isinstance(p, dict)]  # type: ignore[union-attr]
    pid = actor.removeprefix("human:")
    team = next((str(p.get("team") or "") for p in people if p.get("id") == pid), "")
    if not team:
        return []
    return ["human:" + str(p["id"]) for p in people if str(p.get("team") or "") == team and p.get("id") != pid]


def _open_load(conn: Any, actor: str) -> int:
    return int(conn.execute("SELECT COUNT(*) FROM tasks WHERE owner_id=? AND status='open'", (actor,)).fetchone()[0])


def fallback(conn: Any, row: Row) -> str | None:
    """The inbox fallback for a task: its pipeline's manager, else its owner, when an active person who may read the
    task; else the company's default person (who becomes its reader as its owner)."""
    H, E = _h(), _engine()
    pipe: Row = E.pipeline(conn, str(row.get("pipeline_id") or "")) or {}
    for actor in (pipe.get("manager_id"), pipe.get("owner_id")):
        if actor and H.is_human(actor) and _may_take(conn, row, str(actor)):
            return str(actor)
    default = H.default_human(conn)
    return "human:" + default if default else None


def _may_take(conn: Any, row: Row, actor: str) -> bool:
    H, E = _h(), _engine()
    if not E.R.active_person(conn, actor):
        return False
    return not H.task_private(conn, row) or bool(H.task_private_readable(conn, actor, row))


def choose(conn: Any, row: Row, at: datetime) -> tuple[str | None, str]:
    """Who `route` gives the task to, and the step that chose them (recent, owner, team, fallback)."""
    H = _h()
    people: dict[str, Row] = {}

    def state(actor: str) -> str:
        pid = actor.removeprefix("human:")
        if pid not in people:
            people[pid] = person_row(conn, pid) or {"id": pid}
        return effective(people[pid], at)

    def eligible(actor: str | None, allow_async: bool) -> bool:
        if not actor or not H.is_human(actor) or not _may_take(conn, row, actor):
            return False
        now = state(actor)
        return now == ON or (allow_async and now == ASYNC)

    owner, related = relationship(conn, row)
    if not owner:
        mine = [a for a in (row.get("assignee_id"), row.get("owner_id")) if a and H.is_human(a)]
        owner = str(mine[0]) if mine else None
    since = (at.astimezone(ZoneInfo("UTC")) - timedelta(hours=RECENT_HOURS)).strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"
    for helper in _recent(conn, row, related, since):
        if eligible(helper, False):
            return helper, "recent"
    if eligible(owner, not urgent(row)):
        return owner, "owner"
    if owner:
        team = [a for a in _team(conn, owner) if eligible(a, False)]
        if team:
            return min(team, key=lambda a: (_open_load(conn, a), a)), "team"
    return fallback(conn, row), "fallback"


WHY = {
    "recent": "Routed to who worked on it in the last two days",
    "owner": "Routed to its owner",
    "team": "Routed to someone on in its owner's team",
    "fallback": "Routed to the inbox fallback: nobody else eligible was on",
}


def route(conn: Any, task_id: str, at: datetime | None = None) -> str | None:
    """Give an open task to a person, in the caller's transaction: owner and assignee both. Returns who, or None when
    the company has nobody to give it to (no person at all)."""
    H, E = _h(), _engine()
    at = at or now()
    row: Row | None = E.task(conn, task_id)
    if not row or row.get("status") != "open":
        return None
    person, step = choose(conn, row, at)
    if not person:
        return None
    E.reassign(conn, task_id, person, E.SYSTEM, WHY[step])
    held: Row = E.task(conn, task_id) or {}
    if held.get("assignee_id") != person:
        E._write(  # pyright: ignore[reportPrivateUsage]
            conn, task_id, assignee_id=person, version=int(held.get("version") or 0) + 1, updated_at=H.now()
        )
        E._event(conn, task_id, E.SYSTEM, "assignee_id", held.get("assignee_id"), person)  # pyright: ignore[reportPrivateUsage]
    H.event(conn, E.SYSTEM, "task.routed", task_id, {"to": person, "step": step, "urgent": urgent(row)})
    return person


# ----------------------------------------------------------------------------- the Support pipeline
SUPPORT_RECORD: Row = {
    "name": "Support",
    "goal": "Work routed to a person: answered, settled or handed on.",
    "prefix": None,
    "owner_id": None,
    "private": False,
    "factory_id": None,
    "preset": SUPPORT,
    "stages": [
        {
            "id": SUPPORT_STAGE,
            "name": "Open",
            "purpose": "Answer it, settle it or hand it on. Say done when it is settled.",
            "owner": "assignee",
            "by_hand": False,
            "requests_move_on": False,
            "task_limit": None,
            "expected_minutes": 1440,
            "expected_bots_only": False,
            "on_enter": {},
            "requirements": [{"kind": "owner_done"}],
        }
    ],
    "done": {"id": "support-done"},
    "closed": {"id": "support-closed"},
}


def support_pipeline(conn: Any) -> str:
    """The company's Support pipeline id, made the first time routed work needs it. A retired one is not made again:
    routed work then goes to General, as any task with no pipeline."""
    import copy

    from . import pipeline_settings as PS
    from . import stage_rules as _stage_rules

    found = conn.execute(
        "SELECT id, retired_at FROM pipelines WHERE preset=? ORDER BY created_at, id LIMIT 1", (SUPPORT,)
    ).fetchone()
    if found:
        return str(found[0]) if not found[1] else "general"
    record: Row = copy.deepcopy(SUPPORT_RECORD)
    taken = {str(r[0]) for r in conn.execute("SELECT id FROM pipelines")}
    record["id"] = next(pid for pid in ("support", *(f"support-{n}" for n in range(2, 100))) if pid not in taken)
    if conn.execute(
        "SELECT 1 FROM pipelines WHERE name=? COLLATE NOCASE AND retired_at IS NULL", (record["name"],)
    ).fetchone():
        record["name"] = "Routed support"
    record["manager_id"] = PS._general_manager(conn)  # pyright: ignore[reportPrivateUsage]
    used: set[str] = set()
    for stage in (*record["stages"], record["done"], record["closed"]):
        stage["id"] = PS._free_stage_id(conn, stage["id"], used)  # pyright: ignore[reportPrivateUsage]
    saved: Row = PS.save(conn, _engine().SYSTEM, record)
    stage_rules: Any = _stage_rules
    stage_rules.ensure_defaults(conn)  # its urgent rule
    return str(saved["id"])

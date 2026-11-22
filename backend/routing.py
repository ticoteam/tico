# pyright: strict
"""Routing: who takes work that needs a person (docs/dev/pipelines-v3.md "Routing").

A person's availability is `on`, `async` or `off` (`humans.availability`). They set their own, an admin sets anyone's;
`availability_until` ends a set value, and past it the derived value applies again, computed at read with no timer.
Nothing set (or ended) comes from their effective Calendar working hours (calendar_api.work_settings: what they saved,
else Calendar's weekday defaults, in their saved zone else UTC; no Google call): on inside them, async outside.

`choose` runs once, when work needs a person: `POST /api/v2/tasks` with `route: true` picks the person before the task
is made, and the task is then filed with them as owner and assignee, so every send check, stage entry and create hook
runs once against the real recipient. The first of:

1. the recent helper: the newest person who worked another task of the same relationship (`tasks.entity_id`) in the
   last RECENT_HOURS (its history, or comments on it), may read that task, and is on;
2. the relationship's owner (`entities.owner_*`) when a person: on, or async when the work is not urgent;
3. anyone on in that owner's roster team, fewest open tasks first;
4. the inbox fallback: the pipeline's manager, else its owner, when a person, else the company's person
   (waiting_on.company_person).

Every candidate, the fallback included, must be an active person who may read the task: assigning never widens who
reads private work. When no fallback may, the create is refused (422). Work is urgent when it carries the tag
`urgent` (task_tags; `urgent`).

Routed work with no pipeline goes on the company's Support pipeline (`support_pipeline`), made the first time it is
needed: one work stage held by the task's assignee and not by hand. Two default stage rules give its overdue waits to
the pipeline's manager: Support waits (24 h quiet) and Urgent support waits (1 h quiet on an urgent task, told at once
rather than in the hourly batch, backend/stage_rule_timers.py).
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, cast

Row = dict[str, Any]

ON, ASYNC, OFF = "on", "async", "off"
STATES = (ON, ASYNC, OFF)
RECENT_HOURS = 48
RELATED_MOST = 20  # the relationship's tasks a recent helper is looked for in, newest first
URGENT = "urgent"
SUPPORT = "support"  # the Support pipeline's preset
# Whether a task carries the tag `urgent` (one index probe on task_tags); `%s` is the task id expression.
URGENT_SQL = "EXISTS (SELECT 1 FROM task_tags tt JOIN tags g ON g.id = tt.tag_id WHERE tt.task_id = %s AND lower(g.key) = 'urgent')"


def _h() -> Any:
    from . import hubdb

    return hubdb


def now() -> datetime:
    return datetime.now(timezone.utc)


def _stamp(at: datetime) -> str:
    return at.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"


# ----------------------------------------------------------------------------- availability
def from_hours(person: Row, at: datetime) -> str:
    """On inside the person's effective working hours, async outside."""
    from . import calendar_api

    return ON if calendar_api.working_at(person, at) else ASYNC


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


_PERSON = (
    "SELECT id, time_zone, work_starts, work_ends, work_days, availability, availability_until FROM humans WHERE id IN "
)


def person_row(conn: Any, pid: str) -> Row | None:
    found = conn.execute(_PERSON + "(?)", (pid,)).fetchone()
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
        until = _stamp(end)
    if person_row(conn, pid) is None:
        raise Problem("not_found", "Person not found", 404)
    conn.execute("UPDATE humans SET availability=?, availability_until=? WHERE id=?", (value or "", until, pid))
    H.event(conn, by, "person.availability", "human:" + pid, {"availability": value, "until": until})
    after = person_row(conn, pid)
    assert after is not None  # the row updated above
    return view(after, at)


# ----------------------------------------------------------------------------- choose
@dataclass(frozen=True)
class Work:
    """What routing knows about work before it is filed."""

    pipeline_id: str | None  # None: the Support pipeline, made only once the create files it
    entity_id: str
    requester: str
    urgent: bool
    may_read: Callable[[str], bool]  # whether a person may read the task once it is filed, not as its owner


def urgent(conn: Any, task_id: str) -> bool:
    """Whether the task carries the tag `urgent`."""
    return bool(conn.execute("SELECT " + URGENT_SQL % "?", (task_id,)).fetchone()[0])


def _roster(conn: Any) -> list[Row]:
    found = conn.execute("SELECT value FROM registry_metadata WHERE key='people'").fetchone()
    try:
        roster: Any = json.loads(found[0]) if found else {}
    except ValueError:
        roster = {}
    listed: Any = cast(Row, roster).get("people") if isinstance(roster, dict) else None
    return [cast(Row, p) for p in cast(list[Any], listed) if isinstance(p, dict)] if isinstance(listed, list) else []


def _owner(conn: Any, entity_id: str) -> str | None:
    """The relationship's owner as `human:<id>` when a person."""
    if not entity_id:
        return None
    found = conn.execute("SELECT owner_type, owner_id FROM entities WHERE id=?", (entity_id,)).fetchone()
    if not found or found[0] != "human" or not found[1]:
        return None
    return "human:" + str(found[1])


def _recent(conn: Any, work: Work, since: str) -> list[tuple[str, str]]:
    """(person, task id) for people who worked the relationship's other recent tasks since `since`, newest first:
    their history or their comments on it. None for work with no relationship."""
    if not work.entity_id:
        return []
    ids = [
        str(r[0])
        for r in conn.execute(
            "SELECT id FROM tasks WHERE entity_id=? AND updated_at >= ? ORDER BY updated_at DESC LIMIT ?",
            (work.entity_id, since, RELATED_MOST),
        )
    ]
    if not ids:
        return []
    marks = ",".join("?" * len(ids))
    rows = conn.execute(
        f"SELECT actor_id, subject_id, created_at FROM history_events WHERE subject_type='task' "
        f"AND subject_id IN ({marks}) AND actor_id LIKE 'human:%' AND created_at >= ? "
        f"UNION ALL SELECT m.sender_id, json_extract(m.refs, '$.task'), m.created_at FROM messages m "
        f"WHERE m.conversation_id IN (SELECT conversation_id FROM tasks WHERE id IN ({marks})) "
        f"AND json_extract(m.refs, '$.task') IN ({marks}) AND m.sender_id LIKE 'human:%' AND m.deleted_at IS NULL "
        f"AND m.created_at >= ? ORDER BY 3 DESC LIMIT 50",
        (*ids, since, *ids, *ids, since),
    ).fetchall()
    return [(str(r[0]), str(r[1])) for r in rows if r[0] != work.requester]


def choose(conn: Any, work: Work, at: datetime) -> tuple[str, str]:
    """Who gets the work, and the step that chose them (recent, owner, team, fallback). Refuses (422) when nobody may
    take it: no active person who may read it, not even the fallback."""
    from . import task_engine, task_requirements, waiting_on
    from .store import Problem

    H = _h()
    active: frozenset[str] = task_requirements._active_ids(conn)  # pyright: ignore[reportPrivateUsage]
    states: dict[str, str] = {}

    def state(actor: str) -> str:
        if actor not in states:
            person = person_row(conn, actor.removeprefix("human:"))
            states[actor] = effective(person, at) if person else OFF
        return states[actor]

    def may_take(actor: str | None) -> bool:
        return (
            bool(actor) and H.is_human(actor) and str(actor).removeprefix("human:") in active and work.may_read(actor)
        )

    def eligible(actor: str | None, allow_async: bool) -> bool:
        if not actor or not may_take(actor):
            return False
        return state(actor) == ON or (allow_async and state(actor) == ASYNC)

    since = _stamp(at - timedelta(hours=RECENT_HOURS))
    for helper, task_id in _recent(conn, work, since):
        related = H.task(conn, task_id)
        if eligible(helper, False) and (
            not H.task_private(conn, related) or H.task_private_readable(conn, helper, related)
        ):
            return helper, "recent"
    owner = _owner(conn, work.entity_id)
    if eligible(owner, not work.urgent):
        assert owner is not None
        return owner, "owner"
    if owner:
        people = _roster(conn)
        pid = owner.removeprefix("human:")
        team = next((str(p.get("team") or "") for p in people if p.get("id") == pid), "")
        mates = [
            "human:" + str(p["id"])
            for p in people
            if team
            and str(p.get("team") or "") == team
            and p.get("id") != pid
            and eligible("human:" + str(p["id"]), False)
        ]
        if mates:
            load = dict(
                conn.execute(
                    f"SELECT owner_id, COUNT(*) FROM tasks WHERE status='open' AND owner_id IN ({','.join('?' * len(mates))}) "
                    "GROUP BY owner_id",
                    mates,
                ).fetchall()
            )
            return min(mates, key=lambda a: (int(load.get(a) or 0), a)), "team"
    pipe: Row = (task_engine.pipeline(conn, work.pipeline_id) or {}) if work.pipeline_id else {}
    for actor in (pipe.get("manager_id"), pipe.get("owner_id"), waiting_on.company_person(conn)):
        if may_take(actor):
            return str(actor), "fallback"
    raise Problem(
        "route",
        "Nobody can take this routed task: no one on the team who may read it is here. Share it with a person, or "
        "name its owner",
        422,
    )


def person_for_create(conn: Any, who: Any, body: Any, parent_id: str | None, actual_bot: str) -> tuple[str, str]:
    """(person, entity id) for a create with `route: true`, before anything is written (app.task_create and its dry
    run). Its pipeline's first stage must be the assignee's (the Support pipeline's is). A private task goes only to a
    person who reads it without owning it: its filer, its readers, readers of its private parent, or its pipeline's
    manager."""
    from . import task_engine
    from . import task_privacy as _task_privacy
    from .entities import store as ES
    from .store import Problem

    H = _h()
    if body.owner:
        raise Problem("route", "A routed task names no owner: Tico picks the person", 422)
    if body.live_meeting_id:
        raise Problem("route", "A meeting's task names its owner: route work that is not from a meeting", 422)
    pipeline_id: str | None = body.pipeline or support_id(conn)
    refused = first_stage_refusal(conn, pipeline_id) if pipeline_id else ""
    if refused:
        raise Problem("route", refused, 422)
    entity_id = str(ES.target(conn, who.actor, body.entity)) if body.entity else ""
    parent: Row | None = H.task(conn, parent_id) if parent_id else None
    pipe: Row = (task_engine.pipeline(conn, pipeline_id) or {}) if pipeline_id else {}
    task_privacy: Any = _task_privacy  # untyped module
    private = bool(task_privacy.new_task(conn, who, body, None, parent, actual_bot)[2] or pipe.get("private"))
    readers = {str(r) for r in body.readers or ()}
    filer = {str(body.for_actor or ""), *([str(who.actor)] if H.is_human(who.actor) else [])}

    def may_read(actor: str) -> bool:
        return (
            not private
            or actor in filer
            or actor == pipe.get("manager_id")
            or bool(readers & {str(n) for n in H.reader_names(conn, actor)})
            or bool(parent and H.task_private_readable(conn, actor, parent))
        )

    work = Work(
        pipeline_id=pipeline_id,
        entity_id=entity_id,
        requester=str(who.actor),
        urgent=URGENT in H._labels(body.labels),  # pyright: ignore[reportPrivateUsage]
        may_read=may_read,
    )
    return choose(conn, work, now())[0], entity_id


def first_stage_refusal(conn: Any, pipeline_id: str) -> str:
    """Why routed work cannot go in a pipeline ("" when it can): its first stage must be held by the task's assignee."""
    from . import task_engine

    pipe = task_engine.pipeline(conn, pipeline_id)
    work = task_engine.main_work(task_engine.stages(conn, pipeline_id)) if pipe and not pipe.get("retired_at") else []
    if not pipe or not work:
        return f"No pipeline {pipeline_id}"
    if work[0].get("owner") != "assignee":
        return (
            f"A routed task goes in a pipeline whose first stage the task's assignee holds; {pipe['name']}'s does not"
        )
    return ""


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
            "id": "support-open",
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


def support_id(conn: Any) -> str | None:
    """The company's Support pipeline when it has one: its id, General when it retired it, None before it is made."""
    found = conn.execute(
        "SELECT id, retired_at FROM pipelines WHERE preset=? ORDER BY created_at, id LIMIT 1", (SUPPORT,)
    ).fetchone()
    if not found:
        return None
    return str(found[0]) if not found[1] else "general"


def support_pipeline(conn: Any) -> str:
    """The company's Support pipeline id, made the first time routed work is filed. A retired one is not made again:
    routed work then goes to General."""
    import copy

    from . import pipeline_settings as PS
    from . import stage_rules as _stage_rules
    from . import task_engine

    have = support_id(conn)
    if have:
        return have
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
    saved: Row = PS.save(conn, task_engine.SYSTEM, record)
    stage_rules: Any = _stage_rules
    stage_rules.ensure_defaults(conn)  # its two timers
    return str(saved["id"])

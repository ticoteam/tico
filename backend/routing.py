# pyright: strict
"""Routing: who takes work that needs a person (docs/dev/pipelines-v3.md "Routing").

A person's availability is `on`, `async` or `off` (`humans.availability`). They set their own, an admin sets anyone's;
`availability_until` ends a set value, and past it the derived value applies again, computed at read with no timer.
Nothing set (or ended) comes from their effective Calendar working hours (calendar_api.work_settings: what they saved,
else Calendar's weekday defaults) in Calendar's zone for them (calendar_api.effective_zone): on inside them, async
outside. No Google call is made in a write: a Google zone not already held from the last hour is unknown, and the
person is on.

`choose` runs once, when work needs a person: `POST /api/v2/tasks` with `route: true` picks the person before the task
is made, and the task is then filed with them as owner and assignee, so every send check, stage entry and create hook
runs once against the real recipient. The first of:

1. the recent helper: the newest person who worked another task of the same relationship (`tasks.entity_id`) in the
   last RECENT_HOURS (its history, or comments on it; company-visible tasks the filer reads only), and is on;
2. the relationship's owner (`entities.owner_*`) when a person: on, or async when the work is not urgent;
3. anyone on in that owner's roster team, the first by id;
4. the inbox fallback: the pipeline's manager, else its owner, when a person, else the company's person
   (waiting_on.company_person).

Every candidate, the fallback included, must be an active person. Private work is never routed (`prepare` refuses
it): assigning never decides who reads private work. Work is urgent when it carries the tag `urgent` (task_tags).

Routed work goes only on the company's Support pipeline (`support_pipeline`), made the first time it is needed: one
work stage held by the task's assignee and not by hand, managed by the General Manager bot, else the company's person.
Two default stage rules give its overdue waits to that manager: Support waits (24 h quiet) and Urgent support waits (1 h quiet on an urgent task, told at once
rather than in the hourly batch, backend/stage_rule_timers.py).
"""

from __future__ import annotations

import json
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
def from_hours(conn: Any, person: Row, at: datetime) -> str:
    """On inside the person's effective working hours, in Calendar's zone for them (calendar_api.effective_zone, the
    function Calendar reads); async outside. A Google zone Calendar would have to ask Google for now is unknown: then
    the person is on."""
    from . import calendar_api

    zone = calendar_api.person_zone(conn, str(person["id"]))
    if zone is None:
        return ON
    return ON if calendar_api.working_at(person, at, zone) else ASYNC


def _set_now(person: Row, at: datetime) -> str:
    """The value the person (or an admin) set, while it lasts; "" when none is set or it ended."""
    chosen = str(person.get("availability") or "")
    if chosen not in STATES:
        return ""
    until = _h().parse_ts(person.get("availability_until"))
    return chosen if until is None or until > at else ""


def effective(conn: Any, person: Row, at: datetime) -> str:
    """on, async or off now: the set value while it lasts, else from working hours."""
    return _set_now(person, at) or from_hours(conn, person, at)


def view(conn: Any, person: Row, at: datetime) -> Row:
    """What the API answers for one person."""
    chosen = _set_now(person, at)
    return {
        "person": "human:" + str(person["id"]),
        "availability": chosen or from_hours(conn, person, at),
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
    return view(conn, after, at)


# ----------------------------------------------------------------------------- choose
@dataclass(frozen=True)
class Work:
    """What routing knows about work before it is filed."""

    pipeline_id: str | None  # None: the Support pipeline, made only once the create files it
    entity_id: str
    requester: str
    urgent: bool
    visible_sql: str = "0"  # which tasks the filer reads (auth.task_sql): only those shape the pick


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
    """(person, task id) for people who worked the relationship's other recent company-visible tasks since `since`,
    newest first: their history or their comments on it. None for work with no relationship."""
    if not work.entity_id:
        return []
    # tasks_entity is partial (entity_id <> ''): the literal predicate lets SQLite use it. Private work never shapes
    # a company-visible assignment, nor work the filer cannot read: only company-visible tasks they read count.
    ids = [
        str(r[0])
        for r in conn.execute(
            "SELECT id FROM tasks WHERE entity_id <> '' AND entity_id = ? AND private = 0 "
            f"AND updated_at >= ? AND ({work.visible_sql}) ORDER BY updated_at DESC LIMIT ?",
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
    """Who gets the work, and the step that chose them (recent, owner, team, fallback). Every candidate is an active
    person; the work is company-visible (`prepare` refuses private work). Refuses (422) when nobody can sign in."""
    from . import task_engine, task_requirements, waiting_on
    from .store import Problem

    H = _h()
    active: frozenset[str] = task_requirements._active_ids(conn)  # pyright: ignore[reportPrivateUsage]
    states: dict[str, str] = {}

    def state(actor: str) -> str:
        if actor not in states:
            person = person_row(conn, actor.removeprefix("human:"))
            states[actor] = effective(conn, person, at) if person else OFF
        return states[actor]

    def may_take(actor: str | None) -> bool:
        return bool(actor) and H.is_human(actor) and str(actor).removeprefix("human:") in active

    def eligible(actor: str | None, allow_async: bool) -> bool:
        if not actor or not may_take(actor):
            return False
        return state(actor) == ON or (allow_async and state(actor) == ASYNC)

    since = _stamp(at - timedelta(hours=RECENT_HOURS))
    for helper, _ in _recent(conn, work, since):
        if eligible(helper, False):
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
            return min(mates), "team"  # the first by id: a stable order, no other work read
    pipe: Row = (task_engine.pipeline(conn, work.pipeline_id) or {}) if work.pipeline_id else {}
    for actor in (pipe.get("manager_id"), pipe.get("owner_id"), waiting_on.company_person(conn)):
        if may_take(actor):
            return str(actor), "fallback"
    raise Problem("route", "Nobody can take this routed task: no one on the team can sign in. Name its owner", 422)


PRIVATE_REFUSAL = "Private tasks aren't routed; assign someone who can see it"


SUPPORT_ONLY = "Routed work goes to Support: leave pipeline out, or name Support"


def prepare(
    conn: Any, who: Any, body: Any, parent_id: str | None, actual_bot: str, entity_id: str, visible_sql: str
) -> Work:
    """The checks a create with `route: true` makes before anything is written, and what routing then reads. The
    caller has resolved the entity (as the effective reader), read the parent (an unreadable one is its 404) and says
    which tasks the filer reads (`visible_sql`). Refused (422): an owner named, a meeting's task, any pipeline but
    Support (its timers own every routed wait), a Support retired, made private, or changed so its first stage is not
    the assignee's or is moved by hand, and private work (explicitly, under a private parent, in a private pipeline or by the filer's
    private default): routing never decides who reads private work."""
    from . import task_privacy as _task_privacy
    from .store import Problem

    H = _h()
    if body.owner:
        raise Problem("route", "A routed task names no owner: Tico picks the person", 422)
    if body.live_meeting_id:
        raise Problem("route", "A meeting's task names its owner: route work that is not from a meeting", 422)
    support, retired = _support(conn)
    if body.pipeline and body.pipeline != support:
        raise Problem("route", SUPPORT_ONLY, 422)
    if retired:
        raise Problem("route", "The Support pipeline is retired: routed work has nowhere to go. Name its owner", 422)
    refused = first_stage_refusal(conn, support) if support else ""
    if refused:
        raise Problem("route", refused, 422)
    if support and conn.execute("SELECT private FROM pipelines WHERE id=?", (support,)).fetchone()[0]:
        raise Problem("route", PRIVATE_REFUSAL, 422)  # a private Support makes every task in it private
    parent: Row | None = H.task(conn, parent_id) if parent_id else None
    task_privacy: Any = _task_privacy  # untyped module
    if parent and H.task_private(conn, parent):
        raise Problem("route", PRIVATE_REFUSAL, 422)  # its owner would read the private task above it
    if getattr(body, "from_private", False):  # older clients' name for `private: false` (app.task_create)
        body = body.model_copy(update={"private": False, "from_private": False})
    elif body.private is None and task_privacy.private_execution(conn, who):
        raise Problem("route", PRIVATE_REFUSAL, 422)  # a run on private work files private work
    if task_privacy.new_task(conn, who, body, None, parent, actual_bot)[2]:
        raise Problem("route", PRIVATE_REFUSAL, 422)
    return Work(
        pipeline_id=support,
        entity_id=entity_id,
        requester=str(who.actor),
        urgent=URGENT in H._labels(body.labels),  # pyright: ignore[reportPrivateUsage]
        visible_sql=visible_sql,
    )


def person_for_create(
    conn: Any, who: Any, body: Any, parent_id: str | None, actual_bot: str, entity_id: str, visible_sql: str
) -> str:
    """The person a create with `route: true` goes to (`prepare`, then `choose`), before anything is written."""
    work = prepare(conn, who, body, parent_id, actual_bot, entity_id, visible_sql)
    return choose(conn, work, now())[0]


def first_stage_refusal(conn: Any, pipeline_id: str) -> str:
    """Why routed work cannot go in a pipeline ("" when it can): its first stage must be held by the task's assignee."""
    from . import task_engine

    pipe = task_engine.pipeline(conn, pipeline_id)
    work = task_engine.main_work(task_engine.stages(conn, pipeline_id)) if pipe and not pipe.get("retired_at") else []
    if not pipe or not work:
        return f"No pipeline {pipeline_id}"
    if work[0].get("by_hand"):
        return f"A routed task goes in a pipeline whose first stage is timed; {pipe['name']}'s is moved by hand"
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


def _support(conn: Any) -> tuple[str | None, bool]:
    """(id, retired) of the company's Support pipeline; (None, False) before it is made."""
    found = conn.execute(
        "SELECT id, retired_at FROM pipelines WHERE preset=? ORDER BY created_at, id LIMIT 1", (SUPPORT,)
    ).fetchone()
    return (str(found[0]), bool(found[1])) if found else (None, False)


def support_id(conn: Any) -> str | None:
    """The company's live Support pipeline, or None (not made yet, or retired)."""
    found, retired = _support(conn)
    return None if retired else found


def support_pipeline(conn: Any) -> str:
    """The company's Support pipeline id, made the first time routed work is filed (a retired one is refused before,
    by `prepare`). Its manager is the General Manager bot, else the company's person, so its timers wake someone."""
    import copy

    from . import pipeline_settings as PS
    from . import stage_rules as _stage_rules
    from . import task_engine

    have, _ = _support(conn)
    if have:
        return have
    record: Row = copy.deepcopy(SUPPORT_RECORD)
    taken = {str(r[0]) for r in conn.execute("SELECT id FROM pipelines")}
    record["id"] = next(pid for pid in ("support", *(f"support-{n}" for n in range(2, 100))) if pid not in taken)
    if conn.execute(
        "SELECT 1 FROM pipelines WHERE name=? COLLATE NOCASE AND retired_at IS NULL", (record["name"],)
    ).fetchone():
        record["name"] = "Routed support"
    from . import waiting_on

    record["manager_id"] = PS._general_manager(conn) or waiting_on.company_person(conn)  # pyright: ignore[reportPrivateUsage]
    used: set[str] = set()
    for stage in (*record["stages"], record["done"], record["closed"]):
        stage["id"] = PS._free_stage_id(conn, stage["id"], used)  # pyright: ignore[reportPrivateUsage]
    saved: Row = PS.save(conn, task_engine.SYSTEM, record)
    stage_rules: Any = _stage_rules
    stage_rules.ensure_defaults(conn)  # its two timers
    return str(saved["id"])

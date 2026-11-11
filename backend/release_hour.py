# pyright: strict
"""The daily release hour (docs/pipelines-v3.md D13): a routine on the Release Owner.

At each time the release schedule names (release_schedule_routes, company time), the routine does what Release now
does, through the same call (`release_schedule_routes.request_now`): the Release Owner gets ONE Release task, or
keeps its unfinished one. Each slot fires once. The slot and what it did are written in the same transaction as the
task (`release-schedule-fired`, which the schedule's `last` reads), so a restart, a second scheduler or a clock that
jumps back never fires a slot again; a slot older than the newest one fired never fires. A server that was down at a
slot fires it when it is back within GRACE, and skips it after that: the next slot asks again.
"""

import json
from datetime import date, datetime, time, timedelta, timezone, tzinfo
from typing import Any, cast

from . import release_schedule_routes as RS
from .store import H as _H

H: Any = _H

GRACE = timedelta(hours=2)
KEEP = 14  # the fired slots kept: a week of two a day


def instant(day: date, text: str, zone: tzinfo) -> datetime:
    """When the wall time `text` ("HH:MM") happens on `day` in `zone`, as an aware datetime in that zone. A time the
    autumn night has twice is its first; a time the spring gap skips is the first instant after the gap."""
    hour, minute = (int(x) for x in text.split(":"))
    wall = datetime.combine(day, time(hour, minute))
    first = wall.replace(tzinfo=zone, fold=0)
    if first.astimezone(timezone.utc).astimezone(zone).replace(tzinfo=None) == wall:
        return first  # it exists; fold=0 is the earlier of two
    # Skipped: the gap lies between the two readings of the wall time; its end is where the offset changes.
    lo = wall.replace(tzinfo=zone, fold=1).astimezone(timezone.utc)
    hi = first.astimezone(timezone.utc)
    after = hi.astimezone(zone).utcoffset()
    step = lo
    while step < hi and step.astimezone(zone).utcoffset() != after:
        step += timedelta(minutes=1)
    return step.astimezone(zone)


def due(cfg: dict[str, Any], at: datetime) -> tuple[str, datetime] | None:
    """(slot, instant) of the newest slot at or before `at` and no older than GRACE, or None. A slot is the company
    date and the configured time, "2026-11-01T01:30": one per date and time, whatever the clocks do that night."""
    if not cfg["on"]:
        return None
    zone = RS.zone()
    today = at.astimezone(zone).date()
    best: tuple[str, datetime] | None = None
    for day in (today - timedelta(days=1), today):
        for text in cfg["times"]:
            point = instant(day, text, zone)
            if point <= at and at - point <= GRACE and (best is None or point > best[1]):
                best = (f"{day.isoformat()}T{text}", point)
    return best


def following(cfg: dict[str, Any], at: datetime) -> datetime | None:
    """The first release hour after `at` (`instant`), or None while the schedule is off."""
    if not cfg["on"]:
        return None
    zone = RS.zone()
    today = at.astimezone(zone).date()
    points = (instant(today + timedelta(days=n), text, zone) for n in range(3) for text in cfg["times"])
    return min((p for p in points if p > at), default=None)


def next_wake(c: Any, at: datetime) -> datetime | None:
    """When a sleeping server must be up for the release hour: `at` while a due slot has not fired, else the next
    hour (`following`), or None while the schedule is off."""
    return at if unfired(c, at) else following(RS.load(c), at)


def due_slot(cfg: dict[str, Any], at: datetime) -> datetime | None:
    """The due slot's instant (`due`), or None."""
    found = due(cfg, at)
    return found[1] if found else None


def _slot_key(key: Any) -> str | None:
    """A fired slot's key as `due` writes it; one an older server wrote (an instant with its offset) keeps its wall
    date and time. None when it does not parse."""
    try:
        return datetime.fromisoformat(key).strftime("%Y-%m-%dT%H:%M")
    except (TypeError, ValueError):
        return None


def _fired(c: Any) -> dict[str, Any]:
    """{slot: what it did} for the slots fired; a key that does not parse is left out."""
    row = c.execute("SELECT value FROM registry_metadata WHERE key=?", (RS.FIRED,)).fetchone()
    try:
        fired: Any = json.loads(row[0]) if row and row[0] else {}
    except ValueError:
        fired = {}
    out: dict[str, Any] = {}
    # isinstance narrows the parsed JSON to dict[Unknown, Unknown]; its keys and values are any JSON
    items = cast("dict[Any, Any]", fired) if isinstance(fired, dict) else {}
    for key, value in items.items():
        slot = _slot_key(key)
        if slot:
            out[slot] = value
    return out


def unfired(c: Any, at: datetime) -> tuple[str, datetime] | None:
    """The due slot (`due`) when it has not fired and no newer slot has, else None. Read-only: the scheduler asks this
    on every pass and takes the write lock only when it says yes."""
    found = due(RS.load(c), at)
    if found is None or any(done >= found[0] for done in _fired(c)):
        return None
    return found


def tick(c: Any, at: datetime) -> dict[str, Any] | None:
    """Fire the due slot once, in `c` (a write transaction). Returns what Release now answered, with the slot as
    `at`, or None when nothing was due."""
    found = unfired(c, at)
    if found is None:
        return None
    slot, point = found
    answer = {"at": point.isoformat(), **RS.request_now(c, H.KEEPER)}
    fired = _fired(c)
    fired[slot] = answer
    kept = {key: fired[key] for key in sorted(fired)[-KEEP:]}
    c.execute(
        "INSERT INTO registry_metadata VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (RS.FIRED, json.dumps(kept, sort_keys=True)),
    )
    return answer


def words(answer: dict[str, Any] | None) -> str | None:
    """The run's line on the system jobs screen, or None for a pass with nothing due."""
    if answer is None:
        return None
    if answer["sent"]:
        return f"Asked the Release Owner for a release of {answer['tasks']} task{'s' if answer['tasks'] != 1 else ''}"
    return {
        "no_release_manager": "No Release Owner to ask",
        "already_open": "The Release Owner's release task is still open",
    }.get(answer["reason"], str(answer["reason"]))

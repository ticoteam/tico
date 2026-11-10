"""The daily release hour (docs/pipelines-v3.md D13): a routine on the Release Manager.

At each time the release schedule names (release_schedule_routes, company time), the routine does what Release now
does, through the same call (`release_schedule_routes.request_now`): the Release Manager gets ONE Release task, or
keeps its unfinished one. Each slot fires once. The slot and what it did are written in the same transaction as the
task (`release-schedule-fired`, which the schedule's `last` reads), so a restart, a second scheduler or a clock that
jumps back never fires a slot again; a slot older than the newest one fired never fires. A server that was down at a
slot fires it when it is back within GRACE, and skips it after that: the next slot asks again.
"""

import json
from datetime import datetime, timedelta

from . import release_schedule_routes as RS
from .store import H

GRACE = timedelta(hours=2)
KEEP = 14  # the fired slots kept: a week of two a day


def due_slot(cfg, at):
    """The newest slot at or before `at` and no older than GRACE, as an aware datetime in company time, or None (the
    schedule is off, or no slot is that recent)."""
    if not cfg["on"]:
        return None
    local = at.astimezone(RS.zone())
    best = None
    for text in cfg["times"]:
        hour, minute = (int(x) for x in text.split(":"))
        point = local.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if point > local:
            point -= timedelta(days=1)
        best = point if best is None or point > best else best
    return best if best is not None and local - best <= GRACE else None


def _fired(c):
    """{slot: what it did} for the slots fired, the keys parsed; a key that does not parse is left out."""
    row = c.execute("SELECT value FROM registry_metadata WHERE key=?", (RS.FIRED,)).fetchone()
    try:
        fired = json.loads(row[0]) if row and row[0] else {}
    except ValueError:
        fired = {}
    out = {}
    for key, value in (fired if isinstance(fired, dict) else {}).items():
        try:
            out[datetime.fromisoformat(key)] = value
        except (TypeError, ValueError):
            continue
    return out


def unfired(c, at: datetime):
    """The due slot (`due_slot`) when it has not fired and no newer slot has, else None. Read-only: the scheduler asks
    this on every pass and takes the write lock only when it says yes."""
    slot = due_slot(RS.load(c), at)
    if slot is None or any(done >= slot for done in _fired(c)):
        return None
    return slot


def tick(c, at: datetime):
    """Fire the due slot once, in `c` (a write transaction). Returns what Release now answered, with the slot as
    `at`, or None when nothing was due."""
    slot = unfired(c, at)
    if slot is None:
        return None
    answer = {"at": slot.isoformat(), **RS.request_now(c, H.KEEPER)}
    fired = _fired(c)
    fired[slot] = answer
    kept = {key.isoformat(): fired[key] for key in sorted(fired)[-KEEP:]}
    c.execute(
        "INSERT INTO registry_metadata VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (RS.FIRED, json.dumps(kept, sort_keys=True)),
    )
    return answer


def words(answer):
    """The run's line on the system jobs screen, or None for a pass with nothing due."""
    if answer is None:
        return None
    if answer["sent"]:
        return f"Asked the Release Manager for a release of {answer['tasks']} task{'s' if answer['tasks'] != 1 else ''}"
    return {
        "no_release_manager": "No Release Manager to ask",
        "nothing_pending": "Nothing waits for a release",
        "already_open": "The Release Manager's release task is still open",
    }.get(answer["reason"], str(answer["reason"]))

# pyright: strict
"""`calendar.event.created|changed|cancelled`, one way for every calendar home.

Tico's own calendar (backend/calendar_store.py), Tico's writes to Google (backend/calendar_api.py, the appointments
route) and the Google watcher (backend/gcal_watch.py, changes made in Google itself) all emit here, so an automation
on these events hears every home the same way. Each leaves its `events` row and wakes the `event` automations whose
owner may read that calendar: a Google calendar follows its mailbox (a level on it), and Tico's own calendars are read
by everyone in the company. The payload names ids only, never a title, notes or attendees: whoever acts reads the
event through its own access. Outgoing webhooks do not take these (event_catalog `outbound=False`).
"""

import logging
import sqlite3
from collections.abc import Callable, Mapping
from typing import Any

from . import hubdb as _hubdb

H: Any = _hubdb
LOG = logging.getLogger("tico.calendar_events")
ACTIONS = ("created", "changed", "cancelled")
# A scheduled call follows its event (backend/call_prep.py): a change or cancel of a call's event, from any home, has
# that call read again once the write commits. Keyed by the database file, so each hub's sweep hears only its own.
CALL_FOLLOWERS: dict[str, Callable[[str], None]] = {}


def google_detail(calendar_id: str, occurrence_id: str | None = None, **more: object) -> dict[str, object]:
    """The facts of a Google calendar event change: its calendar, its home and the occurrence when one is named."""
    out: dict[str, object] = {"calendar_id": "google:" + calendar_id, "home": "google"}
    if occurrence_id:
        out["occurrence_id"] = occurrence_id
    return {**out, **more}


def emit(
    c: sqlite3.Connection,
    actor: str,
    action: str,
    subject: str,
    detail: Mapping[str, object],
    *,
    mailbox: str | None = None,
) -> None:
    """The event row, then the automations that may hear it. `mailbox`: the Google calendar's address, whose level an
    automation's owner needs; None for Tico's own calendars. Never raises past the row: an automation problem must
    not undo the calendar write that caused it."""
    if action not in ACTIONS:
        raise ValueError(f"unknown calendar event action {action!r}")
    name = "calendar.event." + action
    facts = dict(detail)
    H.event(c, actor, name, subject, facts)
    if action != "created" and CALL_FOLLOWERS:
        _follow_call(c, subject, facts)
    if H.NO_TRIGGERS.get():
        return
    try:
        from . import automations as _automations

        A: Any = _automations
        box = str(mailbox or "").strip().lower()

        def hears(row: Mapping[str, Any]) -> bool:
            return bool(A.may_read_inbox(c, row.get("owner_id"), box))

        A.on_event(c, name, subject, facts, actor, access=hears if box else None)
    except Exception:  # noqa: BLE001 - logged, never raised into the calendar write
        LOG.warning("automations did not hear %s", name, exc_info=True)


def _follow_call(c: sqlite3.Connection, subject: str, facts: dict[str, object]) -> None:
    """Queue the re-read of the scheduled call this event belongs to, for after the write commits."""
    try:
        follow = CALL_FOLLOWERS.get(H._main_path(c))
        hooks = getattr(c, "after_commit", None)
        if follow is None or hooks is None:
            return
        from . import calendar_api as _calendar_api

        call_id = _calendar_api.call_of_event(c, subject, facts)
        if call_id:
            hooks.append(lambda: follow(call_id))
    except Exception:  # noqa: BLE001 - logged; the minute sweep reads the call anyway
        LOG.warning("a scheduled call did not hear its event change", exc_info=True)

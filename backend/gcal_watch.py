# pyright: strict
"""The Google calendar watcher: changes made in Google itself become `calendar.event.*`, as Tico's own calendar's do.

A drain worker (backend/drains.py `calendar_watch`). Each pass checks the Google calendars last checked CHECK_SECONDS
ago or more: Google's `events.list` with the calendar's sync token answers what changed since the last check, and each
change is announced once through backend/calendar_events.py, the way Tico's own calendar announces its writes, so an
automation reacts the same whatever the calendar's home. Watched: the primary calendar of every mailbox whose calendar
home is Google (`gcal_live.connected`: the Workspace key covers it with a calendar scope), read as that mailbox.

- First check: the calendar is listed in full to learn what is on it. Nothing is announced; changes from then on are.
- An event not seen before is `created` (a moved or changed instance of a known series is `changed`); a new sequence is
  `changed`; a cancellation of one seen is `cancelled`. Each is deduped on calendar, event and sequence: what was last
  seen of each event is kept in `calendar_watch_events`, updated in the same write as its event row, so a repeat pass
  or a re-list announces nothing twice.
- An expired sync token (410) lists the calendar in full again and compares it with what was seen: new events are
  created, other sequences changed, and seen events no longer listed cancelled.
- Tico's own Google writes are announced where they are made (`calendar_api._record_google`, the appointments route),
  which mark them with `own_write`: the watcher takes their next sighting quietly.
- Events that ended over a day ago are not news: they are neither announced nor kept.
- A failure leaves the sync token where it was, records its kind on the watch row and, for a refused sign-in, tells
  reconnects (the mailbox's tasks get a card); the next good check clears it.

It keeps no title, notes, place or attendee of any event. The appointments that the route
(backend/connectors.py) left running past ACTION_MINUTES, because the server stopped mid-call, are settled `unknown`
here too: never retried, since Google may have made them.
"""

import logging
import sqlite3
from collections.abc import Callable, Iterable
from datetime import datetime, timedelta, timezone
from typing import Any, cast

from . import calendar_events as _calendar_events
from . import gcal_live as _gcal_live
from . import google_workspace as _google_workspace
from . import hubdb as _hubdb
from .store import Problem

H: Any = _hubdb
gcal: Any = _gcal_live  # looked up on each call, so tests replace its functions
workspace: Any = _google_workspace
events: Any = _calendar_events
Row = dict[str, Any]
LOG = logging.getLogger("tico.gcal_watch")

CHECK_SECONDS = 90  # how often one calendar is asked what changed
PAST = timedelta(days=1)  # an event that ended longer ago than this is not news
OWN_WRITE_MINUTES = 10  # how long a sighting of Tico's own write stays quiet
CANCELLED_DAYS = 30  # how long a cancelled event's row is kept
ACTION_MINUTES = 5  # an appointment the route left running this long is unknown
REFUSED = frozenset(("gcal_refused", "not_connected"))


def _utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _end(text: object) -> str | None:
    """Google's end (a dateTime with an offset, or an all-day date) as a UTC stamp, comparable as text."""
    raw = str(text or "")
    if not raw:
        return None
    try:
        if "T" not in raw:
            return raw[:10] + "T00:00:00Z"
        found = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        return _utc(found if found.tzinfo else found.replace(tzinfo=timezone.utc))
    except ValueError:
        return None


# ---------------------------------------------------------------- what is watched


def watched(c: sqlite3.Connection) -> list[str]:
    """The Google calendars to watch: each Workspace mailbox and roster person whose calendar home is Google."""
    from . import routines as _routines

    R: Any = _routines
    found: set[str] = set()
    for box in cast("Iterable[Row]", workspace.workspace_mailboxes(c)):
        found.add(str(box["address"]).strip().lower())
    for person in cast("list[Row]", R._roster(c).get("people") or []):
        email = str(person.get("email") or "").strip().lower()
        if email and "@" in email and not person.get("hidden"):
            found.add(email)
    return sorted(address for address in found if gcal.connected(c, address))


def _plan(store: Any) -> list[Row]:
    """The watch rows due a check, the rows brought in line with what is watched first (a write only when they
    differ): a calendar no longer on Google stops being watched, and what was seen of it goes."""
    with store.read() as c:
        want = set(watched(c))
        have = {str(r["calendar_id"]) for r in c.execute("SELECT calendar_id FROM calendar_watches").fetchall()}
    if want != have:
        with store.transaction() as c:
            now = H.now()
            for calendar_id in sorted(want - have):
                c.execute(
                    "INSERT OR IGNORE INTO calendar_watches(calendar_id,mailbox,created_at,updated_at) VALUES(?,?,?,?)",
                    (calendar_id, calendar_id, now, now),
                )
            for calendar_id in sorted(have - want):
                c.execute("DELETE FROM calendar_watches WHERE calendar_id=?", (calendar_id,))
                c.execute("DELETE FROM calendar_watch_events WHERE calendar_id=?", (calendar_id,))
    with store.read() as c:
        due = H.shift(H.now(), seconds=-CHECK_SECONDS)
        rows = c.execute(
            "SELECT * FROM calendar_watches WHERE checked_at IS NULL OR checked_at<=? "
            "ORDER BY checked_at IS NOT NULL, checked_at, calendar_id",
            (due,),
        ).fetchall()
        return [dict(r) for r in rows]


# ---------------------------------------------------------------- one check


def check(store: Any, watch: Row) -> int:
    """Ask Google what changed on one calendar and announce it. Returns how many events were announced."""
    calendar_id, mailbox = str(watch["calendar_id"]), str(watch["mailbox"])
    token = cast("str | None", watch.get("sync_token"))
    mode = "changes" if token else ("relist" if watch.get("watching_since_at") else "seed")
    try:
        with store.read() as c:
            try:
                items, following = cast(
                    "tuple[list[Row], str]", gcal.changes(c, mailbox, calendar_id, token if token else None)
                )
            except gcal.SyncExpired:
                mode = "relist"
                items, following = cast("tuple[list[Row], str]", gcal.changes(c, mailbox, calendar_id, None))
    except Problem as refused:
        _failed(store, watch, str(cast(Any, refused).code))
        return 0
    except Exception as exc:  # noqa: BLE001 - one calendar's failure must not stop the others
        LOG.warning("calendar watch of one calendar failed: %s", type(exc).__name__)
        _failed(store, watch, "error")
        return 0
    with store.transaction() as c:
        done = apply(c, calendar_id, mailbox, items, mode)
        c.execute(
            "UPDATE calendar_watches SET sync_token=?,checked_at=?,check_error=NULL,"
            "watching_since_at=coalesce(watching_since_at,?),updated_at=? WHERE calendar_id=?",
            (following, H.now(), H.now(), H.now(), calendar_id),
        )
        from . import reconnects as _reconnects

        cast(Any, _reconnects).google_good(c, "calendar", [mailbox])
    return done


def _failed(store: Any, watch: Row, code: str) -> None:
    """The kind of failure on the watch row (never Google's words); the sync token stays where it was. A refused
    sign-in is that account failing for calendar (backend/reconnects.py)."""
    with store.transaction() as c:
        c.execute(
            "UPDATE calendar_watches SET checked_at=?,check_error=?,updated_at=? WHERE calendar_id=?",
            (H.now(), code[:64], H.now(), watch["calendar_id"]),
        )
        if code in REFUSED:
            from . import reconnects as _reconnects

            box = str(watch["mailbox"])
            cast(Any, _reconnects).google_report(c, "calendar", [box], [box], ok=[])


def _seen(c: sqlite3.Connection, calendar_id: str, ids: list[str] | None) -> dict[str, Row]:
    if ids is None:
        rows = c.execute("SELECT * FROM calendar_watch_events WHERE calendar_id=?", (calendar_id,)).fetchall()
        return {str(r["event_id"]): dict(r) for r in rows}
    out: dict[str, Row] = {}
    for start in range(0, len(ids), 500):
        chunk = ids[start : start + 500]
        rows = c.execute(
            "SELECT * FROM calendar_watch_events WHERE calendar_id=? AND event_id IN ("
            + ",".join("?" * len(chunk))
            + ")",
            (calendar_id, *chunk),
        ).fetchall()
        out.update({str(r["event_id"]): dict(r) for r in rows})
    return out


def _keep(c: sqlite3.Connection, calendar_id: str, item: Row, status: str, now: str) -> None:
    c.execute(
        "INSERT INTO calendar_watch_events(calendar_id,event_id,series_id,sequence,status,repeats,ends_at,own_write,"
        "updated_at) VALUES(?,?,?,?,?,?,?,0,?) ON CONFLICT(calendar_id,event_id) DO UPDATE SET "
        "series_id=coalesce(excluded.series_id,calendar_watch_events.series_id),sequence=excluded.sequence,"
        "status=excluded.status,repeats=excluded.repeats,"
        "ends_at=coalesce(excluded.ends_at,calendar_watch_events.ends_at),own_write=0,updated_at=excluded.updated_at",
        (
            calendar_id,
            item["id"],
            item.get("series_id"),
            int(item.get("sequence") or 0),
            status,
            1 if item.get("repeats") else 0,
            _end(item.get("end")),
            now,
        ),
    )


def apply(c: sqlite3.Connection, calendar_id: str, mailbox: str, items: list[Row], mode: str) -> int:
    """Compare Google's answer with what was seen and announce each difference once. `mode`: `seed` (the first full
    list: learn, announce nothing), `changes` (a sync token's answer) or `relist` (a full list after an expired token:
    seen events no longer listed were cancelled). Returns how many events were announced."""
    now = H.now()
    clock = datetime.now(timezone.utc)
    cutoff = _utc(clock - PAST)
    fresh = _utc(clock - timedelta(minutes=OWN_WRITE_MINUTES))
    full = mode in ("seed", "relist")
    seen = _seen(c, calendar_id, None if full else [str(i["id"]) for i in items])
    series = {str(i["series_id"]) for i in items if i.get("series_id") and str(i["series_id"]) not in seen} - {
        str(i["id"]) for i in items
    }
    seen.update(_seen(c, calendar_id, sorted(series)) if series else {})
    listed: set[str] = set()
    done = 0

    def known(event_id: object) -> bool:
        row = seen.get(str(event_id or ""))
        return row is not None and row["status"] == "confirmed"

    def announce(action: str, item: Row) -> None:
        nonlocal done
        subject = str(item.get("series_id") or item["id"])
        detail = events.google_detail(calendar_id, "google:" + calendar_id + ":" + str(item["id"]))
        events.emit(c, H.KEEPER, action, subject, detail, mailbox=mailbox)
        done += 1

    for item in items:
        event_id = str(item["id"])
        listed.add(event_id)
        prior = seen.get(event_id)
        ended = _end(item.get("end"))
        past = not item.get("repeats") and ended is not None and ended < cutoff
        quiet = bool(prior and prior["own_write"] and str(prior["updated_at"]) >= fresh)
        if item["status"] == "cancelled":
            if prior is None and not (item.get("series_id") and known(item["series_id"])):
                continue  # never seen, or an instance of a series not watched: nothing to tell
            if prior is not None and prior["status"] == "cancelled":
                continue
            if mode != "seed" and not past:
                announce("cancelled", item)  # a cancel Tico made itself is already marked cancelled (own_write)
            _keep(c, calendar_id, item, "cancelled", now)
            seen[event_id] = {**(prior or {}), "status": "cancelled", "own_write": 0, "updated_at": now}
            continue
        if past:
            if prior is not None:
                _keep(c, calendar_id, item, "confirmed", now)
            continue
        action = None
        if prior is None:
            action = "changed" if item.get("series_id") and known(item["series_id"]) else "created"
        elif prior["status"] == "cancelled":
            action = "created"
        elif int(prior["sequence"] or 0) != int(item.get("sequence") or 0):
            action = "changed"
        if action and mode != "seed" and not quiet:
            announce(action, item)
        if action or prior is None or quiet or prior.get("own_write"):
            _keep(c, calendar_id, item, "confirmed", now)
            seen[event_id] = {**(prior or {}), "status": "confirmed", "own_write": 0, "updated_at": now}
    if mode == "relist":
        for event_id, prior in list(seen.items()):
            if event_id in listed or prior.get("status") != "confirmed":
                continue
            ended = cast("str | None", prior.get("ends_at"))
            if not prior.get("repeats") and ended is not None and ended < cutoff:
                continue
            announce("cancelled", {"id": event_id, "series_id": prior.get("series_id")})
            c.execute(
                "UPDATE calendar_watch_events SET status='cancelled',own_write=0,updated_at=? "
                "WHERE calendar_id=? AND event_id=?",
                (now, calendar_id, event_id),
            )
    _prune(c, calendar_id, cutoff)
    return done


def _prune(c: sqlite3.Connection, calendar_id: str, cutoff: str) -> None:
    old = _utc(datetime.now(timezone.utc) - timedelta(days=CANCELLED_DAYS))
    c.execute(
        "DELETE FROM calendar_watch_events WHERE calendar_id=? AND ((repeats=0 AND ends_at IS NOT NULL AND ends_at<?) "
        "OR (status='cancelled' AND updated_at<?) OR (own_write=1 AND updated_at<?))",
        (calendar_id, cutoff, old, old),
    )


def own_write(c: sqlite3.Connection, calendar_id: str, event_id: str, *, cancelled: bool = False) -> None:
    """Tico just wrote this Google event and announced it itself: the watcher's next sighting of it is quiet, and a
    cancellation it already made is not announced again."""
    calendar_id, event_id = str(calendar_id or "").strip().lower(), str(event_id or "")
    if not calendar_id or not event_id:
        return
    now = H.now()
    c.execute(
        "INSERT INTO calendar_watch_events(calendar_id,event_id,sequence,status,own_write,updated_at) "
        "VALUES(?,?,-1,?,?,?) ON CONFLICT(calendar_id,event_id) DO UPDATE SET status=excluded.status,"
        "own_write=excluded.own_write,updated_at=excluded.updated_at",
        (calendar_id, event_id, "cancelled" if cancelled else "confirmed", 0 if cancelled else 1, now),
    )


# ---------------------------------------------------------------- the pass


def settle_stale_actions(c: sqlite3.Connection) -> int:
    """Appointments the route started and never settled (the server stopped mid-call) become `unknown`, never retried:
    Google may have made the event. A computer's own claims (runner_id set) keep their own upkeep."""
    cutoff = H.shift(H.now(), minutes=-ACTION_MINUTES)
    rows = c.execute(
        "SELECT id,requested_by FROM calendar_actions WHERE status='running' AND runner_id IS NULL AND started_at<?",
        (cutoff,),
    ).fetchall()
    for row in rows:
        now = H.now()
        c.execute(
            "UPDATE calendar_actions SET status='unknown',error=?,updated_at=?,finished_at=? "
            "WHERE id=? AND status='running'",
            ("Tico did not hear back from Google; check the calendar before booking again", now, now, row["id"]),
        )
        H.event(c, row["requested_by"], "calendar.schedule.unknown", row["id"], {})
    return len(rows)


def tick(store: Any, budget: Any = None) -> int:
    """One pass: settle stale appointments, then check each calendar due, until the budget is spent. Returns how many
    calendars were checked."""
    from . import drains as _drains

    left = cast("Callable[[Any], bool]", _drains.left)
    with store.read() as c:
        stale = c.execute(
            "SELECT 1 FROM calendar_actions WHERE status='running' AND runner_id IS NULL AND started_at<? LIMIT 1",
            (H.shift(H.now(), minutes=-ACTION_MINUTES),),
        ).fetchone()
    if stale:
        with store.transaction() as c:
            settle_stale_actions(c)
    checked = 0
    for watch in _plan(store):
        if not left(budget):
            break
        check(store, watch)
        checked += 1
    return checked

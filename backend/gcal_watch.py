# pyright: strict
"""The Google calendar watcher: changes made in Google itself become `calendar.event.*`, as Tico's own calendar's do.

A drain worker (backend/drains.py `calendar_watch`). Each pass checks the Google calendars last checked CHECK_SECONDS
ago or more: Google's `events.list` with the calendar's sync token answers what changed since the last check, and each
change is announced once through backend/calendar_events.py, the way Tico's own calendar announces its writes, so an
automation reacts the same whatever the calendar's home. Watched: the primary calendar of every mailbox whose calendar
home is Google (`gcal_live.connected`: the person's own Connect Google grant or the Workspace key reaches it with a
calendar scope), read as that mailbox through the token door (backend/google_tokens.py).

- First check: the calendar is listed in full to learn what is on it. Nothing is announced; changes from then on are.
- An event not seen before is `created` (a moved or changed instance of a known series is `changed`); a new sequence is
  `changed`; a cancellation of one seen is `cancelled`. Each is deduped on calendar, event and sequence: what was last
  seen of each event is kept in `calendar_watch_events`, updated in the same write as its event row, so a repeat pass
  or a re-list announces nothing twice.
- An expired sync token (410) lists the calendar in full again and compares it with what was seen: new events are
  created, other sequences changed, and seen events no longer listed cancelled.
- Tico's own Google writes are announced where they are made (`calendar_api._record_google`, the appointments route),
  which record them (`own_write`) at the exact id and sequence Google gave the write: that version is not news again,
  and any other one is. No time window.
- Events that ended over a day ago are not news: they are neither announced nor kept. Anything Google can still
  return (a cancelled instance of a live series, until its time is past) is kept, so it is never news twice.
- Google answers a page at a time. Each page is applied and its place saved in one write, so a listing longer than a
  pass's budget, or cut short by a failure, goes on from that page next time; a full list marks what it names and
  tells what went only once its last page is in.
- A failure leaves the sync token and a listing's place where they were, records its kind on the watch row and, for a
  refused sign-in, tells reconnects (the mailbox's tasks get a card); the next good check clears it.

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
    # A person's own Connect Google grant, which may be for an address that is not their roster email.
    for row in c.execute("SELECT mailbox FROM google_connects WHERE grant_sealed IS NOT NULL").fetchall():
        found.add(str(row["mailbox"] or "").strip().lower())
    return sorted(address for address in found if "@" in address and gcal.connected(c, address))


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
        # A listing cut short goes on at once; one that failed waits its turn like any check.
        rows = c.execute(
            "SELECT * FROM calendar_watches WHERE (page_token IS NOT NULL AND check_error IS NULL) "
            "OR checked_at IS NULL OR checked_at<=? "
            "ORDER BY page_token IS NULL, checked_at IS NOT NULL, checked_at, calendar_id",
            (due,),
        ).fetchall()
        return [dict(r) for r in rows]


# ---------------------------------------------------------------- one check


def check(store: Any, watch: Row, budget: Any = None) -> int:
    """Ask Google what changed on one calendar and announce it, a page at a time: each page is applied and its place
    saved in one write, so a listing the budget or a failure cuts short goes on from there next time. Returns how many
    events were announced."""
    from . import drains as _drains

    left = cast("Callable[[Any], bool]", _drains.left)
    calendar_id, mailbox = str(watch["calendar_id"]), str(watch["mailbox"])
    sync = cast("str | None", watch.get("sync_token")) or None
    page = cast("str | None", watch.get("page_token")) or None
    mode = str(watch.get("list_mode") or "") if page else ""
    run = cast("str | None", watch.get("list_run")) if page else None
    if not mode:
        mode = "changes" if sync else ("relist" if watch.get("watching_since_at") else "seed")
        run = None if mode == "changes" else str(H.new_id())
    done = 0
    while True:
        try:
            with store.read() as c:
                items, following, synced = cast(
                    "tuple[list[Row], str | None, str | None]",
                    gcal.changes_page(c, mailbox, calendar_id, sync if mode == "changes" else None, page),
                )
        except gcal.SyncExpired:
            mode, run, page = "relist", str(H.new_id()), None
            continue
        except gcal.PageExpired:
            page = None
            run = None if mode == "changes" else str(H.new_id())
            continue
        except Problem as refused:
            _failed(store, watch, str(cast(Any, refused).code))
            return done
        except Exception as exc:  # noqa: BLE001 - one calendar's failure must not stop the others
            LOG.warning("calendar watch of one calendar failed: %s", type(exc).__name__)
            _failed(store, watch, "error")
            return done
        with store.transaction() as c:
            done += apply(c, calendar_id, mailbox, items, mode, run)
            if following:
                c.execute(
                    "UPDATE calendar_watches SET page_token=?,list_mode=?,list_run=?,check_error=NULL,updated_at=? "
                    "WHERE calendar_id=?",
                    (following, mode, run, H.now(), calendar_id),
                )
            else:
                if mode == "relist" and run:
                    done += finish_relist(c, calendar_id, mailbox, run)
                c.execute(
                    "UPDATE calendar_watches SET sync_token=?,page_token=NULL,list_mode=NULL,list_run=NULL,"
                    "checked_at=?,check_error=NULL,watching_since_at=coalesce(watching_since_at,?),updated_at=? "
                    "WHERE calendar_id=?",
                    (synced, H.now(), H.now(), H.now(), calendar_id),
                )
                from . import reconnects as _reconnects

                cast(Any, _reconnects).google_good(c, "calendar", [mailbox])
        if not following:
            return done
        page = following
        if not left(budget):
            return done  # the place is saved: the next pass goes on from it


def _failed(store: Any, watch: Row, code: str) -> None:
    """The kind of failure on the watch row (never Google's words); the sync token and a listing's place stay where
    they were. A refused sign-in is that account failing for calendar (backend/reconnects.py)."""
    with store.transaction() as c:
        c.execute(
            "UPDATE calendar_watches SET checked_at=?,check_error=?,updated_at=? WHERE calendar_id=?",
            (H.now(), code[:64], H.now(), watch["calendar_id"]),
        )
        if code in REFUSED:
            from . import reconnects as _reconnects

            box = str(watch["mailbox"])
            cast(Any, _reconnects).google_report(c, "calendar", [box], [box], ok=[])


def _seen(c: sqlite3.Connection, calendar_id: str, ids: list[str]) -> dict[str, Row]:
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


def _when(item: Row) -> str | None:
    """When an event stops being news: its end, or for a cancelled instance (which carries no end) its start."""
    return _end(item.get("end") or item.get("original_start"))


def _keep(c: sqlite3.Connection, calendar_id: str, item: Row, status: str, now: str) -> None:
    c.execute(
        "INSERT INTO calendar_watch_events(calendar_id,event_id,series_id,sequence,status,repeats,ends_at,updated_at) "
        "VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(calendar_id,event_id) DO UPDATE SET "
        "series_id=coalesce(excluded.series_id,calendar_watch_events.series_id),sequence=excluded.sequence,"
        "status=excluded.status,repeats=excluded.repeats,"
        "ends_at=coalesce(excluded.ends_at,calendar_watch_events.ends_at),updated_at=excluded.updated_at",
        (
            calendar_id,
            item["id"],
            item.get("series_id"),
            int(item.get("sequence") or 0),
            status,
            1 if item.get("repeats") else 0,
            _when(item),
            now,
        ),
    )


def _announce(c: sqlite3.Connection, calendar_id: str, mailbox: str, action: str, event_id: str, series: object) -> int:
    detail = events.google_detail(calendar_id, "google:" + calendar_id + ":" + event_id)
    events.emit(c, H.KEEPER, action, str(series or event_id), detail, mailbox=mailbox)
    return 1


def apply(
    c: sqlite3.Connection, calendar_id: str, mailbox: str, items: list[Row], mode: str, run: str | None = None
) -> int:
    """Compare one page of Google's answer with what was seen and announce each difference once. `mode`: `seed` (the
    first full list: learn, announce nothing), `changes` (a sync token's answer) or `relist` (a full list after an
    expired token). A full list marks what it names with `run`, for `finish_relist`. Returns how many were announced."""
    now = H.now()
    cutoff = _utc(datetime.now(timezone.utc) - PAST)
    full = mode in ("seed", "relist")
    ids = [str(i["id"]) for i in items]
    seen = _seen(c, calendar_id, ids)
    series = {str(i["series_id"]) for i in items if i.get("series_id")} - set(ids) - set(seen)
    seen.update(_seen(c, calendar_id, sorted(series)) if series else {})
    done = 0

    def known(event_id: object) -> bool:
        row = seen.get(str(event_id or ""))
        return row is not None and row["status"] == "confirmed"

    for item in items:
        event_id = str(item["id"])
        prior = seen.get(event_id)
        ended = _when(item)
        past = not item.get("repeats") and ended is not None and ended < cutoff
        if item["status"] == "cancelled":
            if prior is not None and prior["status"] == "cancelled":
                continue
            of_known = bool(item.get("series_id")) and known(item["series_id"])
            # A full list names the cancelled instances of a live series: kept, so a later list finds them known.
            if prior is None and not of_known and not (full and item.get("series_id")):
                continue  # never seen, or an instance of a series not watched: nothing to tell
            if mode != "seed" and not past and (prior is not None or of_known):
                done += _announce(c, calendar_id, mailbox, "cancelled", event_id, item.get("series_id"))
            _keep(c, calendar_id, item, "cancelled", now)
            seen[event_id] = {**(prior or {}), "status": "cancelled"}
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
        if action and mode != "seed":
            done += _announce(c, calendar_id, mailbox, action, event_id, item.get("series_id"))
        if action or prior is None:
            _keep(c, calendar_id, item, "confirmed", now)
            seen[event_id] = {**(prior or {}), "status": "confirmed", "sequence": item.get("sequence")}
    if full and run:
        for start in range(0, len(ids), 500):
            chunk = ids[start : start + 500]
            c.execute(
                "UPDATE calendar_watch_events SET listed_in=? WHERE calendar_id=? AND event_id IN ("
                + ",".join("?" * len(chunk))
                + ")",
                (run, calendar_id, *chunk),
            )
    _prune(c, calendar_id, cutoff)
    return done


def finish_relist(c: sqlite3.Connection, calendar_id: str, mailbox: str, run: str) -> int:
    """A full list after an expired token is complete: an event seen before and still news that it did not name was
    cancelled in the meantime. Returns how many were announced."""
    cutoff = _utc(datetime.now(timezone.utc) - PAST)
    gone = c.execute(
        "SELECT event_id,series_id FROM calendar_watch_events WHERE calendar_id=? AND status='confirmed' "
        "AND listed_in IS NOT ? AND (repeats=1 OR ends_at IS NULL OR ends_at>=?)",
        (calendar_id, run, cutoff),
    ).fetchall()
    for row in gone:
        _announce(c, calendar_id, mailbox, "cancelled", str(row["event_id"]), row["series_id"])
        c.execute(
            "UPDATE calendar_watch_events SET status='cancelled',updated_at=? WHERE calendar_id=? AND event_id=?",
            (H.now(), calendar_id, row["event_id"]),
        )
    return len(gone)


def _prune(c: sqlite3.Connection, calendar_id: str, cutoff: str) -> None:
    """Forget only what Google can no longer return as news: events (and cancelled instances) whose time is past, and
    a cancelled event with no time of its own (a deleted one-off or series, which a full list leaves out) after
    CANCELLED_DAYS, with the instances of such a series."""
    old = _utc(datetime.now(timezone.utc) - timedelta(days=CANCELLED_DAYS))
    c.execute(
        "DELETE FROM calendar_watch_events WHERE calendar_id=? AND repeats=0 AND ends_at IS NOT NULL AND ends_at<?",
        (calendar_id, cutoff),
    )
    gone = [
        str(r["event_id"])
        for r in c.execute(
            "SELECT event_id FROM calendar_watch_events WHERE calendar_id=? AND status='cancelled' AND series_id IS NULL "
            "AND (ends_at IS NULL OR repeats=1) AND updated_at<?",
            (calendar_id, old),
        ).fetchall()
    ]
    for event_id in gone:
        c.execute(
            "DELETE FROM calendar_watch_events WHERE calendar_id=? AND (event_id=? OR series_id=?)",
            (calendar_id, event_id, event_id),
        )


def own_write(
    c: sqlite3.Connection,
    calendar_id: str,
    event_id: str,
    *,
    sequence: int | None = None,
    cancelled: bool = False,
    end: object = None,
    series_id: str | None = None,
    repeats: bool = False,
) -> None:
    """Tico just wrote this Google event (an instance by its own id) and announced it itself: what was seen of it
    becomes the version Google gave that write, so the watcher announces only another one. A write whose version is
    unknown is not recorded: the watcher may then announce it once more, but never misses a change."""
    calendar_id, event_id = str(calendar_id or "").strip().lower(), str(event_id or "")
    if not calendar_id or not event_id:
        return
    now = H.now()
    if cancelled:
        c.execute(
            "INSERT INTO calendar_watch_events(calendar_id,event_id,series_id,status,updated_at) "
            "VALUES(?,?,?,'cancelled',?) ON CONFLICT(calendar_id,event_id) DO UPDATE SET status='cancelled',"
            "updated_at=excluded.updated_at",
            (calendar_id, event_id, series_id, now),
        )
        return
    if sequence is None:
        return
    _keep(
        c,
        calendar_id,
        {"id": event_id, "series_id": series_id, "sequence": sequence, "repeats": repeats, "end": end},
        "confirmed",
        now,
    )


# ---------------------------------------------------------------- the pass


def settle_stale_actions(c: sqlite3.Connection) -> int:
    """Appointments the route started and never settled (the server stopped mid-call) become `unknown`, never retried:
    Google may have made the event."""
    cutoff = H.shift(H.now(), minutes=-ACTION_MINUTES)
    rows = c.execute(
        "SELECT id,requested_by FROM calendar_actions WHERE status='running' AND started_at<?",
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
            "SELECT 1 FROM calendar_actions WHERE status='running' AND started_at<? LIMIT 1",
            (H.shift(H.now(), minutes=-ACTION_MINUTES),),
        ).fetchone()
    if stale:
        with store.transaction() as c:
            settle_stale_actions(c)
    checked = 0
    for watch in _plan(store):
        if not left(budget):
            break
        check(store, watch, budget)
        checked += 1
    return checked

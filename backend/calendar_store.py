# pyright: strict
"""Tico's own calendar, the Tico home (docs/dev/calendar-contract.md, spec sections 5, 7 and 13).

Bots, shared calendars and people with nothing connected keep their events here. Every read returns occurrences
(already expanded instances, the one shape every home shares); nothing outside this module and backend/gcal_live.py
knows about rules. `c` is always a sqlite connection the caller opened (store.read or store.transaction).

- A teammate's calendar is made on first use (`calendar_for`); an admin makes shared ones (`create_shared`).
- A series stores its rule (backend/calendar_recur.py), never instances. One moved, changed or cancelled instance is an
  exception row keyed by the instance's original start. Edits take a scope: `this` (one exception row), `following`
  (the old series ends just before this instance and a new series starts from it), `all` (the series row; exceptions
  whose original start still exists are kept, the rest are cancelled).
- Cancel, never delete: `status = cancelled` until purge_cancelled removes rows 90 days on (the hourly Maintenance
  run's Clean up step).
- `sequence` bumps on every change; an edit that names the sequence it saw is refused as `stale` when it moved.
- Every write leaves an audit_log row and an event (`calendar.event.created|changed|cancelled`).

Who may read or write is the caller's decision (the routes, spec section 13). Reads mark private events
`visibility: 'private'` and list their attendees, so the caller can show them as Busy to everyone else; a search hit on
a private event the viewer does not attend should be dropped, not shown as Busy, since its words matched.
"""

import sqlite3
from collections.abc import Callable, Iterable
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, cast
from zoneinfo import ZoneInfo

from . import audit_log as _audit_log
from . import calendar_recur as CR
from . import thread_search as _thread_search
from .store import H as _H, Problem

H: Any = _H
audit_log: Any = _audit_log
TS: Any = _thread_search
Row = dict[str, Any]
Responses = dict[tuple[str, str | None], str]
Keep = Callable[[Row, Row, Row], bool]
Attendee = tuple[str, str | None, str | None]  # (type, id, email)

TITLE_MAX = 500
TEXT_MAX = 20_000
ATTENDEES_MAX = 200
LONGEST = timedelta(days=366)  # one instance
DEFAULT_LENGTH = timedelta(minutes=30)
PURGE_DAYS = 90
PURGE_BATCH = 1000
SEARCH_SCAN = 500  # matching rows one search expands
RESPONSES = ("needs_action", "accepted", "tentative", "declined")
FIELDS = {
    "title",
    "notes",
    "location",
    "meeting_url",
    "all_day",
    "start",
    "end",
    "time_zone",
    "repeat",
    "attendees",
    "task_id",
    "thread_id",
    "visibility",
    "status",
}
CONTENT = ("title", "notes", "location", "meeting_url", "visibility", "status", "task_id", "thread_id")
TIMES = (
    "all_day",
    "starts_on",
    "ends_on",
    "starts_at_local",
    "ends_at_local",
    "time_zone",
    "starts_at",
    "ends_at",
)
CORE = ("calendar_id", "uid", *CONTENT, *TIMES, "rrule", "until_at", "recurring_event_id", "original_starts_at")


# ----------------------------------------------------------------------------- calendars
def _teammate(c: sqlite3.Connection, owner_type: str, owner_id: str) -> tuple[str, str, str | None] | None:
    """(id, name, time_zone) of a person or bot, or None."""
    if owner_type == "human":
        row = c.execute("SELECT id, name, email, time_zone FROM humans WHERE id=?", (owner_id,)).fetchone()
        return row and (row["id"], row["name"] or row["email"] or row["id"], row["time_zone"])
    if owner_type == "bot":
        bot = H.resolve_bot(c, owner_id)
        return bot and (bot["id"], bot.get("display_name") or bot.get("slug") or bot["id"], None)
    return None


def calendar_for(c: sqlite3.Connection, owner_type: str, owner_id: str, *, create: bool = True) -> Row | None:
    """A teammate's Tico calendar, made on first use when `create` (named for them, in their zone or UTC)."""
    if owner_type not in ("human", "bot"):
        raise Problem("validation", "A teammate's calendar is a human's or a bot's", 422)
    who = _teammate(c, owner_type, owner_id)
    if who is None:
        raise Problem("not_found", f"There is no {owner_type} {owner_id}", 404)
    sql = "SELECT * FROM calendars WHERE owner_type=? AND owner_id=?"
    row = c.execute(sql, (owner_type, who[0])).fetchone()
    if row is None and create:
        at = H.now()
        c.execute(
            "INSERT OR IGNORE INTO calendars(id, owner_type, owner_id, name, home, time_zone, created_by, created_at, "
            "updated_at) VALUES(?,?,?,?,'tico',?,?,?,?)",
            (H.new_id(), owner_type, who[0], who[1], _zone_name(who[2]), f"{owner_type}:{who[0]}", at, at),
        )
        row = c.execute(sql, (owner_type, who[0])).fetchone()
    return dict(row) if row else None


def _zone_name(name: object) -> str:
    try:
        return str(CR.zone(name).key) if name else "UTC"
    except Problem:
        return "UTC"


def create_shared(c: sqlite3.Connection, name: object, actor: str | None, time_zone: str = "UTC") -> Row:
    name = str(name or "").strip()
    if not name:
        raise Problem("validation", "Name the calendar", 422)
    if len(name) > 200:
        raise Problem("validation", "Keep the name under 200 characters", 422)
    CR.zone(time_zone)
    at, cal_id = H.now(), H.new_id()
    c.execute(
        "INSERT INTO calendars(id, owner_type, owner_id, name, home, time_zone, created_by, created_at, updated_at) "
        "VALUES(?,'shared',NULL,?,'tico',?,?,?,?)",
        (cal_id, name, time_zone, actor, at, at),
    )
    row = _calendar(c, cal_id)
    audit_log.write(c, actor, "calendar:" + cal_id, None, {"name": name, "time_zone": time_zone, "owner": "shared"})
    H.event(c, actor, "calendar.created", "calendar:" + cal_id, {"owner_type": "shared"})
    return row


def get(c: sqlite3.Connection, calendar_id: str) -> Row | None:
    row = c.execute("SELECT * FROM calendars WHERE id=?", (calendar_id,)).fetchone()
    return dict(row) if row else None


def shared(c: sqlite3.Connection) -> list[Row]:
    return [dict(r) for r in c.execute("SELECT * FROM calendars WHERE owner_type='shared' ORDER BY name, id")]


def accesses(c: sqlite3.Connection, calendar_id: str) -> list[Row]:
    sql = "SELECT * FROM calendar_accesses WHERE calendar_id=? ORDER BY grantee_type, grantee_id"
    return [dict(r) for r in c.execute(sql, (calendar_id,))]


def set_access(
    c: sqlite3.Connection, calendar_id: str, grantee_type: str, grantee_id: str, level: str | None, actor: str | None
) -> None:
    """Give a person, bot or group `level` ('read' | 'read_write') on a Tico calendar, or take it away (None)."""
    if get(c, calendar_id) is None:
        raise Problem("not_found", "There is no such calendar", 404)
    if grantee_type not in ("human", "bot", "group") or not grantee_id:
        raise Problem("validation", "A grantee is a human, a bot or a group", 422)
    if level not in (None, "read", "read_write"):
        raise Problem("validation", "A level is read or read_write", 422)
    key = (calendar_id, grantee_type, grantee_id)
    before = c.execute(
        "SELECT level FROM calendar_accesses WHERE calendar_id=? AND grantee_type=? AND grantee_id=?", key
    ).fetchone()
    at = H.now()
    if level is None:
        c.execute("DELETE FROM calendar_accesses WHERE calendar_id=? AND grantee_type=? AND grantee_id=?", key)
    else:
        c.execute(
            "INSERT INTO calendar_accesses(id, calendar_id, grantee_type, grantee_id, level, created_by, created_at, "
            "updated_at) VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(calendar_id, grantee_type, grantee_id) DO UPDATE SET "
            "level=excluded.level, updated_at=excluded.updated_at",
            (H.new_id(), *key, level, actor, at, at),
        )
    target = f"calendar:{calendar_id}"
    grantee = f"{grantee_type}:{grantee_id}"
    audit_log.write(
        c,
        actor,
        target,
        before and {"grantee": grantee, "level": before["level"]},
        {"grantee": grantee, "level": level},
    )
    H.event(c, actor, "calendar.access_changed", target, {"grantee": grantee, "level": level})


def _calendar_view(c: sqlite3.Connection, cal: Row, names: dict[tuple[str, str], str]) -> Row:
    if cal["owner_type"] == "shared":
        return {"ref": "shared:" + cal["id"], "type": "shared", "id": cal["id"], "name": cal["name"]}
    key = (cal["owner_type"], cal["owner_id"])
    if key not in names:
        who = _teammate(c, *key)
        names[key] = who[1] if who else cal["name"]
    return {
        "ref": f"{cal['owner_type']}:{cal['owner_id']}",
        "type": cal["owner_type"],
        "id": cal["owner_id"],
        "name": names[key],
    }


# ----------------------------------------------------------------------------- times
def _wall(value: object, tz: ZoneInfo | None, field: str) -> datetime:
    """A start or end the caller gave (ISO with an offset, or a local wall time) as a naive wall time in `tz`."""
    try:
        at = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    except ValueError:
        raise Problem("validation", f"{field} is not an ISO 8601 time", 422) from None
    if at.tzinfo is not None:
        at = at.astimezone(tz)
    return at.replace(tzinfo=None, microsecond=0)


def _date(value: object, field: str) -> date:
    try:
        return date.fromisoformat(str(value).strip()[:10])
    except ValueError:
        raise Problem("validation", f"{field} is not a date (YYYY-MM-DD)", 422) from None


def _times(fields: Row, current: Row | None, default_zone: str | None) -> Row:
    """The time columns for `fields` laid over `current` (a row, or None when creating)."""
    all_day = bool(fields["all_day"]) if "all_day" in fields else bool(current and current["all_day"])
    if all_day:
        if "start" in fields:
            start = _date(fields["start"], "start")
        elif current and current["all_day"]:
            start = _date(current["starts_on"], "start")
        elif current:
            start = CR.local_naive(current["starts_at_local"]).date()
        else:
            raise Problem("validation", "Give a start", 422)
        if "end" in fields and fields["end"]:
            end = _date(fields["end"], "end")
        elif current and current["all_day"]:
            end = start + (_date(current["ends_on"], "end") - _date(current["starts_on"], "start"))
        else:
            end = start + timedelta(days=1)
        if end <= start:
            raise Problem(
                "validation", "An all-day event ends on a later date than it starts (the end is exclusive)", 422
            )
        if end - start > LONGEST:
            raise Problem("validation", "An event lasts at most a year", 422)
        return {
            "all_day": 1,
            "starts_on": start.isoformat(),
            "ends_on": end.isoformat(),
            "starts_at_local": None,
            "ends_at_local": None,
            "time_zone": None,
            "starts_at": start.isoformat() + "T00:00:00Z",
            "ends_at": end.isoformat() + "T00:00:00Z",
        }
    zone_name = fields.get("time_zone") or (current and current["time_zone"]) or default_zone or "UTC"
    tz = CR.zone(zone_name)
    timed = current if current is not None and not current["all_day"] else None
    if "start" in fields:
        start = _wall(fields["start"], tz, "start")
    elif timed is not None:
        start = CR.local_naive(timed["starts_at_local"])
    elif current:
        start = datetime.combine(_date(current["starts_on"], "start"), time(9))
    else:
        raise Problem("validation", "Give a start", 422)
    if "end" in fields and fields["end"]:
        end = _wall(fields["end"], tz, "end")
    elif timed is not None:
        end = start + (CR.local_naive(timed["ends_at_local"]) - CR.local_naive(timed["starts_at_local"]))
    else:
        end = start + DEFAULT_LENGTH
    if end <= start:
        raise Problem("validation", "An event ends after it starts", 422)
    if end - start > LONGEST:
        raise Problem("validation", "An event lasts at most a year", 422)
    starts_at = CR.to_utc(start, tz)
    return {
        "all_day": 0,
        "starts_on": None,
        "ends_on": None,
        "starts_at_local": start.isoformat(),
        "ends_at_local": end.isoformat(),
        "time_zone": str(tz.key),
        "starts_at": CR.z(starts_at),
        "ends_at": CR.z(starts_at + (end - start)),
    }


def _text(fields: Row, key: str, limit: int, required: bool = False) -> str | None:
    value = fields.get(key)
    value = None if value is None else str(value).strip()
    if required and not value:
        raise Problem("validation", f"Give a {key}", 422)
    if value and len(value) > limit:
        raise Problem("validation", f"Keep the {key} under {limit} characters", 422)
    return value or None


def _content(fields: Row, current: Row | None) -> Row:
    """The content columns for `fields` over `current`."""
    out = {k: (current[k] if current else None) for k in CONTENT}
    if not current:
        out |= {"visibility": "default", "status": "confirmed"}
    if "title" in fields or not current:
        out["title"] = _text(fields, "title", TITLE_MAX, required=True)
    for key, limit in (("notes", TEXT_MAX), ("location", 1000), ("meeting_url", 2000), ("task_id", 200)):
        if key in fields:
            out[key] = _text(fields, key, limit)
    if "thread_id" in fields:
        out["thread_id"] = _text(fields, "thread_id", 200)
    if out["meeting_url"] and not out["meeting_url"].startswith(("https://", "http://")):
        raise Problem("validation", "A meeting link starts with https://", 422)
    if "visibility" in fields:
        if fields["visibility"] not in ("default", "private", None):
            raise Problem("validation", "Visibility is default or private", 422)
        out["visibility"] = fields["visibility"] or "default"
    if "status" in fields:
        if fields["status"] not in ("confirmed", "tentative"):
            raise Problem("validation", "Status is confirmed or tentative; cancel to cancel", 422)
        out["status"] = fields["status"]
    return out


def _check_fields(fields: Row) -> None:
    unknown = sorted(set(fields or {}) - FIELDS)
    if unknown:
        raise Problem("validation", f"Unknown field {', '.join(unknown)}", 422)


def _rule_columns(row: Row, rrule: str | None) -> Row:
    """rrule and until_at for a row whose times are set; refuses a rule that yields no instance."""
    if not rrule:
        return {"rrule": None, "until_at": None}
    event = {**row, "rrule": rrule}
    if next(CR.series_starts(event), None) is None:
        raise Problem("repeat", "That rule gives no dates from the start", 422)
    return {"rrule": rrule, "until_at": CR.last_end(event)}


# ----------------------------------------------------------------------------- attendees
def _attendee_items(c: sqlite3.Connection, raw: object) -> list[Attendee]:
    if raw is None:
        return []
    if not isinstance(raw, (list, tuple)):
        raise Problem("validation", "attendees is a list", 422)
    given = cast("list[object]", raw)
    if len(given) > ATTENDEES_MAX:
        raise Problem("validation", f"At most {ATTENDEES_MAX} attendees", 422)
    out: list[Attendee] = []
    seen: set[tuple[str, str | None]] = set()
    for item in given:
        kind, ident, email = _attendee_ref(item)
        if kind == "human":
            row = c.execute("SELECT id, email FROM humans WHERE id=?", (ident,)).fetchone()
            if row is None:
                raise Problem("not_found", f"There is no person {ident}", 404)
            ident, email = row["id"], row["email"]
        elif kind == "bot":
            bot = H.resolve_bot(c, ident)
            if bot is None:
                raise Problem("not_found", f"There is no bot {ident}", 404)
            ident, email = bot["id"], None
        else:
            email = str(email or "").strip().lower()
            if "@" not in email or len(email) > 320:
                raise Problem("validation", f"{email!r} is not an email address", 422)
            row = c.execute("SELECT id FROM humans WHERE lower(email)=?", (email,)).fetchone()
            if row:  # a teammate named by address is the teammate
                kind, ident = "human", row["id"]
        key = (kind, ident if kind != "external" else email)
        if key not in seen:
            seen.add(key)
            out.append((kind, ident if kind != "external" else None, email))
    return out


def _attendee_ref(item: object) -> Attendee:
    if isinstance(item, dict):
        given = cast("dict[str, object]", item)
        ident, email = given.get("id"), given.get("email")
        kind = given.get("type") or ("external" if email and not ident else None)
        if kind not in ("human", "bot", "external"):
            raise Problem("validation", "An attendee's type is human, bot or external", 422)
        if kind != "external" and not ident:
            raise Problem("validation", "Give the attendee's id", 422)
        return str(kind), None if ident is None else str(ident), None if email is None else str(email)
    text = str(item or "").strip()
    head, sep, tail = text.partition(":")
    if sep and head in ("human", "bot") and tail:
        return head, tail, None
    if "@" in text:
        return "external", None, text
    raise Problem("validation", f"{text!r} is not human:<id>, bot:<id> or an email address", 422)


def _write_attendees(
    c: sqlite3.Connection,
    event_id: str,
    items: list[Attendee],
    actor: str | None,
    keep: Responses | None = None,
) -> None:
    """Replace an event's attendees with `items`, keeping each one's response from `keep` ({(type, id|email): resp});
    the actor's own row is accepted."""
    keep = keep or {}
    c.execute("DELETE FROM calendar_event_attendees WHERE calendar_event_id=?", (event_id,))
    at = H.now()
    for kind, ident, email in items:
        response = keep.get((kind, ident or email)) or ("accepted" if f"{kind}:{ident}" == actor else "needs_action")
        c.execute(
            "INSERT INTO calendar_event_attendees(id, calendar_event_id, attendee_type, attendee_id, email, response, "
            "created_at, updated_at) VALUES(?,?,?,?,?,?,?,?)",
            (H.new_id(), event_id, kind, ident, email, response, at, at),
        )


def _attendee_rows(c: sqlite3.Connection, event_id: str) -> list[Row]:
    sql = "SELECT * FROM calendar_event_attendees WHERE calendar_event_id=? ORDER BY created_at, id"
    return [dict(r) for r in c.execute(sql, (event_id,))]


def _responses(rows: list[Row]) -> Responses:
    return {(r["attendee_type"], r["attendee_id"] or r["email"]): r["response"] for r in rows}


def _copy_attendees(c: sqlite3.Connection, source_id: str, target_id: str) -> None:
    rows = _attendee_rows(c, source_id)
    items = [(r["attendee_type"], r["attendee_id"], r["email"]) for r in rows]
    _write_attendees(c, target_id, items, None, _responses(rows))


def _attendees_by_event(c: sqlite3.Connection, event_ids: Iterable[str]) -> dict[str, list[Row]]:
    ids = list(event_ids)
    out: dict[str, list[Row]] = {i: [] for i in ids}
    for at in range(0, len(ids), 500):
        chunk = ids[at : at + 500]
        marks = ",".join("?" * len(chunk))
        sql = (
            f"SELECT a.*, h.name AS human_name, b.display_name AS bot_name FROM calendar_event_attendees a "
            f"LEFT JOIN humans h ON a.attendee_type='human' AND h.id=a.attendee_id "
            f"LEFT JOIN bots b ON a.attendee_type='bot' AND b.id=a.attendee_id "
            f"WHERE a.calendar_event_id IN ({marks}) ORDER BY a.created_at, a.id"
        )
        for r in c.execute(sql, chunk):
            out[r["calendar_event_id"]].append(
                {
                    "type": r["attendee_type"],
                    "id": r["attendee_id"],
                    "email": r["email"],
                    "name": r["human_name"] or r["bot_name"] or r["email"] or r["attendee_id"],
                    "response": r["response"],
                }
            )
    return out


# ----------------------------------------------------------------------------- reads
def _row(c: sqlite3.Connection, event_id: str) -> Row | None:
    row = c.execute("SELECT * FROM calendar_events WHERE id=?", (event_id,)).fetchone()
    return dict(row) if row else None


def _must(c: sqlite3.Connection, event_id: str) -> Row:
    row = _row(c, event_id)
    if row is None:
        raise Problem("not_found", "There is no such event", 404)
    return row


def _calendar(c: sqlite3.Connection, calendar_id: str) -> Row:
    cal = get(c, calendar_id)
    if cal is None:
        raise Problem("not_found", "There is no such calendar", 404)
    return cal


def _exceptions(c: sqlite3.Connection, series_ids: Iterable[str]) -> dict[str, list[Row]]:
    ids = list(series_ids)
    out: dict[str, list[Row]] = {i: [] for i in ids}
    for at in range(0, len(ids), 500):
        chunk = ids[at : at + 500]
        sql = f"SELECT * FROM calendar_events WHERE recurring_event_id IN ({','.join('?' * len(chunk))})"
        for r in c.execute(sql, chunk):
            out[r["recurring_event_id"]].append(dict(r))
    return out


def _occurrence(cal_view: Row, series: Row, inst: Row, source: Row, attendees: list[Row]) -> Row:
    """One occurrence (contract shape, minus the viewer's fields): `series` is the series or single row, `source` the
    row whose content shows (an exception row, or the series), `inst` the expanded instance."""
    recurring = bool(series["rrule"])
    all_day = bool(source["all_day"])
    return {
        "occurrence_id": f"tico:{series['id']}:{inst['original_starts_at']}" if recurring else f"tico:{series['id']}",
        "event_id": series["id"],
        "calendar": cal_view,
        "calendar_id": series["calendar_id"],
        "home": "tico",
        "kind": "event",
        "title": source["title"],
        "notes": source["notes"],
        "location": source["location"],
        "start": inst["starts_at"],
        "end": inst["ends_at"],
        "all_day": all_day,
        "time_zone": None if all_day else source["time_zone"],
        "attendees": attendees,
        "busy_only": False,
        "recurring": recurring,
        "original_starts_at": inst["original_starts_at"] if recurring else None,
        "status": source["status"],
        "visibility": source["visibility"],
        "meeting_url": source["meeting_url"],
        "open_url": None,
        "links": {
            "thread": source["thread_id"],
            "meeting": None,
            "task": source["task_id"],
            "automation": None,
            "attempt": None,
        },
        "sequence": source["sequence"],
    }


def _expanded(
    c: sqlite3.Connection, series_rows: list[Row], start_utc: datetime, end_utc: datetime, keep: Keep | None = None
) -> list[Row]:
    """Occurrences of `series_rows` in the window; `keep(series, inst, source)` filters before the attendee read."""
    exceptions = _exceptions(c, [s["id"] for s in series_rows if s["rrule"]])
    cals: dict[str, Row] = {}
    names: dict[tuple[str, str], str] = {}
    picked: list[tuple[Row, Row, Row]] = []
    for series in series_rows:
        if series["calendar_id"] not in cals:
            cals[series["calendar_id"]] = _calendar_view(c, _calendar(c, series["calendar_id"]), names)
        for inst in CR.expand(series, exceptions.get(series["id"], []), start_utc, end_utc):
            source = inst.get("exception") or series
            if source["status"] == "cancelled" or (keep and not keep(series, inst, source)):
                continue
            picked.append((series, inst, source))
    attendees = _attendees_by_event(c, {source["id"] for _s, _i, source in picked})
    return [_occurrence(cals[s["calendar_id"]], s, i, src, attendees[src["id"]]) for s, i, src in picked]


def occurrences(
    c: sqlite3.Connection, calendar_ids: Iterable[str], start_utc: datetime, end_utc: datetime
) -> list[Row]:
    """Every live instance on `calendar_ids` overlapping [start_utc, end_utc), in start order. A day of margin on the
    row query covers all-day rows (stored as UTC midnights) and moved exceptions; expand decides exactly."""
    ids = [i for i in dict.fromkeys(calendar_ids or ()) if i]
    if not ids:
        return []
    lo, hi = CR.z(start_utc - timedelta(days=1)), CR.z(end_utc + timedelta(days=1))
    marks = ",".join("?" * len(ids))
    rows = c.execute(
        f"SELECT * FROM calendar_events WHERE calendar_id IN ({marks}) AND recurring_event_id IS NULL "
        "AND status<>'cancelled' AND starts_at<? AND (CASE WHEN rrule IS NULL THEN ends_at>? "
        "ELSE until_at IS NULL OR until_at>? END) "
        f"UNION SELECT s.* FROM calendar_events x JOIN calendar_events s ON s.id=x.recurring_event_id "
        f"WHERE x.calendar_id IN ({marks}) AND x.status<>'cancelled' AND s.status<>'cancelled' "
        "AND x.starts_at<? AND x.ends_at>?",
        (*ids, hi, lo, lo, *ids, hi, lo),
    ).fetchall()
    found = _expanded(c, [dict(r) for r in rows], start_utc, end_utc)
    return sorted(found, key=lambda o: (o["start"], o["occurrence_id"]))


def event(c: sqlite3.Connection, event_id: str) -> Row | None:
    """The stored row (series, single or exception) with its attendees, or None."""
    row = _row(c, event_id)
    if row:
        row["attendees"] = _attendee_rows(c, event_id)
    return row


def occurrence_id_parts(occurrence_id: object) -> tuple[str, str | None] | None:
    """`tico:<event id>[:<original start>]` as (event_id, original_starts_at | None); None when not a Tico id."""
    head, _, rest = str(occurrence_id or "").partition(":")
    if head != "tico" or not rest:
        return None
    event_id, _, original = rest.partition(":")
    return event_id, original or None


def _first(c: sqlite3.Connection, series: Row) -> Row:
    """The series' first live occurrence, which a stored row always has (the rule is checked on write)."""
    start = CR.parse_z(series["starts_at"]) - timedelta(days=1)
    end = CR.parse_z(series["until_at"]) + timedelta(days=1) if series["until_at"] else start + timedelta(days=3660)
    found = _expanded_first(c, series, start, end)
    if found is None:
        raise Problem("not_found", "The event has no instance left", 404)
    return found


def _expanded_first(c: sqlite3.Connection, series: Row, start: datetime, end: datetime) -> Row | None:
    exceptions = _exceptions(c, [series["id"]])[series["id"]] if series["rrule"] else []
    for inst in CR.expand(series, exceptions, start, end, cap=CR.CAP):
        source = inst.get("exception") or series
        if source["status"] != "cancelled":
            view = _calendar_view(c, _calendar(c, series["calendar_id"]), {})
            return _occurrence(view, series, inst, source, _attendees_by_event(c, [source["id"]])[source["id"]])
    return None


def _instance_occurrence(c: sqlite3.Connection, series: Row, original: str) -> Row | None:
    """The occurrence of `series` at `original` (its exception applied), or None when cancelled or not an instance."""
    inst = CR.instance_at(series, original)
    if inst is None:
        return None
    exception = c.execute(
        "SELECT * FROM calendar_events WHERE recurring_event_id=? AND original_starts_at=?", (series["id"], original)
    ).fetchone()
    source = dict(exception) if exception else series
    if exception and source["all_day"]:
        inst = {**inst, "starts_at": source["starts_on"], "ends_at": source["ends_on"]}
    elif exception:
        inst = {**inst, "starts_at": source["starts_at"], "ends_at": source["ends_at"]}
    view = _calendar_view(c, _calendar(c, series["calendar_id"]), {})
    return _occurrence(view, series, inst, source, _attendees_by_event(c, [source["id"]])[source["id"]])


# ----------------------------------------------------------------------------- writes
def _core(row: Row) -> Row:
    return {k: row.get(k) for k in CORE} | {"sequence": row.get("sequence"), "status": row.get("status")}


def _audit(
    c: sqlite3.Connection, actor: str | None, action: str, row: Row, before: Row | None, detail: Row | None = None
) -> None:
    audit_log.write(c, actor, "calendar_event:" + row["id"], before and _core(before), _core(row))
    H.event(
        c,
        actor,
        "calendar.event." + action,
        row["id"],
        {"calendar_id": row["calendar_id"], **(detail or {})},
    )


def _insert(c: sqlite3.Connection, row: Row) -> None:
    cols = list(row)
    c.execute(
        f"INSERT INTO calendar_events({','.join(cols)}) VALUES({','.join('?' * len(cols))})",
        [row[k] for k in cols],
    )


def _update(c: sqlite3.Connection, event_id: str, changes: Row) -> None:
    sets = ",".join(f"{k}=?" for k in changes)
    c.execute(f"UPDATE calendar_events SET {sets} WHERE id=?", (*changes.values(), event_id))


def create_event(c: sqlite3.Connection, calendar_id: str, fields: Row, actor: str | None) -> Row:
    """A new event or series on a Tico calendar; returns its first occurrence plus `event_id`."""
    _check_fields(fields)
    cal = _calendar(c, calendar_id)
    if cal["home"] != "tico":
        raise Problem("validation", "That calendar lives in Google", 422)
    if "status" in fields and fields["status"] not in ("confirmed", "tentative"):
        raise Problem("validation", "Status is confirmed or tentative", 422)
    row = _new_row(c, cal, fields, actor)
    _insert(c, row)
    _write_attendees(c, row["id"], _attendee_items(c, fields.get("attendees")), actor)
    _audit(c, actor, "created", row, None)
    return _first(c, row) | {"event_id": row["id"]}


def _new_row(c: sqlite3.Connection, cal: Row, fields: Row, actor: str | None, base: Row | None = None) -> Row:
    at = H.now()
    row = {"id": H.new_id(), "calendar_id": cal["id"], "uid": H.new_id() + "@tico"}
    row |= _content(fields, base)
    row |= _times(fields, base, cal["time_zone"])
    rrule = CR.parse_repeat(fields["repeat"]) if fields.get("repeat") else None
    row |= _rule_columns(row, rrule)
    row |= {
        "recurring_event_id": None,
        "original_starts_at": None,
        "sequence": 0,
        "created_by": actor,
        "created_at": at,
        "updated_at": at,
    }
    return row


def _live(c: sqlite3.Connection, event_id: str) -> tuple[Row, str | None]:
    """(series, original_starts_at from an exception id) for an event id, refusing a missing or cancelled one."""
    row = _must(c, event_id)
    original = None
    if row["recurring_event_id"]:
        original = row["original_starts_at"]
        row = _must(c, row["recurring_event_id"])
    if row["status"] == "cancelled":
        raise Problem("not_found", "That event was cancelled", 404)
    return row, original


def _stale(expected: object, row: Row) -> None:
    if expected is not None and str(expected).strip() != str(row["sequence"]):
        raise Problem(
            "stale",
            "The event changed since you read it; read it again",
            409,
            extra={"sequence": row["sequence"]},
        )


def _exception_for(c: sqlite3.Connection, series: Row, original: str, actor: str | None) -> tuple[Row, bool]:
    """The exception row for one instance, made from the series when there is none yet (attendees copied)."""
    found = c.execute(
        "SELECT * FROM calendar_events WHERE recurring_event_id=? AND original_starts_at=?", (series["id"], original)
    ).fetchone()
    if found:
        return dict(found), False
    inst = CR.instance_at(series, original)
    if inst is None:
        raise Problem("not_found", "The series has no instance at that time", 404)
    at = H.now()
    row = {k: series[k] for k in ("calendar_id", "uid", *CONTENT)}
    if series["all_day"]:
        times = {"starts_on": inst["starts_at"], "ends_on": inst["ends_at"]}
        times |= {"starts_at_local": None, "ends_at_local": None, "time_zone": None}
        times |= {"starts_at": inst["starts_at"] + "T00:00:00Z", "ends_at": inst["ends_at"] + "T00:00:00Z"}
    else:
        local = CR.local_naive(inst["original_start_local"])
        length = CR.local_naive(series["ends_at_local"]) - CR.local_naive(series["starts_at_local"])
        times = {"starts_on": None, "ends_on": None, "time_zone": series["time_zone"]}
        times |= {"starts_at_local": local.isoformat(), "ends_at_local": (local + length).isoformat()}
        times |= {"starts_at": inst["starts_at"], "ends_at": inst["ends_at"]}
    row |= times | {
        "id": H.new_id(),
        "all_day": series["all_day"],
        "rrule": None,
        "until_at": None,
        "recurring_event_id": series["id"],
        "original_starts_at": original,
        "sequence": series["sequence"],
        "created_by": actor,
        "created_at": at,
        "updated_at": at,
    }
    _insert(c, row)
    _copy_attendees(c, series["id"], row["id"])
    return row, True


def _apply(
    c: sqlite3.Connection, row: Row, fields: Row, actor: str | None, default_zone: str | None, *, rule: bool = True
) -> Row:
    """Lay `fields` over a stored row: content, times, rule (when `rule`) and attendees; bumps its sequence."""
    changes = _content(fields, row) | _times(fields, row, default_zone)
    if rule and "repeat" in fields:
        changes |= _rule_columns(row | changes, CR.parse_repeat(fields["repeat"]) if fields["repeat"] else None)
    elif rule and row["rrule"]:
        changes |= _rule_columns(row | changes, row["rrule"])
    changes |= {"sequence": row["sequence"] + 1, "updated_at": H.now()}
    _update(c, row["id"], changes)
    if "attendees" in fields:
        keep = _responses(_attendee_rows(c, row["id"]))
        _write_attendees(c, row["id"], _attendee_items(c, fields["attendees"]), actor, keep)
    return row | changes


def _drop_orphans(c: sqlite3.Connection, series: Row) -> None:
    """Cancel the exceptions whose original start the series no longer has (after its time or rule changed)."""
    for x in _exceptions(c, [series["id"]])[series["id"]]:
        if x["status"] != "cancelled" and CR.instance_at(series, x["original_starts_at"]) is None:
            _update(c, x["id"], {"status": "cancelled", "sequence": x["sequence"] + 1, "updated_at": H.now()})


def _shifted(series: Row, original: str, fields: Row) -> Row:
    """Fields for an `all` edit made from one instance: the instance's move becomes the same move of the series."""
    if "start" not in fields or "all_day" in fields:
        return fields
    inst = CR.instance_at(series, original)
    if inst is None:
        raise Problem("not_found", "The series has no instance at that time", 404)
    if series["all_day"]:
        new_start = _date(fields["start"], "start")
        delta = new_start - date.fromisoformat(inst["original_start_local"])
        out = dict(fields, start=(date.fromisoformat(series["starts_on"]) + delta).isoformat())
        if fields.get("end"):
            out["end"] = (date.fromisoformat(out["start"]) + (_date(fields["end"], "end") - new_start)).isoformat()
        return out
    tz = CR.zone(fields.get("time_zone") or series["time_zone"])
    new_start = _wall(fields["start"], tz, "start")
    delta = new_start - CR.local_naive(inst["original_start_local"])
    out = dict(fields, start=(CR.local_naive(series["starts_at_local"]) + delta).isoformat())
    if fields.get("end"):
        out["end"] = (CR.local_naive(out["start"]) + (_wall(fields["end"], tz, "end") - new_start)).isoformat()
    return out


def update_event(
    c: sqlite3.Connection,
    event_id: str,
    fields: Row,
    actor: str | None,
    *,
    original_starts_at: str | None = None,
    scope: str = "all",
    expected_sequence: object = None,
) -> Row:
    """Change an event. On a series, `scope` is `this` (one instance, by `original_starts_at`), `following` (this
    instance and the ones after it become a new series) or `all`. A stale `expected_sequence` is refused (409)."""
    _check_fields(fields)
    if scope not in ("this", "following", "all"):
        raise Problem("validation", "scope is this, following or all", 422)
    series, from_exception = _live(c, event_id)
    original = original_starts_at or from_exception or ""
    cal = _calendar(c, series["calendar_id"])
    if not series["rrule"]:
        _stale(expected_sequence, series)
        after = _apply(c, series, fields, actor, cal["time_zone"])
        _audit(c, actor, "changed", after, series)
        return _first(c, after) | {"event_id": after["id"]}
    if scope in ("this", "following") and not original:
        raise Problem("validation", f"Say which instance to change (original_starts_at) for scope {scope}", 422)
    if original and CR.instance_at(series, original) is None:
        raise Problem("not_found", "The series has no instance at that time", 404)
    if scope == "this":
        if "repeat" in fields:
            raise Problem("validation", "One instance cannot repeat; change the series instead", 422)
        exception, made = _exception_for(c, series, original, actor)
        if exception["status"] == "cancelled":
            raise Problem("not_found", "That instance was cancelled", 404)
        _stale(expected_sequence, series if made else exception)
        after = _apply(c, exception, fields, actor, cal["time_zone"], rule=False)
        _audit(c, actor, "changed", after, None if made else exception, {"scope": "this", "series_id": series["id"]})
        occ = _instance_occurrence(c, series, original)
        if occ is None:
            raise Problem("not_found", "That instance is gone", 404)
        return occ | {"event_id": series["id"]}
    _stale(expected_sequence, series)
    if scope == "following" and CR.count_before(series, _original_local(series, original)) > 0:
        return _split(c, series, original, fields, actor, cal)
    if original:
        fields = _shifted(series, original, fields)
    after = _apply(c, series, fields, actor, cal["time_zone"])
    _drop_orphans(c, after)
    _audit(c, actor, "changed", after, series, {"scope": "all"})
    return _first(c, after) | {"event_id": after["id"]}


def _original_local(series: Row, original: str) -> datetime:
    inst = CR.instance_at(series, original)
    if inst is None:
        raise Problem("not_found", "The series has no instance at that time", 404)
    return CR.local_naive(inst["original_start_local"])


def _end_before(c: sqlite3.Connection, series: Row, original: str) -> tuple[str, int | None]:
    """Changes that end `series` just before its instance at `original`: COUNT shrinks, or UNTIL is set."""
    before = CR.count_before(series, _original_local(series, original))
    parts = CR.rule_parts(series["rrule"])
    if "COUNT" in parts:
        rrule = CR.with_parts(series["rrule"], COUNT=before)
        remaining = int(parts["COUNT"]) - before
    else:
        if series["all_day"]:
            until = (date.fromisoformat(original) - timedelta(days=1)).strftime("%Y%m%d")
        else:
            until = (CR.parse_z(original) - timedelta(seconds=1)).strftime("%Y%m%dT%H%M%SZ")
        rrule, remaining = CR.with_parts(series["rrule"], UNTIL=until, COUNT=None), None
    return rrule, remaining


def _split(c: sqlite3.Connection, series: Row, original: str, fields: Row, actor: str | None, cal: Row) -> Row:
    """`following`: end the old series just before this instance and start a new series from it."""
    old_rrule, remaining = _end_before(c, series, original)
    ended = {**series, "rrule": old_rrule, "until_at": CR.last_end({**series, "rrule": old_rrule})}
    ended |= {"sequence": series["sequence"] + 1, "updated_at": H.now()}
    _update(c, series["id"], {k: ended[k] for k in ("rrule", "until_at", "sequence", "updated_at")})
    # The new series starts as this instance did, then takes the edit.
    local = _original_local(series, original)
    if series["all_day"]:
        length = date.fromisoformat(series["ends_on"]) - date.fromisoformat(series["starts_on"])
        start = {"all_day": True, "start": local.date().isoformat(), "end": (local.date() + length).isoformat()}
    else:
        length = CR.local_naive(series["ends_at_local"]) - CR.local_naive(series["starts_at_local"])
        start = {"start": local.isoformat(), "end": (local + length).isoformat(), "time_zone": series["time_zone"]}
    base = {**series, **_times(start, series, cal["time_zone"])}
    if "repeat" in fields:
        rrule = CR.parse_repeat(fields["repeat"]) if fields["repeat"] else None
    elif remaining is not None:
        rrule = CR.with_parts(series["rrule"], COUNT=remaining)
    else:
        rrule = series["rrule"]
    at = H.now()
    row = {**base, **_content(fields, base), **_times(fields, base, cal["time_zone"])}
    row |= _rule_columns(row, rrule)
    row |= {"id": H.new_id(), "uid": H.new_id() + "@tico", "sequence": 0, "created_by": actor}
    row |= {"created_at": at, "updated_at": at, "recurring_event_id": None, "original_starts_at": None}
    _insert(c, {k: row[k] for k in series})
    if "attendees" in fields:
        keep = _responses(_attendee_rows(c, series["id"]))
        _write_attendees(c, row["id"], _attendee_items(c, fields["attendees"]), actor, keep)
    else:
        _copy_attendees(c, series["id"], row["id"])
    # Exceptions from here on belong to the new series while it still has their instance.
    for x in _exceptions(c, [series["id"]])[series["id"]]:
        if x["original_starts_at"] >= original and CR.instance_at(row, x["original_starts_at"]) is not None:
            _update(c, x["id"], {"recurring_event_id": row["id"], "uid": row["uid"]})
    _audit(c, actor, "changed", ended, series, {"scope": "following", "new_event_id": row["id"]})
    _audit(c, actor, "created", row, None, {"scope": "following", "from_event_id": series["id"]})
    return _first(c, row) | {"event_id": row["id"]}


def cancel_event(
    c: sqlite3.Connection,
    event_id: str,
    actor: str | None,
    *,
    original_starts_at: str | None = None,
    scope: str = "all",
) -> None:
    """Cancel an event, one instance (`this`), this and the following, or the whole series. Never deletes."""
    if scope not in ("this", "following", "all"):
        raise Problem("validation", "scope is this, following or all", 422)
    row = _must(c, event_id)
    if row["recurring_event_id"]:
        original_starts_at = original_starts_at or row["original_starts_at"]
        row = _must(c, row["recurring_event_id"])
    if row["status"] == "cancelled":
        return None
    at = H.now()
    if row["rrule"] and scope in ("this", "following"):
        if not original_starts_at:
            raise Problem("validation", f"Say which instance to cancel (original_starts_at) for scope {scope}", 422)
        if CR.instance_at(row, original_starts_at) is None:
            raise Problem("not_found", "The series has no instance at that time", 404)
        if scope == "this":
            exception, made = _exception_for(c, row, original_starts_at, actor)
            if exception["status"] == "cancelled":
                return None
            after = exception | {"status": "cancelled", "sequence": exception["sequence"] + 1, "updated_at": at}
            _update(c, exception["id"], {k: after[k] for k in ("status", "sequence", "updated_at")})
            _audit(c, actor, "cancelled", after, None if made else exception, {"scope": "this", "series_id": row["id"]})
            return None
        if CR.count_before(row, _original_local(row, original_starts_at)) > 0:
            rrule, _remaining = _end_before(c, row, original_starts_at)
            after = row | {"rrule": rrule, "until_at": CR.last_end({**row, "rrule": rrule})}
            after |= {"sequence": row["sequence"] + 1, "updated_at": at}
            _update(c, row["id"], {k: after[k] for k in ("rrule", "until_at", "sequence", "updated_at")})
            c.execute(
                "UPDATE calendar_events SET status='cancelled', sequence=sequence+1, updated_at=? "
                "WHERE recurring_event_id=? AND original_starts_at>=? AND status<>'cancelled'",
                (at, row["id"], original_starts_at),
            )
            _audit(c, actor, "cancelled", after, row, {"scope": "following", "from": original_starts_at})
            return None
    after = row | {"status": "cancelled", "sequence": row["sequence"] + 1, "updated_at": at}
    _update(c, row["id"], {k: after[k] for k in ("status", "sequence", "updated_at")})
    _audit(c, actor, "cancelled", after, row, {"scope": "all"})
    return None


def set_response(
    c: sqlite3.Connection, event_id: str, attendee_ref: object, response: str, *, original_starts_at: str | None = None
) -> None:
    """An attendee's reply, to the series or (with `original_starts_at`) one instance. The attendee is the actor."""
    if response not in RESPONSES:
        raise Problem("validation", "A response is needs_action, accepted, tentative or declined", 422)
    series, from_exception = _live(c, event_id)
    original = original_starts_at or from_exception
    actor = str(attendee_ref)
    kind, ident, email = _attendee_ref(attendee_ref)
    target = series
    if series["rrule"] and original:
        target, _made = _exception_for(c, series, original, actor)
        if target["status"] == "cancelled":
            raise Problem("not_found", "That instance was cancelled", 404)
    if kind == "external":
        where, value = "attendee_type='external' AND lower(email)=lower(?)", email
    else:
        where, value = "attendee_type=? AND attendee_id=?", None
    params = (target["id"], value) if kind == "external" else (target["id"], kind, ident)
    found = c.execute(
        f"SELECT * FROM calendar_event_attendees WHERE calendar_event_id=? AND {where}", params
    ).fetchone()
    if found is None:
        raise Problem("not_attendee", "Only an attendee can reply", 403)
    if found["response"] == response:
        return None
    c.execute(
        "UPDATE calendar_event_attendees SET response=?, updated_at=? WHERE id=?", (response, H.now(), found["id"])
    )
    audit_log.write(c, actor, "calendar_event:" + target["id"], {"response": found["response"]}, {"response": response})
    detail = {"calendar_id": series["calendar_id"], "response": response}
    H.event(c, actor, "calendar.event.changed", target["id"], detail)
    return None


# ----------------------------------------------------------------------------- search
def search(
    c: sqlite3.Connection,
    calendar_ids: Iterable[str],
    q: object,
    start_utc: datetime,
    end_utc: datetime,
    limit: int = 50,
    *,
    now: datetime | None = None,
) -> list[Row]:
    """Instances in the window whose title, notes or location match `q` (each word a prefix), nearest to now first."""
    ids = [i for i in dict.fromkeys(calendar_ids or ()) if i]
    expression = str(TS.match_expression(q))
    if not ids or not expression:
        return []
    marks = ",".join("?" * len(ids))
    rows = c.execute(
        "SELECT e.* FROM calendar_event_search s JOIN calendar_event_search_keys k ON k.id=s.rowid "
        f"JOIN calendar_events e ON e.id=k.calendar_event_id WHERE calendar_event_search MATCH ? "
        f"AND e.calendar_id IN ({marks}) AND e.status<>'cancelled' LIMIT {SEARCH_SCAN}",
        (expression, *ids),
    ).fetchall()
    matched = {r["id"] for r in rows}
    series_ids = {r["recurring_event_id"] or r["id"] for r in rows}
    series: list[Row] = []
    for sid in series_ids:
        row = _row(c, sid)
        if row and row["status"] != "cancelled":
            series.append(row)

    def keep(s: Row, inst: Row, source: Row) -> bool:
        # An instance matches through its own words: a changed exception that no longer matches is not a hit.
        return source["id"] in matched

    found = _expanded(c, series, start_utc, end_utc, keep)
    now = now or datetime.now(timezone.utc)
    found.sort(key=lambda o: abs((_start_utc(o) - now).total_seconds()))
    return found[: max(1, min(int(limit), 500))]


def _start_utc(occ: Row) -> datetime:
    if occ["all_day"]:
        return datetime.combine(date.fromisoformat(occ["start"]), time(), timezone.utc)
    return CR.parse_z(occ["start"])


# ----------------------------------------------------------------------------- absences
def absences(c: sqlite3.Connection, human_ids: Iterable[str], start_date: object, end_date: object) -> list[Row]:
    """Time away overlapping [start_date, end_date) as all-day `away` occurrences (end exclusive)."""
    ids = [i for i in dict.fromkeys(human_ids or ()) if i]
    if not ids:
        return []
    start, end = _date(start_date, "from"), _date(end_date, "to")
    marks = ",".join("?" * len(ids))
    rows = c.execute(
        f"SELECT a.*, h.name AS human_name, h.email AS human_email FROM absences a LEFT JOIN humans h ON h.id=a.human_id "
        f"WHERE a.human_id IN ({marks}) AND a.starts_on<? AND a.ends_on>=? ORDER BY a.starts_on, a.id",
        (*ids, end.isoformat(), start.isoformat()),
    ).fetchall()
    return [_away(r) for r in rows]


def _away(r: Any) -> Row:
    name = r["human_name"] or r["human_email"] or r["human_id"]
    return {
        "occurrence_id": "away:" + r["id"],
        "event_id": None,
        "calendar": {"ref": "human:" + r["human_id"], "type": "human", "id": r["human_id"], "name": name},
        "home": "tico",
        "kind": "away",
        "title": r["note"] or "Away",
        "start": r["starts_on"],
        "end": (date.fromisoformat(r["ends_on"]) + timedelta(days=1)).isoformat(),
        "all_day": True,
        "time_zone": None,
        "attendees": [],
        "busy_only": False,
        "recurring": False,
        "status": "confirmed",
        "visibility": "default",
        "meeting_url": None,
        "open_url": None,
        "links": {"thread": None, "meeting": None, "task": None, "automation": None, "attempt": None},
    }


def add_absence(
    c: sqlite3.Connection, human_id: str, starts_on: object, ends_on: object, note: object, actor: str | None
) -> Row:
    """Days away for a person, `starts_on` through `ends_on` (both days away)."""
    row = c.execute("SELECT id, name, email FROM humans WHERE id=?", (human_id,)).fetchone()
    if row is None:
        raise Problem("not_found", f"There is no person {human_id}", 404)
    start, end = _date(starts_on, "starts_on"), _date(ends_on or starts_on, "ends_on")
    if end < start:
        raise Problem("validation", "Time away ends on or after the day it starts", 422)
    if end - start > LONGEST:
        raise Problem("validation", "Time away is at most a year at a time", 422)
    note = _text({"note": note}, "note", 500)
    at, absence_id = H.now(), H.new_id()
    c.execute(
        "INSERT INTO absences(id, human_id, starts_on, ends_on, note, created_by, created_at, updated_at) "
        "VALUES(?,?,?,?,?,?,?,?)",
        (absence_id, human_id, start.isoformat(), end.isoformat(), note, actor, at, at),
    )
    core = {"human_id": human_id, "starts_on": start.isoformat(), "ends_on": end.isoformat(), "note": note}
    audit_log.write(c, actor, "absence:" + absence_id, None, core)
    H.event(c, actor, "calendar.absence.created", "absence:" + absence_id, {"human_id": human_id})
    stored = c.execute(
        "SELECT a.*, ? AS human_name, ? AS human_email FROM absences a WHERE a.id=?",
        (row["name"], row["email"], absence_id),
    ).fetchone()
    return _away(stored)


# ----------------------------------------------------------------------------- retention
def purge_cancelled(c: sqlite3.Connection, now: str | datetime) -> int:
    """Remove cancelled rows 90 days after they were cancelled: whole cancelled events and series (their exceptions,
    attendees and links with them), and cancelled exceptions whose instance the series no longer has. A cancelled
    exception of a live series stays while its instance exists, or the instance would come back. Returns rows removed."""
    now = now if isinstance(now, str) else now.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    cutoff = H.shift(now, days=-PURGE_DAYS)
    gone = 0
    heads = [
        r["id"]
        for r in c.execute(
            "SELECT id FROM calendar_events WHERE status='cancelled' AND recurring_event_id IS NULL AND updated_at<? "
            f"LIMIT {PURGE_BATCH}",
            (cutoff,),
        )
    ]
    for head in heads:
        ids = [
            head,
            *(r["id"] for r in c.execute("SELECT id FROM calendar_events WHERE recurring_event_id=?", (head,))),
        ]
        _purge_rows(c, ids)
        gone += len(ids)
    orphans = c.execute(
        "SELECT id, original_starts_at, recurring_event_id FROM calendar_events "
        f"WHERE recurring_event_id IS NOT NULL AND status='cancelled' AND updated_at<? LIMIT {PURGE_BATCH}",
        (cutoff,),
    ).fetchall()
    for r in orphans:
        series = _row(c, r["recurring_event_id"])
        if series is not None and CR.instance_at(series, r["original_starts_at"]) is None:
            _purge_rows(c, [r["id"]])
            gone += 1
    return gone


def _purge_rows(c: sqlite3.Connection, ids: list[str]) -> None:
    marks = ",".join("?" * len(ids))
    c.execute(f"DELETE FROM calendar_event_links WHERE tico_event_id IN ({marks})", ids)
    c.execute(f"DELETE FROM calendar_event_attendees WHERE calendar_event_id IN ({marks})", ids)
    c.execute(f"DELETE FROM calendar_events WHERE recurring_event_id IN ({marks})", ids)
    c.execute(f"DELETE FROM calendar_events WHERE id IN ({marks})", ids)

# pyright: strict
"""The Calendar's HTTP routes, `/api/v2/calendar/*` (docs/dev/calendar-contract.md, HTTP and Access; spec 6, 8, 12, 13).

One way in for every home. Reads merge, per calendar in `who`:
- Google (backend/gcal_live.py), read live and stored nowhere. A person reads another person's calendar as themselves
  (their own address, the other address as the calendar id), so Google's sharing decides. A bot reads a Google calendar
  only through a level it holds on that mailbox, or its operator's (connectors.calendar_allowed). A calendar Google will
  not answer lands in `errors`, never as an empty, free-looking day.
- Tico (backend/calendar_store.py): bots, shared calendars, people without Google. Everyone in the company reads them;
  a private event shows as Busy to everyone but its owner and attendees.
- Derived (backend/calendar_derived.py): bot runs, planned automation runs, due dates. Runs show only for a bot in view: a
  person's calendar does not add the bots that report to them.

Writes route by home (spec 8.2). People view; bots and the Assistant write (decision 13), except a person with no
Google writing their own Tico calendar. A bot booking people passes `reach.may_book` for each person; a booking that
includes a connected person lands on Google (that person's calendar; with several, the bot's manager's when on it,
else the first connected one), otherwise on Tico. A Google write happens outside the write lock, then its receipt,
audit and live event are recorded with `mutate`; a Tico write is one `mutate`. Every Tico calendar change is a row on
the `calendar` live topic (backend/events.py triggers); a Google write adds one by hand.
"""

import logging
import time as clock
from collections.abc import Callable, Iterable
from concurrent.futures import Future, ThreadPoolExecutor, wait
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Literal, cast
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import Request
from pydantic import Field

from . import audit_log as _audit_log
from . import calendar_derived as _derived
from . import calendar_store as _store
from . import connectors as _connectors
from . import gcal_live as _gcal
from . import google_workspace as _workspace
from . import groups as _groups
from . import people as _people
from . import reach as _reach
from .models import Contract
from .store import H as _H, Problem

# Untyped modules, used through Any (as calendar_store does). gcal_live is looked up on each call, so tests replace its
# functions.
H: Any = _H
audit_log: Any = _audit_log
connectors: Any = _connectors
workspace: Any = _workspace
groups: Any = _groups
people_mod: Any = _people
reach: Any = _reach
gcal: Any = _gcal
derived: Any = _derived
cal_store: Any = _store

Row = dict[str, Any]
log = logging.getLogger(__name__)

WINDOW_MAX = timedelta(days=62)
DEFAULT_WINDOW = timedelta(days=7)
RUN_DETAIL_MAX = timedelta(days=7)  # longer windows answer runs as counts per bot per day
WHO_MAX = 20
TEAM_MAX = 50  # calendars a team expands to
KINDS = ("event", "run", "due", "away")
GOOGLE_WORKERS = 8
SEARCH_SECONDS = 3.0  # Google's share of one search, all calendars together
SEARCH_CALENDARS = 24
SEARCH_BACK, SEARCH_AHEAD = timedelta(days=90), timedelta(days=180)
SEARCH_RUNS_BACK = timedelta(days=31)
SEARCH_LIMIT = 100
MEETING_LINKS = 200
LIST_MAX = 500
BOT_HOSTS = 10  # host Google calendars read for one bot's tagged events
FREE_SLOTS = 50
ZONE_SECONDS = 3600
WORK_STARTS, WORK_ENDS, WORK_DAYS = "08:00", "18:00", "mon,tue,wed,thu,fri"
DAY_NAMES = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
NO_LINKS: Row = {"thread": None, "meeting": None, "task": None, "automation": None, "attempt": None}
VIEW_ONLY = "People view the calendar; ask your Assistant or a bot to add or change an event, or open it in Google"
COMPANY_OFF = "Changing calendars is off for this company (Tools > Google > What Tico may do)"
_ZONES: dict[str, tuple[float, str | None]] = {}


# ----------------------------------------------------------------------------- bodies
class EventFields(Contract):
    title: str | None = Field(default=None, max_length=500)
    start: str | None = Field(default=None, max_length=40)
    end: str | None = Field(default=None, max_length=40)
    all_day: bool | None = None
    time_zone: str | None = Field(default=None, max_length=64)
    repeat: str | None = Field(default=None, max_length=500)
    attendees: list[str] | None = Field(default=None, max_length=200)
    notes: str | None = Field(default=None, max_length=20_000)
    location: str | None = Field(default=None, max_length=1000)
    add_meet: bool | None = None
    visibility: Literal["default", "private"] | None = None
    task_id: str | None = Field(default=None, max_length=200)
    thread_id: str | None = Field(default=None, max_length=200)


class EventCreate(EventFields):
    calendar: str | None = Field(default=None, max_length=600)


class EventChange(EventFields):
    scope: Literal["this", "following", "all"] | None = None
    expected_sequence: int | None = None


class EventCancel(Contract):
    scope: Literal["this", "following", "all"] | None = None


class EventResponse(Contract):
    response: Literal["needs_action", "accepted", "tentative", "declined"]
    scope: Literal["this", "all"] | None = None


class CalendarSettings(Contract):
    time_zone: str | None = Field(default=None, max_length=64)
    work_starts: str | None = Field(default=None, max_length=5)
    work_ends: str | None = Field(default=None, max_length=5)
    work_days: str | None = Field(default=None, max_length=40)
    record_on_join: bool | None = None


class SharedCreate(Contract):
    name: str = Field(min_length=1, max_length=200)
    time_zone: str = Field(default="UTC", max_length=64)


class SharedAccess(Contract):
    grantee: str = Field(min_length=3, max_length=300)
    level: Literal["read", "read_write"] | None = None


class AbsenceCreate(Contract):
    human: str | None = Field(default=None, max_length=200)
    starts_on: str = Field(min_length=10, max_length=10)
    ends_on: str | None = Field(default=None, max_length=10)
    note: str | None = Field(default=None, max_length=500)


# ----------------------------------------------------------------------------- times
def _rows(value: object) -> list[Row]:
    return cast("list[Row]", value) if isinstance(value, list) else []


def _map(value: object) -> Row:
    return cast(Row, value) if isinstance(value, dict) else {}


def _utc(at: datetime) -> datetime:
    return at.replace(tzinfo=timezone.utc) if at.tzinfo is None else at.astimezone(timezone.utc)


def _z(at: datetime) -> str:
    return _utc(at).strftime("%Y-%m-%dT%H:%M:%SZ")


def _instant(value: object, field: str) -> datetime:
    """An ISO 8601 time (a naive one is UTC) or a date (its UTC midnight)."""
    text = str(value or "").strip()
    try:
        if len(text) == 10:
            return datetime.combine(date.fromisoformat(text), time(), timezone.utc)
        return _utc(datetime.fromisoformat(text.replace("Z", "+00:00")))
    except ValueError:
        raise Problem("range", f"{field} is not an ISO 8601 time or a date", 422) from None


def _window(start: object, end: object, limit: timedelta = WINDOW_MAX) -> tuple[datetime, datetime]:
    lo = _instant(start, "from") if start else datetime.combine(datetime.now(timezone.utc).date(), time(), timezone.utc)
    hi = _instant(end, "to") if end else lo + DEFAULT_WINDOW
    if hi <= lo:
        raise Problem("range", "to must be after from", 422)
    if hi - lo > limit:
        raise Problem("range", f"A calendar window is at most {limit.days} days", 422)
    return lo, hi


def _occ_start(occ: Row) -> datetime:
    value = str(occ.get("start") or "")
    try:
        if len(value) == 10:
            return datetime.combine(date.fromisoformat(value), time(), timezone.utc)
        return _utc(datetime.fromisoformat(value.replace("Z", "+00:00")))
    except ValueError:
        return datetime.min.replace(tzinfo=timezone.utc)


def _occ_end(occ: Row) -> datetime:
    value = str(occ.get("end") or "")
    try:
        if len(value) == 10:
            return datetime.combine(date.fromisoformat(value), time(), timezone.utc)
        return _utc(datetime.fromisoformat(value.replace("Z", "+00:00")))
    except ValueError:
        return _occ_start(occ)


def _zone(name: object) -> ZoneInfo | None:
    try:
        return ZoneInfo(str(name)) if name else None
    except (ZoneInfoNotFoundError, ValueError):
        return None


def _clock(value: object, field: str) -> str:
    text = str(value or "").strip()
    try:
        parsed = time.fromisoformat(text) if len(text) == 5 else None
    except ValueError:
        parsed = None
    if parsed is None:
        raise Problem("validation", f"{field} is a time like 08:00", 422)
    return parsed.strftime("%H:%M")


def _days(value: object) -> str:
    parts = [p.strip().lower()[:3] for p in str(value or "").split(",") if p.strip()]
    if not parts or any(p not in DAY_NAMES for p in parts):
        raise Problem("validation", "work_days lists days like mon,tue,wed", 422)
    return ",".join(d for d in DAY_NAMES if d in parts)


# ----------------------------------------------------------------------------- who is asking
def _caller(c: Any, who: Any) -> Row:
    """The caller as a calendar party: a person (`person`), a person's Assistant (`assistant`) or a bot (`bot`)."""
    role = str(getattr(who, "role", ""))
    if role in ("owner", "human"):
        pid = str(H.actor_id(who.actor))
        human = _map(H.human(c, pid))
        email = str(human.get("email") or getattr(who, "email", "") or "").strip().lower()
        kind = "assistant" if getattr(who, "via", "") == "assistant" else "person"
        return {"kind": kind, "ref": "human:" + pid, "id": pid, "email": email, "actor": who.actor}
    if role == "bot":
        bot = H.resolve_bot(c, H.actor_id(who.actor))
        if not bot:
            raise Problem("identity", "This bot is not on the team", 403)
        return {"kind": "bot", "ref": "bot:" + bot["id"], "id": bot["id"], "email": "", "actor": who.actor}
    raise Problem("identity", "The calendar is for people and bots", 403)


def _viewer_google(c: Any, caller: Row) -> str | None:
    """The caller's own Google address when their home is Google (people and their Assistant only)."""
    if caller["kind"] == "bot" or not caller["email"]:
        return None
    return caller["email"] if gcal.connected(c, caller["email"]) else None


# ----------------------------------------------------------------------------- calendars
def _obj(cal: Row) -> Row:
    return {"ref": cal["ref"], "type": cal["type"], "id": cal["id"], "name": cal["name"]}


def _person(c: Any, pid: str) -> Row | None:
    row = c.execute("SELECT * FROM humans WHERE id=?", (pid,)).fetchone()
    if not row:
        return None
    human = dict(row)
    email = str(human.get("email") or "").strip().lower()
    home = "google" if email and gcal.connected(c, email) else "tico"
    name = human.get("name") or email or pid
    return {"ref": "human:" + pid, "type": "human", "id": pid, "name": name, "home": home, "email": email, "row": human}


def _bot(c: Any, ident: str) -> Row | None:
    bot = H.resolve_bot(c, ident)
    if not bot:
        return None
    name = bot.get("display_name") or bot.get("slug") or bot["id"]
    return {
        "ref": "bot:" + bot["id"],
        "type": "bot",
        "id": bot["id"],
        "slug": bot.get("slug"),
        "name": name,
        "home": "tico",
    }


def _shared(c: Any, ident: str) -> Row | None:
    row = cal_store.get(c, ident)
    if not row or row["owner_type"] != "shared":
        return None
    return {
        "ref": "shared:" + row["id"],
        "type": "shared",
        "id": row["id"],
        "name": row["name"],
        "home": "tico",
        "row": row,
    }


def _google(ident: str, name: str | None = None) -> Row:
    return {"ref": "google:" + ident, "type": "google", "id": ident, "name": name or ident, "home": "google"}


def _team(c: Any, gid: str) -> list[Row]:
    """A team's members, its sub-teams' too: people first, then bots."""
    roster = groups._roster(c)  # pyright: ignore[reportPrivateUsage]
    found = cast(
        "list[Row]",
        groups.rows(roster, groups._configs(c), groups._archived(c)),  # pyright: ignore[reportPrivateUsage]
    )
    if not any(g["id"] == gid for g in found):
        raise Problem("not_found", f"There is no team {gid}", 404)
    parents = {g["id"]: g.get("parent") for g in found}

    def within(group_id: str) -> bool:
        seen: set[str] = set()
        current: str | None = group_id
        while current and current not in seen:
            if current == gid:
                return True
            seen.add(current)
            current = parents.get(current)
        return False

    out: list[Row] = []
    for g in found:
        if within(g["id"]):
            out += [cal for pid in _rows(g.get("people")) if (cal := _person(c, str(pid)))]
            out += [cal for slug in _rows(g.get("bots")) if (cal := _bot(c, str(slug)))]
    return out[:TEAM_MAX]


def _resolve(c: Any, caller: Row, ref: object) -> list[Row]:
    """The calendars a ref names (a team names several)."""
    text = str(ref or "").strip()
    if text in ("", "me"):
        found = _person(c, caller["id"]) if caller["kind"] != "bot" else _bot(c, caller["id"])
        if not found:
            raise Problem("not_found", "You have no calendar", 404)
        return [found]
    kind, sep, ident = text.partition(":")
    ident = ident.strip()
    single: Row | None = None
    if sep and kind == "human":
        single = _person(c, ident)
    elif sep and kind == "bot":
        single = _bot(c, ident)
    elif sep and kind == "shared":
        single = _shared(c, ident)
    elif sep and kind in ("team", "group"):
        return _team(c, ident)
    elif sep and kind == "google" and ident:
        return [_google(ident)]
    elif "@" in text:
        address = text.lower()
        row = c.execute("SELECT id FROM humans WHERE lower(email)=?", (address,)).fetchone()
        if row:
            single = _person(c, row["id"])
        else:
            owner = connectors.human_of(c, address)
            if owner and owner["id"]:
                single = _person(c, owner["id"])
            elif gcal.connected(c, address):
                single = _google(address)  # a shared mailbox's calendar, such as legal@
    elif not sep:
        single = _person(c, text) or _bot(c, text)
    if single is None:
        raise Problem("not_found", f"No calendar is called {text}", 404)
    return [single]


def _resolve_all(c: Any, caller: Row, refs: Iterable[str]) -> tuple[list[Row], list[Row]]:
    cals: list[Row] = []
    errors: list[Row] = []
    seen: set[str] = set()
    for ref in refs:
        try:
            found = _resolve(c, caller, ref)
        except Problem as refused:
            if refused.status != 404:
                raise
            errors.append({"ref": ref, "reason": refused.detail})
            continue
        for cal in found:
            if cal["ref"] not in seen:
                seen.add(cal["ref"])
                cals.append(cal)
    return cals, errors


def _refs(value: object) -> list[str]:
    refs = [part.strip() for part in str(value or "").split(",") if part.strip()] or ["me"]
    if len(refs) > WHO_MAX:
        raise Problem("validation", f"At most {WHO_MAX} calendars at a time", 422)
    return list(dict.fromkeys(refs))


def _kinds(value: object) -> set[str]:
    asked = {part.strip() for part in str(value or "").split(",") if part.strip()}
    unknown = asked - set(KINDS)
    if unknown:
        raise Problem("validation", "kinds are event, run, due and away", 422)
    return asked or set(KINDS)


def _tico_id(c: Any, cal: Row) -> str | None:
    """The calendar's Tico row id, without making one (a read never creates rows)."""
    if cal["type"] == "shared":
        return cal["id"]
    if cal["type"] in ("human", "bot"):
        row = cal_store.calendar_for(c, cal["type"], cal["id"], create=False)
        return row["id"] if row else None
    return None


def _google_zone(c: Any, address: str) -> str | None:
    """A connected person's Google time zone, kept an hour: one Google call per person, not per read."""
    held = _ZONES.get(address)
    if held and held[0] > clock.monotonic():
        return held[1]
    try:
        zone = gcal.time_zone(c, address)
    except Problem:
        zone = None
    _ZONES[address] = (clock.monotonic() + ZONE_SECONDS, zone)
    return zone


def _settings_of(c: Any, cal: Row) -> Row:
    """time_zone and working hours of a person (their own or the defaults); a bot works every hour of every day."""
    if cal["type"] != "human":
        zone = _map(cal.get("row")).get("time_zone") or "UTC"
        return {"time_zone": zone, "work_starts": None, "work_ends": None, "work_days": None}
    human = _map(cal.get("row"))
    zone = human.get("time_zone")
    if not zone and cal["home"] == "google":
        zone = _google_zone(c, cal["email"])
    return {
        "time_zone": zone or "UTC",
        "work_starts": human.get("work_starts") or WORK_STARTS,
        "work_ends": human.get("work_ends") or WORK_ENDS,
        "work_days": human.get("work_days") or WORK_DAYS,
    }


def _view(c: Any, auth: Any, who: Any, caller: Row, cal: Row) -> Row:
    out = {**_obj(cal), "home": cal["home"]}
    if cal["type"] == "google":
        out |= {"time_zone": None, "work_starts": None, "work_ends": None, "work_days": None}
    else:
        out |= _settings_of(c, cal)
    out["can_write"] = _write_refusal(c, auth, who, caller, cal) is None
    return out


# ----------------------------------------------------------------------------- who may write
def _groups_of(c: Any, caller: Row) -> set[str]:
    roster = groups._roster(c)  # pyright: ignore[reportPrivateUsage]
    found = cast(
        "list[Row]",
        groups.rows(roster, groups._configs(c), groups._archived(c)),  # pyright: ignore[reportPrivateUsage]
    )
    key = "people" if caller["kind"] != "bot" else "bots"
    return {g["id"] for g in found if caller["id"] in (g.get(key) or [])}


def _granted(c: Any, caller: Row, calendar_id: str | None) -> bool:
    """Read and write given on a Tico calendar to the caller, or to a team they are in."""
    if not calendar_id:
        return False
    kind = "bot" if caller["kind"] == "bot" else "human"
    for row in cal_store.accesses(c, calendar_id):
        if row["level"] != "read_write":
            continue
        if row["grantee_type"] == kind and row["grantee_id"] == caller["id"]:
            return True
        if row["grantee_type"] == "group" and row["grantee_id"] in _groups_of(c, caller):
            return True
    return False


def _is_admin(c: Any, auth: Any, who: Any) -> bool:
    from . import teammate_access

    access: Any = teammate_access
    return bool(access.is_admin(c, who, auth))


def _company_writes(c: Any) -> Problem | None:
    if not workspace.permissions(c)["change_calendar"]:
        return Problem("company_off", COMPANY_OFF, 403)
    return None


def _mailbox_write(c: Any, who: Any, address: str, person: Row | None = None) -> Problem | None:
    try:
        connectors.calendar_allowed(c, who, address, person, change=True)
    except Problem as refused:
        return refused
    return _company_writes(c)


def _write_refusal(c: Any, auth: Any, who: Any, caller: Row, cal: Row) -> Problem | None:
    """Why the caller may not write this calendar, or None (spec 13). Booking a person is the bot's to ask of
    reach.may_book, checked by the write itself."""
    kind = caller["kind"]
    if kind == "person":
        if cal["type"] == "human" and cal["id"] == caller["id"] and cal["home"] == "tico":
            return None
        return Problem("view_only", VIEW_ONLY, 403)
    if cal["type"] == "human":
        if cal["home"] == "google":
            if kind == "bot":
                return _company_writes(c)
            return _mailbox_write(c, who, cal["email"], cal.get("row"))
        if kind == "bot" or cal["id"] == caller["id"] or _granted(c, caller, _tico_id(c, cal)):
            return None
        return Problem("forbidden", f"{cal['name']}'s calendar is not yours to change", 403)
    if cal["type"] == "bot":
        if kind == "bot" and cal["id"] == caller["id"]:
            return None
        if kind == "assistant" and auth.bot_manager(c, who, cal["id"]):
            return None
        if _granted(c, caller, _tico_id(c, cal)):
            return None
        return Problem("forbidden", f"Only {cal['name']} and its managers change its calendar", 403)
    if cal["type"] == "shared":
        if kind == "assistant" and _is_admin(c, auth, who):
            return None
        if _granted(c, caller, cal["id"]):
            return None
        return Problem("forbidden", f"{cal['name']} needs Read and write for you to change it", 403)
    return _mailbox_write(c, who, cal["id"])


# ----------------------------------------------------------------------------- reading Google
def _people_map(c: Any) -> dict[str, Row]:
    out: dict[str, Row] = {}
    for row in c.execute("SELECT id, name, email FROM humans WHERE email IS NOT NULL AND email<>''"):
        out[str(row["email"]).strip().lower()] = {"type": "human", "id": row["id"], "name": row["name"] or row["email"]}
    return out


def _google_source(c: Any, who: Any, caller: Row, viewer: str | None, cal: Row) -> Row:
    """How to read one Google-home calendar for this caller: {as, calendar_id, mode} or {error}."""
    address = cal["email"] if cal["type"] == "human" else cal["id"]
    if caller["kind"] == "bot":
        try:
            connectors.calendar_allowed(c, who, address, cal.get("row"))
        except Problem as refused:
            return {"error": refused.detail}
        return {"as": address, "calendar_id": address, "mode": "events"}
    if viewer:
        return {"as": viewer, "calendar_id": address, "mode": "events"}
    if cal["type"] == "human":
        # A person without Google of their own: the other person's busy times, read as that mailbox.
        return {"as": address, "calendar_id": address, "mode": "busy"}
    return {"error": "Connect Google to read Google calendars"}


def _fetch(store: Any, source: Row, start: datetime, end: datetime, people: dict[str, Row]) -> list[Row]:
    """One Google read, on its own read connection (it runs on a worker thread)."""
    with store.read() as c:
        if source["mode"] == "busy":
            found = gcal.freebusy(c, source["as"], [source["calendar_id"]], start, end)
            if source["calendar_id"] not in found:
                raise Problem("gcal_refused", "Google Calendar shares nothing of this calendar", 403)
            return [{**_busy(source, lo, hi)} for lo, hi in cast("list[tuple[str, str]]", found[source["calendar_id"]])]
        return cast(
            "list[Row]",
            gcal.events(
                c,
                source["as"],
                source["calendar_id"],
                start,
                end,
                q=source.get("q"),
                calendar=source.get("calendar"),
                people=people,
                bot=source.get("bot"),
                limit=source.get("limit", 250),
            ),
        )


def _busy(source: Row, start: str, end: str) -> Row:
    return {
        "occurrence_id": f"google:{source['calendar_id']}:busy:{start}",
        "event_id": None,
        "calendar": source.get("calendar"),
        "home": "google",
        "kind": "event",
        "title": "Busy",
        "start": start,
        "end": end,
        "all_day": False,
        "time_zone": None,
        "attendees": [],
        "response": None,
        "busy_only": True,
        "recurring": False,
        "status": "confirmed",
        "meeting_url": None,
        "open_url": None,
        "links": dict(NO_LINKS),
        "can": {"join": False, "edit": False},
        "original_starts_at": start,
    }


def _reason(error: BaseException) -> str:
    if isinstance(error, Problem):
        return str(getattr(error, "detail", ""))
    log.warning("calendar: a Google read failed", exc_info=error)
    return "Google Calendar could not be read; try again"


def _run_google(
    store: Any,
    sources: list[Row],
    start: datetime,
    end: datetime,
    people: dict[str, Row],
    budget: float | None = None,
) -> tuple[list[Row], list[Row], bool]:
    """Read the sources GOOGLE_WORKERS at a time: (occurrences, errors, partial). With `budget` (seconds), what is not
    back by then is left out and `partial` is set."""
    if not sources:
        return [], [], False
    occurrences: list[Row] = []
    errors: list[Row] = []
    pool = ThreadPoolExecutor(max_workers=min(GOOGLE_WORKERS, len(sources)), thread_name_prefix="calendar-google")
    futures: dict[Future[list[Row]], Row] = {
        pool.submit(_fetch, store, source, source.get("start", start), source.get("end", end), people): source
        for source in sources
    }
    done, pending = wait(futures, timeout=budget)
    pool.shutdown(wait=False, cancel_futures=True)
    for future in done:
        source = futures[future]
        error = future.exception()
        if error is not None:
            errors.append({"ref": source["ref"], "reason": _reason(error)})
            continue
        found = future.result()
        keep = source.get("keep")
        occurrences += [o for o in found if keep is None or keep(o)]
    return occurrences, errors, bool(pending)


# ----------------------------------------------------------------------------- Tico and derived
def _attending_copies(c: Any, cals: list[Row], start: datetime, end: datetime) -> list[Row]:
    """Tico events on someone else's calendar that a person or bot in view attends (a bot booked them), shown on the
    attendee's calendar too, as Google shows an invitation on each attendee's own calendar."""
    out: list[Row] = []
    for cal in cals:
        if cal["type"] not in ("human", "bot"):
            continue
        own = _tico_id(c, cal)
        hosts = [
            r["calendar_id"]
            for r in c.execute(
                "SELECT DISTINCT e.calendar_id FROM calendar_event_attendees a "
                "JOIN calendar_events e ON e.id=a.calendar_event_id "
                "WHERE a.attendee_type=? AND a.attendee_id=? AND e.status<>'cancelled' LIMIT 200",
                (cal["type"], cal["id"]),
            )
            if r["calendar_id"] != own
        ]
        for occ in cast("list[Row]", cal_store.occurrences(c, hosts, start, end)):
            if any(a["type"] == cal["type"] and a["id"] == cal["id"] for a in occ["attendees"]):
                out.append({**occ, "calendar": _obj(cal)})
    return out


def _bot_hosts(c: Any, bot_id: str) -> list[str]:
    """The Google calendars that hold events Tico wrote for this bot (calendar_event_links, `bot`)."""
    rows = c.execute(
        "SELECT google_calendar_id, MAX(created_at) AS at FROM calendar_event_links "
        "WHERE link_type='bot' AND link_id=? AND google_calendar_id IS NOT NULL "
        f"GROUP BY google_calendar_id ORDER BY at DESC LIMIT {BOT_HOSTS}",
        (bot_id,),
    ).fetchall()
    return [r["google_calendar_id"] for r in rows]


# ----------------------------------------------------------------------------- one occurrence for one viewer
def _fill_links(c: Any, occurrences: list[Row]) -> None:
    """links.meeting/thread/task from calendar_event_links: a Tico event by its id, a Google one by its iCalUID, each
    with the instance's original start (a link without one holds for the whole series)."""
    tico = {o["event_id"] for o in occurrences if o.get("home") == "tico" and o.get("event_id")}
    uids = {o["ical_uid"] for o in occurrences if o.get("home") == "google" and o.get("ical_uid")}
    if not tico and not uids:
        return
    found: dict[tuple[str, str | None], dict[str, str]] = {}
    for column, values in (("tico_event_id", sorted(tico)), ("ical_uid", sorted(uids))):
        for at in range(0, len(values), 500):
            chunk = values[at : at + 500]
            rows = c.execute(
                f"SELECT l.*, m.imported_meeting_id FROM calendar_event_links l "
                f"LEFT JOIN live_meetings m ON l.link_type='meeting' AND m.id=l.link_id "
                f"WHERE l.{column} IN ({','.join('?' * len(chunk))}) AND l.link_type IN ('meeting','thread','task') "
                f"ORDER BY l.created_at",
                chunk,
            ).fetchall()
            for r in rows:
                link = r["imported_meeting_id"] or r["link_id"] if r["link_type"] == "meeting" else r["link_id"]
                found.setdefault((r[column], r["original_starts_at"]), {})[r["link_type"]] = link
    for occ in occurrences:
        key = occ.get("event_id") if occ.get("home") == "tico" else occ.get("ical_uid")
        if not key:
            continue
        original = occ.get("original_starts_at")
        mine = {**found.get((key, None), {}), **(found.get((key, original), {}) if original else {})}
        if mine:
            occ["links"] = {**NO_LINKS, **_map(occ.get("links")), **mine}


def _hide(occ: Row, title: str = "Busy") -> None:
    occ.update(
        title=title,
        busy_only=True,
        attendees=[],
        notes=None,
        location=None,
        meeting_url=None,
        open_url=None,
        links=dict(NO_LINKS),
        response=None,
    )


def _decorate(c: Any, auth: Any, who: Any, caller: Row, occurrences: list[Row]) -> list[Row]:
    """The viewer's fields: hidden private details, `response`, `can`, the links; internal keys dropped."""
    _fill_links(c, occurrences)
    may: dict[str, bool] = {}
    bot_links: set[str] = set()
    if caller["kind"] == "bot":
        bot_links = {
            r["ical_uid"]
            for r in c.execute(
                "SELECT ical_uid FROM calendar_event_links WHERE link_type='bot' AND link_id=? AND ical_uid IS NOT NULL",
                (caller["id"],),
            )
        }

    bot_made: set[str] = set()
    if caller["kind"] == "bot":
        ids = [o["event_id"] for o in occurrences if o.get("home") == "tico" and o.get("event_id")]
        for i in range(0, len(ids), 500):
            chunk = ids[i : i + 500]
            bot_made |= {
                r["id"]
                for r in c.execute(
                    f"SELECT id FROM calendar_events WHERE created_by=? AND id IN ({','.join('?' * len(chunk))})",
                    (caller["actor"], *chunk),
                )
            }

    def cal_rows(cal: Row) -> Row:
        return cast(Row, _cal_of_ref(c, cal["ref"]))

    def writable(cal: Row) -> bool:
        if cal["ref"] not in may:
            try:
                found = _cal_of_ref(c, cal["ref"])
                may[cal["ref"]] = found is not None and _write_refusal(c, auth, who, caller, found) is None
            except Problem:
                may[cal["ref"]] = False
        return may[cal["ref"]]

    out: list[Row] = []
    for occ in occurrences:
        occ = dict(occ)
        home, cal = occ.get("home"), cast(Row, occ.get("calendar") or {})
        mine = cal.get("ref") == caller["ref"]
        attending = any(
            a.get("type") == caller["ref"].split(":")[0] and a.get("id") == caller["id"]
            for a in _rows(occ.get("attendees"))
        )
        if home == "tico" and occ.get("kind") == "event":
            if occ.get("visibility") == "private" and not mine and not attending:
                _hide(occ)
            elif attending:
                occ["response"] = next(
                    a["response"]
                    for a in occ["attendees"]
                    if a.get("type") == caller["ref"].split(":")[0] and a.get("id") == caller["id"]
                )
            occ.setdefault("response", None)
            edit = not occ.get("busy_only") and writable(cal)
            if edit and caller["kind"] == "bot" and str(cal.get("ref", "")).startswith("human:"):
                # Same rule as _tico_target: on a person's calendar a bot changes only what it added, or with Read and write.
                edit = occ.get("event_id") in bot_made or _granted(c, caller, _tico_id(c, cal_rows(cal)))
        elif home == "derived":
            if occ.get("visibility") == "private" and not mine:
                _hide(occ, "Private")
                occ["busy_only"] = False
            edit = False
        else:  # google
            edit = bool(_map(occ.get("can")).get("edit")) and caller["kind"] != "person"
            edit = edit or (caller["kind"] == "bot" and occ.get("ical_uid") in bot_links)
        occ.setdefault("response", None)
        occ["can"] = {"join": bool(occ.get("meeting_url")) and not occ.get("busy_only"), "edit": bool(edit)}
        occ["links"] = {**NO_LINKS, **_map(occ.get("links"))}
        occ.pop("calendar_id", None)
        out.append(occ)
    return out


def _cal_of_ref(c: Any, ref: str) -> Row | None:
    kind, _, ident = ref.partition(":")
    if kind == "human":
        return _person(c, ident)
    if kind == "bot":
        return _bot(c, ident)
    if kind == "shared":
        return _shared(c, ident)
    if kind == "google":
        return _google(ident)
    return None


def _sorted(occurrences: list[Row]) -> list[Row]:
    return sorted(occurrences, key=lambda o: (_occ_start(o), str(o.get("occurrence_id"))))


# ----------------------------------------------------------------------------- the read
def _gather(
    store: Any,
    auth: Any,
    who: Any,
    refs: list[str],
    start: datetime,
    end: datetime,
    kinds: set[str],
    group: bool = False,
) -> Row:
    now = datetime.now(timezone.utc)
    with store.read() as c:
        caller = _caller(c, who)
        cals, errors = _resolve_all(c, caller, refs)
        viewer = _viewer_google(c, caller)
        people = _people_map(c)
        found: list[Row] = []
        sources: list[Row] = []
        if kinds & {"event", "away"}:
            for cal in cals:
                if cal["home"] == "google":
                    source = _google_source(c, who, caller, viewer, cal)
                    if "error" in source:
                        errors.append({"ref": cal["ref"], "reason": source["error"]})
                    else:
                        sources.append({**source, "ref": cal["ref"], "calendar": _obj(cal)})
                if cal["type"] == "bot":
                    for host in _bot_hosts(c, cal["id"]):
                        sources.append(
                            {
                                "as": host,
                                "calendar_id": host,
                                "mode": "events",
                                "bot": cal["id"],
                                "ref": cal["ref"],
                                "calendar": _obj(cal),
                            }
                        )
        if "event" in kinds:
            ids = [i for i in (_tico_id(c, cal) for cal in cals) if i]
            found += cast("list[Row]", cal_store.occurrences(c, ids, start, end))
            found += _attending_copies(c, cals, start, end)
        if "away" in kinds:
            humans = [cal["id"] for cal in cals if cal["type"] == "human"]
            found += cast(
                "list[Row]",
                cal_store.absences(c, humans, start.date().isoformat(), (end + timedelta(days=1)).date().isoformat()),
            )
        if "due" in kinds:
            owners = [(cal["type"], cal["id"]) for cal in cals if cal["type"] in ("human", "bot")]
            found += cast("list[Row]", derived.due(c, owners, start, end))
        month_counts: list[Row] | None = None
        if "run" in kinds:
            # Only a bot in view brings its runs: a person's calendar no longer adds the bots that report to them.
            bots = list(dict.fromkeys(cal["id"] for cal in cals if cal["type"] == "bot"))
            if bots:
                found += cast("list[Row]", derived.scheduled(c, bots, start, end, now))
                if end - start <= RUN_DETAIL_MAX:
                    runs = cast("list[Row]", derived.runs(c, bots, start, end, now))
                    found += cast("list[Row]", derived.group_runs(runs)) if group else runs
                else:
                    zone = (
                        _settings_of(c, _person(c, caller["id"]) or {"type": "bot"})["time_zone"]
                        if caller["kind"] != "bot"
                        else "UTC"
                    )
                    month_counts = cast("list[Row]", derived.month_counts(c, bots, start, end, _zone(zone)))
        google, google_errors, _partial = _run_google(store, sources, start, end, people)
        errors += google_errors
        found += [o for o in google if o.get("kind") in kinds]
        occurrences = _decorate(c, auth, who, caller, found)
        answer: Row = {
            "from": _z(start),
            "to": _z(end),
            "calendars": [_view(c, auth, who, caller, cal) for cal in cals],
            "occurrences": _sorted(occurrences),
            "errors": errors,
        }
        if month_counts is not None:
            answer["month_counts"] = month_counts
        return answer


# ----------------------------------------------------------------------------- search
SEARCH_TOKENS = ("with", "bot", "kind", "before", "after", "has", "automation", "trigger", "longer")


def _parse_search(q: str) -> tuple[list[str], dict[str, list[str]]]:
    words: list[str] = []
    tokens: dict[str, list[str]] = {}
    for part in q.split():
        key, sep, value = part.partition(":")
        if sep and key.lower() in SEARCH_TOKENS and value:
            tokens.setdefault(key.lower(), []).append(value)
        else:
            words.append(part)
    return words, tokens


def _minutes(value: str) -> int:
    text = value.strip().lower().rstrip("m")
    if not text.isdigit():
        raise Problem("validation", "longer: takes minutes, such as longer:10m", 422)
    return int(text)


def _bot_by_name(c: Any, name: str) -> Row | None:
    found = _bot(c, name)
    if found:
        return found
    row = c.execute(
        "SELECT id FROM bots WHERE lower(display_name)=? OR lower(slug)=? LIMIT 1", (name.lower(), name.lower())
    ).fetchone()
    return _bot(c, row["id"]) if row else None


def _search(store: Any, auth: Any, who: Any, q: str, who_refs: list[str], start: object, end: object) -> Row:
    began = clock.monotonic()
    words, tokens = _parse_search(q)
    now = datetime.now(timezone.utc)
    lo = _instant(start, "from") if start else now - SEARCH_BACK
    hi = _instant(end, "to") if end else now + SEARCH_AHEAD
    for value in tokens.get("after", []):
        lo = max(lo, _instant(value, "after"))
    for value in tokens.get("before", []):
        hi = min(hi, _instant(value, "before"))
    if hi <= lo:
        raise Problem("range", "Nothing is both after and before those dates", 422)
    kinds = _kinds(",".join(tokens["kind"])) if "kind" in tokens else set(KINDS)
    longer = max((_minutes(v) for v in tokens.get("longer", [])), default=0)
    run_words = any(k in tokens for k in ("automation", "trigger", "longer", "bot")) or tokens.get("kind") == ["run"]
    text = " ".join(words)
    partial = False
    with store.read() as c:
        caller = _caller(c, who)
        cals, errors = _resolve_all(c, caller, who_refs)
        for name in tokens.get("bot", []):
            bot = _bot_by_name(c, name)
            if bot is None:
                errors.append({"ref": "bot:" + name, "reason": f"No bot is called {name}"})
            elif bot["ref"] not in {cal["ref"] for cal in cals}:
                cals.append(bot)
        viewer = _viewer_google(c, caller)
        people = _people_map(c)
        found: list[Row] = []
        if text and "event" in kinds:
            ids = [i for i in (_tico_id(c, cal) for cal in cals) if i]
            hits = cast("list[Row]", cal_store.search(c, ids, text, lo, hi, SEARCH_LIMIT))
            # A private event's words matched, so a viewer who may not see it gets nothing rather than "Busy".
            found += [
                o
                for o in hits
                if o.get("visibility") != "private"
                or o["calendar"]["ref"] == caller["ref"]
                or any(a.get("id") == caller["id"] for a in _rows(o.get("attendees")))
            ]
        if "run" in kinds and (run_words or text):
            bots = [cal["id"] for cal in cals if cal["type"] == "bot"]
            if bots:
                run_lo, run_hi = max(lo, now - SEARCH_RUNS_BACK), min(hi, now)
                if run_lo < run_hi:
                    found += _run_hits(c, bots, run_lo, run_hi, now, words, tokens)
        sources: list[Row] = []
        if text and kinds & {"event", "away"}:
            for cal in cals:
                if cal["home"] == "google":
                    source = _google_source(c, who, caller, viewer, cal)
                    if "error" in source:
                        errors.append({"ref": cal["ref"], "reason": source["error"]})
                    elif source["mode"] == "events":
                        sources.append({**source, "ref": cal["ref"], "calendar": _obj(cal), "q": text, "limit": 50})
            if viewer:
                named = {s["calendar_id"] for s in sources}
                try:
                    listed = cast("list[Row]", gcal.calendar_list(c, viewer))
                except Problem as refused:
                    errors.append({"ref": "google:" + viewer, "reason": refused.detail})
                    listed = []
                for item in listed:
                    cid = str(item["id"])
                    if cid in named or (item.get("primary") and viewer in named):
                        continue
                    named.add(cid)
                    sources.append(
                        {
                            "as": viewer,
                            "calendar_id": cid,
                            "mode": "events",
                            "ref": "google:" + cid,
                            "q": text,
                            "calendar": _obj(_google(cid, item.get("name"))),
                            "limit": 50,
                        }
                    )
        sources = sources[:SEARCH_CALENDARS]
        meeting_sources: list[Row] = []
        if words and "event" in kinds:
            meeting_hits, meeting_sources = _meeting_hits(c, who, caller, viewer, words)
            found += meeting_hits
        remaining = max(0.0, SEARCH_SECONDS - (clock.monotonic() - began))
        google, google_errors, partial = _run_google(store, sources + meeting_sources, lo, hi, people, remaining)
        errors += google_errors
        found += google
        seen: set[str] = set()
        merged: list[Row] = []
        for occ in found:
            if occ["occurrence_id"] in seen or occ.get("kind") not in kinds:
                continue
            seen.add(occ["occurrence_id"])
            merged.append(occ)
        merged = _decorate(c, auth, who, caller, merged)
        merged = [o for o in merged if _search_keeps(o, tokens, longer)]
        merged.sort(key=lambda o: abs((_occ_start(o) - now).total_seconds()))
        merged = merged[:SEARCH_LIMIT]
    elapsed = int((clock.monotonic() - began) * 1000)
    with store.transaction() as c:
        # Latency and empty answers per search (decision 4: no Google index until the numbers say otherwise).
        H.event(
            c,
            who.actor,
            "calendar.search",
            "",
            {"ms": elapsed, "count": len(merged), "partial": partial, "google_calendars": len(sources)},
        )
    return {"occurrences": merged, "partial": partial, "errors": errors}


def _search_keeps(occ: Row, tokens: dict[str, list[str]], longer: int) -> bool:
    for name in tokens.get("with", []):
        needle = name.lower()
        if (
            not any(
                needle in str(a.get("name") or "").lower() or needle in str(a.get("email") or "").lower()
                for a in _rows(occ.get("attendees"))
            )
            and needle not in str(occ["calendar"].get("name") or "").lower()
        ):
            return False
    if "transcript" in [v.lower() for v in tokens.get("has", [])] and not _map(occ.get("links")).get("meeting"):
        return False
    if longer and not occ.get("all_day") and (_occ_end(occ) - _occ_start(occ)) <= timedelta(minutes=longer):
        return False
    return True


def _run_hits(
    c: Any,
    bots: list[str],
    lo: datetime,
    hi: datetime,
    now: datetime,
    words: list[str],
    tokens: dict[str, list[str]],
) -> list[Row]:
    runs = cast("list[Row]", derived.runs(c, bots, lo, hi, now))
    if not runs:
        return []
    attempt_ids = [str(o["links"]["attempt"]) for o in runs if _map(o.get("links")).get("attempt")]
    triggers: dict[str, str] = {}
    for at in range(0, len(attempt_ids), 500):
        chunk = attempt_ids[at : at + 500]
        for r in c.execute(
            f"SELECT id, coalesce(trigger_kind, trigger) AS trigger FROM attempts WHERE id IN ({','.join('?' * len(chunk))})",
            chunk,
        ):
            triggers[r["id"]] = str(r["trigger"] or "")
    automation_ids = sorted({str(o["links"]["automation"]) for o in runs if _map(o.get("links")).get("automation")})
    names: dict[str, str] = {}
    if automation_ids:
        marks = ",".join("?" * len(automation_ids))
        for r in c.execute(f"SELECT id, name FROM automations WHERE id IN ({marks})", automation_ids):
            names[r["id"]] = str(r["name"] or "")
    lowered = [w.lower() for w in words]
    out: list[Row] = []
    for occ in runs:
        links = _map(occ.get("links"))
        title = str(occ.get("title") or "").lower()
        if lowered and not all(w in title for w in lowered):
            continue
        automation = names.get(str(links.get("automation") or ""), "").lower()
        if any(a.lower() not in automation for a in tokens.get("automation", [])):
            continue
        trigger = triggers.get(str(links.get("attempt") or ""), "").lower()
        if any(t.lower() != trigger for t in tokens.get("trigger", [])):
            continue
        out.append(occ)
    return out


def _meeting_hits(c: Any, who: Any, caller: Row, viewer: str | None, words: list[str]) -> tuple[list[Row], list[Row]]:
    """Events whose meeting's words match: the meetings a viewer may read (context_search.readable), joined to their
    events through calendar_event_links. Tico events answer here; Google ones become reads for the budgeted pool."""
    from . import context_search as _context_search

    context_search: Any = _context_search

    lowered = [w.lower() for w in words]
    hits: list[Row] = []
    sources: list[Row] = []
    rows = c.execute(
        "SELECT l.*, m.imported_meeting_id FROM calendar_event_links l JOIN live_meetings m ON m.id=l.link_id "
        f"WHERE l.link_type='meeting' AND m.imported_meeting_id IS NOT NULL ORDER BY l.created_at DESC LIMIT {MEETING_LINKS}"
    ).fetchall()
    for link in rows:
        try:
            meeting = context_search.readable(c, who, link["imported_meeting_id"])
        except Problem:
            continue
        said = " ".join([str(meeting.get("title") or ""), str(context_search.transcript_text(meeting) or "")]).lower()
        if not all(w in said for w in lowered):
            continue
        original = link["original_starts_at"]
        if link["tico_event_id"]:
            row = cal_store.event(c, link["tico_event_id"])
            if not row or row["status"] == "cancelled":
                continue
            at = _instant(original or row["starts_at"], "start")
            for occ in cast(
                "list[Row]",
                cal_store.occurrences(c, [row["calendar_id"]], at - timedelta(minutes=1), at + timedelta(minutes=1)),
            ):
                if occ["event_id"] == link["tico_event_id"] and (
                    not original or occ.get("original_starts_at") == original
                ):
                    hits.append(occ)
        elif link["ical_uid"] and link["google_calendar_id"] and original:
            cal = _google(link["google_calendar_id"])
            source = _google_source(c, who, caller, viewer, cal)
            if source.get("mode") != "events":
                continue
            at = _instant(original, "start")
            uid = link["ical_uid"]

            def same(occ: Row, uid: str = uid, original: str = original) -> bool:
                return occ.get("ical_uid") == uid and occ.get("original_starts_at") == original

            sources.append(
                {
                    **source,
                    "ref": cal["ref"],
                    "calendar": _obj(cal),
                    "start": at,
                    "end": at + timedelta(minutes=1),
                    "keep": same,
                    "limit": 20,
                }
            )
        if len(hits) + len(sources) >= 10:
            break
    return hits, sources


# ----------------------------------------------------------------------------- free times
def _free(store: Any, who: Any, refs: list[str], minutes: int, within: str, start: object) -> Row:
    lo = _instant(start, "from") if start else datetime.now(timezone.utc)
    lo = lo.replace(second=0, microsecond=0)
    lo += timedelta(minutes=(-lo.minute) % 15)
    hi = lo + _span(within)
    with store.read() as c:
        caller = _caller(c, who)
        cals, errors = _resolve_all(c, caller, refs)
        if caller["kind"] == "bot":
            for cal in cals:
                if cal["type"] == "human":
                    allowed, reason = reach.may_book(c, who.actor, cal["ref"])
                    if not allowed:
                        raise Problem("book_rule", reason, 403)
        viewer = _viewer_google(c, caller)
        busy: list[tuple[datetime, datetime]] = []
        open_hours: list[list[tuple[datetime, datetime]]] = []
        google_ids: dict[str, Row] = {}
        for cal in cals:
            if cal["type"] == "google":
                errors.append({"ref": cal["ref"], "reason": "Free times are for people and bots"})
                continue
            if cal["home"] == "google":
                google_ids[cal["email"]] = cal
            ids = [i for i in [_tico_id(c, cal)] if i]
            occs = cast("list[Row]", cal_store.occurrences(c, ids, lo, hi)) + _attending_copies(c, [cal], lo, hi)
            if cal["type"] == "bot":
                occs += cast("list[Row]", derived.scheduled(c, [cal["id"]], lo, hi, datetime.now(timezone.utc)))
            if cal["type"] == "human":
                occs += cast(
                    "list[Row]",
                    cal_store.absences(
                        c, [cal["id"]], lo.date().isoformat(), (hi + timedelta(days=1)).date().isoformat()
                    ),
                )
            busy += [(_occ_start(o), _occ_end(o)) for o in occs if not _declined(o, cal)]
            open_hours.append(_working(_settings_of(c, cal), lo, hi))
        if google_ids:
            reader = viewer or (next(iter(google_ids)) if caller["kind"] == "bot" else None)
            # Times only, so any connected reader will do: the viewer, else each person's own mailbox.
            asks = [(reader, list(google_ids))] if reader else [(address, [address]) for address in google_ids]
            for as_mailbox, ids in asks:
                try:
                    answered = cast("dict[str, list[tuple[str, str]]]", gcal.freebusy(c, as_mailbox, ids, lo, hi))
                except Problem as refused:
                    errors += [{"ref": google_ids[i]["ref"], "reason": refused.detail} for i in ids]
                    continue
                for cid in ids:
                    if cid not in answered:
                        errors.append({"ref": google_ids[cid]["ref"], "reason": "Google Calendar would not say when"})
                        continue
                    busy += [(_instant(a, "start"), _instant(b, "end")) for a, b in answered[cid]]
    if errors:
        # A calendar whose busy times are unknown would make every slot a guess.
        return {"slots": [], "errors": errors}
    return {"slots": _slots(open_hours, busy, lo, hi, minutes), "errors": []}


def _declined(occ: Row, cal: Row) -> bool:
    return any(
        a.get("type") == cal["type"] and a.get("id") == cal["id"] and a.get("response") == "declined"
        for a in _rows(occ.get("attendees"))
    )


def _span(value: str) -> timedelta:
    text = (value or "5d").strip().lower()
    unit = text[-1:]
    number = text[:-1]
    if unit not in ("d", "h") or not number.isdigit() or int(number) < 1:
        raise Problem("validation", "within is a length such as 5d or 12h", 422)
    span = timedelta(days=int(number)) if unit == "d" else timedelta(hours=int(number))
    if span > WINDOW_MAX:
        raise Problem("range", f"within is at most {WINDOW_MAX.days} days", 422)
    return span


def _working(settings: Row, lo: datetime, hi: datetime) -> list[tuple[datetime, datetime]]:
    """A teammate's working hours in [lo, hi), in their zone; a bot's are every hour."""
    if not settings.get("work_starts"):
        return [(lo, hi)]
    zone = _zone(settings["time_zone"]) or ZoneInfo("UTC")
    starts = time.fromisoformat(settings["work_starts"])
    ends = time.fromisoformat(settings["work_ends"])
    days = set(str(settings["work_days"]).split(","))
    out: list[tuple[datetime, datetime]] = []
    day = lo.astimezone(zone).date() - timedelta(days=1)
    while day <= hi.astimezone(zone).date():
        if DAY_NAMES[day.weekday()] in days and starts < ends:
            a = _utc(datetime.combine(day, starts, zone))
            b = _utc(datetime.combine(day, ends, zone))
            a, b = max(a, lo), min(b, hi)
            if a < b:
                out.append((a, b))
        day += timedelta(days=1)
    return out


def _slots(
    open_hours: list[list[tuple[datetime, datetime]]],
    busy: list[tuple[datetime, datetime]],
    lo: datetime,
    hi: datetime,
    minutes: int,
) -> list[Row]:
    """Stretches of at least `minutes` inside everyone's working hours and outside everyone's busy times."""
    free = [(lo, hi)]
    for hours in open_hours:
        free = [(max(a, x), min(b, y)) for a, b in free for x, y in hours if max(a, x) < min(b, y)]
    for x, y in sorted(busy):
        cut: list[tuple[datetime, datetime]] = []
        for a, b in free:
            if y <= a or x >= b:
                cut.append((a, b))
                continue
            if a < x:
                cut.append((a, x))
            if y < b:
                cut.append((y, b))
        free = cut
    length = timedelta(minutes=minutes)
    out = [{"start": _z(a), "end": _z(b)} for a, b in sorted(free) if b - a >= length]
    return out[:FREE_SLOTS]


# ----------------------------------------------------------------------------- writes
def _attendee(c: Any, caller: Row, ref: str) -> list[Row]:
    """An attendee ref (human:, bot:, team:, an email, a name) as [{type, id, email, home, name}]."""
    text = str(ref or "").strip()
    if "@" in text and ":" not in text:
        address = text.lower()
        row = c.execute("SELECT id FROM humans WHERE lower(email)=?", (address,)).fetchone()
        if row:
            person = _person(c, row["id"])
            if person:
                return [person]
        return [{"type": "external", "id": None, "email": address, "home": None, "name": address, "ref": address}]
    return _resolve(c, caller, text)


def _attendees(c: Any, caller: Row, refs: list[str] | None) -> list[Row]:
    out: list[Row] = []
    seen: set[str] = set()
    for ref in refs or []:
        for found in _attendee(c, caller, ref):
            if found["type"] in ("shared", "google"):
                raise Problem("validation", f"{ref} is a calendar, not a person or a bot", 422)
            key = found["ref"]
            if key not in seen:
                seen.add(key)
                out.append(found)
    return out


def _check_books(c: Any, who: Any, caller: Row, parties: Iterable[Row]) -> None:
    if caller["kind"] != "bot":
        return
    for party in parties:
        if party["type"] == "human":
            allowed, reason = reach.may_book(c, who.actor, party["ref"])
            if not allowed:
                raise Problem("book_rule", reason, 403)


def _tico_fields(fields: Row, attendees: list[Row] | None) -> Row:
    out = {k: v for k, v in fields.items() if k not in ("add_meet", "calendar", "scope", "expected_sequence")}
    if attendees is not None:
        out["attendees"] = [a["ref"] if a["type"] != "external" else a["email"] for a in attendees]
    return out


def _google_fields(fields: Row, attendees: list[Row] | None, caller: Row, bot: str | None) -> Row:
    out = {
        k: v for k, v in fields.items() if k not in ("calendar", "scope", "expected_sequence", "task_id", "thread_id")
    }
    if attendees is not None:
        out["attendees"] = [{"email": a["email"]} for a in attendees if a.get("email")]
    out["actor"] = caller["actor"]
    if bot:
        out["bot"] = bot
    return out


def _manager(c: Any, bot_id: str) -> str | None:
    row = c.execute("SELECT reports_to FROM bot_config WHERE bot=?", (bot_id,)).fetchone()
    target = str((row["reports_to"] if row else "") or "")
    return target[len("human:") :] if target.startswith("human:") else None


def _plan_create(c: Any, auth: Any, store: Any, who: Any, body: EventCreate) -> Row:
    """Where a new event goes and as whom (spec 8.2), after every permission check."""
    caller = _caller(c, who)
    targets = _resolve(c, caller, body.calendar or "me")
    if len(targets) != 1:
        raise Problem("validation", "Add an event to one calendar; invite a team as attendees", 422)
    target = targets[0]
    fields = body.model_dump(exclude_none=True, exclude={"calendar"})
    if not fields.get("title") or not fields.get("start"):
        raise Problem("validation", "An event needs a title and a start", 422)
    attendees = _attendees(c, caller, body.attendees)
    refused = _write_refusal(c, auth, who, caller, target)
    if refused is not None:
        raise refused
    _check_books(c, who, caller, [target, *attendees])
    if caller["kind"] == "bot" and store.settings.block_external_invites:
        outside = [a["email"] for a in attendees if a["type"] == "external"]
        if outside:
            raise Problem(
                "external_attendee",
                "A bot may invite only people on the company roster; " + ", ".join(outside[:3]) + " is not",
                403,
            )
    bot = caller["id"] if caller["kind"] == "bot" else (target["id"] if target["type"] == "bot" else None)
    if target["type"] in ("human", "bot") and (caller["kind"] == "bot" or target["type"] == "bot"):
        # A booking: a connected person on it puts it on Google (decision 7), else it stays in Tico.
        parties = ([target] if target["type"] == "human" else []) + [a for a in attendees if a["type"] == "human"]
        connected = [p for p in parties if p["home"] == "google"]
        if connected:
            manager = _manager(c, bot) if bot else None
            host = next((p for p in connected if p["id"] == manager), connected[0])
            refused = _company_writes(c)
            if refused is not None:
                raise refused
            invited = [p for p in [target, *attendees] if p.get("email") and p["ref"] != host["ref"]]
            google = _google_fields(fields, invited, caller, bot)
            # The host's own copy lands needing their reply, unless their own Assistant booked it.
            mine = caller["kind"] == "assistant" and caller["id"] == host["id"]
            google["attendees"] = [
                {"email": host["email"], "response": "accepted" if mine else "needs_action"},
                *google["attendees"],
            ]
            return {
                "home": "google",
                "as": host["email"],
                "calendar_id": host["email"],
                "fields": google,
                "bot": bot,
                "calendar": _obj(host),
                "caller": caller,
            }
    if target["home"] == "google":
        google = _google_fields(fields, attendees, caller, bot)
        address = target["email"] if target["type"] == "human" else target["id"]
        if target["type"] == "human" and caller["kind"] == "assistant" and caller["id"] == target["id"]:
            google["attendees"] = [{"email": address, "response": "accepted"}, *google["attendees"]]
        return {
            "home": "google",
            "as": address,
            "calendar_id": address,
            "fields": google,
            "bot": bot,
            "calendar": _obj(target),
            "caller": caller,
        }
    calendar_id = target["id"] if target["type"] == "shared" else None
    return {
        "home": "tico",
        "target": target,
        "calendar_id": calendar_id,
        "fields": _tico_fields(fields, attendees),
        "caller": caller,
    }


def _parse_occurrence(occurrence_id: str) -> Row:
    head, _, rest = occurrence_id.partition(":")
    if head == "tico":
        parts = cal_store.occurrence_id_parts(occurrence_id)
        if parts is None:
            raise Problem("not_found", "There is no such event", 404)
        return {"home": "tico", "event_id": parts[0], "original": parts[1]}
    if head == "google":
        calendar_id, _, event_id = rest.partition(":")
        if not calendar_id or not event_id or event_id.startswith("busy:"):
            raise Problem("validation", "Only busy time is known of that event; it cannot be changed here", 422)
        return {"home": "google", "calendar_id": calendar_id, "event_id": event_id}
    raise Problem("validation", "Runs, due dates and time away are not events to change", 422)


def _tico_target(c: Any, auth: Any, who: Any, caller: Row, event_id: str) -> Row:
    """The stored event and its calendar, refusing a caller who may not change it."""
    row = cal_store.event(c, event_id)
    if not row:
        raise Problem("not_found", "There is no such event", 404)
    stored = cal_store.get(c, row["calendar_id"])
    if not stored:
        raise Problem("not_found", "There is no such calendar", 404)
    if stored["owner_type"] == "shared":
        cal = _shared(c, stored["id"])
    else:
        cal = _person(c, stored["owner_id"]) if stored["owner_type"] == "human" else _bot(c, stored["owner_id"])
    if cal is None:
        raise Problem("not_found", "There is no such calendar", 404)
    refused = _write_refusal(c, auth, who, caller, cal)
    series = cal_store.event(c, row["recurring_event_id"]) if row["recurring_event_id"] else row
    made_it = caller["kind"] != "person" and (series or row).get("created_by") == caller["actor"]
    if refused is not None and not made_it:
        raise refused
    # A bot may book anyone (_write_refusal, then _check_books), but it changes a person's event only if it added that
    # event or holds Read and write on the person's calendar: the same rule as _google_target.
    if caller["kind"] == "bot" and cal["type"] == "human" and not (made_it or _granted(c, caller, _tico_id(c, cal))):
        raise Problem(
            "forbidden", "Only the bot that added it, or someone with Read and write, changes this event", 403
        )
    return {"row": row, "calendar": cal}


def _google_target(c: Any, who: Any, caller: Row, parsed: Row) -> Row:
    """A Google event to change: read as its own calendar's mailbox, writable by a bot that Tico wrote it for or by a
    caller holding Read and write on that mailbox, behind the company's Change calendar switch."""
    if caller["kind"] == "person":
        raise Problem("view_only", VIEW_ONLY, 403)
    address = parsed["calendar_id"]
    current = cast(Row, gcal.event(c, address, address, parsed["event_id"]))
    tagged = caller["kind"] == "bot" and bool(
        current.get("ical_uid")
        and c.execute(
            "SELECT 1 FROM calendar_event_links WHERE link_type='bot' AND link_id=? AND ical_uid=?",
            (caller["id"], current["ical_uid"]),
        ).fetchone()
    )
    refused = _company_writes(c) if tagged else _mailbox_write(c, who, address)
    if refused is not None:
        raise refused
    return current


def _record_google(c: Any, who: Any, caller: Row, action: str, occ: Row, calendar_id: str, bot: str | None) -> Row:
    """A Google write's own facts in Tico: the bot it is for (so the bot's calendar finds it), the audit row, the
    event and a `calendar` live change. Nothing of the event itself is stored."""
    if bot and occ.get("ical_uid") and action == "created":
        c.execute(
            "INSERT INTO calendar_event_links(id, google_calendar_id, ical_uid, tico_event_id, original_starts_at, "
            "link_type, link_id, created_at) VALUES(?,?,?,NULL,NULL,'bot',?,?)",
            (H.new_id(), calendar_id, occ["ical_uid"], bot, H.now()),
        )
    target = "calendar_google:" + str(occ.get("occurrence_id") or calendar_id)
    audit_log.write(c, caller["actor"], target, None, {"calendar": "google:" + calendar_id, "action": action})
    H.event(
        c,
        who.actor,
        "calendar.event." + action,
        str(occ.get("event_id") or occ.get("occurrence_id") or ""),
        {"calendar_id": "google:" + calendar_id, "home": "google", **({"bot": bot} if bot else {})},
    )
    c.execute(
        "INSERT INTO changes(topic, kind, subject_id, ref, actor, created_at) VALUES('calendar','google',?,?,?,?)",
        (occ.get("occurrence_id"), "google:" + calendar_id, who.actor, H.now()),
    )
    return occ


def _one_occurrence(c: Any, auth: Any, who: Any, occurrence_id: str) -> Row:
    """One event occurrence as the caller sees it, read the way the list reads its home: a Tico one from the store
    (a private one shows as Busy to a non-attendee, as in the list), a Google one through the caller's own Google
    access (`_google_source`). Anything else, or what the caller cannot see, is 404."""
    gone = Problem("not_found", "There is no such event", 404)
    caller = _caller(c, who)
    try:
        parsed = _parse_occurrence(occurrence_id)
    except Problem:
        raise gone from None
    if parsed["home"] == "tico":
        row = cal_store.event(c, parsed["event_id"])
        if not row or row.get("recurring_event_id") or row.get("status") == "cancelled":
            raise gone
        if parsed["original"]:
            if not row.get("rrule"):
                raise gone
            occ = cal_store._instance_occurrence(c, row, parsed["original"])  # pyright: ignore[reportPrivateUsage]
        else:
            if row.get("rrule"):
                raise gone
            try:
                occ = cal_store._first(c, row)  # pyright: ignore[reportPrivateUsage]
            except Problem:
                raise gone from None
        if not occ or occ.get("status") == "cancelled" or occ.get("occurrence_id") != occurrence_id:
            raise gone
        return _decorate(c, auth, who, caller, [occ])[0]
    cal = _google(parsed["calendar_id"])
    source = _google_source(c, who, caller, _viewer_google(c, caller), cal)
    if "error" in source:
        raise gone
    try:
        occ = cast(
            Row,
            gcal.event(
                c, source["as"], parsed["calendar_id"], parsed["event_id"], calendar=_obj(cal), people=_people_map(c)
            ),
        )
    except Problem as refused:
        if refused.status in (403, 404):
            raise gone from None
        raise
    return _decorate(c, auth, who, caller, [occ])[0]


def meeting_link_facts(c: Any, who: Any, occurrence_id: object) -> Row | None:
    """What a calendar_event_links row needs for a live meeting started from an occurrence, or None when the
    occurrence cannot be resolved (the meeting starts anyway). A Google one is read as the caller."""
    text = str(occurrence_id or "").strip()
    try:
        parsed = _parse_occurrence(text)
        if parsed["home"] == "tico":
            row = cal_store.event(c, parsed["event_id"])
            if not row:
                return None
            series = row["recurring_event_id"] or row["id"]
            original = parsed["original"] or row["original_starts_at"]
            return {"tico_event_id": series, "original_starts_at": original}
        caller = _caller(c, who)
        viewer = _viewer_google(c, caller) or parsed["calendar_id"]
        occ = cast(Row, gcal.event(c, viewer, parsed["calendar_id"], parsed["event_id"]))
    except Problem:
        log.info("calendar: no link for meeting from %s", text[:120])
        return None
    if not occ.get("ical_uid"):
        return None
    return {
        "google_calendar_id": parsed["calendar_id"],
        "ical_uid": occ["ical_uid"],
        "original_starts_at": occ.get("original_starts_at"),
    }


def record_link(c: Any, facts: Row, link_type: str, link_id: str) -> None:
    c.execute(
        "INSERT INTO calendar_event_links(id, google_calendar_id, ical_uid, tico_event_id, original_starts_at, "
        "link_type, link_id, created_at) VALUES(?,?,?,?,?,?,?,?)",
        (
            H.new_id(),
            facts.get("google_calendar_id"),
            facts.get("ical_uid"),
            facts.get("tico_event_id"),
            facts.get("original_starts_at"),
            link_type,
            link_id,
            H.now(),
        ),
    )


# ----------------------------------------------------------------------------- settings
def _settings_view(c: Any, pid: str, zone: str | None = None) -> Row:
    cal = _person(c, pid)
    if cal is None:
        raise Problem("not_found", "You are not on the roster", 404)
    human = cal["row"]
    record = human.get("record_on_join")
    return {
        "time_zone": human.get("time_zone") or zone or "UTC",
        "time_zone_set": bool(human.get("time_zone")),
        "work_starts": human.get("work_starts") or WORK_STARTS,
        "work_ends": human.get("work_ends") or WORK_ENDS,
        "work_days": human.get("work_days") or WORK_DAYS,
        "record_on_join": record is None or bool(record),
    }


# ----------------------------------------------------------------------------- routes
def install_calendar(app: Any, store: Any, mutate: Callable[..., Any]) -> None:
    auth = app.state.auth

    def key_of(request: Request) -> str | None:
        return request.headers.get("idempotency-key")

    @app.get("/api/v2/calendar/events")
    def calendar_events(request: Request) -> Row:
        """Occurrences on the calendars in `who` (default the caller) in [from, to), at most 62 days."""
        params = request.query_params
        start, end = _window(params.get("from"), params.get("to"))
        group = str(params.get("group") or "").lower() in ("1", "true", "yes")
        return _gather(
            store,
            auth,
            request.state.identity,
            _refs(params.get("who")),
            start,
            end,
            _kinds(params.get("kinds")),
            group,
        )

    @app.get("/api/v2/calendar/calendars")
    def calendar_list(request: Request, q: str = "") -> Row:
        who = request.state.identity
        needle = q.strip().lower()
        with store.read() as c:
            caller = _caller(c, who)
            viewer = _viewer_google(c, caller)
            out: list[Row] = []

            def add(cal: Row, kind: str, *more: object) -> None:
                words = " ".join(str(w or "") for w in (cal["name"], cal["id"], *more)).lower()
                if len(out) < LIST_MAX and (not needle or needle in words):
                    out.append({**_view(c, auth, who, caller, cal), "kind": kind})

            roster = groups._roster(c)  # pyright: ignore[reportPrivateUsage]
            for person in cast("list[Row]", people_mod.visible_people(roster)):
                cal = _person(c, str(person.get("id")))
                if cal:
                    add(cal, "person", cal["email"])
            for bot in c.execute("SELECT id FROM bots WHERE state<>'archived' ORDER BY display_name, slug"):
                cal = _bot(c, bot["id"])
                if cal:
                    add(cal, "bot", cal.get("slug"))
            for row in cast("list[Row]", cal_store.shared(c)):
                cal = _shared(c, row["id"])
                if cal:
                    add(cal, "shared")
            for g in cast(
                "list[Row]",
                groups.rows(roster, groups._configs(c), groups._archived(c)),  # pyright: ignore[reportPrivateUsage]
            ):
                if len(out) < LIST_MAX and (not needle or needle in str(g["name"]).lower()):
                    out.append(
                        {
                            "ref": "team:" + g["id"],
                            "type": "team",
                            "id": g["id"],
                            "name": g["name"],
                            "home": None,
                            "time_zone": None,
                            "work_starts": None,
                            "work_ends": None,
                            "work_days": None,
                            "can_write": False,
                            "kind": "team",
                        }
                    )
            errors: list[Row] = []
            if viewer:
                try:
                    for item in cast("list[Row]", gcal.calendar_list(c, viewer)):
                        if item.get("primary"):
                            continue
                        add(_google(str(item["id"]), item.get("name")), "google")
                except Problem as refused:
                    errors.append({"ref": "google:" + viewer, "reason": refused.detail})
        return {"calendars": out, "errors": errors}

    @app.get("/api/v2/calendar/search")
    def calendar_search(request: Request, q: str = "") -> Row:
        params = request.query_params
        if not q.strip():
            raise Problem("validation", "Give words to search for", 422)
        if len(q) > 500:
            raise Problem("validation", "Keep a search under 500 characters", 422)
        return _search(
            store, auth, request.state.identity, q, _refs(params.get("who")), params.get("from"), params.get("to")
        )

    @app.get("/api/v2/calendar/free")
    def calendar_free(request: Request, minutes: int = 30, within: str = "5d") -> Row:
        if not 5 <= minutes <= 8 * 60:
            raise Problem("validation", "minutes is 5 to 480", 422)
        params = request.query_params
        return _free(store, request.state.identity, _refs(params.get("who")), minutes, within, params.get("from"))

    @app.post("/api/v2/calendar/events")
    def calendar_create(request: Request, body: EventCreate) -> Any:
        who = request.state.identity
        with store.read() as c:
            replay = store.saved(c, who, request.url.path, key_of(request))
            plan = None if replay else _plan_create(c, auth, store, who, body)
        if plan is None or plan["home"] == "tico":

            def work(c: Any) -> Row:
                again = _plan_create(c, auth, store, who, body)
                if again["home"] != "tico":
                    raise Problem("receipt_expired", "This write's receipt expired; send it again", 409)
                target = again["target"]
                calendar_id = again["calendar_id"] or cal_store.calendar_for(c, target["type"], target["id"])["id"]
                created = cast(Row, cal_store.create_event(c, calendar_id, again["fields"], again["caller"]["actor"]))
                return _decorate(c, auth, who, again["caller"], [{**created, "calendar": _obj(target)}])[0]

            return mutate(request, body, work)
        with store.read() as c:
            occ = cast(Row, gcal.insert(c, plan["as"], plan["calendar_id"], plan["fields"]))
        occ["calendar"] = plan["calendar"]

        def record(c: Any) -> Row:
            _record_google(c, who, plan["caller"], "created", occ, plan["calendar_id"], plan["bot"])
            return _decorate(c, auth, who, plan["caller"], [occ])[0]

        return mutate(request, body, record)

    def google_scope(scope: str, current: Row, event_id: str) -> str:
        if scope == "following":
            raise Problem("scope", "On Google, change this one or all of a repeated event", 422)
        return str(current.get("event_id") or event_id) if scope == "all" else event_id

    @app.get("/api/v2/calendar/events/{occurrence_id:path}")
    def calendar_event(request: Request, occurrence_id: str) -> Row:
        """One occurrence as the caller sees it (the Recorder checks a Join link against it). 404 when it is not an
        event, or the caller cannot see it."""
        with store.read() as c:
            return _one_occurrence(c, auth, request.state.identity, occurrence_id)

    @app.patch("/api/v2/calendar/events/{occurrence_id:path}")
    def calendar_change(request: Request, occurrence_id: str, body: EventChange) -> Any:
        who = request.state.identity
        parsed = _parse_occurrence(occurrence_id)
        fields = {
            k: v for k, v in body.model_dump(exclude_unset=True).items() if k not in ("scope", "expected_sequence")
        }
        scope = body.scope or ("this" if parsed.get("original") else "all")
        if parsed["home"] == "tico":

            def work(c: Any) -> Row:
                caller = _caller(c, who)
                target = _tico_target(c, auth, who, caller, parsed["event_id"])
                attendees = _attendees(c, caller, body.attendees) if "attendees" in fields else None
                _check_books(c, who, caller, attendees or [])
                changed = cast(
                    Row,
                    cal_store.update_event(
                        c,
                        parsed["event_id"],
                        _tico_fields(fields, attendees),
                        caller["actor"],
                        original_starts_at=parsed["original"],
                        scope=scope,
                        expected_sequence=body.expected_sequence,
                    ),
                )
                return _decorate(c, auth, who, caller, [{**changed, "calendar": _obj(target["calendar"])}])[0]

            return mutate(request, body, work)
        occ: Row = {}
        with store.read() as c:
            caller = _caller(c, who)
            replay = store.saved(c, who, request.url.path, key_of(request))
            if not replay:
                current = _google_target(c, who, caller, parsed)
                attendees = _attendees(c, caller, body.attendees) if "attendees" in fields else None
                _check_books(c, who, caller, attendees or [])
                bot = caller["id"] if caller["kind"] == "bot" else None
                event_id = google_scope(scope, current, parsed["event_id"])
                occ = cast(
                    Row,
                    gcal.patch(
                        c,
                        parsed["calendar_id"],
                        parsed["calendar_id"],
                        event_id,
                        _google_fields(fields, attendees, caller, bot),
                    ),
                )
                occ["calendar"] = current.get("calendar") or occ.get("calendar")

        def record(c: Any) -> Row:
            if replay:
                raise Problem("receipt_expired", "This write's receipt expired; send it again", 409)
            _record_google(c, who, caller, "changed", occ, parsed["calendar_id"], None)
            return _decorate(c, auth, who, caller, [occ])[0]

        return mutate(request, body, record)

    @app.delete("/api/v2/calendar/events/{occurrence_id:path}")
    def calendar_cancel(request: Request, occurrence_id: str, scope: str | None = None) -> Any:
        who = request.state.identity
        body = EventCancel.model_validate({"scope": scope} if scope else {})
        parsed = _parse_occurrence(occurrence_id)
        chosen = body.scope or ("this" if parsed.get("original") else "all")
        if parsed["home"] == "tico":

            def work(c: Any) -> Row:
                caller = _caller(c, who)
                _tico_target(c, auth, who, caller, parsed["event_id"])
                cal_store.cancel_event(
                    c, parsed["event_id"], caller["actor"], original_starts_at=parsed["original"], scope=chosen
                )
                return {"occurrence_id": occurrence_id, "cancelled": True, "scope": chosen}

            return mutate(request, body, work)
        with store.read() as c:
            caller = _caller(c, who)
            replay = store.saved(c, who, request.url.path, key_of(request))
            if not replay:
                current = _google_target(c, who, caller, parsed)
                gcal.delete(
                    c, parsed["calendar_id"], parsed["calendar_id"], google_scope(chosen, current, parsed["event_id"])
                )

        def record(c: Any) -> Row:
            if replay:
                raise Problem("receipt_expired", "This write's receipt expired; send it again", 409)
            _record_google(c, who, caller, "cancelled", {"occurrence_id": occurrence_id}, parsed["calendar_id"], None)
            return {"occurrence_id": occurrence_id, "cancelled": True, "scope": chosen}

        return mutate(request, body, record)

    @app.post("/api/v2/calendar/events/{occurrence_id:path}/response")
    def calendar_respond(request: Request, occurrence_id: str, body: EventResponse) -> Any:
        who = request.state.identity
        parsed = _parse_occurrence(occurrence_id)
        if parsed["home"] == "tico":

            def work(c: Any) -> Row:
                caller = _caller(c, who)
                original = parsed["original"] if (body.scope or "all") == "this" else None
                cal_store.set_response(c, parsed["event_id"], caller["ref"], body.response, original_starts_at=original)
                return {"occurrence_id": occurrence_id, "response": body.response}

            return mutate(request, body, work)
        with store.read() as c:
            caller = _caller(c, who)
            viewer = _viewer_google(c, caller)
            replay = store.saved(c, who, request.url.path, key_of(request))
            if not viewer:
                raise Problem("validation", "Only a person on Google replies to a Google event", 422)
            if not replay:
                refused = _company_writes(c)
                if refused is not None:
                    raise refused
                # The reply goes on the viewer's own copy of the event, whoever's calendar it was read from.
                current = cast(Row, gcal.event(c, viewer, viewer, parsed["event_id"]))
                event_id = google_scope(body.scope or "this", current, parsed["event_id"])
                answers = [
                    {"email": a["email"], "response": body.response if a.get("email") == viewer else a["response"]}
                    for a in _rows(current.get("attendees"))
                    if a.get("email")
                ]
                if not any(a["email"] == viewer for a in answers):
                    raise Problem("not_attendee", "Only an attendee can reply", 403)
                gcal.patch(c, viewer, viewer, event_id, {"attendees": answers, "actor": caller["actor"]})

        def record(c: Any) -> Row:
            if replay:
                raise Problem("receipt_expired", "This write's receipt expired; send it again", 409)
            _record_google(c, who, caller, "changed", {"occurrence_id": occurrence_id}, viewer or "", None)
            return {"occurrence_id": occurrence_id, "response": body.response}

        return mutate(request, body, record)

    def person_of(c: Any, who: Any) -> Row:
        caller = _caller(c, who)
        if caller["kind"] == "bot":
            raise Problem("identity", "Calendar settings are a person's own", 403)
        return caller

    @app.get("/api/v2/calendar/settings")
    def calendar_settings(request: Request) -> Row:
        with store.read() as c:
            caller = person_of(c, request.state.identity)
            cal = _person(c, caller["id"])
            zone = _google_zone(c, caller["email"]) if cal and cal["home"] == "google" else None
            return _settings_view(c, caller["id"], zone)

    @app.put("/api/v2/calendar/settings")
    def calendar_settings_put(request: Request, body: CalendarSettings) -> Any:
        who = request.state.identity
        given = body.model_dump(include=body.model_fields_set)
        changes: Row = {}
        if "time_zone" in given:
            if given["time_zone"] is not None and _zone(given["time_zone"]) is None:
                raise Problem("validation", "time_zone is an IANA zone such as Europe/London", 422)
            changes["time_zone"] = given["time_zone"]
        for key in ("work_starts", "work_ends"):
            if key in given:
                changes[key] = None if given[key] is None else _clock(given[key], key)
        if "work_days" in given:
            changes["work_days"] = None if given["work_days"] is None else _days(given["work_days"])
        if "record_on_join" in given:
            changes["record_on_join"] = None if given["record_on_join"] is None else int(given["record_on_join"])
        with store.read() as c:
            caller = person_of(c, who)
            cal = _person(c, caller["id"])
            zone = _google_zone(c, caller["email"]) if cal and cal["home"] == "google" else None

        def work(c: Any) -> Row:
            pid = person_of(c, who)["id"]
            before = dict(
                c.execute(
                    "SELECT time_zone, work_starts, work_ends, work_days, record_on_join FROM humans WHERE id=?", (pid,)
                ).fetchone()
            )
            after = {**before, **changes}
            starts, ends = after["work_starts"] or WORK_STARTS, after["work_ends"] or WORK_ENDS
            if starts >= ends:
                raise Problem("validation", "Working hours end after they start", 422)
            if changes:
                sets = ",".join(f"{k}=?" for k in changes)
                c.execute(f"UPDATE humans SET {sets} WHERE id=?", (*changes.values(), pid))
                audit_log.write(c, who.actor, "calendar_settings:human:" + pid, before, after)
                H.event(c, who.actor, "calendar.settings.changed", "human:" + pid, {"fields": sorted(changes)})
            return _settings_view(c, pid, zone)

        return mutate(request, body, work)

    @app.post("/api/v2/calendar/shared")
    def calendar_shared_create(request: Request, body: SharedCreate) -> Any:
        who = request.state.identity

        def work(c: Any) -> Row:
            caller = _caller(c, who)
            if caller["kind"] == "bot" or not _is_admin(c, auth, who):
                raise Problem("forbidden", "Only an owner or an admin makes a shared calendar", 403)
            if _zone(body.time_zone) is None:
                raise Problem("validation", "time_zone is an IANA zone such as Europe/London", 422)
            row = cast(Row, cal_store.create_shared(c, body.name, who.actor, body.time_zone))
            cal = _shared(c, row["id"])
            return {"calendar": _view(c, auth, who, caller, cal or {})}

        return mutate(request, body, work)

    @app.put("/api/v2/calendar/shared/{calendar_id}/access")
    def calendar_shared_access(request: Request, calendar_id: str, body: SharedAccess) -> Any:
        who = request.state.identity

        def work(c: Any) -> Row:
            caller = _caller(c, who)
            if caller["kind"] == "bot" or not _is_admin(c, auth, who):
                raise Problem("forbidden", "Only an owner or an admin shares a shared calendar", 403)
            if _shared(c, calendar_id) is None:
                raise Problem("not_found", "There is no such shared calendar", 404)
            kind, _, ident = body.grantee.partition(":")
            if kind == "team":
                kind = "group"
            if kind == "bot":
                bot = H.resolve_bot(c, ident)
                if not bot:
                    raise Problem("not_found", f"There is no bot {ident}", 404)
                ident = bot["id"]
            elif kind == "human" and not H.human(c, ident):
                raise Problem("not_found", f"There is no person {ident}", 404)
            cal_store.set_access(c, calendar_id, kind, ident, body.level, who.actor)
            return {"calendar_id": calendar_id, "accesses": cal_store.accesses(c, calendar_id)}

        return mutate(request, body, work)

    @app.post("/api/v2/calendar/absences")
    def calendar_absence(request: Request, body: AbsenceCreate) -> Any:
        who = request.state.identity

        def work(c: Any) -> Row:
            caller = person_of(c, who)
            human = str(body.human or caller["id"]).removeprefix("human:")
            if human != caller["id"] and not _is_admin(c, auth, who):
                raise Problem("forbidden", "Only an owner or an admin records someone else's time away", 403)
            return cast(Row, cal_store.add_absence(c, human, body.starts_on, body.ends_on, body.note, who.actor))

        return mutate(request, body, work)

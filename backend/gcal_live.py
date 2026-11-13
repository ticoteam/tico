# pyright: strict
"""Read and write Google calendars on the server with the company's Workspace key, storing nothing.

The Google home of Calendar (docs/dev/calendar-contract.md). Every read impersonates the viewer (`as_mailbox`) and asks
for the other calendar by its id, so Google's own sharing decides what comes back: full details, time only, or nothing.
Reads mint the read-only calendar scope; writes the full one. Only the token cache in google_workspace survives a call.
Occurrences come out in the contract's shape; nothing outside this module knows Google's event fields.
"""

import importlib
import json
import sqlite3
import urllib.error
import urllib.parse
import urllib.request
import uuid
from collections.abc import Callable
from datetime import date, datetime, timezone
from typing import Any, NoReturn, cast

from connectors.mail import calendar as mail_calendar

from . import google_workspace
from .store import Problem

Json = dict[str, Any]
Conn = sqlite3.Connection | None
When = datetime | str

# The untyped modules this one leans on, with the shapes it uses. Looked up on each call, so tests can replace them.
_meeting_url = cast("Callable[[Json], str]", mail_calendar._meeting_url)  # pyright: ignore[reportPrivateUsage]


def _mint(c: Conn, mailbox: str, scopes: list[str]) -> str:
    mint = cast("Callable[..., Json]", google_workspace.mint)
    return str(mint(c, mailbox, "calendar", scopes=scopes)["access_token"])


def _active(c: Conn) -> Json | None:
    return cast("Callable[[Conn], Json | None]", google_workspace.active)(c)


def _authorized(row: Json | None) -> list[str]:
    return cast("Callable[[Json | None], list[str]]", google_workspace.authorized)(row)


def _authority(c: Conn, address: str) -> str:
    return cast("Callable[[Conn, str], str]", google_workspace.authority)(c, address)


WORKSPACE_SCOPES = google_workspace.WORKSPACE_SCOPES
READ_ONLY_SCOPES = google_workspace.READ_ONLY_SCOPES

BASE = "https://www.googleapis.com/calendar/v3/"
SECONDS = 10
HTTP_MAX_BYTES = 8 * 1024 * 1024
PAGE_SIZE = 250  # Google's own default page; its maximum is 2500
LIMIT_MAX = 2500
MAX_PAGES = 10
FREEBUSY_BATCH = 50  # Google answers at most 50 calendars per freeBusy query
LIST_MAX = 500  # calendars on one person's list
TEXT_MAX = 2000
ATTENDEES_MAX = 200
RESPONSES: dict[str, str] = {
    "needsAction": "needs_action",
    "accepted": "accepted",
    "tentative": "tentative",
    "declined": "declined",
}
WRITE_RESPONSES = {value: key for key, value in RESPONSES.items()}
# A 403 with one of these reasons is a quota, not a refusal (developers.google.com/calendar/api/guides/errors).
RATE_REASONS = frozenset(("rateLimitExceeded", "userRateLimitExceeded", "quotaExceeded"))
OWN_ROLES = frozenset(("owner", "writer"))
SEND_UPDATES = frozenset(("all", "externalOnly", "none"))
SKIPPED_TYPES = frozenset(("workingLocation",))  # a place marker, not time spent


def gcal_call(url: str, *, token: str, method: str = "GET", data: Json | None = None) -> tuple[int, Json]:
    """(status, JSON object), or status 0 when Google cannot answer. Tests replace this function.

    A refusal answers {"reason": <Google's reason word>} only, so no Google text reaches a caller."""
    request = urllib.request.Request(
        url, method=method, headers={"Accept": "application/json", "Authorization": "Bearer " + token}
    )
    if data is not None:
        request.data = json.dumps(data).encode()
        request.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(request, timeout=SECONDS) as answer:
            status, raw = int(answer.status), bytes(answer.read(HTTP_MAX_BYTES + 1))
    except urllib.error.HTTPError as refused:
        with refused:
            try:
                found = _dict(json.loads(refused.read(64 * 1024) or b"{}"))
                errors = _list(_dict(found.get("error")).get("errors")) or [{}]
                reason = str(_dict(errors[0]).get("reason") or "")[:64]
            except (ValueError, UnicodeError, OSError):
                reason = ""
            return refused.code, {"reason": reason} if reason else {}
    except (OSError, ValueError):
        return 0, {}
    if len(raw) > HTTP_MAX_BYTES:
        raise Problem("gcal_size", "This Google Calendar response is too large; narrow the range", 502)
    if not raw and status in (200, 204):
        return status, {}
    try:
        body: Any = json.loads(raw)
    except (ValueError, UnicodeError):
        return 0, {}
    return (status, cast(Json, body)) if isinstance(body, dict) else (0, {})


def _dict(value: Any) -> Json:
    return cast(Json, value) if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return cast("list[Any]", value) if isinstance(value, list) else []


def _quote(value: object) -> str:
    return urllib.parse.quote(str(value), safe="")


def _raise(status: int, body: Json, write: bool) -> NoReturn:
    if status == 400:
        raise Problem("gcal_query", "Google Calendar refused this request; check its fields and try again", 422)
    if (status == 403 and body.get("reason") in RATE_REASONS) or status == 429:
        raise Problem("gcal_unavailable", "Google Calendar's quota was reached; try again shortly", 503, retryable=True)
    if status in (401, 403):
        verb = "change" if write else "read"
        raise Problem(
            "gcal_refused",
            f"Google Calendar refused this {verb}; check the Workspace connection and calendar sharing",
            403,
        )
    if status == 410:
        raise Problem("gone", "This Google Calendar event was already deleted", 404)
    if status == 404:
        raise Problem("not_found", "Google Calendar could not find this calendar or event", 404)
    if status == 409:
        raise Problem("conflict", "Google Calendar already has this event", 409)
    raise Problem("gcal_unavailable", "Google Calendar did not answer; try again", 502, retryable=True)


def _call(
    path: str, token: str, *, method: str = "GET", data: Json | None = None, write: bool = False, **query: object
) -> Json:
    url = BASE + path
    given = {key: value for key, value in query.items() if value is not None}
    if given:
        url += "?" + urllib.parse.urlencode(given, doseq=True)
    status, body = gcal_call(url, token=token, method=method, data=data)
    body = _dict(body)
    if status in (200, 201, 204) and "error" not in body:
        return body
    _raise(status, body, write)


def _read_token(c: Conn, mailbox: str) -> str:
    # A key from before the read-only scopes were authorized holds only the full scope; Google refuses any other.
    row = _active(c) if c is not None else None
    held = set(_authorized(row)) if row else set[str]()
    readonly, full = READ_ONLY_SCOPES["calendar"], WORKSPACE_SCOPES["calendar"]
    scopes = full if row and not set(readonly) <= held and set(full) <= held else readonly
    return _mint(c, mailbox, scopes)


def _write_token(c: Conn, mailbox: str) -> str:
    return _mint(c, mailbox, WORKSPACE_SCOPES["calendar"])


def connected(c: Conn, address: str) -> bool:
    """Whether this address's calendar home is Google: the company key covers it, is active, and was delegated a
    calendar scope, and the person has no Connect Google grant of their own (that keeps the computer path)."""
    if _authority(c, address) != "workspace":
        return False
    held = set(_authorized(_active(c)))
    return any(set(table["calendar"]) <= held for table in (WORKSPACE_SCOPES, READ_ONLY_SCOPES))


# ---------------------------------------------------------------- normalizing


def _utc(text: object) -> str:
    dt = datetime.fromisoformat(str(text).replace("Z", "+00:00"))
    if dt.tzinfo is None:
        raise ValueError("no offset")
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _authuser(url: str | None, mailbox: str) -> str | None:
    if not url or not mailbox:
        return url or None
    return url + ("&" if "?" in url else "?") + urllib.parse.urlencode({"authuser": mailbox})


def _calendar(calendar_id: str, calendar: Json | None) -> Json:
    return calendar or {"ref": "google:" + calendar_id, "type": "google", "id": calendar_id, "name": calendar_id}


def _attendee(raw: Json, people: dict[str, Json] | None) -> Json:
    email = str(raw.get("email") or "").strip().lower()
    known = _dict((people or {}).get(email))
    kind = known.get("type") if known.get("type") in ("human", "bot") else "external"
    return {
        "type": kind,
        "id": known.get("id") if kind != "external" else None,
        "email": email or None,
        "name": str(known.get("name") or raw.get("displayName") or email)[:200],
        "response": RESPONSES.get(str(raw.get("responseStatus") or ""), "needs_action"),
    }


def normalize(
    event: object,
    as_mailbox: str,
    calendar_id: str,
    *,
    calendar: Json | None = None,
    people: dict[str, Json] | None = None,
    access_role: str = "",
    list_zone: str | None = None,
) -> Json | None:
    """One Google event instance as an occurrence, or None for one Tico never shows (cancelled, a working-location
    marker, or one without usable times)."""
    event = _dict(event)
    if not event.get("id"):
        return None
    if event.get("status") == "cancelled" or event.get("eventType") in SKIPPED_TYPES:
        return None
    viewer = str(as_mailbox or "").strip().lower()
    start, end = _dict(event.get("start")), _dict(event.get("end"))
    try:
        if start.get("dateTime") and end.get("dateTime"):
            all_day, begin, finish = False, _utc(start["dateTime"]), _utc(end["dateTime"])
        elif start.get("date") and end.get("date"):
            all_day, begin, finish = True, str(start["date"])[:10], str(end["date"])[:10]
            if date.fromisoformat(finish) <= date.fromisoformat(begin):
                return None
        else:
            return None
    except (TypeError, ValueError):
        return None
    if not all_day and finish <= begin:
        return None
    raw_attendees = [_dict(a) for a in _list(event.get("attendees")) if isinstance(a, dict)]
    mine = next(
        (a for a in raw_attendees if a.get("self") or str(a.get("email") or "").strip().lower() == viewer), None
    )
    organizer = str(_dict(event.get("organizer")).get("email") or "").strip().lower()
    involved = mine is not None or bool(organizer and organizer == viewer)
    # Google drops the summary (and attendees, links) where its sharing lets the viewer see only the time:
    # a free/busy-only calendar, or someone else's private event read with reader access.
    busy_only = access_role == "freeBusyReader" or (
        "summary" not in event and access_role not in OWN_ROLES and not involved
    )
    away = event.get("eventType") == "outOfOffice"
    first = _dict(event.get("originalStartTime"))
    original = str(first.get("dateTime") or first.get("date") or "")
    try:
        original_starts_at = _utc(original) if "T" in original else (original or begin)
    except ValueError:
        original_starts_at = begin
    meeting = None if busy_only else _meeting_url(event) or None
    if meeting and "meet.google.com/" in meeting:
        meeting = _authuser(meeting, viewer)
    title = "Busy" if busy_only else str(event.get("summary") or ("Away" if away else "(No title)"))[:240]
    attendees = [] if busy_only else [_attendee(a, people) for a in raw_attendees[:ATTENDEES_MAX]]
    status = "tentative" if event.get("status") == "tentative" else "confirmed"
    return {
        "occurrence_id": "google:" + calendar_id + ":" + str(event["id"]),
        "event_id": str(event.get("recurringEventId") or event["id"]),
        "calendar": _calendar(calendar_id, calendar),
        "home": "google",
        "kind": "away" if away else "event",
        "title": title,
        "start": begin,
        "end": finish,
        "all_day": all_day,
        "time_zone": start.get("timeZone") or list_zone or None,
        "attendees": attendees,
        "response": RESPONSES.get(str(mine.get("responseStatus") or ""), "needs_action")
        if mine and not busy_only
        else None,
        "busy_only": busy_only,
        "recurring": bool(event.get("recurringEventId") or event.get("recurrence")),
        "status": status,
        "meeting_url": meeting,
        "open_url": None if busy_only else _authuser(str(event.get("htmlLink") or ""), viewer),
        "links": {"thread": None, "meeting": None, "task": None, "automation": None, "attempt": None},
        "can": {"join": bool(meeting), "edit": access_role in OWN_ROLES and not busy_only},
        # Tico's own facts about a Google event (calendar_event_links) key on these: every attendee's copy shares
        # the iCalUID, and the original start names one instance of a series.
        "ical_uid": None if busy_only else event.get("iCalUID") or None,
        "original_starts_at": original_starts_at,
        "location": None if busy_only else (str(event["location"])[:TEXT_MAX] if event.get("location") else None),
    }


def _busy_block(calendar_id: str, calendar: Json | None, start: str, end: str) -> Json:
    return {
        "occurrence_id": "google:" + calendar_id + ":busy:" + start,
        "event_id": None,
        "calendar": _calendar(calendar_id, calendar),
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
        "links": {"thread": None, "meeting": None, "task": None, "automation": None, "attempt": None},
        "can": {"join": False, "edit": False},
        "ical_uid": None,
        "original_starts_at": start,
        "location": None,
    }


def _iso(value: When) -> str:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        return _utc(value)
    except (TypeError, ValueError):
        raise Problem("range", "A calendar range needs times with an offset", 422) from None


# ---------------------------------------------------------------- reads


def events(
    c: Conn,
    as_mailbox: str,
    calendar_id: str,
    start_utc: When,
    end_utc: When,
    *,
    q: str | None = None,
    limit: int = 250,
    calendar: Json | None = None,
    people: dict[str, Json] | None = None,
    bot: str | None = None,
) -> list[Json]:
    """Occurrences on one calendar in [start_utc, end_utc), read as `as_mailbox`, in start order.

    `people` maps a lower-case address to {type, id, name} for the roster; `bot` keeps only the events Tico wrote for
    that bot (its private `tico_bot` property). A coworker's calendar that refuses the event list (403/404: shared
    with nobody, or with this viewer for nothing but free/busy) answers its busy blocks instead."""
    cap = min(LIMIT_MAX, max(1, int(limit)))
    token = _read_token(c, as_mailbox)
    query: dict[str, Any] = {
        "singleEvents": "true",
        "orderBy": "startTime",
        "showDeleted": "false",
        "timeMin": _iso(start_utc),
        "timeMax": _iso(end_utc),
        "maxResults": min(PAGE_SIZE, cap),
        "q": q or None,
        "privateExtendedProperty": "tico_bot=" + str(bot) if bot else None,
    }
    path = "calendars/" + _quote(calendar_id) + "/events"
    out: list[Json] = []
    seen: set[str] = set()
    cursor: object = None
    for _ in range(MAX_PAGES):
        try:
            page = _call(path, token, **query, pageToken=cursor)
        except Problem as refused:
            own = calendar_id in ("primary", str(as_mailbox).strip().lower())
            if refused.status in (403, 404) and not own and not out:
                return _fallback(calendar_id, start_utc, end_utc, calendar, token, q, bot)
            raise
        if not isinstance(page.get("items", []), list):
            raise Problem("gcal_unavailable", "Google Calendar returned an incomplete page; try again", 502, True)
        items = _list(page.get("items"))
        role, list_zone = str(page.get("accessRole") or ""), str(page.get("timeZone") or "") or None
        for item in items:
            row = normalize(
                item, as_mailbox, calendar_id, calendar=calendar, people=people, access_role=role, list_zone=list_zone
            )
            if row and row["occurrence_id"] not in seen:
                seen.add(row["occurrence_id"])
                out.append(row)
        cursor = page.get("nextPageToken")
        if len(out) >= cap or not cursor or not isinstance(cursor, str):
            break
    return out[:cap]


def _fallback(
    calendar_id: str,
    start_utc: When,
    end_utc: When,
    calendar: Json | None,
    token: str,
    q: str | None,
    bot: str | None,
) -> list[Json]:
    # A search or a bot's tagged events cannot be answered from free/busy: nothing matched that this viewer may see.
    if q or bot:
        return []
    found = _freebusy(token, [calendar_id], start_utc, end_utc)
    if calendar_id not in found:
        raise Problem("gcal_refused", "Google Calendar shares nothing of this calendar with you", 403)
    return [_busy_block(calendar_id, calendar, start, end) for start, end in found[calendar_id]]


def event(
    c: Conn,
    as_mailbox: str,
    calendar_id: str,
    event_id: str,
    *,
    calendar: Json | None = None,
    people: dict[str, Json] | None = None,
) -> Json:
    """One event instance (or a series' first) as an occurrence; a cancelled one is not found."""
    raw = _call("calendars/" + _quote(calendar_id) + "/events/" + _quote(event_id), _read_token(c, as_mailbox))
    own = calendar_id in ("primary", str(as_mailbox).strip().lower())
    row = normalize(raw, as_mailbox, calendar_id, calendar=calendar, people=people, access_role="owner" if own else "")
    if row is None:
        raise Problem("not_found", "Google Calendar could not find this calendar or event", 404)
    return row


def calendar_list(c: Conn, as_mailbox: str) -> list[Json]:
    """The viewer's own Google calendar list: [{id, name, primary, access_role, time_zone}]."""
    token, out = _read_token(c, as_mailbox), list[Json]()
    cursor: object = None
    for _ in range(MAX_PAGES):
        page = _call("users/me/calendarList", token, maxResults=250, pageToken=cursor)
        if not isinstance(page.get("items", []), list):
            raise Problem("gcal_unavailable", "Google Calendar returned an incomplete list; try again", 502, True)
        for item in map(_dict, _list(page.get("items"))):
            if item.get("id") and not item.get("deleted"):
                out.append(
                    {
                        "id": str(item["id"]),
                        "name": str(item.get("summaryOverride") or item.get("summary") or item["id"])[:240],
                        "primary": bool(item.get("primary")),
                        "access_role": str(item.get("accessRole") or ""),
                        "time_zone": item.get("timeZone") or None,
                    }
                )
        cursor = page.get("nextPageToken")
        if not cursor or len(out) >= LIST_MAX:
            break
    return out[:LIST_MAX]


def time_zone(c: Conn, as_mailbox: str) -> str | None:
    """The mailbox's Google Calendar time zone (IANA), or None."""
    value = _call("users/me/settings/timezone", _read_token(c, as_mailbox)).get("value")
    return str(value)[:64] if isinstance(value, str) and value else None


def _freebusy(token: str, calendar_ids: list[str], start_utc: When, end_utc: When) -> dict[str, list[tuple[str, str]]]:
    found: dict[str, list[tuple[str, str]]] = {}
    ids = list(dict.fromkeys(calendar_ids))
    for offset in range(0, len(ids), FREEBUSY_BATCH):
        batch = ids[offset : offset + FREEBUSY_BATCH]
        body: Json = {"timeMin": _iso(start_utc), "timeMax": _iso(end_utc), "items": [{"id": cid} for cid in batch]}
        answer = _call("freeBusy", token, method="POST", data=body)
        calendars = _dict(answer.get("calendars"))
        for cid in batch:
            result = _dict(calendars.get(cid))
            if not result or result.get("errors") or not isinstance(result.get("busy"), list):
                continue
            spans: list[tuple[str, str]] = []
            for span in map(_dict, _list(result["busy"])):
                try:
                    lo, hi = _utc(span["start"]), _utc(span["end"])
                except (KeyError, TypeError, ValueError):
                    raise Problem(
                        "gcal_unavailable", "Google Calendar returned an invalid busy time", 502, True
                    ) from None
                if lo < hi:
                    spans.append((lo, hi))
            found[cid] = sorted(spans)
    return found


def freebusy(
    c: Conn, as_mailbox: str, calendar_ids: list[str], start_utc: When, end_utc: When
) -> dict[str, list[tuple[str, str]]]:
    """{calendar_id: [(start, end) UTC ISO]} for each calendar Google answered. A calendar Google could not check
    (not shared, unknown) is absent, never an empty list, so a caller reports it instead of calling it free."""
    return _freebusy(_read_token(c, as_mailbox), calendar_ids, start_utc, end_utc)


# ---------------------------------------------------------------- writes


def _when(value: object, all_day: bool, zone: str | None) -> dict[str, str]:
    if all_day:
        return {"date": str(value)[:10]}
    part: dict[str, str] = {"dateTime": value.isoformat() if isinstance(value, datetime) else str(value)}
    if zone:
        part["timeZone"] = zone
    return part


def _recurrence(fields: Json) -> list[str]:
    if fields.get("recurrence") is not None:
        rules: object = fields["recurrence"]
        return [str(rule) for rule in (_list(rules) if isinstance(rules, list) else [rules]) if rule]
    repeat = str(fields.get("repeat") or "").strip()
    if not repeat:
        return []
    if not (repeat.upper().startswith("RRULE:") or repeat.upper().startswith("FREQ=")):
        # Words ("weekly on mon") are calendar_recur's job; Google takes only the rule.
        parse = cast("Callable[[str], str]", importlib.import_module("backend.calendar_recur").parse_repeat)
        repeat = parse(repeat)
    return [repeat if repeat.upper().startswith("RRULE:") else "RRULE:" + repeat]


def google_body(fields: Json, *, partial: bool = False) -> Json:
    """A Google event body from the contract's POST /events fields (title, start, end, all_day, time_zone, repeat or
    recurrence, attendees as addresses or {email}, notes, location, add_meet, visibility, response) plus `actor`
    (who in Tico wrote it) and `bot` (the bot it is for). `partial` builds a patch: only the fields given."""
    body: Json = {}

    def has(key: str) -> bool:
        return key in fields and (fields[key] is not None or not partial)

    if has("title"):
        body["summary"] = str(fields.get("title") or "")[:1024]
    if has("notes"):
        body["description"] = str(fields.get("notes") or "")[:8000]
    if has("location"):
        body["location"] = str(fields.get("location") or "")[:TEXT_MAX]
    all_day, zone = bool(fields.get("all_day")), str(fields.get("time_zone") or "") or None
    if has("start"):
        body["start"] = _when(fields["start"], all_day, zone)
    if has("end"):
        body["end"] = _when(fields["end"], all_day, zone)
    if not partial and ("start" not in body or "end" not in body):
        raise Problem("time", "An event needs a start and an end", 422)
    if has("repeat") or has("recurrence"):
        body["recurrence"] = _recurrence(fields)
    if has("attendees"):
        attendees: list[dict[str, str]] = []
        for item in _list(fields.get("attendees")):
            given = _dict(item)
            email = str(given.get("email") if given else item or "").strip().lower()
            if "@" not in email or email in {a["email"] for a in attendees}:
                continue
            row = {"email": email}
            if str(given.get("response")) in WRITE_RESPONSES:
                row["responseStatus"] = WRITE_RESPONSES[str(given["response"])]
            attendees.append(row)
        body["attendees"] = attendees
    if fields.get("visibility") in ("private", "public", "default"):
        body["visibility"] = fields["visibility"]
    if fields.get("add_meet"):
        body["conferenceData"] = {
            "createRequest": {"requestId": uuid.uuid4().hex, "conferenceSolutionKey": {"type": "hangoutsMeet"}}
        }
    private: dict[str, str] = {}
    if fields.get("actor"):
        private["tico_actor"] = str(fields["actor"])[:200]
    if fields.get("bot"):
        private["tico_bot"] = str(fields["bot"])[:200]
    if private:
        body["extendedProperties"] = {"private": private}
    return body


def _send(send_updates: str) -> str:
    if send_updates not in SEND_UPDATES:
        raise Problem("send_updates", "sendUpdates is all, externalOnly or none", 422)
    return send_updates


def _written(raw: Json, as_mailbox: str, calendar_id: str) -> Json:
    row = normalize(raw, as_mailbox, calendar_id, access_role="owner")
    if row is None:
        raise Problem("gcal_unavailable", "Google Calendar returned an incomplete event; try again", 502, True)
    return row


def insert(c: Conn, as_mailbox: str, calendar_id: str, body: Json, *, send_updates: str = "all") -> Json:
    """Create the event on `calendar_id` as `as_mailbox` and answer it as an occurrence. `body` is the contract's
    POST fields (see `google_body`); a Meet link is asked for with conferenceDataVersion=1."""
    google = google_body(body)
    if not body.get("actor"):
        raise Problem("actor", "A Tico write names who made it", 422)
    raw = _call(
        "calendars/" + _quote(calendar_id) + "/events",
        _write_token(c, as_mailbox),
        method="POST",
        data=google,
        write=True,
        sendUpdates=_send(send_updates),
        conferenceDataVersion=1 if "conferenceData" in google else None,
    )
    return _written(raw, as_mailbox, calendar_id)


def patch(c: Conn, as_mailbox: str, calendar_id: str, event_id: str, body: Json, *, send_updates: str = "all") -> Json:
    """Change only the given fields. Google replaces extendedProperties.private whole on a patch, so the event's own
    Tico properties are read first and kept (a person's change must not untag a bot's event)."""
    google = google_body(body, partial=True)
    if not body.get("actor"):
        raise Problem("actor", "A Tico write names who made it", 422)
    token = _write_token(c, as_mailbox)
    path = "calendars/" + _quote(calendar_id) + "/events/" + _quote(event_id)
    if "extendedProperties" in google:
        current = _call(path, token, write=True)
        kept = _dict(_dict(current.get("extendedProperties")).get("private"))
        google["extendedProperties"] = {"private": {**kept, **_dict(google["extendedProperties"]).get("private", {})}}
    raw = _call(
        path,
        token,
        method="PATCH",
        data=google,
        write=True,
        sendUpdates=_send(send_updates),
        conferenceDataVersion=1 if "conferenceData" in google else None,
    )
    return _written(raw, as_mailbox, calendar_id)


def delete(c: Conn, as_mailbox: str, calendar_id: str, event_id: str, *, send_updates: str = "all") -> None:
    """Delete (cancel) the event; one Google already deleted (410) is done, not an error."""
    try:
        _call(
            "calendars/" + _quote(calendar_id) + "/events/" + _quote(event_id),
            _write_token(c, as_mailbox),
            method="DELETE",
            write=True,
            sendUpdates=_send(send_updates),
        )
    except Problem as refused:
        if refused.code != "gone":
            raise

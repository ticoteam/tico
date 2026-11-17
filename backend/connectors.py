"""The calendar appointment routes: a person's or bot's appointments, read live, and a booking made on Google.

The appointment routes (`/calendar/appointments`, `/calendar/upcoming`) read a calendar live on the server
(backend/gcal_live.py, a 60 s cache) with the server's credential for it (backend/google_tokens.py, the one door: the
Workspace key or the person's own Connect Google grant). A booking is made on Google in the request, its
`calendar_actions` row the receipt. No computer reads, books or holds a key for a calendar or a mailbox.
"""

from collections import OrderedDict
from datetime import datetime, timedelta, timezone
import hashlib
import json
import re
import threading
import time

from fastapi import Request

from pydantic import Field, field_validator, model_validator

from . import models as M
from . import gcal_live, routines
from . import google_workspace as workspace
from .store import H, P, Problem, encode


EMAIL = re.compile(r"^[^\s@]{1,200}@[A-Za-z0-9.-]{1,200}$")


def instant(value):
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("Calendar timestamps must include a timezone")
    return parsed


SNAPSHOT_DAYS = 14  # the appointment routes' window: from the start of today (UTC)
SNAPSHOT_EVENTS = 500
LIVE_SECONDS = 60  # one calendar's live read is reused this long
LIVE_CACHED = 256  # calendars held in the cache
MEET_TRIES = 5  # reads of a new event while Google makes its Meet link, one a second
_LIVE: OrderedDict = OrderedDict()  # address -> (expires, read at, events)
_LIVE_LOCK = threading.Lock()


def _recordable(occurrence, address):
    """One Google occurrence in the appointments' shape (CalendarEvent), or None for one that is no meeting of this
    person's: all day, time away, busy only, declined, or a hold with nobody else and no meeting link."""
    if occurrence.get("all_day") or occurrence.get("kind") != "event" or occurrence.get("busy_only"):
        return None
    if occurrence.get("response") == "declined":
        return None
    emails = []
    for attendee in occurrence.get("attendees") or []:
        email = str(attendee.get("email") or "").strip().lower()
        if email and email not in emails:
            emails.append(email)
    url = occurrence.get("meeting_url") or ""
    if len(set(emails) | {address}) < 2 and not url:
        return None
    stable = occurrence.get("ical_uid") or occurrence.get("event_id") or occurrence["occurrence_id"]
    return {
        "occurrence_id": hashlib.sha256(f"{stable}\n{occurrence['start']}".encode()).hexdigest()[:24],
        "event_id": str(occurrence["occurrence_id"]).removeprefix("google:" + address + ":"),
        "title": str(occurrence.get("title") or "Meeting")[:240],
        "start": occurrence["start"],
        "end": occurrence["end"],
        "attendees": emails[:100],
        "meeting_url": url,
    }


def live_appointments(c, address):
    """(read at, events) of `address`'s own Google calendar from the start of today for SNAPSHOT_DAYS, read as that
    mailbox and reused for LIVE_SECONDS. Raises gcal_live's Problem when Google cannot answer."""
    now = time.monotonic()
    with _LIVE_LOCK:
        hit = _LIVE.get(address)
        if hit and hit[0] > now:
            _LIVE.move_to_end(address)
            return hit[1], list(hit[2])
    start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    found = gcal_live.events(c, address, address, start, start + timedelta(days=SNAPSHOT_DAYS), limit=SNAPSHOT_EVENTS)
    rows = [row for row in (_recordable(o, address) for o in found) if row][:SNAPSHOT_EVENTS]
    at = H.now()
    with _LIVE_LOCK:
        _LIVE[address] = (now + LIVE_SECONDS, at, rows)
        _LIVE.move_to_end(address)
        while len(_LIVE) > LIVE_CACHED:
            _LIVE.popitem(last=False)
    return at, list(rows)


def forget_live(address=None):
    """Drop the cached read of one calendar (a booking just changed it), or of every calendar."""
    with _LIVE_LOCK:
        if address is None:
            _LIVE.clear()
        else:
            _LIVE.pop(address, None)


def appointments_view(c, address):
    """The appointments answer for one calendar, read live from Google with the server's credential for it (the
    person's own Connect Google grant or the Workspace key). A calendar the server holds none for, or a Google
    failure, answers stale with its kind, never an error page, so the Recorder and the native view keep their last
    list."""
    if not gcal_live.connected(c, address):
        return {"calendar": address, "events": [], "updated_at": None, "stale": True, "error": "not_connected"}
    try:
        at, events = live_appointments(c, address)
    except Problem as failed:
        return {"calendar": address, "events": [], "updated_at": None, "stale": True, "error": failed.code}
    return {"calendar": address, "events": events, "updated_at": at, "version": 1, "stale": False}


class CalendarAppointmentCreate(M.Contract):
    calendar: str = Field(default="", max_length=320)
    title: str = Field(min_length=1, max_length=240)
    start: str = Field(max_length=50)
    end: str = Field(max_length=50)
    attendees: list[str] = Field(default_factory=list, max_length=100)
    description: str = Field(default="", max_length=4000)
    add_meet: bool = True

    @field_validator("calendar")
    @classmethod
    def calendar_address(cls, value):
        value = value.lower()
        if value and not EMAIL.fullmatch(value):
            raise ValueError("Calendar must be an email address")
        return value

    @field_validator("attendees")
    @classmethod
    def attendee_addresses(cls, values):
        normalized = []
        for value in values:
            value = str(value).strip().lower()
            if not EMAIL.fullmatch(value) or value in normalized:
                raise ValueError("Calendar attendees must be distinct email addresses")
            normalized.append(value)
        return normalized

    @model_validator(mode="after")
    def appointment_interval(self):
        start, end = instant(self.start), instant(self.end)
        if end <= start or (end - start).total_seconds() > 7 * 24 * 60 * 60:
            raise ValueError("Calendar appointment interval is invalid")
        return self


def calendar_action(row):
    """The durable scheduling record (its description stays with the event, never in this answer)."""
    out = {
        "id": row["id"],
        "requested_by": row["requested_by"],
        "calendar": row["calendar_email"],
        "title": row["summary"],
        "start": row["start"],
        "end": row["end"],
        "attendees": json.loads(row["attendees"] or "[]"),
        "add_meet": bool(row["add_meet"]),
        "status": row["status"],
        "result": json.loads(row["result"] or "{}"),
        "error": row["error"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }
    return out


def human_of(c, address):
    """A mailbox's person; a known shared inbox has an address but no person id."""
    address = str(address or "").strip().lower()
    row = c.execute("SELECT id,email FROM humans WHERE lower(email)=?", (address,)).fetchone()
    if row:
        return row
    from . import mailbox_access

    mailbox = mailbox_access._row(c, address)
    if mailbox and mailbox["status"] == "active":
        if mailbox["person_id"]:
            return c.execute("SELECT id,email FROM humans WHERE id=?", (mailbox["person_id"],)).fetchone()
    from .views import roster

    people = roster(c)
    found = routines.person_for_mailbox(address, routines.message_bot_mailboxes(c, people), people)
    if found:
        return c.execute("SELECT id,email FROM humans WHERE id=?", (found["id"],)).fetchone()
    if mailbox and mailbox["status"] == "active" and mailbox["kind"] == "shared":
        return {"id": None, "email": address}
    return None


def calendar_allowed(c, who, address, person=None, change=False):
    """A calendar follows its mailbox (backend/mailbox_access.py): reading needs a level on it, changing it Read and
    write (the company's Change calendar is checked by the caller). Either the calendar's address or its person's
    email counts. A bot also reads, never changes, its operator's calendar, as before."""
    from . import mailbox_access

    email = str(person["email"] or "").strip().lower() if person else ""
    named = list(dict.fromkeys(a for a in (address, email) if a))
    levels = [mailbox_access.level(c, who, a) for a in named]
    if change:
        if "read_write" in levels:
            return
        raise Problem("forbidden", "Changing that calendar needs Read and write on its mailbox", 403)
    if any(level != "none" for level in levels):
        return
    if who.role == "bot":
        row = c.execute(
            "SELECT h.email FROM bot_config b JOIN humans h ON h.id=b.owner_id WHERE b.bot=?",
            (H.actor_id(who.actor),),
        ).fetchone()
        operator = str(row["email"] if row else "").strip().lower()
        if operator and operator in named:
            return
    raise Problem("forbidden", "That calendar is not yours to read or schedule on", 403)


def install(app, store, execution, mutate):
    CALENDAR_OFF = (
        "The company turned Create and change calendar events off in Tico (Tools > Settings > Email > What Tico "
        "may do), so Tico will not add the event"
    )

    def calendar_person(c, address):
        """The person a calendar belongs to and the address its events are made as: the mailbox their message bot
        declares, else their email. Either address names the calendar."""
        address = str(address or store.settings.owner_email).strip().lower()
        if not EMAIL.fullmatch(address):
            raise Problem("calendar", "Calendar must be an email address", 422)
        person = human_of(c, address)
        if not person:
            raise Problem("not_found", "Calendar is not on the company roster", 404)
        from .views import roster

        people = roster(c)
        rostered = P.person(person["id"], people)
        if rostered:
            address = routines.mailbox_of(rostered, routines.message_bot_mailboxes(c, people))
        return person, address

    @app.get("/api/v2/calendar/appointments")
    def appointments(request: Request, calendar: str = ""):
        who = request.state.identity
        if who.role not in ("bot", "human", "owner"):
            raise Problem("identity", "Calendar appointments are available to bots and people", 403)
        # Read outside the write lock; the audit event is written in a short transaction before anything is returned.
        with store.read() as c:
            person, address = calendar_person(c, calendar)
            calendar_allowed(c, who, address, person)
            view = appointments_view(c, address)
        with store.transaction() as c:
            H.event(c, who.actor, "calendar.read", address, {})
        return view

    @app.post("/api/v2/calendar/appointments")
    def schedule_appointment(request: Request, body: CalendarAppointmentCreate):
        """Book one appointment, made on Google in this request with the server's credential for the calendar, without
        Google's own invitations: an invitation is Tico's email, frozen with its Meet link and queued through the
        send rules and approvals (calendar_action_id). The calendar_actions row is the receipt, settled here; an
        answer Google did not give is `unknown`, never retried. A calendar the server holds no credential for is
        refused; no computer books one."""
        who = request.state.identity
        if who.role not in ("bot", "human", "owner"):
            raise Problem("identity", "Only a bot or person may schedule an appointment", 403)
        made = {}

        def work(c):
            person, address = calendar_person(c, body.calendar)
            calendar_allowed(c, who, address, person, change=True)
            if not workspace.permissions(c)["change_calendar"]:
                raise Problem("company_off", CALENDAR_OFF, 403)
            if body.attendees:
                from . import mailbox_access
                from .email_sending import effective

                if effective(c, address, H.actor_id(who.actor) if who.role == "bot" else "")["mode"] == "never":
                    raise Problem("policy", "Never blocks this inbox's invitations", 403)
                if not mailbox_access.may(c, who.actor, address)["send"]:
                    # The invitation is an email from this inbox: one that could not be queued is refused now, before
                    # anything is made on Google.
                    raise Problem("forbidden", "Inviting people from that calendar needs Send on its inbox", 403)
            start = instant(body.start)
            if start.astimezone(timezone.utc) < datetime.now(timezone.utc):
                raise Problem("start", "Calendar appointments must start in the future", 422)
            known = {str(row["email"] or "").lower() for row in c.execute("SELECT email FROM humans")}
            outside = [address for address in body.attendees if address.lower() not in known]
            if outside:
                # An invitation outside the company is a send, which stays the owner's whatever this install allows
                # other bots (backend/strategy_acts.py).
                from . import strategy_acts

                strategy_acts.gate(c, who.actor, "send", f"invite {', '.join(outside[:3])} to \"{body.title}\"")
            if who.role == "bot" and store.settings.block_external_invites:
                # TICO_BLOCK_EXTERNAL_INVITES=1: an invitation is an email from the company's calendar, so a
                # bot may invite only the people on the roster; anyone else is a person's act.
                if outside:
                    raise Problem(
                        "external_attendee",
                        "A bot may invite only people on the company roster; "
                        + ", ".join(outside[:3])
                        + " is not. Put the time in a draft for a person to send",
                        403,
                    )
            if not gcal_live.connected(c, address):
                raise Problem(
                    "not_connected",
                    "Tico can't reach that calendar: connect Google for it in Tools > Settings > Email",
                    403,
                    extra={"kind": "not_connected"},
                )
            action_id, now = H.new_id(), H.now()
            c.execute(
                "INSERT INTO calendar_actions(id,requested_by,calendar_email,summary,start,end,"
                "attendees,description,add_meet,status,result,error,created_at,updated_at,started_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,'{}','',?,?,?)",
                (
                    action_id,
                    who.actor,
                    address,
                    body.title,
                    body.start,
                    body.end,
                    encode(body.attendees),
                    body.description,
                    1 if body.add_meet else 0,
                    "running",
                    now,
                    now,
                    now,
                ),
            )
            made["id"] = action_id
            H.event(
                c,
                who.actor,
                "calendar.schedule.requested",
                action_id,
                {"calendar": address, "start": body.start, "attendees": len(body.attendees)},
            )
            return {
                "action": calendar_action(
                    c.execute("SELECT * FROM calendar_actions WHERE id=?", (action_id,)).fetchone()
                )
            }

        answer = mutate(request, body, work)
        if made:
            return book(made["id"])
        if answer["action"]["status"] == "running":
            # A retried request: the receipt as it stands now, never a second booking.
            with store.read() as c:
                row = c.execute("SELECT * FROM calendar_actions WHERE id=?", (answer["action"]["id"],)).fetchone()
            return {"action": calendar_action(row)} if row else answer
        return answer

    def book(action_id):
        """Make the booked event on Google, then settle its receipt: succeeded (with the invitation queued),
        failed when Google refused it, or unknown when Google's answer never came."""
        with store.read() as c:
            row = dict(c.execute("SELECT * FROM calendar_actions WHERE id=?", (action_id,)).fetchone())
        address, actor = row["calendar_email"], row["requested_by"]
        attendees = json.loads(row["attendees"] or "[]")
        fields = {
            "title": row["summary"],
            "start": row["start"],
            "end": row["end"],
            "notes": row["description"] or None,
            "attendees": attendees,
            "add_meet": bool(row["add_meet"]),
            "actor": actor,
            **({"bot": H.actor_id(actor)} if str(actor).startswith("bot:") else {}),
        }
        # A fixed id: whatever happens to this request, Google can hold at most one event for this receipt.
        event_id = hashlib.sha256(action_id.encode()).hexdigest()[:32]
        status, result, error, raw = "unknown", {}, "", None
        try:
            with store.read() as c:
                raw = gcal_live.insert_raw(c, address, address, fields, send_updates="none", event_id=event_id)
                for _ in range(MEET_TRIES if row["add_meet"] else 0):
                    if gcal_live.meeting_url(raw):
                        break
                    time.sleep(1)
                    raw = gcal_live.get_raw(c, address, address, str(raw.get("id") or event_id))
            result = {"event_id": str(raw.get("id") or event_id), "meeting_url": gcal_live.meeting_url(raw)}
            if row["add_meet"] and not result["meeting_url"]:
                status, error = "failed", "Google made the event but not its Meet link, so no invitation went out"
            else:
                status = "succeeded"
        except Problem as refused:
            if refused.status in (401, 403, 404, 409, 422) and not refused.retryable:
                status, error = "failed", "Google Calendar refused the event (" + refused.code + ")"
            else:
                error = "Tico did not hear back from Google; check the calendar before booking again"
        except Exception:  # noqa: BLE001 - the receipt says unknown; nothing is retried
            error = "Tico did not hear back from Google; check the calendar before booking again"
        classification = raw_mail = None
        if status == "succeeded" and attendees and raw is not None:
            from .mail_lib import compose
            from . import email_sending

            start, end = instant(row["start"]), instant(row["end"])
            text = row["description"] or row["summary"]
            raw_mail = compose.build(attendees, row["summary"], text, from_addr=address)
            raw_mail = compose.with_invitation(raw_mail, raw, address, attendees, start, end, row["summary"])
            classification = {
                **email_sending.classify(app, store, email_sending.snapshot(raw_mail, address)),
                "lint": [],
            }
        forget_live(address)
        with store.transaction() as c:
            current = c.execute("SELECT status FROM calendar_actions WHERE id=?", (action_id,)).fetchone()
            if current and current["status"] == "running":
                if raw_mail is not None:
                    from . import email_sending

                    try:
                        send = email_sending.enqueue(
                            c,
                            execution.auth,
                            actor,
                            email_sending.Enqueue(mailbox=address, raw=raw_mail, calendar_action_id=action_id),
                            classification,
                            wait_for_gmail=True,  # the event is made: Gmail not answering lets the invitation wait
                        )
                        result["invitation_send_id"] = send["id"]
                    except Problem as refused:
                        status, error = (
                            "failed",
                            "The event was made, but its invitation was refused: " + refused.detail,
                        )
                now = H.now()
                c.execute(
                    "UPDATE calendar_actions SET status=?,result=?,error=?,updated_at=?,finished_at=? "
                    "WHERE id=? AND status='running'",
                    (status, encode(result), error, now, now, action_id),
                )
                H.event(
                    c,
                    actor,
                    "calendar.schedule." + status,
                    action_id,
                    {"calendar": address, "event_id": result.get("event_id", "")},
                )
                if result.get("event_id"):
                    from . import calendar_events, gcal_watch

                    written = raw or {}
                    gcal_watch.own_write(
                        c,
                        address,
                        result["event_id"],
                        sequence=written.get("sequence") if isinstance(written.get("sequence"), int) else None,
                        end=(written.get("end") or {}).get("dateTime") or row["end"],
                    )
                    calendar_events.emit(
                        c,
                        actor,
                        "created",
                        result["event_id"],
                        calendar_events.google_detail(address, "google:" + address + ":" + result["event_id"]),
                        mailbox=address,
                    )
            settled = c.execute("SELECT * FROM calendar_actions WHERE id=?", (action_id,)).fetchone()
        return {"action": calendar_action(settled)}

    @app.get("/api/v2/calendar/actions/{action_id}")
    def calendar_action_status(action_id: str, request: Request):
        who = request.state.identity
        if who.role not in ("bot", "human", "owner"):
            raise Problem("identity", "Only a bot or person may read a calendar action", 403)
        with store.read() as c:
            row = c.execute("SELECT * FROM calendar_actions WHERE id=?", (action_id,)).fetchone()
            if not row or (who.role != "owner" and row["requested_by"] != who.actor):
                raise Problem("not_found", "Calendar action not found", 404)
            return {"action": calendar_action(row)}

    def upcoming(request: Request):
        who = request.state.identity
        if who.role not in ("human", "owner"):
            raise Problem("identity", "Calendar snapshots are available only to signed-in people", 403)
        with store.read() as c:
            person = c.execute("SELECT id,email FROM humans WHERE id=?", (H.actor_id(who.actor),)).fetchone()
            if not person or not person["email"]:
                return {"events": [], "updated_at": None, "stale": True}
            try:
                _person, address = calendar_person(c, person["email"])
            except Problem:
                return {"events": [], "updated_at": None, "stale": True}
            view = appointments_view(c, address)
            view.pop("calendar", None)
            return view

    app.get("/api/v2/calendar/upcoming")(upcoming)
    # The existing native WebView calls this compatibility path. It has identical auth and data.
    app.get("/api/calendar/upcoming")(upcoming)

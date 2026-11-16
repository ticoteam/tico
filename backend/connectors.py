"""Bounded reads and queued writes through explicitly authorized local connectors, and the calendar appointment routes.

No computer holds a Google credential. A computer that owns a mailbox asks for a short-lived access token for it
(`/connectors/mail/workspace/token`), which the server takes from the Workspace key or the person's own Connect Google
grant (backend/google_tokens.py, the one door). Who may act for which mailbox is decided in backend/google_rights.py.

The appointment routes (`/calendar/appointments`, `/calendar/upcoming`) read a calendar live on the server
(backend/gcal_live.py, a 60 s cache), and a booking is made on Google in the request, its `calendar_actions` row the
receipt. No computer reads or books a calendar any more.
"""

from collections import OrderedDict
from datetime import datetime, timedelta, timezone
import hashlib
import json
import re
import threading
import time

from fastapi import Request
from typing import Literal

from pydantic import Field, field_validator, model_validator

from . import models as M
from . import gcal_live, google_connect, google_rights, reconnects, routines
from . import google_workspace as workspace
from .store import H, P, Problem, encode


EMAIL = re.compile(r"^[^\s@]{1,200}@[A-Za-z0-9.-]{1,200}$")


def instant(value):
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("Calendar timestamps must include a timezone")
    return parsed


class CalendarEvent(M.Contract):
    occurrence_id: str = Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9_-]+$")
    event_id: str = Field(default="", max_length=500)
    title: str = Field(min_length=1, max_length=240)
    start: str = Field(max_length=50)
    end: str = Field(max_length=50)
    attendees: list[str] = Field(default_factory=list, max_length=100)
    meeting_url: str = Field(default="", max_length=2000)

    @field_validator("attendees")
    @classmethod
    def emails(cls, values):
        normalized = []
        for value in values:
            value = str(value).strip().lower()
            if not EMAIL.fullmatch(value) or value in normalized:
                raise ValueError("Calendar attendees must be distinct email addresses")
            normalized.append(value)
        return normalized

    @field_validator("meeting_url")
    @classmethod
    def secure_url(cls, value):
        if value and not re.fullmatch(r"https://[^\s<>]{1,1992}", value):
            raise ValueError("Calendar meeting links must use HTTPS")
        return value

    @model_validator(mode="after")
    def interval(self):
        start, end = instant(self.start), instant(self.end)
        if end <= start or (end - start).total_seconds() > 7 * 24 * 60 * 60:
            raise ValueError("Calendar event interval is invalid")
        return self


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


class CalendarSnapshot(M.Contract):
    email: str = Field(min_length=3, max_length=320)
    # The start of today plus SNAPSHOT_DAYS (an older computer sends its next 24 hours, which still fits).
    events: list[CalendarEvent] = Field(default_factory=list, max_length=SNAPSHOT_EVENTS)

    @field_validator("email")
    @classmethod
    def email_address(cls, value):
        value = value.lower()
        if not EMAIL.fullmatch(value):
            raise ValueError("Calendar target must be an email address")
        return value


class CalendarPublish(M.Contract):
    snapshots: list[CalendarSnapshot] = Field(min_length=1, max_length=25)
    # The calendars read successfully this tick (plan v5 T); absent from an older runner, the snapshots' calendars.
    ok_accounts: list[str] | None = Field(default=None, max_length=200)


class WorkspaceToken(M.Contract):
    mailbox: str = Field(min_length=3, max_length=320)
    service: Literal["gmail", "calendar"]


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


class CalendarActionResult(M.Contract):
    status: str = Field(pattern=r"^(succeeded|failed|unknown)$")
    event_id: str = Field(default="", max_length=500)
    meeting_url: str = Field(default="", max_length=2000)
    error: str = Field(default="", max_length=200)

    @field_validator("meeting_url")
    @classmethod
    def meeting_link(cls, value):
        if value and not re.fullmatch(r"https://[^\s<>]{1,1992}", value):
            raise ValueError("Calendar meeting links must use HTTPS")
        return value

    @model_validator(mode="after")
    def result_shape(self):
        if self.status == "succeeded" and not self.event_id:
            raise ValueError("A succeeded calendar action must include the provider event id")
        return self


def calendar_action(row, *, connector=False):
    """The durable scheduling record; description is exposed only to its connector."""
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
    if connector:
        out["description"] = row["description"]
    return out


MAIL_BODY_MAX = 32 * 1024
MAIL_CLIP = {
    "thread_id": 200,
    "date": 80,
    "from_addr": 500,
    "from_header": 500,
    "subject": 998,
    "snippet": 2000,
    "list_id": 500,
    "to": 100,
    "cc": 100,
    "labels": 50,
    "attachments": 50,
    "rule_hits": 100,
}
MAIL_BATCH = 100


class MailMessage(M.Contract):
    msg_id: str = Field(min_length=1, max_length=200, pattern=r"^[A-Za-z0-9._-]+$")
    thread_id: str = Field(default="", max_length=200)
    epoch: int = Field(default=0, ge=0)
    date: str = Field(default="", max_length=80)
    from_addr: str = Field(default="", max_length=500)
    from_header: str = Field(default="", max_length=500)
    to: list[str] = Field(default_factory=list, max_length=100)
    cc: list[str] = Field(default_factory=list, max_length=100)
    subject: str = Field(default="", max_length=998)
    snippet: str = Field(default="", max_length=2000)
    labels: list[str] = Field(default_factory=list, max_length=50)
    body: str = Field(default="", max_length=MAIL_BODY_MAX)
    body_truncated: bool = False
    attachments: list = Field(default_factory=list, max_length=50)
    list_id: str = Field(default="", max_length=500)
    is_internal: bool = False
    has_unsubscribe: bool = False
    rule_hits: list = Field(default_factory=list, max_length=100)
    # Set by the computer from its copy of the owner's sends (connectors/mail/db.py). None from a computer that predates it.
    known_sender: bool | None = None
    send_facts: dict[str, str] = Field(default_factory=dict, max_length=5)

    # A field over its cap is cut, not refused: the batch is never acknowledged, so one refused
    # message came back first on every retry and stopped its mailbox's copy for hours. Only the
    # message id stays strict; it has to be exact.
    @model_validator(mode="before")
    @classmethod
    def clip(cls, data):
        if not isinstance(data, dict):
            return data
        data = dict(data)
        if isinstance(data.get("body"), str) and len(data["body"]) > MAIL_BODY_MAX:
            data["body"], data["body_truncated"] = data["body"][:MAIL_BODY_MAX], True
        for key, cap in MAIL_CLIP.items():
            if isinstance(data.get(key), (str, list)) and len(data[key]) > cap:
                data[key] = data[key][:cap]
        return data


class ConnectorFailure(M.Contract):
    account: str = Field(min_length=3, max_length=320)
    # Only the kind of failure reaches the cloud, never the provider's message or a local path.
    reason: Literal["signin", "network", "error", "delegation", "admin_policy"]


class ConnectorHealth(M.Contract):
    service: Literal["calendar", "mail"]
    failing: list[ConnectorFailure] = Field(default_factory=list, max_length=50)
    # The accounts this computer read successfully this tick (plan v5 T). Absent from an older runner: every account it
    # reports on, except the failing ones, counts as read.
    ok_accounts: list[str] | None = Field(default=None, max_length=200)


CONNECTOR_REASON = {
    "signin": "its Google sign-in was refused, so retrying won't fix it",
    "network": "Google or the network can't be reached; the Mac keeps retrying",
    "error": "the connector failed; the Mac keeps retrying",
    "admin_policy": "the Google Workspace for {domain} has a policy that blocks Tico; only that "
    "Workspace's administrator can allow it (Admin console > Security > API controls > "
    "App access control), and Tico does not ask for more access to get around it",
    "delegation": "the Google service account has no domain-wide delegation for the domain {domain}, "
    "so it can't act as this mailbox; add that domain's delegation in Google Workspace, "
    "or give the message bot a mailbox on a domain the key covers",
}


class MailPublish(M.Contract):
    mailbox: str = Field(min_length=3, max_length=320)
    messages: list[MailMessage] = Field(default_factory=list, max_length=MAIL_BATCH)
    deleted: list[str] = Field(default_factory=list, max_length=MAIL_BATCH)
    synced_at: str = Field(min_length=1, max_length=50)
    history_id: str | None = Field(default=None, max_length=80)

    @field_validator("mailbox")
    @classmethod
    def mailbox_address(cls, value):
        value = value.lower()
        if not EMAIL.fullmatch(value):
            raise ValueError("Mail target must be an email address")
        return value

    @field_validator("deleted")
    @classmethod
    def deleted_ids(cls, values):
        seen = []
        for value in values:
            value = str(value).strip()
            if not value or len(value) > 200 or not re.fullmatch(r"[A-Za-z0-9._-]+", value):
                raise ValueError("Deleted mail ids must be Gmail message ids")
            if value not in seen:
                seen.append(value)
        return seen

    @field_validator("synced_at")
    @classmethod
    def synced_stamp(cls, value):
        instant(value)
        return value


def _mail_fts(c, mailbox, msg_id, subject="", body="", from_addr="", to_text=""):
    c.execute("DELETE FROM mail_fts WHERE mailbox=? AND msg_id=?", (mailbox, msg_id))
    c.execute(
        "INSERT INTO mail_fts(subject,body,from_addr,to_text,mailbox,msg_id) VALUES(?,?,?,?,?,?)",
        (subject, body, from_addr, to_text, mailbox, msg_id),
    )


def _refresh_mailbox(c, address, person_id, runner_id, synced_at, watching_since):
    stats = c.execute(
        "SELECT count(*) AS n, min(epoch) AS oldest, max(epoch) AS newest "
        "FROM mail_messages WHERE mailbox=? AND deleted_at IS NULL",
        (address,),
    ).fetchone()
    c.execute(
        "INSERT INTO mail_mailboxes(address,person_id,runner_id,synced_at,message_count,"
        "oldest_epoch,newest_epoch,error,watching_since_at) VALUES(?,?,?,?,?,?,?,NULL,?) "
        "ON CONFLICT(address) DO UPDATE SET person_id=excluded.person_id,"
        "runner_id=excluded.runner_id,synced_at=excluded.synced_at,"
        "message_count=excluded.message_count,oldest_epoch=excluded.oldest_epoch,"
        "newest_epoch=excluded.newest_epoch,error=NULL,"
        "watching_since_at=COALESCE(mail_mailboxes.watching_since_at,excluded.watching_since_at)",
        (address, person_id, runner_id, synced_at, stats["n"], stats["oldest"], stats["newest"], watching_since),
    )


def human_of(c, address):
    """A connector mailbox's person; a known shared inbox has an address but no person id."""
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


def install_connectors(app, store, execution, mutate):
    def publisher(c, who):
        runner = execution.runner(c, who)
        if runner["owner_id"] not in (store.settings.processing_operators or (execution.auth.owner_id(c),)):
            raise Problem("forbidden", "This machine is not assigned the private app-connector capability", 403)
        return runner

    def member(c, who):
        """The computer making a call, and whether it is a publisher. Raises for a person, a bot or a revoked computer;
        each route then applies google_rights.refusal to the mailboxes it names."""
        runner = google_rights.runner_of(execution, c, who)
        return runner, google_rights.publishes(store, execution, c, runner)

    def reader(c, who, mailbox=None):
        """The gate of a connector call that a publisher or a computer owning a mailbox may make (plan v3 B, v4 O).
        Anyone else gets publisher()'s refusal, as before. With `mailbox`, a computer that owns nothing is told why about
        that mailbox first (workspace_owned or personal_grant, else forbidden)."""
        runner, named = member(c, who)
        owned = workspace.owned_mailboxes(c, runner["id"])
        if not named and not owned:
            if mailbox:
                google_rights.check(c, runner["id"], named, mailbox, owned)
            raise Problem("forbidden", google_rights.FORBIDDEN, 403)
        return runner, named, owned

    @app.get("/api/v2/runners/connectors")
    def assigned(request: Request):
        """Which connector jobs this computer runs: all of them when the owner named it an operator in
        TICO_PROCESSING_OPERATORS, and mail and calendar when it owns a mailbox a Google Workspace key or a person's
        Connect Google grant covers (google_workspace.owned_mailboxes)."""
        with store.read() as c:
            runner = execution.runner(c, request.state.identity)
            named = runner["owner_id"] in store.settings.processing_operators
            if named or workspace.owned_mailboxes(c, runner["id"]):
                return {"connectors": ["mail", "calendar"]}
            return {"connectors": []}

    google_connect.install(app, store, mutate)

    @app.get("/api/v2/connectors/calendar/targets")
    def targets(request: Request):
        with store.read() as c:
            runner, named, owned = reader(c, request.state.identity)
        # Every calendar is read live on the server now: a computer is given none to read. `hours` is what a computer
        # from before `days` reads.
        return {"people": [], "hours": 24, "days": SNAPSHOT_DAYS, "refresh_seconds": 45}

    @app.post("/api/v2/connectors/calendar/snapshots")
    def publish(request: Request, body: CalendarPublish):
        def work(c):
            runner, named, owned = reader(c, request.state.identity)
            seen = set()
            for snapshot in body.snapshots:
                if snapshot.email in seen:
                    raise Problem("duplicate", "Publish at most one snapshot per person", 422)
                seen.add(snapshot.email)
                google_rights.check(c, runner["id"], named, snapshot.email, owned)
                person = human_of(c, snapshot.email)
                if not person or not person["id"]:
                    raise Problem(
                        "not_found", "Calendar target is not on the " + store.settings.app_name + " roster", 404
                    )
                payload = {"events": [event.model_dump() for event in snapshot.events]}
                c.execute(
                    "INSERT INTO connector_snapshots(kind,owner,version,payload,updated_at,runner_id) "
                    "VALUES('calendar',?,1,?,?,?) ON CONFLICT(kind,owner) DO UPDATE SET "
                    "version=connector_snapshots.version+1,payload=excluded.payload,"
                    "updated_at=excluded.updated_at,runner_id=excluded.runner_id",
                    (person["id"], encode(payload), H.now(), runner["id"]),
                )
            read = list(body.ok_accounts) if body.ok_accounts is not None else list(seen)
            for account in read:
                google_rights.check(c, runner["id"], named, account, owned)
            google_rights.report(c, "calendar", runner["id"], H.now(), ok=read)
            reconnects.google_good(c, "calendar", read)  # a published calendar is that account working
            return {"ok": True, "snapshots": len(seen)}

        return mutate(request, body, work)

    @app.post("/api/v2/connectors/health")
    def connector_health(request: Request, body: ConnectorHealth):
        """See programmatically when something disconnected. The Mac reports each refresh: the accounts it could not read
        and the ones it did (plan v5 T). A failing account stays failing until a report lists it as read; a clean report
        is the heartbeat and clears only what it lists."""

        def work(c):
            runner, named, owned = reader(c, request.state.identity)
            failing = {f.account.strip().lower(): f for f in body.failing}
            read = [account.strip().lower() for account in body.ok_accounts] if body.ok_accounts is not None else None
            for account in list(failing) + list(read or []):
                google_rights.check(c, runner["id"], named, account, owned)
            allowed = google_rights.reportable(c, runner["id"], named, owned)
            if read is None:  # an older runner: every account it may report on, except the failing ones, was read
                read = sorted(account for account in allowed if account not in failing)
            label = "Calendar" if body.service == "calendar" else "Mail"
            messages = {
                account: (
                    f"{label} can't refresh for {account}: "
                    + CONNECTOR_REASON[f.reason].format(domain=account.rpartition("@")[2]),
                    f.reason,
                )
                for account, f in failing.items()
            }
            google_rights.report(
                c, body.service, runner["id"], H.now(), failing=messages, ok=read, reportable_set=allowed
            )
            # The tasks a refused sign-in stops get a card for the mailbox's person; an account this service no longer
            # lists works again for it (backend/reconnects.py).
            refused = [account for account, f in failing.items() if f.reason == "signin"]
            still = google_rights.failing_accounts(c, body.service, H.now())
            reconnects.google_report(
                c, body.service, refused, list(failing), ok=[account for account in read if account not in still]
            )
            return {"ok": not failing}

        return mutate(request, body, work)

    CALENDAR_OFF = (
        "The company turned Create and change calendar events off in Tico (Tools > Settings > Email > What Tico "
        "may do), so Tico will not add the event"
    )

    @app.post("/api/v2/connectors/calendar/actions/claim")
    def claim_calendar_action(request: Request):
        """Claim once: an uncertain provider result is never retried into a duplicate invite."""
        with store.transaction() as c:
            runner, named, owned = reader(c, request.state.identity)
            cutoff = H.shift(H.now(), minutes=-5)
            stale = c.execute(
                "SELECT id,requested_by FROM calendar_actions WHERE status='running' AND started_at<?", (cutoff,)
            ).fetchall()
            for row in stale:
                now = H.now()
                c.execute(
                    "UPDATE calendar_actions SET status='unknown',error=?,updated_at=?,finished_at=? "
                    "WHERE id=? AND status='running'",
                    ("The connector did not confirm the provider result", now, now, row["id"]),
                )
                H.event(c, row["requested_by"], "calendar.schedule.unknown", row["id"], {})
            if not workspace.permissions(c)["change_calendar"]:
                # What Tico may do has Change calendar off: what this computer would claim fails now rather than
                # lands later. Calendars it may not act for wait for their own computer, which fails them the same way.
                now = H.now()
                for row in c.execute(
                    "SELECT id,requested_by,calendar_email FROM calendar_actions WHERE status='pending'"
                ).fetchall():
                    if google_rights.refusal(c, runner["id"], named, row["calendar_email"], owned) is not None:
                        continue
                    c.execute(
                        "UPDATE calendar_actions SET status='failed',error=?,updated_at=?,finished_at=? "
                        "WHERE id=? AND status='pending'",
                        (CALENDAR_OFF, now, now, row["id"]),
                    )
                    H.event(
                        c,
                        row["requested_by"],
                        "calendar.schedule.failed",
                        row["id"],
                        {"calendar": row["calendar_email"], "event_id": ""},
                    )
                return {"action": None}
            # The first pending action on a calendar this computer may act for (plan v3 B): never the whole queue.
            row = next(
                (
                    found
                    for found in c.execute(
                        "SELECT * FROM calendar_actions WHERE status='pending' ORDER BY created_at,id"
                    ).fetchall()
                    if google_rights.refusal(c, runner["id"], named, found["calendar_email"], owned) is None
                ),
                None,
            )
            if not row:
                return {"action": None}
            now = H.now()
            c.execute(
                "UPDATE calendar_actions SET status='running',runner_id=?,started_at=?,updated_at=? WHERE id=?",
                (runner["id"], now, now, row["id"]),
            )
            row = c.execute("SELECT * FROM calendar_actions WHERE id=?", (row["id"],)).fetchone()
            return {"action": calendar_action(row, connector=True)}

    @app.post("/api/v2/connectors/calendar/actions/{action_id}/result")
    def finish_calendar_action(action_id: str, request: Request, body: CalendarActionResult):
        with store.transaction() as c:
            runner = google_rights.runner_of(execution, c, request.state.identity)
            row = c.execute("SELECT * FROM calendar_actions WHERE id=?", (action_id,)).fetchone()
            if not row or row["runner_id"] != runner["id"]:
                # Not the lease holder: only then does the gate apply, and the answer is the same as before.
                reader(c, request.state.identity)
            if not row:
                raise Problem("not_found", "Calendar action not found", 404)
            if row["runner_id"] != runner["id"]:
                raise Problem("forbidden", "This calendar action belongs to another connector", 403)
            if row["status"] in ("succeeded", "failed", "unknown"):
                return {"action": calendar_action(row)}
            if row["status"] != "running":
                raise Problem("state", "Calendar action is not running", 409)
            result = {"event_id": body.event_id, "meeting_url": body.meeting_url}
            now = H.now()
            c.execute(
                "UPDATE calendar_actions SET status=?,result=?,error=?,updated_at=?,finished_at=? WHERE id=?",
                (body.status, encode(result), body.error, now, now, action_id),
            )
            H.event(
                c,
                row["requested_by"],
                "calendar.schedule." + body.status,
                action_id,
                {"calendar": row["calendar_email"], "event_id": body.event_id},
            )
            return {
                "action": calendar_action(
                    c.execute("SELECT * FROM calendar_actions WHERE id=?", (action_id,)).fetchone()
                )
            }

    def calendar_person(c, address):
        """The person a calendar belongs to and the address the connector acts as: the mailbox their
        message bot declares, else their email. Either address names the calendar."""
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
        if status == "succeeded" and attendees:
            from connectors.mail import compose
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

    @app.get("/api/v2/connectors/mail/targets")
    def mail_targets(request: Request):
        with store.read() as c:
            runner, named, owned = reader(c, request.state.identity)
            targets = google_rights.visible(c, runner["id"], named, owned=owned)
            company = workspace.permissions(c)
        # The server watches every mailbox's mail itself (backend/gmail_watch.py), so no computer syncs one: `mailboxes`
        # is empty. `acts_for` are the mailboxes whose automation mail actions and queued sends this computer still
        # carries out (runner_support/connectors.py) until the server does those too.
        acts_for = [{"address": target["address"], "person_id": target["person_id"]} for target in targets]
        # What Tico may do (Tools), which the mail CLI the runner runs checks every write against
        # (connectors/mail/policy.py runner_company): it reads it here, over the runner's own registration.
        return {
            "mailboxes": [],
            "acts_for": acts_for,
            "batch": MAIL_BATCH,
            "retention_days": store.settings.mail_retention_days,
            "company": company,
        }

    @app.post("/api/v2/connectors/mail/messages")
    def mail_publish(request: Request, body: MailPublish):
        def work(c):
            runner, named, owned = reader(c, request.state.identity, body.mailbox)
            google_rights.check(c, runner["id"], named, body.mailbox, owned)
            seen = set()
            for message in body.messages:
                if message.msg_id in seen:
                    raise Problem("duplicate", "Publish a message at most once per batch", 422)
                seen.add(message.msg_id)
            person = human_of(c, body.mailbox)
            if not person:
                raise Problem("not_found", "Mail target is not on the " + store.settings.app_name + " roster", 404)
            now = H.now()
            from . import automations

            # The mailbox's first batch sets its watch and later batches keep it (automations.mail_watch); a mailbox
            # with no `mailboxes` row keeps it on its sync row until this computer route goes.
            kept = c.execute("SELECT watching_since_at FROM mail_mailboxes WHERE address=?", (body.mailbox,)).fetchone()
            watch = (kept and kept["watching_since_at"]) or automations.mail_watch(c, body.mailbox, body.synced_at)
            from . import mailbox_access
            from .entities import ingest

            box_id = str((mailbox_access.row_by_address(c, body.mailbox) or {}).get("id") or "")
            for message in body.messages:
                # A message seen for the first time is what a mail automation hears (backend/automations.py).
                fresh = not c.execute(
                    "SELECT 1 FROM mail_messages WHERE mailbox=? AND msg_id=?", (body.mailbox, message.msg_id)
                ).fetchone()
                c.execute(
                    "INSERT INTO mail_messages(mailbox,msg_id,thread_id,epoch,date,from_addr,from_header,"
                    "to_addresses,cc,subject,snippet,labels,body,body_truncated,attachments,"
                    "list_id,is_internal,has_unsubscribe,rule_hits,updated_at,deleted_at) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,NULL) "
                    "ON CONFLICT(mailbox,msg_id) DO UPDATE SET "
                    "thread_id=excluded.thread_id,epoch=excluded.epoch,date=excluded.date,"
                    "from_addr=excluded.from_addr,from_header=excluded.from_header,"
                    "to_addresses=excluded.to_addresses,cc=excluded.cc,subject=excluded.subject,"
                    "snippet=excluded.snippet,labels=excluded.labels,body=excluded.body,"
                    "body_truncated=excluded.body_truncated,attachments=excluded.attachments,"
                    "list_id=excluded.list_id,is_internal=excluded.is_internal,"
                    "has_unsubscribe=excluded.has_unsubscribe,rule_hits=excluded.rule_hits,"
                    "updated_at=excluded.updated_at,deleted_at=NULL",
                    (
                        body.mailbox,
                        message.msg_id,
                        message.thread_id,
                        message.epoch,
                        message.date,
                        message.from_addr,
                        message.from_header,
                        encode(message.to),
                        encode(message.cc),
                        message.subject,
                        message.snippet,
                        encode(message.labels),
                        message.body,
                        1 if message.body_truncated else 0,
                        encode(message.attachments),
                        message.list_id,
                        1 if message.is_internal else 0,
                        1 if message.has_unsubscribe else 0,
                        encode(message.rule_hits),
                        now,
                    ),
                )
                _mail_fts(
                    c,
                    body.mailbox,
                    message.msg_id,
                    message.subject,
                    message.body,
                    message.from_addr,
                    " ".join(message.to),
                )
                c.execute(
                    "UPDATE mail_messages SET send_facts=? WHERE mailbox=? AND msg_id=?",
                    (
                        encode(
                            {
                                key: str(message.send_facts.get(key) or "")[
                                    : 4096 if key == "delivery-status" else 1000
                                ]
                                for key in (
                                    "auto-submitted",
                                    "precedence",
                                    "return-path",
                                    "message-id",
                                    "delivery-status",
                                )
                            }
                        ),
                        body.mailbox,
                        message.msg_id,
                    ),
                )
                from .email_sending import record_bounces

                record_bounces(
                    c,
                    body.mailbox,
                    message.from_addr,
                    message.body + "\n" + message.send_facts.get("delivery-status", "")[:4096],
                )
                if fresh:
                    automations.on_mail(c, body.mailbox, message, watch)
                    headers = {
                        "from_addr": message.from_addr,
                        "from_header": message.from_header,
                        "list_id": message.list_id,
                        "has_unsubscribe": message.has_unsubscribe,
                        "send_facts": message.send_facts,
                    }
                    ingest.safely(c, "mail", ingest.from_mail, c, box_id, headers)
            for msg_id in body.deleted:
                existing = c.execute(
                    "SELECT epoch FROM mail_messages WHERE mailbox=? AND msg_id=?", (body.mailbox, msg_id)
                ).fetchone()
                epoch = (
                    existing["epoch"] if existing and existing["epoch"] else int(instant(body.synced_at).timestamp())
                )
                if existing:
                    c.execute(
                        "UPDATE mail_messages SET deleted_at=?, updated_at=? WHERE mailbox=? AND msg_id=?",
                        (now, now, body.mailbox, msg_id),
                    )
                else:
                    c.execute(
                        "INSERT INTO mail_messages(mailbox,msg_id,thread_id,epoch,date,from_addr,from_header,"
                        "to_addresses,cc,subject,snippet,labels,body,body_truncated,attachments,"
                        "list_id,is_internal,has_unsubscribe,rule_hits,updated_at,deleted_at) "
                        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (
                            body.mailbox,
                            msg_id,
                            "",
                            epoch,
                            "",
                            "",
                            "",
                            "[]",
                            "[]",
                            "",
                            "",
                            "[]",
                            "",
                            0,
                            "[]",
                            "",
                            0,
                            0,
                            "[]",
                            now,
                            now,
                        ),
                    )
                c.execute("DELETE FROM mail_fts WHERE mailbox=? AND msg_id=?", (body.mailbox, msg_id))
            _refresh_mailbox(c, body.mailbox, person["id"], runner["id"], body.synced_at, watch)
            c.execute(
                "UPDATE mailboxes SET watching_since_at=coalesce(watching_since_at,?) WHERE address=?",
                (watch, body.mailbox),
            )
            accepted = len(body.messages) + len(body.deleted)
            google_rights.report(c, "mail", runner["id"], now, ok=[body.mailbox])  # that account is working
            reconnects.google_good(c, "mail", [body.mailbox])  # a synced mailbox is that account working
            return {"accepted": accepted}

        return mutate(request, body, work)

    @app.post("/api/v2/connectors/mail/workspace/token")
    def workspace_token(request: Request, body: WorkspaceToken):
        """A short-lived access token, for one service, for a mailbox this computer owns: from the Google Workspace key,
        or from the person's own Connect Google grant, which wins (backend/google_tokens.py). Allowed for the mailboxes
        it owns (the connector: its sync and the actions the server leased it) and for those its turns hold a level on
        now (backend/mailbox_access.py: its bots', a live Assistant turn's person's), checked again on every mint. A
        turn's mailbox held read only gets the read-only scopes, and a personal grant never more than it holds (the
        answer's `scopes`, which the computer refuses writes by). Never through mutate(): tokens stay out of the
        idempotency store."""
        from . import google_tokens

        box = body.mailbox.strip().lower()
        with store.read() as c:
            runner, named = member(c, request.state.identity)
            auth = workspace.authority(c, box)
            if auth == "legacy":
                raise Problem(
                    "not_connected", "The Google Workspace does not cover " + box, 403, extra={"kind": "not_connected"}
                )
            owned = workspace.owned_mailboxes(c, runner["id"])
            from . import mailbox_access

            connector = mailbox_access.connector_for(c, runner["id"], named, owned)
            held = (
                "read_write"
                if box in owned and mailbox_access.level(c, connector, box) != "none"
                else google_rights.turn_level(c, runner["id"], box, execution.auth)
            )
            if held == "none":
                kind = "workspace_owned" if named else "not_this_mailbox"
                raise Problem(
                    kind,
                    google_rights.MESSAGES.get(
                        kind, "This computer does not run the message bot that reads " + box
                    ).format(box=box),
                    403,
                    extra={"kind": kind},
                )
            scopes = "allowed" if held == "read_write" else "read"
            if auth == "personal":
                return google_tokens.access(c, box, body.service, scopes)
            row = workspace.active(c)
            try:
                answer, failure = google_tokens.access(c, box, body.service, scopes), None
            except Problem as refused:
                answer, failure = None, refused
        with store.transaction() as c:
            # The outcome counts toward the key's failing clock (backend/google_workspace.py record_mint).
            workspace.record_mint(
                c,
                row,
                box,
                body.service,
                runner["id"],
                failure is None,
                "" if failure is None else failure.extra.get("kind", "other"),
            )
        if failure is not None:
            raise failure
        return answer

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

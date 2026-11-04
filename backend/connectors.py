"""Bounded reads and queued writes through explicitly authorized local connectors.

The cloud never receives Google credentials. A computer that owns a mailbox asks for a short-lived access token for it
(`/connectors/mail/workspace/token`, from the server's Google Workspace key; backend/google_workspace.py), and the
personal grants stay with the computer holding them. Calendar reads use the provider-normalized snapshot published by
the runner; writes are durable actions that the same runner claims once. Who may act for which mailbox is decided in
backend/google_rights.py.
"""

from datetime import datetime, timezone
import json
import re

from fastapi import Request
from typing import Literal

from pydantic import Field, field_validator, model_validator

from . import models as M
from . import google_connect, google_rights, reconnects, routines
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


class CalendarSnapshot(M.Contract):
    email: str = Field(min_length=3, max_length=320)
    events: list[CalendarEvent] = Field(default_factory=list, max_length=200)

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
        "id": row["id"], "requested_by": row["requested_by"],
        "calendar": row["calendar_email"], "title": row["summary"],
        "start": row["start"], "end": row["end"],
        "attendees": json.loads(row["attendees"] or "[]"),
        "add_meet": bool(row["add_meet"]), "status": row["status"],
        "result": json.loads(row["result"] or "{}"),
        "error": row["error"], "created_at": row["created_at"], "updated_at": row["updated_at"],
    }
    if connector:
        out["description"] = row["description"]
    return out


MAIL_BODY_MAX = 32 * 1024
MAIL_CLIP = {"thread_id": 200, "date": 80, "from_addr": 500, "from_header": 500, "subject": 998,
             "snippet": 2000, "list_id": 500, "to": 100, "cc": 100, "labels": 50, "attachments": 50,
             "rule_hits": 100}
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


CONNECTOR_REASON = {"signin": "its Google sign-in was refused, so retrying won't fix it",
                    "network": "Google or the network can't be reached; the Mac keeps retrying",
                    "error": "the connector failed; the Mac keeps retrying",
                    "admin_policy": "the Google Workspace for {domain} has a policy that blocks Tico; only that "
                                    "Workspace's administrator can allow it (Admin console > Security > API controls > "
                                    "App access control), and Tico does not ask for more access to get around it",
                    "delegation": "the Google service account has no domain-wide delegation for the domain {domain}, "
                                  "so it can't act as this mailbox; add that domain's delegation in Google Workspace, "
                                  "or give the message bot a mailbox on a domain the key covers"}


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
    c.execute("INSERT INTO mail_fts(subject,body,from_addr,to_text,mailbox,msg_id) VALUES(?,?,?,?,?,?)",
              (subject, body, from_addr, to_text, mailbox, msg_id))


def _refresh_mailbox(c, address, person_id, runner_id, synced_at, watching_since):
    stats = c.execute("SELECT count(*) AS n, min(epoch) AS oldest, max(epoch) AS newest "
                      "FROM mail_messages WHERE mailbox=? AND deleted_at IS NULL", (address,)).fetchone()
    c.execute("INSERT INTO mail_mailboxes(address,person_id,runner_id,synced_at,message_count,"
              "oldest_epoch,newest_epoch,error,watching_since_at) VALUES(?,?,?,?,?,?,?,NULL,?) "
              "ON CONFLICT(address) DO UPDATE SET person_id=excluded.person_id,"
              "runner_id=excluded.runner_id,synced_at=excluded.synced_at,"
              "message_count=excluded.message_count,oldest_epoch=excluded.oldest_epoch,"
              "newest_epoch=excluded.newest_epoch,error=NULL,"
              "watching_since_at=COALESCE(mail_mailboxes.watching_since_at,excluded.watching_since_at)",
              (address, person_id, runner_id, synced_at, stats["n"], stats["oldest"], stats["newest"], watching_since))


def human_of(c, address):
    """The person row a connector address belongs to: by roster email, else by a message bot's declared mailbox."""
    address = str(address or "").strip().lower()
    row = c.execute("SELECT id,email FROM humans WHERE lower(email)=?", (address,)).fetchone()
    if row:
        return row
    from .views import roster
    people = roster(c)
    found = routines.person_for_mailbox(address, routines.message_bot_mailboxes(c, people), people)
    return c.execute("SELECT id,email FROM humans WHERE id=?", (found["id"],)).fetchone() if found else None


def install_connectors(app, store, execution, mutate):
    def publisher(c, who):
        runner = execution.runner(c, who)
        if runner["owner_id"] not in (store.settings.processing_operators
                                      or (execution.auth.owner_id(c),)):
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
        TICO_PROCESSING_OPERATORS; mail and calendar when it owns a mailbox (a Google Workspace one whose message bot it
        hosts, or a person's Connect Google grant it holds); and mail while a person's Connect Google waits for a computer
        (the owner's when no operator is named, or the one hosting that person's message bot)."""
        with store.read() as c:
            runner = execution.runner(c, request.state.identity)
            named = runner["owner_id"] in store.settings.processing_operators
            if named:
                return {"connectors": ["mail", "calendar"]}
            if workspace.owned_mailboxes(c, runner["id"]):
                return {"connectors": ["mail", "calendar"]}
            waiting = google_connect.pending(c, runner["id"], google_rights.publishes(store, execution, c, runner))
            return {"connectors": ["mail"] if waiting else []}

    google_connect.install(app, store, member, mutate)

    @app.get("/api/v2/connectors/calendar/targets")
    def targets(request: Request):
        with store.read() as c:
            runner, named, owned = reader(c, request.state.identity)
            # The mailboxes the message bots manage, not every person on the roster: a bot's declared
            # mailbox, else its person's email. People who left (hidden) are left out.
            people = [{"id": box["person_id"], "email": box["address"]}
                      for box in google_rights.visible(c, runner["id"], named, owned=owned)]
        return {"people": people, "hours": 24, "refresh_seconds": 45}

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
                if not person:
                    raise Problem("not_found", "Calendar target is not on the "
                                  + store.settings.app_name + " roster", 404)
                payload = {"events": [event.model_dump() for event in snapshot.events]}
                c.execute("INSERT INTO connector_snapshots(kind,owner,version,payload,updated_at,runner_id) "
                          "VALUES('calendar',?,1,?,?,?) ON CONFLICT(kind,owner) DO UPDATE SET "
                          "version=connector_snapshots.version+1,payload=excluded.payload,"
                          "updated_at=excluded.updated_at,runner_id=excluded.runner_id",
                          (person["id"], encode(payload), H.now(), runner["id"]))
            read = list(body.ok_accounts) if body.ok_accounts is not None else list(seen)
            for account in read:
                google_rights.check(c, runner["id"], named, account, owned)
            google_rights.report(c, "calendar", runner["id"], H.now(), ok=read)
            reconnects.google_good(c, "calendar", read)      # a published calendar is that account working
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
            if read is None:       # an older runner: every account it may report on, except the failing ones, was read
                read = sorted(account for account in allowed if account not in failing)
            label = "Calendar" if body.service == "calendar" else "Mail"
            messages = {account: (f"{label} can't refresh for {account}: "
                                  + CONNECTOR_REASON[f.reason].format(domain=account.rpartition("@")[2]), f.reason)
                        for account, f in failing.items()}
            google_rights.report(c, body.service, runner["id"], H.now(), failing=messages, ok=read,
                                 reportable_set=allowed)
            # The tasks a refused sign-in stops get a card for the mailbox's person; an account this service no longer
            # lists works again for it (backend/reconnects.py).
            refused = [account for account, f in failing.items() if f.reason == "signin"]
            still = google_rights.failing_accounts(c, body.service, H.now())
            reconnects.google_report(c, body.service, refused, list(failing),
                                     ok=[account for account in read if account not in still])
            return {"ok": not failing}
        return mutate(request, body, work)

    @app.post("/api/v2/connectors/calendar/actions/claim")
    def claim_calendar_action(request: Request):
        """Claim once: an uncertain provider result is never retried into a duplicate invite."""
        with store.transaction() as c:
            runner, named, owned = reader(c, request.state.identity)
            cutoff = H.shift(H.now(), minutes=-5)
            stale = c.execute(
                "SELECT id,requested_by FROM calendar_actions WHERE status='running' "
                "AND started_at<?", (cutoff,)).fetchall()
            for row in stale:
                now = H.now()
                c.execute("UPDATE calendar_actions SET status='unknown',error=?,updated_at=?,finished_at=? "
                          "WHERE id=? AND status='running'",
                          ("The connector did not confirm the provider result", now, now, row["id"]))
                H.event(c, row["requested_by"], "calendar.schedule.unknown", row["id"], {})
            # The first pending action on a calendar this computer may act for (plan v3 B): never the whole queue.
            row = next((found for found in c.execute("SELECT * FROM calendar_actions WHERE status='pending' "
                                                     "ORDER BY created_at,id").fetchall()
                        if google_rights.refusal(c, runner["id"], named, found["calendar_email"], owned) is None), None)
            if not row:
                return {"action": None}
            now = H.now()
            c.execute("UPDATE calendar_actions SET status='running',runner_id=?,started_at=?,updated_at=? "
                      "WHERE id=?", (runner["id"], now, now, row["id"]))
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
            c.execute("UPDATE calendar_actions SET status=?,result=?,error=?,updated_at=?,finished_at=? "
                      "WHERE id=?", (body.status, encode(result), body.error, now, now, action_id))
            H.event(c, row["requested_by"], "calendar.schedule." + body.status, action_id,
                    {"calendar": row["calendar_email"], "event_id": body.event_id})
            return {"action": calendar_action(c.execute(
                "SELECT * FROM calendar_actions WHERE id=?", (action_id,)).fetchone())}

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

    def calendar_allowed(c, who, address, person=None):
        """People: the calendars whose mail they may read. Bots: the owner's and their operator's."""
        if who.role == "owner":
            return
        if who.role == "human":
            from .mail import visible_addresses
            if address in visible_addresses(c, who):
                return
        elif who.role == "bot":
            row = c.execute("SELECT h.email FROM bot_config b JOIN humans h ON h.id=b.owner_id WHERE b.bot=?",
                            (H.actor_id(who.actor),)).fetchone()
            named = {store.settings.owner_email, str(row["email"] if row else "").lower()} - {""}
            if address in named or (str(person["email"] or "").lower() if person else "") in named:
                return
        raise Problem("forbidden", "That calendar is not yours to read or schedule on", 403)

    @app.get("/api/v2/calendar/appointments")
    def appointments(request: Request, calendar: str = ""):
        who = request.state.identity
        if who.role not in ("bot", "human", "owner"):
            raise Problem("identity", "Calendar appointments are available to bots and people", 403)
        # Read outside the write lock; the audit event is written in a short transaction before anything is returned.
        with store.read() as c:
            person, address = calendar_person(c, calendar)
            calendar_allowed(c, who, address, person)
            row = c.execute("SELECT * FROM connector_snapshots WHERE kind='calendar' AND owner=?",
                            (person["id"],)).fetchone()
        with store.transaction() as c:
            H.event(c, who.actor, "calendar.read", address, {})
        if not row:
            return {"calendar": address, "events": [], "updated_at": None, "stale": True}
        payload = json.loads(row["payload"])
        stale = row["updated_at"] < H.shift(H.now(), seconds=-180)
        return {"calendar": address, "events": payload["events"], "updated_at": row["updated_at"],
                "version": row["version"], "stale": stale}

    @app.post("/api/v2/calendar/appointments")
    def schedule_appointment(request: Request, body: CalendarAppointmentCreate):
        who = request.state.identity
        if who.role not in ("bot", "human", "owner"):
            raise Problem("identity", "Only a bot or person may schedule an appointment", 403)

        def work(c):
            person, address = calendar_person(c, body.calendar)
            calendar_allowed(c, who, address, person)
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
                    raise Problem("external_attendee", "A bot may invite only people on the company roster; "
                                  + ", ".join(outside[:3]) + " is not. Put the time in a draft for a person to send", 403)
            action_id, now = H.new_id(), H.now()
            c.execute("INSERT INTO calendar_actions(id,requested_by,calendar_email,summary,start,end,"
                      "attendees,description,add_meet,status,result,error,created_at,updated_at) "
                      "VALUES(?,?,?,?,?,?,?,?,?,'pending','{}','',?,?)",
                      (action_id, who.actor, address, body.title, body.start, body.end,
                       encode(body.attendees), body.description, 1 if body.add_meet else 0, now, now))
            H.event(c, who.actor, "calendar.schedule.requested", action_id,
                    {"calendar": address, "start": body.start, "attendees": len(body.attendees)})
            return {"action": calendar_action(c.execute(
                "SELECT * FROM calendar_actions WHERE id=?", (action_id,)).fetchone())}
        return mutate(request, body, work)

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
            freshness = {row["address"]: row for row in c.execute("SELECT * FROM mail_mailboxes")}
        mailboxes = []
        for target in targets:
            address, person_id = target["address"], target["person_id"]
            row = freshness.get(address)
            mailboxes.append({
                "address": address, "person_id": person_id,
                "newest_epoch": row["newest_epoch"] if row else None,
                "oldest_epoch": row["oldest_epoch"] if row else None,
                "message_count": row["message_count"] if row else 0,
                "synced_at": row["synced_at"] if row else None,
                "error": row["error"] if row else None,
            })
        return {"mailboxes": mailboxes, "batch": MAIL_BATCH,
                "retention_days": store.settings.mail_retention_days}

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
                raise Problem("not_found", "Mail target is not on the "
                              + store.settings.app_name + " roster", 404)
            now = H.now()
            from . import automations
            # The mailbox's first batch sets its watch and later batches keep it (automations.mail_watch).
            watch = automations.mail_watch(c, body.mailbox, body.synced_at)
            for message in body.messages:
                # A message seen for the first time is what a mail automation hears (backend/automations.py).
                fresh = not c.execute("SELECT 1 FROM mail_messages WHERE mailbox=? AND msg_id=?",
                                                    (body.mailbox, message.msg_id)).fetchone()
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
                    (body.mailbox, message.msg_id, message.thread_id, message.epoch, message.date,
                     message.from_addr, message.from_header, encode(message.to), encode(message.cc),
                     message.subject, message.snippet, encode(message.labels), message.body,
                     1 if message.body_truncated else 0, encode(message.attachments), message.list_id,
                     1 if message.is_internal else 0, 1 if message.has_unsubscribe else 0,
                     encode(message.rule_hits), now))
                _mail_fts(c, body.mailbox, message.msg_id, message.subject, message.body,
                          message.from_addr, " ".join(message.to))
                if fresh:
                    automations.on_mail(c, body.mailbox, message, watch)
            for msg_id in body.deleted:
                existing = c.execute("SELECT epoch FROM mail_messages WHERE mailbox=? AND msg_id=?",
                                     (body.mailbox, msg_id)).fetchone()
                epoch = existing["epoch"] if existing and existing["epoch"] else int(instant(body.synced_at).timestamp())
                if existing:
                    c.execute("UPDATE mail_messages SET deleted_at=?, updated_at=? WHERE mailbox=? AND msg_id=?",
                              (now, now, body.mailbox, msg_id))
                else:
                    c.execute(
                        "INSERT INTO mail_messages(mailbox,msg_id,thread_id,epoch,date,from_addr,from_header,"
                        "to_addresses,cc,subject,snippet,labels,body,body_truncated,attachments,"
                        "list_id,is_internal,has_unsubscribe,rule_hits,updated_at,deleted_at) "
                        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (body.mailbox, msg_id, "", epoch, "", "", "", "[]", "[]", "", "", "[]", "",
                         0, "[]", "", 0, 0, "[]", now, now))
                c.execute("DELETE FROM mail_fts WHERE mailbox=? AND msg_id=?", (body.mailbox, msg_id))
            _refresh_mailbox(c, body.mailbox, person["id"], runner["id"], body.synced_at, watch)
            accepted = len(body.messages) + len(body.deleted)
            google_rights.report(c, "mail", runner["id"], now, ok=[body.mailbox])   # that account is working
            reconnects.google_good(c, "mail", [body.mailbox])  # a synced mailbox is that account working
            return {"accepted": accepted}
        return mutate(request, body, work)

    def bot_mailboxes_of(c, runner_id):
        return google_rights.bot_mailboxes(c, runner_id)

    @app.post("/api/v2/connectors/mail/workspace/token")
    def workspace_token(request: Request, body: WorkspaceToken):
        """A short-lived access token, for one service, for a mailbox this computer owns: the Google Workspace's, minted
        here from the server's key. Allowed for the mailboxes it owns (the sync job) and for those its message bots read
        on their turns (routines.token_mailboxes). Never through mutate(): tokens stay out of the idempotency store."""
        box = body.mailbox.strip().lower()
        with store.read() as c:
            runner, named = member(c, request.state.identity)
            auth = workspace.authority(c, box)
            if auth == "personal":
                raise Problem("personal_grant", google_rights.MESSAGES["personal_grant"].format(box=box), 403,
                              extra={"kind": "personal_grant"})
            if auth == "legacy":
                raise Problem("not_connected", "The Google Workspace does not cover " + box, 403,
                              extra={"kind": "not_connected"})
            owned = workspace.owned_mailboxes(c, runner["id"])
            if box not in owned and box not in bot_mailboxes_of(c, runner["id"]):
                kind = "workspace_owned" if named else "not_this_mailbox"
                raise Problem(kind, google_rights.MESSAGES.get(kind, "This computer does not run the message bot that "
                                                               "reads " + box).format(box=box), 403,
                              extra={"kind": kind})
            row = workspace.active(c)
            if row is None:
                raise Problem("not_connected", "The Google Workspace connection is not set up", 403,
                              extra={"kind": "not_connected"})
            try:
                answer, failure = workspace.mint(c, box, body.service, row=row), None
            except Problem as refused:
                answer, failure = None, refused
        with store.transaction() as c:
            # The outcome counts toward the key's failing clock (backend/google_workspace.py record_mint).
            workspace.record_mint(c, row, box, body.service, runner["id"], failure is None,
                                  "" if failure is None else failure.extra.get("kind", "other"))
        if failure is not None:
            raise failure
        return answer

    def upcoming(request: Request):
        who = request.state.identity
        if who.role not in ("human", "owner"):
            raise Problem("identity", "Calendar snapshots are available only to signed-in people", 403)
        with store.read() as c:
            person = c.execute("SELECT id FROM humans WHERE id=?", (H.actor_id(who.actor),)).fetchone()
            row = c.execute("SELECT * FROM connector_snapshots WHERE kind='calendar' AND owner=?",
                            (person["id"],)).fetchone()
            if not row:
                return {"events": [], "updated_at": None, "stale": True}
            payload = json.loads(row["payload"])
            stale = row["updated_at"] < H.shift(H.now(), seconds=-180)
            return {"events": payload["events"], "updated_at": row["updated_at"],
                    "version": row["version"], "stale": stale}

    app.get("/api/v2/calendar/upcoming")(upcoming)
    # The existing native WebView calls this compatibility path. It has identical auth and data.
    app.get("/api/calendar/upcoming")(upcoming)

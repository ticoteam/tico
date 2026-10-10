"""Contact support: a person writes to the Tico team from the app, and the replies come back to it.

A request goes to Tico HQ (`POST <TICO_HQ_URL>/v1/support`, hq/support.py) only when a person submits the form. Nothing here
is automatic and nothing is sent in demo mode. The counting choice (PRIVACY.md) does not block it: this is a message the
person chose to send, and what goes with it is shown on the form and switchable there.

HQ answers with a ticket id and a secret for that ticket alone. This server keeps both (the secret never reaches a
browser), and asks HQ for the status and messages of the caller's open tickets when the page asks (`refresh`), never on a
timer of its own. Every route shows a person their own tickets and no one else's. Ticket text, from either side, is data:
it is stored as plain text and the page escapes it.

`TICO_SUPPORT=off` removes the feature (an air-gapped install); PRIVACY.md and docs/support.md describe it.
"""
import json
import logging
import os
import re
import secrets
from typing import Literal

import httpx

from pydantic import Field

from . import diagnostics
from .census import hq_url
from .models import Contract
from .replication import rehearsal_on
from .store import H, Problem, encode

log = logging.getLogger("tico.support")

SCHEMA = """
CREATE TABLE IF NOT EXISTS support_tickets(
 id TEXT PRIMARY KEY, actor TEXT NOT NULL, created TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'open',
 message TEXT NOT NULL, email TEXT NOT NULL DEFAULT '', sent_json TEXT NOT NULL DEFAULT '{}',
 hq_id TEXT NOT NULL, hq_secret TEXT NOT NULL, messages_json TEXT NOT NULL DEFAULT '[]',
 seen_id INTEGER NOT NULL DEFAULT 0, checked TEXT, request_key TEXT);
CREATE INDEX IF NOT EXISTS support_tickets_actor ON support_tickets(actor, created);
"""
MAX_MESSAGE = 4000
PER_DAY = 10                 # new tickets one person may file in a day; HQ limits the address as well
FRESH_S = 30                 # an open ticket is asked about at most this often
CLOSED_FRESH_S = 6 * 3600    # a closed one, in case the team reopened it
LISTED = 50
HQ_WAIT = 8
TRANSPORT = None             # tests replace it with an httpx.MockTransport: nothing reaches the network
CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f  ‪-‮⁦-⁩]")
EMAIL = re.compile(r"[^@\s<>\",;()\[\]\\]{1,64}@[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?\.[A-Za-z]{2,24}")
VERSION = re.compile(r"[0-9]{1,4}\.[0-9]{1,4}\.[0-9]{1,4}(?:-[0-9A-Za-z][0-9A-Za-z.-]{0,31})?")


class SupportTicket(Contract):
    message: str = Field(min_length=1, max_length=MAX_MESSAGE)
    email: str = Field(default="", max_length=254)
    include_ids: bool = True            # the version and the install ID go with it, unless the person unticks it
    diagnostics: str = Field(default="", max_length=64)     # the digest of the bundle the person previewed (backend/diagnostics.py)


class SupportMessage(Contract):
    message: str = Field(min_length=1, max_length=MAX_MESSAGE)
    diagnostics: str = Field(default="", max_length=64)


class BrowserFailure(Contract):
    kind: Literal["error", "unhandledrejection"]
    at: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{3})?Z$")
    file: str = Field(default="", max_length=80)
    line: int = Field(default=0, ge=0, le=10000000)
    column: int = Field(default=0, ge=0, le=10000000)
    count: int = Field(default=1, ge=1, le=1000000)


class DiagnosticsCapture(Contract):
    browser: list[BrowserFailure] = Field(default_factory=list, max_length=20)


class DiagnosticsEdit(Contract):
    id: str = Field(min_length=64, max_length=64)
    text: str = Field(max_length=diagnostics.MAX_BYTES)


def off_reason(settings):
    if settings.demo:
        return "demo"
    if settings.rehearsal or rehearsal_on():
        return "rehearsal"
    if os.environ.get("TICO_SUPPORT", "").strip().lower() in ("off", "0", "false", "no", "disabled"):
        return "TICO_SUPPORT"
    return ""


def require_on(settings):
    if off_reason(settings):
        raise Problem("support_off", "Contact support is not available here.", 409)


def require_person(who):
    """A person at their own browser: not a token, not the Assistant acting for them, not a bot or a computer. BotOps
    acting with a person's delegated rights (backend/app.py delegated_identity) files as that person; BotOps with its
    own rights is a bot and is refused."""
    if who.role not in ("owner", "human") or who.via_token or (who.via and not (who.via == "botops" and who.confirmed)):
        raise Problem("forbidden", "Only a signed-in person can contact support.", 403)


def clean_message(value):
    value = str(value or "").replace("\r\n", "\n").replace("\r", "\n")
    if CONTROL.search(value) or not value.strip():
        raise Problem("validation", "Write a message.", 422)
    if len(value) > MAX_MESSAGE:
        raise Problem("validation", f"Keep it under {MAX_MESSAGE} characters.", 422)
    return value.strip()


def clean_email(value):
    value = str(value or "").strip()
    if not value:
        return ""
    if len(value) > 254 or not EMAIL.fullmatch(value):
        raise Problem("validation", "That email address looks wrong.", 422)
    return value


def _ask_hq(method, path, *, body=None, secret=""):
    """One request to HQ, or a Problem the person can act on. Nothing from HQ's answer is logged."""
    headers = {"Accept": "application/json", "User-Agent": "tico-support"}
    if secret:
        headers["X-Ticket-Secret"] = secret
    try:
        with httpx.Client(timeout=HQ_WAIT, transport=TRANSPORT, follow_redirects=False) as http:
            return http.request(method, hq_url() + path, json=body, headers=headers)
    except httpx.HTTPError as exc:
        diagnostics.RING.handle(logging.LogRecord("tico.support", logging.WARNING, "", 0,
            "HQ did not answer (%s)", (type(exc).__name__,), None))
        raise Problem("support_unreachable", "Could not reach Tico support. Try again in a moment.", 502,
                      retryable=True) from None


def _answer(response, success):
    """HQ's JSON when the status is one of `success`, else the Problem for it."""
    if response.status_code in success:
        try:
            value = response.json()
            if isinstance(value, dict):
                return value
        except ValueError:
            pass
    if response.status_code == 429:
        raise Problem("support_busy", "Too many requests just now. Try again later.", 429, retryable=True)
    if response.status_code == 404:
        raise Problem("not_found", "Ticket not found", 404)
    if response.status_code == 409:
        raise Problem("support_closed", "This request is closed.", 409)
    if response.status_code == 422:
        raise Problem("validation", "Tico support did not accept that. Check the message and email.", 422)
    raise Problem("support_unreachable", "Could not reach Tico support. Try again in a moment.", 502, retryable=True)


def _messages(value):
    """HQ's message list cut down to the fields the page shows, whatever HQ sent."""
    out = []
    for item in (value if isinstance(value, list) else [])[:200]:
        if isinstance(item, dict) and isinstance(item.get("id"), int):
            out.append({"id": item["id"], "created": str(item.get("created") or "")[:30],
                        "from": "person" if item.get("from") == "person" else "staff",
                        "body": str(item.get("body") or "")[:8000],
                        "has_diagnostics": item.get("has_diagnostics") is True})
    return out


def view(row):
    """A ticket as the page sees it. The HQ secret and id are not in it."""
    messages = json.loads(row["messages_json"] or "[]")
    return {"id": row["id"], "created": row["created"], "status": row["status"], "message": row["message"],
            "email": row["email"], "sent": json.loads(row["sent_json"] or "{}"), "messages": messages,
            "unread": sum(1 for m in messages if m["from"] == "staff" and m["id"] > row["seen_id"])}


class Support:
    def __init__(self, store, settings, census):
        self.store, self.settings, self.census = store, settings, census
        self.previews = diagnostics.Previews()

    # ------------------------------------------------------------------ the form
    def compose(self, who):
        """What the form will send by default and where it goes; creates the install ID if it has none yet."""
        from . import releases
        version = releases.version()
        return {"enabled": True, "email": who.email or "", "version": version if VERSION.fullmatch(version) else "",
                "install_id": self.census.install_id(), "to": hq_url().split("://", 1)[-1].split("/", 1)[0],
                "max": MAX_MESSAGE, "diagnostics": True}

    def preview(self, who, app, browser=None):
        """Build the diagnostics bundle and keep it for this person, so the request that files the ticket sends these
        very bytes. Returns its digest and the text the page shows."""
        bundle = diagnostics.build(self.store, self.settings, app.state.auth, who, self.census,
                                   github=getattr(app.state, "github_app", None))
        if browser:
            from pathlib import Path
            ui = Path(__file__).resolve().parent.parent / "ui"
            files = {p.name for p in ui.glob("*.js")} | {p.name for p in (ui / "app").glob("*.js")} | {"app.bundle.js"}
            bundle["browser"] = [{**event.model_dump(), "file": event.file if event.file in files else ""} for event in browser]
            bundle = diagnostics.fit(bundle)
        text = diagnostics.canonical(bundle)
        return {"id": self.previews.keep(who.actor, bundle), "bytes": len(text.encode()), "text": text}

    def edit_preview(self, who, body):
        original = self.previews.take(who.actor, body.id)
        if original is None:
            raise Problem("diagnostics_stale", "This preview expired. Refresh diagnostics and review again.", 409)
        try:
            value = json.loads(body.text, parse_constant=lambda _: (_ for _ in ()).throw(ValueError("Invalid number")))
            diagnostics.validate_edit(value, original)
            if value.get("format") != diagnostics.FORMAT:
                raise ValueError("Keep the diagnostics format field unchanged.")
            with self.store.read() as c:
                from .views import roster
                people = roster(c).get("people") or []
                redactor = diagnostics.Redactor(diagnostics._domains(self.settings, {"people": people}),
                    [(b["slug"], [b.get("display_name")]) for b in H.bots(c)],
                    [(p.get("id", ""), [p.get("name"), p.get("email")]) for p in people])
            value = redactor.clean(diagnostics.allowed(value))
            value.setdefault("capture", {})["edited"] = True
            text = diagnostics.canonical(value)
            if len(text.encode()) > diagnostics.MAX_BYTES:
                raise ValueError("Keep diagnostics under 256 KB.")
        except (ValueError, TypeError, RecursionError) as exc:
            detail = str(exc) if not isinstance(exc, json.JSONDecodeError) else "Enter valid JSON before saving."
            raise Problem("validation", detail, 422) from None
        return {"id": self.previews.keep(who.actor, value), "bytes": len(text.encode()), "text": text}

    def attachment(self, who, key):
        bundle = self.previews.take(who.actor, key)
        if bundle is None:
            raise Problem("diagnostics_stale", "This preview expired. Refresh diagnostics and review again.", 409)
        return bundle

    def file(self, who, body, key=""):
        """Send the ticket to HQ, then keep HQ's id and secret. Nothing is kept when HQ refuses or cannot be reached."""
        message, email = clean_message(body.message), clean_email(body.email)
        with self.store.read() as c:
            if key:
                again = c.execute("SELECT * FROM support_tickets WHERE actor=? AND request_key=?",
                                  (who.actor, key)).fetchone()
                if again:
                    return view(again)
            since = H.shift(H.now(), hours=-24)
            recent = c.execute("SELECT count(*) FROM support_tickets WHERE actor=? AND created>?",
                               (who.actor, since)).fetchone()[0]
        if recent >= PER_DAY:
            raise Problem("support_busy", "That is enough requests for today. Try again tomorrow.", 429)
        sent, payload = {}, {"message": message}
        if email:
            payload["email"] = email
        if body.include_ids:
            details = self.compose(who)
            sent = {k: details[k] for k in ("version", "install_id") if details[k]}
            payload.update(sent)
        if body.diagnostics:
            payload["diagnostics"] = self.attachment(who, body.diagnostics)
        answer = _ask_hq("POST", "/v1/support", body=payload)
        if answer.status_code == 422 and "diagnostics" in payload:
            raise Problem("diagnostics_rejected", "Support could not accept this attachment. Review it or remove diagnostics to send just the message.", 422)
        made = _answer(answer, (201, 200))
        if "diagnostics" in payload:
            sent["diagnostics"] = len(diagnostics.canonical(payload["diagnostics"]).encode())
        hq_id, hq_secret = str(made.get("ticket_id") or ""), str(made.get("secret") or "")
        if not hq_id or not hq_secret:
            raise Problem("support_unreachable", "Could not reach Tico support. Try again in a moment.", 502, retryable=True)
        ticket_id = "sup_" + secrets.token_hex(6)
        with self.store.transaction() as c:
            c.execute("INSERT INTO support_tickets(id,actor,created,status,message,email,sent_json,hq_id,hq_secret,"
                      "request_key) VALUES(?,?,?,?,?,?,?,?,?,?)",
                      (ticket_id, who.actor, H.now(), "open", message, email, encode(sent), hq_id, hq_secret, key or None))
            H.event(c, who.actor, "support.filed", ticket_id, {"email": bool(email), "ids": bool({k: v for k, v in sent.items() if k != "diagnostics"}),
                                                              "diagnostics": "diagnostics" in sent})
            return view(c.execute("SELECT * FROM support_tickets WHERE id=?", (ticket_id,)).fetchone())

    def write(self, who, ticket_id, text, attachment=""):
        """Add a message to one of the caller's tickets."""
        text = clean_message(text)
        row = self._own(who, ticket_id)
        payload = {"message": text}
        if attachment:
            payload["diagnostics"] = self.attachment(who, attachment)
        answer = _ask_hq("POST", f"/v1/support/{row['hq_id']}/messages", body=payload, secret=row["hq_secret"])
        if answer.status_code == 422 and attachment:
            raise Problem("diagnostics_rejected", "Support could not accept this attachment. Review it or remove diagnostics to send just the message.", 422)
        _answer(answer, (201, 200))
        self._pull(row)
        return self.one(who, ticket_id)

    def remove(self, who, ticket_id):
        """Delete the ticket at HQ, then here."""
        row = self._own(who, ticket_id)
        answer = _ask_hq("DELETE", f"/v1/support/{row['hq_id']}", secret=row["hq_secret"])
        if answer.status_code not in (200, 404):        # 404: the team already deleted it
            _answer(answer, (200,))
        with self.store.transaction() as c:
            c.execute("DELETE FROM support_tickets WHERE id=?", (ticket_id,))
            H.event(c, who.actor, "support.deleted", ticket_id, {})
        return {"deleted": True}

    # ------------------------------------------------------------------ the list
    def _own(self, who, ticket_id):
        with self.store.read() as c:
            row = c.execute("SELECT * FROM support_tickets WHERE id=? AND actor=?", (ticket_id, who.actor)).fetchone()
        if not row:
            raise Problem("not_found", "Ticket not found", 404)
        return row

    def one(self, who, ticket_id):
        return view(self._own(who, ticket_id))

    def listing(self, who):
        with self.store.read() as c:
            rows = c.execute("SELECT * FROM support_tickets WHERE actor=? ORDER BY created DESC, id DESC LIMIT ?",
                             (who.actor, LISTED)).fetchall()
        tickets = [view(r) for r in rows]
        return {"enabled": True, "tickets": tickets, "unread": sum(t["unread"] for t in tickets)}

    def _pull(self, row):
        """Ask HQ about one ticket and keep the answer. Returns False when HQ could not be reached."""
        try:
            answer = _ask_hq("GET", f"/v1/support/{row['hq_id']}", secret=row["hq_secret"])
        except Problem:
            return False
        if answer.status_code == 404:
            status, messages = "gone", None            # deleted at HQ: stop asking
        elif answer.status_code == 200:
            try:
                data = answer.json()
            except ValueError:
                return False
            status = data.get("status") if data.get("status") in ("open", "answered", "closed") else row["status"]
            messages = _messages(data.get("messages"))
        else:
            return False
        with self.store.transaction() as c:
            c.execute("UPDATE support_tickets SET status=?, messages_json=COALESCE(?,messages_json), checked=? WHERE id=?",
                      (status, encode(messages) if messages is not None else None, H.now(), row["id"]))
        return True

    def refresh(self, who):
        """Ask HQ about the caller's tickets that are due, then list them. HQ being down keeps what was known."""
        now = H.now()
        with self.store.read() as c:
            rows = c.execute("SELECT * FROM support_tickets WHERE actor=? AND status!='gone' "
                             "ORDER BY created DESC LIMIT 20", (who.actor,)).fetchall()
        failed = False
        for row in rows:
            wait = CLOSED_FRESH_S if row["status"] == "closed" else FRESH_S
            if row["checked"] and row["checked"] > H.shift(now, seconds=-wait):
                continue
            if not self._pull(row):
                failed = True
                break                                  # HQ is down: do not ask again for every ticket
        result = self.listing(who)
        result["refresh_failed"] = failed
        return result

    def read(self, who, ticket_id):
        row = self._own(who, ticket_id)
        newest = max([m["id"] for m in json.loads(row["messages_json"] or "[]")] or [0])
        with self.store.transaction() as c:
            c.execute("UPDATE support_tickets SET seen_id=MAX(seen_id,?) WHERE id=?", (newest, ticket_id))
        return self.one(who, ticket_id)


def install(app, store, settings, census):
    from fastapi import Request

    support = Support(store, settings, census)
    diagnostics.watch_logs()

    def person(request):
        who = request.state.identity
        require_person(who)
        return who

    @app.get("/api/v2/support/tickets")
    def support_list(request: Request):
        who = person(request)
        if off_reason(settings):
            return {"enabled": False, "tickets": [], "unread": 0}
        return support.listing(who)

    @app.get("/api/v2/support/compose")
    def support_compose(request: Request):
        who = person(request)
        require_on(settings)
        return support.compose(who)

    @app.get("/api/v2/support/diagnostics")
    def support_diagnostics(request: Request):
        who = person(request)
        require_on(settings)
        return support.preview(who, request.app)

    @app.post("/api/v2/support/diagnostics/capture")
    def support_capture_diagnostics(request: Request, body: DiagnosticsCapture):
        who = person(request)
        require_on(settings)
        return support.preview(who, request.app, body.browser)

    @app.post("/api/v2/support/diagnostics")
    def support_edit_diagnostics(request: Request, body: DiagnosticsEdit):
        who = person(request)
        require_on(settings)
        return support.edit_preview(who, body)

    @app.post("/api/v2/support/tickets")
    def support_file(request: Request, body: SupportTicket):
        who = person(request)
        require_on(settings)
        return support.file(who, body, (request.headers.get("idempotency-key") or "")[:200])

    @app.post("/api/v2/support/tickets/refresh")
    def support_refresh(request: Request):
        who = person(request)
        require_on(settings)
        return support.refresh(who)

    @app.post("/api/v2/support/tickets/{ticket_id}/messages")
    def support_write(request: Request, ticket_id: str, body: SupportMessage):
        who = person(request)
        require_on(settings)
        return support.write(who, ticket_id, body.message, body.diagnostics)

    @app.post("/api/v2/support/tickets/{ticket_id}/read")
    def support_read(request: Request, ticket_id: str):
        return support.read(person(request), ticket_id)

    @app.delete("/api/v2/support/tickets/{ticket_id}")
    def support_delete(request: Request, ticket_id: str):
        who = person(request)
        require_on(settings)
        return support.remove(who, ticket_id)

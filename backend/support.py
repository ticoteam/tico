"""Contact support: a person writes to the Tico team from the app, and the replies come back to it.

A request goes to Tico HQ (`POST <TICO_HQ_URL>/v1/support`, docs/telemetry.md) only when a person submits the form. Nothing here
is automatic and nothing is sent in demo mode. The counting choice (PRIVACY.md) does not block it: this is a message the
person chose to send, and what goes with it is shown on the form and switchable there.

HQ answers with a ticket id and a secret for that ticket alone. This server keeps both (the secret never reaches a
browser, and is sealed with the credential vault's key: `seal_plaintext_secrets`), and asks HQ for the status and messages of the caller's open tickets when the page asks (`refresh`), never on a
timer of its own. Every route shows a person their own tickets and no one else's. Ticket text, from either side, is data:
it is stored as plain text and the page escapes it.

A bot (or the Assistant, or a person's token) reports a Tico bug the same way, with one step more: it drafts the report as
a `support` approval (backend/hubdb.py `lint_support`: the message, its hash, whether the IDs go), a person reads it in
Needs you and says yes, and only then may the bot send it (`file_approved`). The ticket is the approving person's: it is
filed as them, counts against their day, and its replies show on their Support page. No diagnostics bundle goes this way.

`TICO_SUPPORT=off` removes the feature (an air-gapped install); PRIVACY.md and docs/support.md describe it.
"""
import json
import logging
import os
import re
import secrets
import threading
from datetime import datetime, timedelta, timezone
from typing import Annotated, Literal

import httpx

from pydantic import Field

from . import diagnostics, support_asks
from .census import hq_url
from .auth import Identity
from .models import Contract
from .replication import rehearsal_on
from .credentials import SEALED
from .store import H, Problem, encode

log = logging.getLogger("tico.support")

SCHEMA = """
CREATE TABLE IF NOT EXISTS support_tickets(
 id TEXT PRIMARY KEY, actor TEXT NOT NULL, created_at TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'open',
 message TEXT NOT NULL, email TEXT NOT NULL DEFAULT '', sent TEXT NOT NULL DEFAULT '{}',
 hq_id TEXT NOT NULL, hq_secret TEXT NOT NULL, messages TEXT NOT NULL DEFAULT '[]',
 seen_id INTEGER NOT NULL DEFAULT 0, checked TEXT, request_key TEXT);
CREATE INDEX IF NOT EXISTS support_tickets_actor ON support_tickets(actor, created_at);
"""
MAX_MESSAGE = 4000
PER_DAY = 10                 # new tickets one person may file in a day; HQ limits the address as well
# How often a ticket is asked about, at most. HQ counts every ask from this install (one address) together, so a ticket
# nobody has written on for an hour is asked about less often.
FRESH_S = 30                 # a ticket written on (or filed) within ACTIVE_S, or the one in view
QUIET_FRESH_S = 300          # an open or answered one with nothing new for longer
CLOSED_FRESH_S = 6 * 3600    # a closed one, in case the team reopened it
ACTIVE_S = 3600
ASKED = 20                   # tickets asked about in one refresh, at most
EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
LISTED = 50
HQ_WAIT = 8
# The sources this server names when it files (HQ's `source` field; HQ owns the full list and stores `unknown` for any
# value it does not know): a person, a bot's report a person approved, BotOps, the Health Monitor.
SOURCES = ("person", "bot_report", "botops", "health_monitor")
TRANSPORT = None             # tests replace it with an httpx.MockTransport: nothing reaches the network
CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f\u2028\u2029\u202a-\u202e\u2066-\u2069]")
EMAIL = re.compile(r"[^@\s<>\",;()\[\]\\]{1,64}@[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?\.[A-Za-z]{2,24}")
VERSION = re.compile(r"[0-9]{1,4}\.[0-9]{1,4}\.[0-9]{1,4}(?:-[0-9A-Za-z][0-9A-Za-z.-]{0,31})?")


class SupportTicket(Contract):
    message: str = Field(min_length=1, max_length=MAX_MESSAGE)
    email: str = Field(default="", max_length=254)
    include_ids: bool = True            # the version and the install ID go with it, unless the person unticks it
    # The desktop app's own version (window.ticoNativeVersion) when the page runs in it; goes with the IDs.
    app_version: str = Field(default="", max_length=40)
    diagnostics: str = Field(default="", max_length=64)     # the digest of the bundle the person previewed (backend/diagnostics.py)
    # What the person is asking for: help, help with setting up (backend/support_asks.py setup_snapshot), or feedback.
    kind: Literal["support", "setup", "feedback"] = "support"
    # Who is filing, for HQ's queue (`source`; HQ owns the list): the person's own app sends nothing, so `person`; only
    # BotOps' `hub support file` says `botops`, kept only when BotOps makes the request. It sorts tickets at HQ and
    # grants nothing.
    source: Literal["person", "botops"] = "person"


class FeedbackAnswer(Contract):
    moment: Literal["first_result", "day_7", "day_30"]
    answer: str = Field(default="", max_length=MAX_MESSAGE - 200)     # empty: the person skipped the question


class SupportFromApproval(Contract):
    approval: str = Field(min_length=1, max_length=64)     # an approved `support` approval the caller asked for


class SupportRefresh(Contract):
    ticket: str = Field(default="", max_length=64)          # the ticket a person has in view: asked about first


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


class AppShell(Contract):
    """The desktop app the page runs in: its version and the end of its log file (app/src/applog.rs)."""
    version: str = Field(default="", max_length=40)
    lines: list[Annotated[str, Field(max_length=2000)]] = Field(default_factory=list, max_length=diagnostics.APP_LINES)
    mac: bool = False           # the Mac app, which is Beta until it is signed (backend/beta.py)


class CheckRow(Contract):
    """One Setup check row (backend/setup_check.py)."""
    name: str = Field(min_length=1, max_length=120)
    status: Literal["ok", "bad", "warn", "info"]
    detail: str = Field(default="", max_length=600)
    fix: str = Field(default="", max_length=1200)
    source: Literal["doctor", "health"] = "doctor"


class Report(Contract):
    """What a person reports with Report this (an error toast, a failed run, a red Health line, a stuck bot) or from
    Help's Setup check. `group` names the error the same way each time, so the same problem reads as one."""
    kind: Literal["toast", "run", "health", "stuck", "setup_check"]
    title: str = Field(default="", max_length=200)
    detail: str = Field(default="", max_length=2000)
    ref: str = Field(default="", max_length=120)        # the run, Health check or bot it is about
    group: str = Field(default="", max_length=120)
    checks: list[CheckRow] = Field(default_factory=list, max_length=40)


class DiagnosticsCapture(Contract):
    browser: list[BrowserFailure] = Field(default_factory=list, max_length=20)
    app: AppShell | None = None
    # The computers' performance over this many hours (backend/performance.py); 0 leaves it out.
    performance_hours: int = Field(default=24, ge=0, le=336)
    report: Report | None = None


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


def _ask_hq(method, path, *, body=None, secret="", wait=HQ_WAIT):
    """One request to HQ, or a Problem the person can act on. Nothing from HQ's answer is logged. `wait` is longer for
    a long poll (backend/support_access.py)."""
    headers = {"Accept": "application/json", "User-Agent": "tico-support"}
    if secret:
        headers["X-Ticket-Secret"] = secret
    try:
        with httpx.Client(timeout=wait, transport=TRANSPORT, follow_redirects=False) as http:
            return http.request(method, hq_url() + path, json=body, headers=headers)
    except httpx.HTTPError as exc:
        diagnostics.RING.handle(logging.LogRecord("tico.support", logging.WARNING, "", 0,
            "HQ did not answer (%s)", (type(exc).__name__,), None))
        raise Problem("support_unreachable", "Could not reach Tico support. Try again in a moment.", 502,
                      retryable=True) from None


def _refused_field(response):
    try:
        return response.json().get("field")
    except (ValueError, AttributeError):
        return None


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


def _last_activity(row):
    """When the ticket was last written on, by either side, or filed: an aware datetime (the epoch when none reads)."""
    times = [H.parse_ts(m.get("created_at")) for m in json.loads(row["messages"] or "[]")] + [H.parse_ts(row["created_at"])]
    return max((t for t in times if t and t.tzinfo), default=EPOCH)


def view(row):
    """A ticket as the page sees it. The HQ secret and id are not in it."""
    messages = json.loads(row["messages"] or "[]")
    return {"id": row["id"], "created_at": row["created_at"], "status": row["status"], "message": row["message"],
            "email": row["email"], "sent": json.loads(row["sent"] or "{}"), "messages": messages,
            "unread": sum(1 for m in messages if m["from"] == "staff" and m["id"] > row["seen_id"])}


def _context(ticket_id):
    return "support_tickets:" + ticket_id


def seal_plaintext_secrets(store, vault):
    """Seal, with the credential vault, every ticket secret an older release kept in plaintext. Idempotent: a sealed one
    is left alone. Returns how many were sealed. Run on start; a vault that cannot be opened now (KMS unreachable)
    leaves them for the next start or the next use (`Support.secret`)."""
    with store.transaction() as c:
        rows = c.execute("SELECT id,hq_secret FROM support_tickets WHERE substr(hq_secret,1,?)<>?",
                         (len(SEALED), SEALED)).fetchall()
        for row in rows:
            c.execute("UPDATE support_tickets SET hq_secret=? WHERE id=? AND hq_secret=?",
                      (vault.cipher.seal(c, _context(row["id"]), row["hq_secret"]), row["id"], row["hq_secret"]))
    if rows:
        log.info("Sealed %d support ticket secret(s) kept in plaintext by an earlier release", len(rows))
    return len(rows)


class Support:
    def __init__(self, store, settings, census, vault=None):
        self.store, self.settings, self.census, self.vault = store, settings, census, vault
        self.previews = diagnostics.Previews()
        self.sending = threading.Lock()     # one approval is sent once, even when two calls race

    def secret(self, row):
        """The ticket's HQ secret, opened with the vault. One still in plaintext (sealing failed at start) is sealed
        now, best effort, and used as it is."""
        stored = row["hq_secret"]
        if not stored.startswith(SEALED):
            try:
                seal_plaintext_secrets(self.store, self.vault)
            except Exception as exc:
                log.warning("Support ticket secrets are still in plaintext: %s", type(exc).__name__)
            return stored
        with self.store.read() as c:
            return self.vault.cipher.unseal(c, _context(row["id"]), stored)

    # ------------------------------------------------------------------ the form
    def compose(self, who):
        """What the form will send by default and where it goes; creates the install ID if it has none yet."""
        from . import releases
        version = releases.version()
        return {"enabled": True, "email": who.email or "", "version": version if VERSION.fullmatch(version) else "",
                "install_id": self.census.install_id(), "to": hq_url().split("://", 1)[-1].split("/", 1)[0],
                "max": MAX_MESSAGE, "diagnostics": True}

    def preview(self, who, app, browser=None, performance_hours=24, shell=None, report=None):
        """Build the diagnostics bundle and keep it for this person, so the request that files the ticket sends these
        very bytes. Returns its digest and the text the page shows."""
        bundle = diagnostics.build(self.store, self.settings, app.state.auth, who, self.census,
                                   github=getattr(app.state, "github_app", None), performance_hours=performance_hours,
                                   shell=shell.model_dump() if shell else None,
                                   report=report.model_dump() if report else None)
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

    def file(self, who, body, key="", on_filed=None, source=""):
        """Send the ticket to HQ, then keep HQ's id and secret. Nothing is kept when HQ refuses or cannot be reached.
        `on_filed(c, ticket_id)` runs in the transaction that keeps it (an approval spent with it). `source` is who
        filed it, as HQ's queue sorts tickets (one of SOURCES; the body's own when not given)."""
        message, email = clean_message(body.message), clean_email(body.email)
        with self.store.read() as c:
            if key:
                again = c.execute("SELECT * FROM support_tickets WHERE actor=? AND request_key=?",
                                  (who.actor, key)).fetchone()
                if again:
                    return view(again)
            since = H.shift(H.now(), hours=-24)
            recent = c.execute("SELECT count(*) FROM support_tickets WHERE actor=? AND created_at>?",
                               (who.actor, since)).fetchone()[0]
        if recent >= PER_DAY:
            raise Problem("support_busy", "That is enough requests for today. Try again tomorrow.", 429)
        # The secret HQ answers with is sealed with the vault's key: make sure it opens (or, on a fresh install, make
        # it, as the first saved credential would) before anything goes to HQ, so no ticket is made that this server
        # could not keep.
        with self.store.transaction() as c:
            self.vault.cipher.key(c)
        # Only BotOps says botops: the same claim from a person's own app or the Assistant is the person's ticket.
        source = source or ("person" if body.source == "botops" and who.via != "botops" else body.source)
        if source not in SOURCES:
            raise ValueError(f"not a support source: {source}")
        sent, payload = {}, {"message": message, "source": source}
        if email:
            payload["email"] = email
        if body.include_ids:
            details = self.compose(who)
            sent = {k: details[k] for k in ("version", "install_id") if details[k]}
            if VERSION.fullmatch(body.app_version):
                sent["app_version"] = body.app_version
            payload.update(sent)
        if body.diagnostics:
            payload["diagnostics"] = self.attachment(who, body.diagnostics)
        if body.kind != "support":
            payload["kind"] = sent["kind"] = body.kind
        answer = _ask_hq("POST", "/v1/support", body=payload)
        if answer.status_code == 422 and _refused_field(answer) == "fields":
            # An HQ from before source refuses it as an unknown field: the ticket goes again without it, keeping the
            # rest (BotOps' and the Health Monitor's messages start with their name, which is how such an HQ reads it).
            payload.pop("source")
            answer = _ask_hq("POST", "/v1/support", body=payload)
        if answer.status_code == 422 and {"app_version", "kind"} & set(payload) and _refused_field(answer) == "fields":
            # An HQ from before app_version or kind refuses unknown fields; the ticket still goes without them, and
            # names its kind in the message instead.
            for name in ("app_version", "kind"):
                payload.pop(name, None)
                sent.pop(name, None)
            named = f"[{body.kind.capitalize()}] {message}"
            if body.kind != "support" and len(named) <= MAX_MESSAGE:
                payload["message"] = named
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
            c.execute("INSERT INTO support_tickets(id,actor,created_at,status,message,email,sent,hq_id,hq_secret,"
                      "request_key) VALUES(?,?,?,?,?,?,?,?,?,?)",
                      (ticket_id, who.actor, H.now(), "open", message, email, encode(sent), hq_id,
                       self.vault.cipher.seal(c, _context(ticket_id), hq_secret), key or None))
            H.event(c, who.actor, "support.filed", ticket_id, {"email": bool(email), "ids": bool({k: v for k, v in sent.items() if k != "diagnostics"}),
                                                              "diagnostics": "diagnostics" in sent})
            if on_filed:
                on_filed(c, ticket_id)
            return view(c.execute("SELECT * FROM support_tickets WHERE id=?", (ticket_id,)).fetchone())

    def file_approved(self, who, approval_id):
        """Send the report a person approved, for whoever asked for the approval. The ticket is filed as the person who
        approved it (theirs to read and answer, counted against their day) and the approval is spent in the same
        transaction that keeps the ticket: HQ refusing or out of reach spends nothing, so the call can be made again.
        A second call for a spent approval answers with the same ticket."""
        with self.sending:
            with self.store.read() as c:
                row = H.approval(c, approval_id)
                quarantined = H.is_bot(who.actor) and (H.bot(c, H.actor_id(who.actor)) or {}).get("state") == "quarantined"
            if not row:
                raise Problem("not_found", "Approval not found", 404)
            if row["requested_by"] != who.actor:
                raise Problem("forbidden", "Only whoever asked for this approval may send it.", 403)
            if quarantined:
                raise Problem("forbidden", "A quarantined bot sends nothing until a person clears it.", 403)
            if row["kind"] != "support":
                raise Problem("approval", f"{approval_id} is a {row['kind']} approval, not a support report.", 422)
            if row["decision"] != "approved":
                state = row["decision"] or "waiting for a person"
                raise Problem("approval", f"This report is {state}: only a report a person approved is sent.", 409)
            payload = row["payload"] or {}
            if H.lint_support(payload) or not H.support_text_matches(payload):
                raise Problem("approval", "The approved message no longer matches its hash; nothing was sent.", 409)
            person = Identity(row["decided_by"], "human")
            key = "approval:" + approval_id
            with self.store.read() as c:      # a Health Monitor report a person approved is still the Health Monitor's
                health = c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='health_reports'").fetchone() \
                    and c.execute("SELECT 1 FROM health_reports WHERE approval_id=?", (approval_id,)).fetchone()
            source = "health_monitor" if health else "bot_report"
            if row["consumed_at"]:
                with self.store.read() as c:
                    done = c.execute("SELECT * FROM support_tickets WHERE actor=? AND request_key=?",
                                     (person.actor, key)).fetchone()
                if not done:
                    raise Problem("approval", f"{approval_id} was already spent at {row['consumed_at']}.", 409)
                return {"ticket": view(done), "approval": approval_id, "filed_as": person.actor, "again": True}

            def spend(c, _ticket_id=None):
                spent = c.execute("UPDATE approvals SET consumed_at=? WHERE id=? AND consumed_at IS NULL",
                                  (H.now(), approval_id)).rowcount
                if spent:
                    H.event(c, who.actor, "approval.consume", approval_id, {"kind": "support"})

            ticket = self.file(person, SupportTicket(message=payload["message"],
                                                     include_ids=payload.get("include_ids", True) is not False),
                               key, on_filed=spend, source=source)
            with self.store.transaction() as c:      # a ticket kept before its approval was spent (an older crash)
                spend(c)
            return {"ticket": ticket, "approval": approval_id, "filed_as": person.actor, "again": False}

    def write(self, who, ticket_id, text, attachment=""):
        """Add a message to one of the caller's tickets."""
        text = clean_message(text)
        row = self._own(who, ticket_id)
        payload = {"message": text}
        if attachment:
            payload["diagnostics"] = self.attachment(who, attachment)
        answer = _ask_hq("POST", f"/v1/support/{row['hq_id']}/messages", body=payload, secret=self.secret(row))
        if answer.status_code == 422 and attachment:
            raise Problem("diagnostics_rejected", "Support could not accept this attachment. Review it or remove diagnostics to send just the message.", 422)
        _answer(answer, (201, 200))
        self._pull(row)
        return self.one(who, ticket_id)

    def note(self, row, text):
        """Tell HQ that a ticket this install filed has changed, such as a request withdrawn (backend/cloud_agents.py). It
        goes with the ticket's own secret, not as a person's words; a ticket HQ already deleted has no one to hear it."""
        answer = _ask_hq("POST", f"/v1/support/{row['hq_id']}/messages", body={"message": clean_message(text)},
                         secret=self.secret(row))
        if answer.status_code != 404:
            _answer(answer, (201, 200))

    def remove(self, who, ticket_id):
        """Delete the ticket at HQ, then here."""
        row = self._own(who, ticket_id)
        answer = _ask_hq("DELETE", f"/v1/support/{row['hq_id']}", secret=self.secret(row))
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
            rows = c.execute("SELECT * FROM support_tickets WHERE actor=? ORDER BY created_at DESC, id DESC LIMIT ?",
                             (who.actor, LISTED)).fetchall()
        tickets = [view(r) for r in rows]
        return {"enabled": True, "tickets": tickets, "unread": sum(t["unread"] for t in tickets)}

    def _pull(self, row):
        """Ask HQ about one ticket and keep the answer. Returns False when HQ could not be reached."""
        try:
            answer = _ask_hq("GET", f"/v1/support/{row['hq_id']}", secret=self.secret(row))
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
            c.execute("UPDATE support_tickets SET status=?, messages=COALESCE(?,messages), checked=? WHERE id=?",
                      (status, encode(messages) if messages is not None else None, H.now(), row["id"]))
        return True

    def refresh(self, who, ticket=""):
        """Ask HQ about the caller's tickets that are due, the most recently written on first, then list them. `ticket`
        is the one a person has in view: it is due every FRESH_S whatever its pace. HQ being down, or asking us to slow
        down (429), ends the round and keeps what was known."""
        now = H.now()
        with self.store.read() as c:
            rows = c.execute("SELECT * FROM support_tickets WHERE actor=? AND status!='gone' "
                             "ORDER BY created_at DESC LIMIT ?", (who.actor, LISTED)).fetchall()
        active_since = H.parse_ts(now) - timedelta(seconds=ACTIVE_S)
        rows = sorted(rows, key=lambda r: (r["id"] == ticket, _last_activity(r)), reverse=True)
        failed, asked = False, 0
        for row in rows:
            if row["id"] == ticket or (row["status"] != "closed" and _last_activity(row) > active_since):
                wait = FRESH_S
            else:
                wait = CLOSED_FRESH_S if row["status"] == "closed" else QUIET_FRESH_S
            if row["checked"] and row["checked"] > H.shift(now, seconds=-wait):
                continue
            if asked == ASKED:
                break
            asked += 1
            if not self._pull(row):
                failed = True
                break                                  # HQ is down or busy: do not ask again for every ticket
        result = self.listing(who)
        result["refresh_failed"] = failed
        return result

    def read(self, who, ticket_id):
        row = self._own(who, ticket_id)
        newest = max([m["id"] for m in json.loads(row["messages"] or "[]")] or [0])
        with self.store.transaction() as c:
            c.execute("UPDATE support_tickets SET seen_id=MAX(seen_id,?) WHERE id=?", (newest, ticket_id))
        return self.one(who, ticket_id)


def install(app, store, settings, census):
    from fastapi import Request

    vault = app.state.vault
    support = Support(store, settings, census, vault)
    app.state.support = support          # the Health Monitor files its reports through it (backend/health_monitor.py)
    try:
        seal_plaintext_secrets(store, vault)
    except Exception as exc:              # KMS unreachable, or credential.key missing: Health names the vault's state
        log.warning("Support ticket secrets kept in plaintext by an earlier release are not sealed yet: %s",
                    type(exc).__name__)
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
    def support_diagnostics(request: Request, performance_hours: int = 24):
        who = person(request)
        require_on(settings)
        return support.preview(who, request.app, performance_hours=max(0, min(performance_hours, 336)))

    @app.post("/api/v2/support/diagnostics/capture")
    def support_capture_diagnostics(request: Request, body: DiagnosticsCapture):
        who = person(request)
        require_on(settings)
        return support.preview(who, request.app, body.browser, body.performance_hours, body.app, body.report)

    @app.post("/api/v2/support/setup-check")
    def support_setup_check(request: Request):
        """Help's Setup check: doctor's checks from this server plus Health's failing lines (backend/setup_check.py).
        It works with support turned off too; only attaching the result needs support."""
        from . import onboarding, setup_check
        who = person(request)
        return setup_check.run(store, who, settings, request.app.state.auth, getattr(request.app.state, "github_app", None),
                               lambda c: onboarding.config_view(c, settings, who))

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

    @app.get("/api/v2/support/setup-snapshot")
    def support_setup_snapshot(request: Request, asked_from: Literal["wizard", "health", "bot"] = "health"):
        """The text a "Want help setting up?" request starts with; the person reads and edits it before sending."""
        who = person(request)
        require_on(settings)
        github = getattr(request.app.state, "github_app", None)
        with store.read() as c:
            return support_asks.setup_snapshot(c, who, settings, request.app.state.auth,
                                               github.row(c) if github else None, asked_from)

    @app.post("/api/v2/support/feedback")
    def support_feedback(request: Request, body: FeedbackAnswer):
        """The answer to "How is Tico working for you?", sent as a feedback request, or the person skipping it."""
        who = person(request)
        require_on(settings)
        return support_asks.answer_feedback(store, support, who, body)

    @app.post("/api/v2/support/tickets/from-approval")
    def support_file_approved(request: Request, body: SupportFromApproval):
        """A bot, a person's token or the Assistant sends the report a person approved (docs/support.md)."""
        require_on(settings)
        return support.file_approved(request.state.identity, body.approval)

    @app.post("/api/v2/support/tickets/refresh")
    def support_refresh(request: Request, body: SupportRefresh | None = None):
        who = person(request)
        require_on(settings)
        return support.refresh(who, body.ticket if body else "")

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

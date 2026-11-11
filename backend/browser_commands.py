# pyright: strict
"""Website tools' browser commands: an in-memory mailbox between whoever drives a website (a bot's turn, a person's live
view) and the computer that runs its Chrome.

A bot never gets the browser itself: it sends one verb (`POST /api/v2/browser/commands`), the server picks the account
(backend/website_tools.py `pick`), decides where it runs and holds the request open until that computer reports, at most
REPLY_WAIT seconds. Where it runs: the company's browser host (one computer an admin picks, `registry_metadata`
`browser-host`) when it is online and has Chrome; else, unless the tool is central only, the caller's own computer;
else 409. A computer collects its commands with a long poll (`GET /api/v2/runner-browser-commands?wait=25`) and reports
each one (`POST .../{id}/report`, a download's bytes first through `POST .../{id}/file`).

One caller at a time per account: a bot's commands hold it HOLD_SECONDS after its last one (409 `busy` for anyone
else), and a person who takes control in the live view holds it until they give it back or PERSON_IDLE seconds pass
(409 `person_in_control` for bots). A person's view is a picture refreshed at most once a second however many people
watch (one screenshot command per FRAME_EVERY seconds), and their clicks and keys are commands too.

Nothing here is persisted: the server is one process (backend/meeting_screen.py assumes the same). A restart drops
in-flight commands and holds; a caller retries. A login's value reaches only the payload a computer collects for a
`fill` or `copy`: never a reply, an event, an Activity row or an idempotency row. The 2FA key never leaves the server:
`code` is answered here and a `fill`/`copy` of the code carries only the current code.
"""

import asyncio
import base64
import binascii
import re
import sqlite3
import threading
import time
import uuid
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal, TypedDict, cast
from urllib.parse import quote, quote_plus, urlsplit

from fastapi import Request
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field

from . import hubdb
from .models import Contract
from .store import Problem

if TYPE_CHECKING:
    from fastapi import FastAPI

H: Any = hubdb

KEY = "browser-host"  # registry_metadata: the company's browser host, a runner id
CAPABILITY = "browser_v1"
TOOLS_MANAGER = "tools-manager"  # the one bot a browser host also takes
HOST_FRESH = 90  # a computer that checked in this recently is online
HOLD_SECONDS = 120.0
PERSON_IDLE = 300.0
REPLY_WAIT = 30.0
POLL_WAIT = 25.0
FRAME_EVERY = 1.0
FRAME_WAIT = 10.0
FRAME_KEEP = 600.0
RECENT_KEEP = 120.0
RECENT_MAX = 256  # Idempotency-Keys remembered at once
# Verbs whose answer is too big to keep for a retry: a retry of one that ran is refused (409 `already_ran`).
UNKEPT = ("read", "screenshot", "download")
SENT_KEEP = 600.0  # a login value sent for a fill or copy is scrubbed from every answer on its account this long
SENT_MIN = {"username": 8, "password": 4, "code": 4}  # shorter values are too common to scrub
MASK = "•••"
GONE_CHECK = 2.0  # how often a waiting command checks that its caller is still there
QUEUE_MAX = 64
MAX_FILE = 20_000_000
MAX_UPLOAD = 10_000_000
MAX_IMAGE_B64 = 1_400_000
HEADERS = {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"}

Verb = Literal[
    "open",
    "read",
    "click",
    "type",
    "press",
    "scroll",
    "back",
    "screenshot",
    "fill",
    "copy",
    "code",
    "download",
    "upload",
    "close",
]
LOGGED = ("open", "fill", "copy", "code", "download", "upload")
FIELDS = ("username", "password", "code")
REF = re.compile(r"^[A-Za-z0-9_-]{1,40}$")
ERROR_CODE = re.compile(r"^[a-z][a-z0-9_]{0,39}$")
REQUEST_ERRORS = ("not_on_site", "not_a_password_field")


# ----------------------------------------------------------------------------------------------------- request bodies
class BrowserCommand(Contract):
    site: str = Field(min_length=1, max_length=2000)
    verb: Verb
    args: dict[str, Any] = Field(default_factory=dict)


class BrowserNeedsYou(Contract):
    site: str = Field(min_length=1, max_length=2000)
    line: str = Field(min_length=1, max_length=300)
    task: str | None = Field(default=None, max_length=200)


class BrowserOpen(Contract):
    pass


class BrowserControl(Contract):
    take: bool


class BrowserInput(Contract):
    kind: Literal["click", "type", "key", "scroll"]
    x: float | None = Field(default=None, ge=0, le=100_000)
    y: float | None = Field(default=None, ge=0, le=100_000)
    text: str | None = Field(default=None, max_length=10_000)
    key: str | None = Field(default=None, max_length=40)
    dy: int | None = Field(default=None, ge=-100_000, le=100_000)


class BrowserHostSet(Contract):
    on: bool


class BrowserReport(BaseModel):
    """A computer's answer to one command. Read leniently: a newer runner may add facts."""

    model_config = ConfigDict(extra="ignore")
    ok: bool
    text: str | None = Field(default=None, max_length=200_000)
    image: str | None = Field(default=None, max_length=MAX_IMAGE_B64)
    url: str | None = Field(default=None, max_length=4000)
    title: str | None = Field(default=None, max_length=1000)
    error: str | None = Field(default=None, max_length=1000)
    signed_in: bool | None = None
    width: int | None = Field(default=None, ge=1, le=20_000)
    height: int | None = Field(default=None, ge=1, le=20_000)


# ----------------------------------------------------------------------------------------------------- shapes
class Who(TypedDict):
    subject: str
    kind: str
    id: str
    name: str
    initials: str


class Session(TypedDict):
    state: str
    where: str | None
    computer: str | None
    by: Who | None


class Host(TypedDict):
    runner_id: str
    label: str
    online: bool


Outcome = BrowserReport | Problem


@dataclass(eq=False)
class Command:
    id: str
    runner_id: str
    label: str
    central: bool
    attempt_id: str
    account_id: str
    tool_id: str
    verb: str
    args: dict[str, Any]
    sites: list[str]
    value: str | None
    reply: "asyncio.Future[Outcome]"
    by: Who | None = None
    file: tuple[str, bytes] | None = None
    delivered: bool = False
    at: float = field(default_factory=time.monotonic)

    def payload(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "id": self.id,
            "account": self.account_id,
            "tool": self.tool_id,
            "verb": self.verb,
            "args": self.args,
            "sites": self.sites,
            "profile": self.account_id,
        }
        if not self.central:
            # On the caller's own computer the browser goes out the way that bot's browsing does (its exit).
            out["where"] = "own"
            if self.by is not None and self.by["kind"] == "bot":
                out["bot"] = self.by["id"]
        else:
            out["where"] = "central"
        if self.value is not None:
            out["value"] = self.value
        return out


@dataclass
class Hold:
    actor: str
    who: Who
    person: bool
    until: float


@dataclass
class SessionFacts:
    state: str = "unknown"  # signed_in, signed_out, needs_you, unknown
    ask: str | None = None  # the open "needs you" ask's requirement id, while state is needs_you
    before: str = "unknown"  # the state needs_you replaced, back once it clears
    where: str | None = None
    runner_id: str | None = None
    computer: str | None = None
    by: Who | None = None
    url: str = ""
    title: str = ""


@dataclass
class Frame:
    data: bytes = b""
    url: str = ""
    width: int = 0
    height: int = 0
    issued: float = 0.0
    at: float = 0.0
    pending: Command | None = None


class Mailbox:
    """Every command, hold, session fact and frame. Mutated on the event loop; read from worker threads under `lock`."""

    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.reset()

    def reset(self) -> None:
        self.loop: asyncio.AbstractEventLoop | None = None
        self.queues: dict[str, list[Command]] = {}
        self.inflight: dict[str, Command] = {}
        self.wakers: dict[str, asyncio.Event] = {}
        self.holds: dict[str, Hold] = {}
        self.sessions: dict[str, SessionFacts] = {}
        self.frames: dict[str, Frame] = {}
        self.recent: dict[tuple[str, str], tuple[float, "asyncio.Future[Outcome | dict[str, Any]]"]] = {}
        self.sent: dict[str, list[tuple[float, str]]] = {}

    # -------------------------------------------------------------------- values sent, scrubbed from every answer
    def remember(self, account_id: str, field: str, value: str) -> None:
        """A login value went to a computer: for SENT_KEEP seconds every answer on this account has it scrubbed, so a
        page that shows it later (a "show password" click, a form sent by GET) does not hand it back."""
        if len(value) < SENT_MIN.get(field, 4):
            return
        now = time.monotonic()
        with self.lock:
            kept = [(at, v) for at, v in self.sent.get(account_id, ()) if now - at < SENT_KEEP and v != value]
            self.sent[account_id] = [*kept, (now, value)]

    def scrub(self, account_id: str, text: str | None) -> str | None:
        """`text` with every value recently sent on the account (and its URL-encoded forms) replaced by MASK."""
        if not text:
            return text
        now = time.monotonic()
        with self.lock:
            kept = [(at, v) for at, v in self.sent.get(account_id, ()) if now - at < SENT_KEEP]
            if kept:
                self.sent[account_id] = kept
            else:
                self.sent.pop(account_id, None)
        forms = {f for _, v in kept for f in (v, quote(v, safe=""), quote_plus(v), quote(v)) if f}
        for form in sorted(forms, key=len, reverse=True):
            text = text.replace(form, MASK)
        return text

    def scrub_report(self, account_id: str, report: "BrowserReport") -> "BrowserReport":
        changes = {name: self.scrub(account_id, getattr(report, name)) for name in ("text", "title", "url", "error")}
        return report.model_copy(update=changes)

    # -------------------------------------------------------------------- the loop
    def bind(self) -> asyncio.AbstractEventLoop:
        loop = asyncio.get_running_loop()
        self.loop = loop
        return loop

    def soon(self, fn: Callable[[], None]) -> None:
        """Run `fn` on the event loop: at once when called there, else scheduled (a worker thread's disconnect)."""
        loop = self.loop
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if loop is None or running is loop or loop.is_closed():
            fn()
        else:
            loop.call_soon_threadsafe(fn)

    def waker(self, runner_id: str) -> asyncio.Event:
        found = self.wakers.get(runner_id)
        if found is None:
            found = self.wakers[runner_id] = asyncio.Event()
        return found

    # -------------------------------------------------------------------- holds
    def take(self, account_id: str, actor: str, who: Who, person: bool = False) -> None:
        """Hold the account for `actor`, or refuse: `person_in_control` for a bot's command while a person holds it
        (the Assistant's too, though it acts under its person's id), `busy` while another caller does. A person taking
        control takes it from a bot."""
        now = time.monotonic()
        with self.lock:
            held = self.holds.get(account_id)
            if held and held.until > now:
                if held.person and not person:
                    raise Problem(
                        "person_in_control",
                        f"{held.who['name']} has control of this browser; wait until they give it back",
                        409,
                        extra={"by": held.who},
                    )
                if held.actor != actor and held.person:
                    raise Problem(
                        "busy", f"{held.who['name']} has control of this browser now", 409, extra={"by": held.who}
                    )
                if held.actor != actor and not person:
                    raise Problem(
                        "busy",
                        f"{held.who['name']} is using this account; try again in a minute",
                        409,
                        extra={"by": held.who},
                    )
            self.holds[account_id] = Hold(actor, who, person, now + (PERSON_IDLE if person else HOLD_SECONDS))

    def release(self, account_id: str, actor: str, person: bool = False) -> bool:
        """End `actor`'s hold of the kind asked: a bot's close never ends its person's control, nor the reverse."""
        with self.lock:
            held = self.holds.get(account_id)
            if held and held.actor == actor and held.person == person:
                del self.holds[account_id]
                return True
            return False

    def holder(self, account_id: str) -> Hold | None:
        with self.lock:
            held = self.holds.get(account_id)
            if held and held.until <= time.monotonic():
                del self.holds[account_id]
                return None
            return held

    # -------------------------------------------------------------------- commands
    def enqueue(self, cmd: Command) -> None:
        with self.lock:
            queue = self.queues.setdefault(cmd.runner_id, [])
            if len(queue) >= QUEUE_MAX:
                raise Problem("slow_down", "That computer has too many browser commands waiting; try again soon", 429)
            queue.append(cmd)
        self.waker(cmd.runner_id).set()

    def queued(self, runner_id: str) -> list[Command]:
        with self.lock:
            return list(self.queues.get(runner_id, ()))

    def hand_out(self, runner_id: str, ids: set[str]) -> list[Command]:
        with self.lock:
            queue = self.queues.get(runner_id, [])
            taken = [c for c in queue if c.id in ids and not c.reply.done()]
            self.queues[runner_id] = [c for c in queue if c.id not in ids and not c.reply.done()]
            for cmd in taken:
                cmd.delivered = True
                self.inflight[cmd.id] = cmd
            return taken

    def drop(self, cmd: Command) -> None:
        with self.lock:
            self.inflight.pop(cmd.id, None)
            queue = self.queues.get(cmd.runner_id)
            if queue and cmd in queue:
                queue.remove(cmd)
            for frame in self.frames.values():
                if frame.pending is cmd:
                    frame.pending = None

    def settle(self, cmd: Command, outcome: Outcome) -> None:
        self.drop(cmd)
        if not cmd.reply.done():
            cmd.reply.set_result(outcome)

    def disconnect(self, runner_id: str) -> None:
        """A computer removed: its commands fail, its sessions and the frames they made are gone."""

        def run() -> None:
            with self.lock:
                cmds = [c for c in self.inflight.values() if c.runner_id == runner_id]
                cmds += self.queues.pop(runner_id, [])
                for account_id, facts in list(self.sessions.items()):
                    if facts.runner_id == runner_id:
                        facts.where = facts.runner_id = facts.computer = None
                        self.frames.pop(account_id, None)
            for cmd in cmds:
                self.settle(cmd, Problem("browser_host_offline", "That computer was removed", 409))

        self.soon(run)

    # -------------------------------------------------------------------- facts
    def note(self, cmd: Command, report: BrowserReport) -> None:
        """What a report says about the session (and, from the browser host, its newest picture)."""
        with self.lock:
            facts = self.sessions.setdefault(cmd.account_id, SessionFacts())
            facts.where = "central" if cmd.central else "own"
            facts.runner_id, facts.computer = cmd.runner_id, cmd.label
            if cmd.by is not None:
                facts.by = cmd.by
            if report.url:
                facts.url = report.url
            if report.title is not None:
                facts.title = report.title
            if report.signed_in is not None:
                known = "signed_in" if report.signed_in else "signed_out"
                if facts.state == "needs_you":
                    facts.before = known
                else:
                    facts.state = known
            if cmd.verb == "close" and report.ok:
                facts.where = facts.runner_id = facts.computer = None
            if cmd.central and cmd.verb == "screenshot" and report.ok and report.image:
                data = _b64(report.image)
                if data and data.startswith(b"\xff\xd8"):
                    frame = self.frames.setdefault(cmd.account_id, Frame())
                    width, height = jpeg_size(data)
                    frame.data, frame.at = data, time.monotonic()
                    frame.url = report.url or facts.url
                    frame.width, frame.height = report.width or width, report.height or height

    def facts(self, account_id: str) -> SessionFacts:
        with self.lock:
            return self.sessions.setdefault(account_id, SessionFacts())

    def needs_you(self, account_id: str, ask: str | None, by: Who) -> None:
        with self.lock:
            facts = self.sessions.setdefault(account_id, SessionFacts())
            if facts.state != "needs_you":
                facts.before = facts.state
            facts.state, facts.ask, facts.by = "needs_you", ask, by

    def clear_needs_you(self, account_id: str, ask: str | None = None) -> None:
        """Needs you is over (its ask answered, control given back, the bot's next command worked): the state it
        replaced comes back. With `ask`, only when that is still the ask the state is for."""
        with self.lock:
            facts = self.sessions.get(account_id)
            if facts is None or facts.state != "needs_you" or (ask is not None and facts.ask != ask):
                return
            facts.state, facts.ask = facts.before, None

    def view(self, account_id: str, c: sqlite3.Connection | None = None) -> Session:
        with self.lock:
            facts = self.sessions.get(account_id)
            ask = facts.ask if facts is not None and facts.state == "needs_you" else None
        if c is not None and ask and not open_ask(c, requirement_id=ask):
            self.clear_needs_you(account_id, ask)  # answered (Done), withdrawn, or its task moved on
        held = self.holder(account_id)
        with self.lock:
            facts = self.sessions.get(account_id) or SessionFacts()
            state = facts.state
            by = facts.by
            if state == "needs_you" and not (held and held.person):
                pass
            elif held:
                state, by = "in_use", held.who
            elif state not in ("signed_in", "signed_out"):
                state = "unknown"
            return {
                "state": state,
                "where": facts.where if facts.where in ("central", "own") else None,
                "computer": facts.computer,
                "by": by,
            }

    # -------------------------------------------------------------------- retries
    def recent_outcome(self, actor: str, key: str) -> "tuple[asyncio.Future[Outcome | dict[str, Any]], bool]":
        """The answer a retry of the same Idempotency-Key gets: the first call's (awaited if still running), and whether
        this call is that first one."""
        now = time.monotonic()
        with self.lock:
            self.recent = {k: v for k, v in self.recent.items() if now - v[0] < RECENT_KEEP}
            found = self.recent.get((actor, key))
            if found is not None:
                return found[1], False
            for old in list(self.recent)[: max(0, len(self.recent) - RECENT_MAX + 1)]:
                del self.recent[old]  # oldest first: a retry that late runs again
            fut: asyncio.Future[Outcome | dict[str, Any]] = asyncio.get_running_loop().create_future()
            self.recent[(actor, key)] = (now, fut)
            return fut, True


MAILBOX = Mailbox()


# ----------------------------------------------------------------------------------------------------- small helpers
def _b64(text: str) -> bytes | None:
    try:
        return base64.b64decode(text, validate=True)
    except (binascii.Error, ValueError):
        return None


def jpeg_size(data: bytes) -> tuple[int, int]:
    """(width, height) from a JPEG's frame header, without decoding pixels; (0, 0) when none is found."""
    pos = 2
    while pos + 4 <= len(data):
        if data[pos] != 0xFF:
            break
        while pos < len(data) and data[pos] == 0xFF:
            pos += 1
        if pos >= len(data):
            break
        marker = data[pos]
        pos += 1
        if marker in (0xD8, 0xD9, 0xDA):
            break
        if 0xD0 <= marker <= 0xD7 or marker == 1:
            continue
        length = int.from_bytes(data[pos : pos + 2], "big")
        if length < 2 or pos + length > len(data):
            break
        if marker in (0xC0, 0xC1, 0xC2) and length >= 8:
            return int.from_bytes(data[pos + 5 : pos + 7], "big"), int.from_bytes(data[pos + 3 : pos + 5], "big")
        pos += length
    return 0, 0


def _initials(name: str) -> str:
    words = [w for w in re.split(r"[\s._@-]+", name) if w]
    return "".join(w[0] for w in words[:2]).upper() or "?"


def who_of(c: sqlite3.Connection, actor: str) -> Who:
    from . import names

    lookup: Any = cast(Any, names).lookup
    name = str(lookup(c, {actor}).get(actor) or H.actor_id(actor))
    kind = "bot" if actor.startswith("bot:") else "person"
    return {"subject": actor, "kind": kind, "id": H.actor_id(actor), "name": name, "initials": _initials(name)}


def page_of(url: str) -> str:
    """Host and path, never the query string: what Activity records of a page."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return ""
    return ((parts.hostname or "") + (parts.path or ""))[:400]


def first_url(sites: list[str]) -> str:
    site = sites[0] if sites else ""
    if site.startswith(("http://", "https://")):
        return site.rstrip("/") + "/"
    return "https://" + site.strip("/") + "/"


def _scrub(text: str | None, value: str | None) -> str | None:
    if text is None or not value:
        return text
    return text.replace(value, "••••")


def _idle(request: Request, began: float) -> None:
    # A deliberate wait is not server processing (backend/app.py timing; backend/meeting_screen.py does the same).
    request.state.idle_wait_ms = getattr(request.state, "idle_wait_ms", 0) + (time.perf_counter() - began) * 1000


def _wt() -> Any:
    from . import website_tools

    return website_tools


# ----------------------------------------------------------------------------------------------------- the browser host
def host_id(c: sqlite3.Connection) -> str | None:
    row = c.execute("SELECT value FROM registry_metadata WHERE key=?", (KEY,)).fetchone()
    return str(row[0]) if row and row[0] else None


def _computer(c: sqlite3.Connection, runner_id: str | None) -> dict[str, Any] | None:
    if not runner_id:
        return None
    row = c.execute(
        "SELECT id,label,last_seen_at,capabilities FROM runners_with_state WHERE id=? AND revoked_at IS NULL",
        (runner_id,),
    ).fetchone()
    return dict(row) if row else None


def _online(row: dict[str, Any] | None) -> bool:
    return bool(row and row.get("last_seen_at") and str(row["last_seen_at"]) > H.shift(H.now(), seconds=-HOST_FRESH))


def _has_browser(row: dict[str, Any] | None) -> bool:
    return row is not None and CAPABILITY in (H._json(row.get("capabilities"), []) or [])


def host_view(c: sqlite3.Connection) -> Host | None:
    """The browser host for the tool page: `{runner_id, label, online}`, or None when no computer is picked."""
    rid = host_id(c)
    row = _computer(c, rid)
    if rid is None or row is None:
        return None
    return {"runner_id": rid, "label": str(row["label"] or rid), "online": _online(row)}


def session_view(account_id: str, c: sqlite3.Connection | None = None) -> Session:
    """What is known of an account's browser session, from memory: `unknown` when nothing is. With a connection, a
    `needs_you` whose ask is no longer open clears first."""
    return MAILBOX.view(account_id, c)


# An ask still waiting: not withdrawn, on its task's current visit, with no standing answer (backend/task_privacy.py
# _ASKERS reads the same).
_OPEN_ASK = (
    "SELECT 1 FROM requirements r JOIN visits v ON v.id=r.visit_id "
    "WHERE r.withdrawn_at IS NULL AND v.left_at IS NULL AND NOT EXISTS (SELECT 1 FROM answers a "
    "WHERE a.requirement_id=r.id AND a.visit_id=v.id AND a.undone_at IS NULL AND a.answer NOT IN ('give','again'))"
)


def open_ask(
    c: sqlite3.Connection, show: str | None = None, *, person: str | None = None, requirement_id: str | None = None
) -> bool:
    """Whether a "needs you" ask is still open: the one named by `requirement_id`, or any that shows `show`
    (`browser:<tool>/<account>`) and is addressed to `person`."""
    if requirement_id is not None:
        return c.execute(_OPEN_ASK + " AND r.id=? LIMIT 1", (requirement_id,)).fetchone() is not None
    if not show or not person:
        return False
    return (
        c.execute(
            _OPEN_ASK + " AND r.person_id=? AND r.shows IS NOT NULL "
            "AND EXISTS (SELECT 1 FROM json_each(r.shows) s WHERE s.value=?) LIMIT 1",
            (person, show),
        ).fetchone()
        is not None
    )


def health_problem(c: sqlite3.Connection) -> str | None:
    """Health's line about the browser host, or None when there is none to say (no host, or a host that is fine)."""
    row = c.execute(
        "SELECT m.value AS picked,r.id,r.label,r.last_seen_at,r.capabilities,r.revoked_at FROM registry_metadata m "
        "LEFT JOIN runners_with_state r ON r.id=m.value WHERE m.key=?",
        (KEY,),
    ).fetchone()
    if not row:
        return None
    found = cast(dict[str, Any], dict(row))
    if not found.get("id") or found.get("revoked_at"):
        return "The browser host was removed: pick another computer in Settings > Computers."
    label = str(found["label"] or found["id"])
    if not _online(found):
        return f"Browser host offline: {label} has not checked in, so website tools that run centrally wait."
    if not _has_browser(found):
        return f"Chrome missing on {label}: install Google Chrome there so website tools can run."
    return None


def check_placement(c: sqlite3.Connection, bot: str, runner_id: str) -> None:
    """Refuse putting a bot on the browser host: its Chrome profiles hold signed-in sessions every bot there could
    reach. The Tools Manager is the one bot it takes."""
    if host_id(c) != runner_id:
        return
    found: dict[str, Any] = dict(H.bot(c, bot) or {})
    slug = str(found.get("slug") or bot)
    if TOOLS_MANAGER in (bot, slug):
        return
    raise Problem(
        "browser_host",
        f"This computer is the browser host, whose signed-in browsers every bot on it could reach, so {slug} cannot "
        "go there. Pick another computer, or stop using this one as the browser host (Settings > Computers).",
        409,
    )


def set_host(c: sqlite3.Connection, actor: str, runner_id: str, on: bool) -> dict[str, Any]:
    if on:
        others = [
            str(r[0])
            for r in c.execute(
                "SELECT b.slug FROM assignments a JOIN bots b ON b.id=a.bot WHERE a.runner_id=? AND b.state<>'archived' "
                "ORDER BY b.slug",
                (runner_id,),
            )
            if r[0] != TOOLS_MANAGER
        ]
        if others:
            raise Problem("browser_host", "Move these bots to another computer first: " + ", ".join(others), 409)
        c.execute(
            "INSERT INTO registry_metadata VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (KEY, runner_id),
        )
    else:
        c.execute("DELETE FROM registry_metadata WHERE key=? AND value=?", (KEY, runner_id))
    H.event(c, actor, "runner.browser_host", runner_id, {"on": on})
    return {"on": on}


def disconnect(runner_id: str) -> None:
    """A removed computer's commands, sessions and frames go (backend/computers.py disconnect)."""
    MAILBOX.disconnect(runner_id)


# ----------------------------------------------------------------------------------------------------- placement
@dataclass
class Place:
    runner_id: str
    label: str
    central: bool


def _offline(row: dict[str, Any] | None) -> Problem:
    if row is None:
        return Problem(
            "browser_host_offline",
            "No computer is the browser host: an admin picks one in Settings > Computers",
            409,
        )
    label = str(row["label"] or row["id"])
    why = f"Chrome is missing on {label}" if _online(row) else f"{label} is offline"
    return Problem("browser_host_offline", why, 409, extra={"computer": label})


def central(c: sqlite3.Connection) -> Place:
    row = _computer(c, host_id(c))
    if row is None or not _online(row) or not _has_browser(row):
        raise _offline(row)
    return Place(str(row["id"]), str(row["label"] or row["id"]), True)


def place(c: sqlite3.Connection, who: Any, picked: Any, verb: str) -> Place:
    """Where this caller's command runs: the browser host, else (unless central only) the caller's own computer."""
    central_only = bool(picked["central_only"])
    own = _computer(c, who.runner_id)
    own_ok = own is not None and _online(own) and _has_browser(own)
    if verb == "copy":
        if central_only:
            raise Problem("central_only", "This website's login stays on the browser host: copy is off", 403)
        if not own_ok or own is None:
            raise Problem("no_browser", "This computer cannot run Chrome, so nothing can be copied here", 409)
        return Place(str(own["id"]), str(own["label"] or own["id"]), False)
    host = _computer(c, host_id(c))
    if host is not None and _online(host) and _has_browser(host):
        return Place(str(host["id"]), str(host["label"] or host["id"]), True)
    if central_only or (host is not None and not own_ok):
        raise _offline(host)
    if own_ok and own is not None:
        return Place(str(own["id"]), str(own["label"] or own["id"]), False)
    raise Problem("no_browser", "No computer can run this browser: pick a browser host in Settings > Computers", 409)


# ----------------------------------------------------------------------------------------------------- verbs
def _text(args: dict[str, Any], name: str, limit: int, required: bool = True) -> str | None:
    value = args.get(name)
    if value is None and not required:
        return None
    if not isinstance(value, str) or not value or len(value) > limit:
        raise Problem("invalid_request", f"{name} is required (at most {limit} characters)", 422)
    return value


def _ref(args: dict[str, Any], required: bool = True) -> str | None:
    ref = _text(args, "ref", 40, required)
    if ref is not None and not REF.fullmatch(ref):
        raise Problem("invalid_request", "ref is an element reference from read, like e12", 422)
    return ref


def clean_args(verb: str, args: dict[str, Any], sites: list[str], site_matches: Callable[..., bool]) -> dict[str, Any]:
    """The arguments the computer gets, checked; an open's address must be one of the tool's sites."""
    if verb == "open":
        url = _text(args, "url", 4000, required=False) or first_url(sites)
        if not url.startswith(("https://", "http://")) or not site_matches(sites, url):
            raise Problem("not_on_site", "That address is not one of this website's sites", 422)
        return {"url": url}
    if verb == "click":
        return {"ref": _ref(args)}
    if verb == "type":
        out: dict[str, Any] = {"ref": _ref(args), "text": _text(args, "text", 10_000)}
        if args.get("submit"):
            out["submit"] = True
        return out
    if verb == "press":
        return {"key": _text(args, "key", 40)}
    if verb == "scroll":
        dy = args.get("dy", 600)
        if not isinstance(dy, int) or isinstance(dy, bool) or not -100_000 <= dy <= 100_000:
            raise Problem("invalid_request", "dy is a whole number of pixels", 422)
        return {"dy": dy}
    if verb in ("fill", "copy"):
        what = args.get("field")
        if what not in FIELDS:
            raise Problem("invalid_request", "field is username, password or code", 422)
        out = {"field": what}
        if verb == "fill" and (ref := _ref(args, required=False)):
            out["ref"] = ref
        return out
    if verb == "upload":
        data = _text(args, "data", MAX_UPLOAD * 4 // 3 + 4)
        if data is None or _b64(data) is None:
            raise Problem("invalid_request", "data is the file's bytes in base64", 422)
        return {"ref": _ref(args), "name": _text(args, "name", 255), "data": data}
    return {}


# ----------------------------------------------------------------------------------------------------- waiting
async def wait_reply(request: Request, cmd: Command, within: float) -> BrowserReport:
    """The computer's report, or a Problem: 504 `browser_timeout` once `within` seconds pass. However the wait ends (a
    timeout, the caller gone or cancelled) the command is settled and leaves the queue: nothing piles up for a computer
    that is offline."""
    began = time.perf_counter()
    deadline = time.monotonic() + within
    try:
        while True:
            left = deadline - time.monotonic()
            if left <= 0:
                raise Problem(
                    "browser_timeout", f"{cmd.label} did not answer within {int(within)} seconds; try again", 504
                )
            try:
                outcome = await asyncio.wait_for(asyncio.shield(cmd.reply), min(left, GONE_CHECK))
                break
            except TimeoutError:
                if await request.is_disconnected():
                    raise Problem("gone", "The caller went away before the browser answered", 499) from None
    finally:
        _idle(request, began)
        if not cmd.reply.done():
            MAILBOX.settle(cmd, Problem("browser_timeout", "The browser did not answer in time", 504))
    if isinstance(outcome, Problem):
        raise outcome
    if not outcome.ok:
        code = outcome.error if outcome.error and ERROR_CODE.fullmatch(outcome.error) else "browser_error"
        detail = MAILBOX.scrub(cmd.account_id, _scrub(outcome.text, cmd.value)) or {
            "stale_ref": "That element is gone: read the page again",
            "not_on_site": "This page is not one of the website's sites, so nothing was filled",
            "not_a_password_field": "That is not a password field, so nothing was filled",
        }.get(code, "The browser could not do that")
        raise Problem(code, detail[:500], 422 if code in REQUEST_ERRORS else 409)
    return outcome


def _new(
    loop: asyncio.AbstractEventLoop,
    spot: Place,
    picked: Any,
    verb: str,
    args: dict[str, Any],
    value: str | None = None,
    attempt_id: str = "",
    by: Who | None = None,
) -> Command:
    return Command(
        id=uuid.uuid4().hex,
        runner_id=spot.runner_id,
        label=spot.label,
        central=spot.central,
        attempt_id="" if spot.central else attempt_id,
        account_id=str(picked["account_id"]),
        tool_id=str(picked["tool_id"]),
        verb=verb,
        args=args,
        sites=list(picked["sites"]),
        value=value,
        reply=loop.create_future(),
        by=by,
    )


# ----------------------------------------------------------------------------------------------------- routes
def install(app: "FastAPI", store: Any, auth: Any, mutate: "Callable[..., Any]") -> None:
    mailbox = MAILBOX
    mailbox.reset()  # one app at a time: a test's second app starts empty
    app.state.browser_mailbox = mailbox

    def read() -> AbstractContextManager[sqlite3.Connection]:
        return cast(AbstractContextManager[sqlite3.Connection], store.read())

    def write() -> AbstractContextManager[sqlite3.Connection]:
        return cast(AbstractContextManager[sqlite3.Connection], store.transaction())

    def vault() -> Any:
        return app.state.vault

    def driver(who: Any) -> None:
        """A run's own call: a bot's turn, or the Assistant's turn acting for its person. Never a person's agent token,
        an external agent's credential or a computer."""
        if who.role == "runner" or not who.attempt_id or not who.runner_id or who.agent:
            raise Problem("forbidden", "Only a bot's turn drives a website tool's browser", 403)

    def person(who: Any) -> None:
        if who.role not in ("owner", "human") or who.via or who.via_token or who.attempt_id:
            raise Problem("forbidden", "Open the browser in Tico yourself, signed in", 403)

    def log(picked: Any, who: Any, verb: str, page: str, where: str, result: str = "use") -> None:
        with write() as c:
            _wt().log(c, picked, who, verb, page, where, result)

    async def once(request: Request, actor: str, run: "Callable[[], Any]", keep: bool = True) -> Any:
        """Run `run` once per Idempotency-Key: a retry gets the first call's answer, awaited if it is still running.
        Without `keep` (a page's text, a picture, a file) the answer is not kept: a retry of one that ran is refused."""
        key = request.headers.get("idempotency-key") or ""
        if not 1 <= len(key) <= 200:
            return await run()
        fut, first = mailbox.recent_outcome(actor, key)
        if not first:
            outcome = await asyncio.shield(fut)
            if isinstance(outcome, Problem):
                raise outcome
            return outcome
        try:
            answer = await run()
        except Problem as refused:
            fut.set_result(refused)
            raise
        except BaseException:
            fut.cancel()
            with mailbox.lock:
                mailbox.recent.pop((actor, key), None)
            raise
        fut.set_result(
            answer
            if keep
            else Problem(
                "already_ran", "That command already ran: send it again with a new Idempotency-Key to repeat it", 409
            )
        )
        return answer

    # ------------------------------------------------------------------------------------------------ a bot's verbs
    @app.post("/api/v2/browser/commands")
    async def browser_command(request: Request, body: BrowserCommand) -> Any:
        """One verb on a website tool's browser for the bot in this turn (integrations/website.md)."""
        who = request.state.identity
        driver(who)
        loop = mailbox.bind()
        return await once(
            request, who.actor, lambda: run_command(request, who, body, loop), keep=body.verb not in UNKEPT
        )

    async def run_command(request: Request, who: Any, body: BrowserCommand, loop: asyncio.AbstractEventLoop) -> Any:
        wt = _wt()

        def prepare() -> tuple[Any, Command | None, dict[str, Any] | None]:
            with write() as c:
                picked = wt.pick(c, who, body.site)
                verb = body.verb
                if verb == "code":
                    if picked["central_only"]:
                        raise Problem("central_only", "This website's codes stay on the browser host", 403)
                    code = str(wt.secret(c, vault(), picked["account_id"], "code"))
                    wt.log(c, picked, who, "code", page_of(first_url(list(picked["sites"]))), "own")
                    left = int(wt.expires_in(c, vault(), picked["account_id"]))
                    return picked, None, {"ok": True, "where": "own", "text": code, "expires_in": left}
                args = clean_args(verb, body.args, list(picked["sites"]), wt.site_matches)
                spot = place(c, who, picked, verb)
                by = who_of(c, who.actor)
                if verb != "copy":
                    mailbox.take(str(picked["account_id"]), who.actor, by)
                value = (
                    str(wt.secret(c, vault(), picked["account_id"], args["field"]))
                    if verb in ("fill", "copy")
                    else None
                )
                if value is not None:
                    mailbox.remember(str(picked["account_id"]), str(args["field"]), value)
                return picked, _new(loop, spot, picked, verb, args, value, who.attempt_id, by), None

        picked, cmd, answered = await asyncio.to_thread(prepare)
        if answered is not None:
            mailbox.clear_needs_you(str(picked["account_id"]))
            return answered
        assert cmd is not None
        mailbox.enqueue(cmd)
        where = "central" if cmd.central else "own"
        try:
            report = await wait_reply(request, cmd, REPLY_WAIT)
        except Problem as refused:
            if cmd.verb == "fill" and refused.code in REQUEST_ERRORS:
                await asyncio.to_thread(
                    log, picked, who, "fill", page_of(mailbox.facts(cmd.account_id).url), where, "refused"
                )
            raise
        finally:
            if cmd.verb == "close":
                mailbox.release(cmd.account_id, who.actor)
        # Scrubbed again here as well as on arrival: a value sent after the report came in is still kept out.
        report = mailbox.scrub_report(cmd.account_id, report)
        mailbox.note(cmd, report)
        mailbox.clear_needs_you(cmd.account_id)  # the bot is past what it needed a person for
        if cmd.verb in LOGGED:
            page = page_of(
                report.url or (cmd.args.get("url") if cmd.verb == "open" else "") or mailbox.facts(cmd.account_id).url
            )
            await asyncio.to_thread(log, picked, who, cmd.verb, page, where)
        out: dict[str, Any] = {"ok": True, "where": where}
        if cmd.verb in ("read",) and report.text is not None:
            out["text"] = _scrub(report.text, cmd.value)
        if cmd.verb == "screenshot" and report.image:
            out["image"] = report.image
        if cmd.verb == "download":
            if cmd.file is None:
                raise Problem("no_download", "No finished download in this browser yet", 409)
            name = mailbox.scrub(cmd.account_id, cmd.file[0]) or "download"
            out["file"] = {"name": name, "data": base64.b64encode(cmd.file[1]).decode()}
        if report.url is not None:
            out["url"] = report.url
        if report.title is not None:
            out["title"] = _scrub(report.title, cmd.value)
        return out

    @app.post("/api/v2/browser/needs-you")
    def browser_needs_you(request: Request, body: BrowserNeedsYou) -> Any:
        """A bot stuck on a website's text code, captcha or new-device check asks a person, on its task, to take over the
        browser: an answer ask with Done that shows the live view (`browser:<tool>/<account>`)."""
        who = request.state.identity
        if who.role != "bot" or not who.attempt_id or who.agent:
            raise Problem("forbidden", "Only a bot's turn on a task asks a person to step in", 403)
        wt = _wt()

        def work(c: sqlite3.Connection) -> dict[str, Any]:
            from . import reconnects

            picked = wt.pick(c, who, body.site)
            task = _turn_task(c, who, body.task)
            if task is None:
                raise Problem("needs_a_task", "Ask for help on a task: file one, then ask there", 409)
            holder = str(picked.get("holder") or "")
            person = (
                holder
                if holder.startswith("human:")
                else cast(Any, reconnects).person_for(c, store.settings, H.actor_id(who.actor), task)
            )
            if not person:
                raise Problem("no_person", "Nobody who can read this task can take over the browser", 409)
            made = H.task_ask(
                c,
                who.actor,
                task["id"],
                person,
                body.line,
                kind="answer",
                choices=["Done"],
                shows=[f"browser:{picked['tool_id']}/{picked['account_id']}"],
                seen_version=task.get("version"),  # a stuck browser, not the task's text: no version to have read
            )
            facts = mailbox.facts(str(picked["account_id"]))
            mailbox.needs_you(str(picked["account_id"]), made.get("requirement_id"), who_of(c, who.actor))
            wt.log(c, picked, who, "needs_you", page_of(facts.url), facts.where or "central")
            return {"requirement_id": made.get("requirement_id")}

        return mutate(request, body, work)

    def _turn_task(c: sqlite3.Connection, who: Any, named: str | None) -> dict[str, Any] | None:
        """The live task the bot in this turn owns: the one it names, else the task its turn's message is on."""
        task_id = named
        if not task_id:
            row = c.execute(
                "SELECT j.message_id FROM attempts a JOIN jobs j ON j.id=a.job_id WHERE a.id=?", (who.attempt_id,)
            ).fetchone()
            msg = cast(dict[str, Any] | None, H.message(c, row[0]) if row else None)
            if msg:
                conversation = cast(dict[str, Any], H.conversation(c, msg["conversation_id"]) or {})
                refs = cast(dict[str, Any], msg.get("refs") or {})
                task_id = conversation.get("task_id") or H._task_ref(refs.get("task"))
        task = cast(dict[str, Any] | None, H.task(c, task_id) if task_id else None)
        if task and task["owner_id"] == who.actor and task["status"] in H.ACTIVE_STATUSES:
            return task
        return None

    # ------------------------------------------------------------------------------------------------ a computer's side
    @app.get("/api/v2/runner-browser-commands")
    async def runner_browser_commands(request: Request, wait: float = POLL_WAIT) -> Any:
        """The browser commands queued for this computer: at once when there are some, else [] after `wait` seconds.
        Only the browser host's own, and an own session's while its run is live, are handed out."""
        who = request.state.identity
        if who.role != "runner":
            raise Problem("identity", "A registered runner is required", 403)
        runner_id = str(who.runner_id)
        mailbox.bind()
        deadline = time.monotonic() + max(0.0, min(wait, POLL_WAIT))
        waker = mailbox.waker(runner_id)
        while True:
            waker.clear()
            queued = mailbox.queued(runner_id)
            if queued:
                allowed = await asyncio.to_thread(may_run, runner_id, queued)
                for cmd in queued:
                    if cmd.id not in allowed:
                        mailbox.settle(
                            cmd,
                            Problem("browser_host_offline", "The browser moved to another computer; try again", 409),
                        )
                taken = mailbox.hand_out(runner_id, allowed)
                if taken:
                    return {"commands": [cmd.payload() for cmd in taken]}
            left = deadline - time.monotonic()
            if left <= 0 or await request.is_disconnected():
                return {"commands": []}
            began = time.perf_counter()
            try:
                await asyncio.wait_for(waker.wait(), min(left, 5.0))
            except TimeoutError:
                pass
            finally:
                _idle(request, began)

    def may_run(runner_id: str, cmds: list[Command]) -> set[str]:
        """The commands this computer may run: the browser host's, and an own session's while its run is live there."""
        now = H.now()
        with read() as c:
            host = host_id(c) == runner_id
            live = {
                str(r[0])
                for r in c.execute(
                    "SELECT id FROM attempts WHERE runner_id=? AND state IN ('leased','running') AND lease_until>?",
                    (runner_id, now),
                )
            }
        return {cmd.id for cmd in cmds if (host if cmd.central else cmd.attempt_id in live)}

    def delivered(request: Request, cid: str) -> Command:
        who = request.state.identity
        if who.role != "runner":
            raise Problem("identity", "A registered runner is required", 403)
        with mailbox.lock:
            cmd = mailbox.inflight.get(cid)
        if cmd is None or cmd.runner_id != who.runner_id:
            raise Problem("not_found", "No such browser command waits for this computer", 404)
        return cmd

    @app.post("/api/v2/runner-browser-commands/{cid}/report")
    async def runner_browser_report(request: Request, cid: str, body: BrowserReport) -> Any:
        """A computer's answer to one command it collected."""
        cmd = delivered(request, cid)
        if body.image is not None and (_b64(body.image) or b"")[:2] != b"\xff\xd8":
            body = body.model_copy(update={"image": None, "ok": False, "error": "screenshot_failed"})
        mailbox.settle(cmd, mailbox.scrub_report(cmd.account_id, body))
        return {"ok": True}

    @app.post("/api/v2/runner-browser-commands/{cid}/file")
    async def runner_browser_file(request: Request, cid: str, name: str = "download") -> Any:
        """A download's bytes, sent before its report (at most MAX_FILE bytes)."""
        cmd = delivered(request, cid)
        if cmd.verb != "download":
            raise Problem("not_found", "That command is not a download", 404)
        data = bytearray()
        async for chunk in request.stream():
            if len(data) + len(chunk) > MAX_FILE:
                raise Problem("too_large", "A download over 20 MB is not sent", 413)
            data.extend(chunk)
        clean = re.sub(r"[^\w .()-]+", "_", name).strip()[:200] or "download"
        cmd.file = (clean, bytes(data))
        return {"ok": True}

    # ------------------------------------------------------------------------------------------------ a person's view
    base = "/api/v2/website-tools/{tool_id}/accounts/{account_id}/browser"

    def viewer(request: Request, tool_id: str, account_id: str) -> Any:
        who = request.state.identity
        person(who)
        with read() as c:
            return _wt().picked_account(c, who, tool_id, account_id)

    def on_host(picked: Any) -> Place:
        with read() as c:
            return central(c)

    @app.post(base)
    async def browser_open(request: Request, tool_id: str, account_id: str, body: BrowserOpen) -> Any:
        """Open, or wake, this account's browser on the browser host for the person's live view."""
        who = request.state.identity
        loop = mailbox.bind()
        picked = await asyncio.to_thread(viewer, request, tool_id, account_id)
        spot = await asyncio.to_thread(on_host, picked)
        facts = mailbox.facts(account_id)
        sites = list(picked["sites"])
        url = facts.url if facts.url and _wt().site_matches(sites, facts.url) else first_url(sites)
        by = await asyncio.to_thread(lambda: _with_read(lambda c: who_of(c, who.actor)))
        cmd = _new(loop, spot, picked, "open", {"url": url}, by=by)
        mailbox.enqueue(cmd)
        report = await wait_reply(request, cmd, REPLY_WAIT)
        mailbox.note(cmd, report)
        await asyncio.to_thread(log, picked, who, "open", page_of(report.url or url), "central")
        with mailbox.lock:
            frame = mailbox.frames.get(account_id)
            if frame:
                frame.issued = 0.0
        return {"ok": True, "where": "central"}

    def _with_read(fn: Callable[[sqlite3.Connection], Any]) -> Any:
        with read() as c:
            return fn(c)

    @app.get(
        base + "/frame",
        response_class=Response,
        responses={
            200: {"description": "The newest picture of the page", "content": {"image/jpeg": {}}},
            204: {"description": "No picture yet"},
        },
    )
    async def browser_frame(request: Request, tool_id: str, account_id: str) -> Response:
        """The newest picture of the account's browser on the browser host (JPEG), with X-Page-Url, X-Page-Width and
        X-Page-Height. However many people watch, the host takes at most one screenshot a second."""
        loop = mailbox.bind()
        picked = await asyncio.to_thread(viewer, request, tool_id, account_id)
        spot = await asyncio.to_thread(on_host, picked)
        now = time.monotonic()
        with mailbox.lock:
            mailbox.frames = {
                k: f for k, f in mailbox.frames.items() if f.pending or now - max(f.at, f.issued) < FRAME_KEEP
            }
            frame = mailbox.frames.setdefault(account_id, Frame())
            pending = frame.pending
            if pending is not None and now - pending.at > REPLY_WAIT:
                mailbox.settle(pending, Problem("browser_timeout", "The browser did not answer in time", 504))
                pending = frame.pending = None
            issue = pending is None and now - frame.issued >= FRAME_EVERY
            if issue:
                # The slot and the command in one step: a watcher who comes in now waits for this picture.
                frame.issued = now
                frame.pending = pending = cmd = _new(loop, spot, picked, "screenshot", {})
                cmd.reply.add_done_callback(lambda fut, c=cmd: _noted(c, fut))
        if issue and pending is not None:
            try:
                mailbox.enqueue(pending)
            except Problem as refused:
                mailbox.settle(pending, refused)
                raise
        if pending is not None:
            began = time.perf_counter()
            try:
                await asyncio.wait_for(asyncio.shield(pending.reply), FRAME_WAIT)
            except TimeoutError:
                pass
            finally:
                _idle(request, began)
        with mailbox.lock:
            data, url, width, height = frame.data, frame.url, frame.width, frame.height
        url = mailbox.scrub(account_id, url) or ""
        if not data:
            return Response(status_code=204, headers=HEADERS)
        return Response(
            data,
            media_type="image/jpeg",
            headers={
                **HEADERS,
                "X-Page-Url": url.encode("ascii", "ignore").decode()[:2000],
                "X-Page-Width": str(width),
                "X-Page-Height": str(height),
            },
        )

    def _noted(cmd: Command, fut: "asyncio.Future[Outcome]") -> None:
        if fut.cancelled():
            return
        outcome = fut.result()
        if isinstance(outcome, BrowserReport):
            mailbox.note(cmd, outcome)

    @app.post(base + "/control")
    async def browser_control(request: Request, tool_id: str, account_id: str, body: BrowserControl) -> Any:
        """Take control of the account's browser (bots wait until it is given back, or 5 minutes idle), or give it back."""
        who = request.state.identity
        await asyncio.to_thread(viewer, request, tool_id, account_id)
        if body.take:
            by = await asyncio.to_thread(lambda: _with_read(lambda c: who_of(c, who.actor)))
            mailbox.take(account_id, who.actor, by, person=True)
            return {"ok": True, "control": True}
        mailbox.release(account_id, who.actor, person=True)
        mailbox.clear_needs_you(account_id)  # the person did what the bot asked for
        return {"ok": True, "control": False}

    @app.post(base + "/input")
    async def browser_input(request: Request, tool_id: str, account_id: str, body: BrowserInput) -> Any:
        """A click, typed text, a key or a scroll from the person in control; x and y are pixels of the frame."""
        who = request.state.identity
        loop = mailbox.bind()
        picked = await asyncio.to_thread(viewer, request, tool_id, account_id)
        held = mailbox.holder(account_id)
        if held is None or held.actor != who.actor or not held.person:
            raise Problem("no_control", "Take control first", 409)
        held.until = time.monotonic() + PERSON_IDLE
        if body.kind == "click":
            if body.x is None or body.y is None:
                raise Problem("invalid_request", "A click needs x and y", 422)
            verb, args = "input_click", {"x": body.x, "y": body.y}
        elif body.kind == "type":
            if not body.text:
                raise Problem("invalid_request", "Typing needs text", 422)
            verb, args = "input_type", {"text": body.text}
        elif body.kind == "key":
            if not body.key:
                raise Problem("invalid_request", "A key needs its name", 422)
            verb, args = "input_key", {"key": body.key}
        else:
            verb, args = "input_scroll", {"dy": body.dy if body.dy is not None else 600}

        async def run() -> Any:
            spot = await asyncio.to_thread(on_host, picked)
            cmd = _new(loop, spot, picked, verb, args, by=held.who)
            mailbox.enqueue(cmd)
            report = await wait_reply(request, cmd, REPLY_WAIT)
            mailbox.note(cmd, report)
            with mailbox.lock:
                frame = mailbox.frames.get(account_id)
                if frame:
                    frame.issued = 0.0  # the next picture shows what the input did
            return {"ok": True}

        return await once(request, who.actor, run)

    # ------------------------------------------------------------------------------------------------ the pick
    @app.post("/api/v2/runners/{rid}/browser-host")
    def browser_host(request: Request, rid: str, body: BrowserHostSet) -> Any:
        """Make this computer the company's browser host (one per company), or stop. It then takes no other bots."""
        who = request.state.identity

        def work(c: sqlite3.Connection) -> dict[str, Any]:
            if who.role not in ("owner", "human") or not auth.bot_admin(who):
                raise Problem("forbidden", "An admin picks the browser host", 403)
            if not c.execute("SELECT 1 FROM runners WHERE id=? AND revoked_at IS NULL", (rid,)).fetchone():
                raise Problem("not_found", "No such computer", 404)
            return set_host(c, who.actor, rid, body.on)

        return mutate(request, body, work)

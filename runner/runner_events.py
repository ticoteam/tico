"""The server's word on what changed for this computer, in place of a dozen timed polls.

A runner used to ask about a dozen endpoints every few seconds (work, sign-ins, harness actions,
credential imports, its bots, their credentials, repositories, worktrees) whether anything was new,
and almost always heard no. Now it holds `GET /api/v2/runners/me/events` open (backend/events.py):
each `runner` event names a kind, and only the reads that kind covers run (CHANNELS). Everything
also runs once every BACKUP_POLL_S, so a missed event costs minutes, never work.

While the stream is down (it will not connect, it broke, the server refused it) the runner polls
exactly as it did before, until the stream is back; reconnects back off to RECONNECT_MAX_S and resume
from the last event id, so nothing said meanwhile is lost. A server without the endpoint (404) is
polled as before and asked again every LEGACY_RETRY_S.
"""

import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from clients.tico import CLIENT_VERSION

BACKUP_POLL_S = 300        # every read runs at least this often while the stream is up
RECONNECT_MAX_S = 60
LEGACY_RETRY_S = 1800      # a server without the stream is asked again this often
READ_TIMEOUT_S = 90        # the server sends a keepalive every 15 s by default and at most every 50 s (backend/events.py)
HEARTBEAT_LIVE_S = 60      # the full readiness report while the stream is up; 15 s otherwise

# What each event kind asks the runner to read again (runner/service.py `wants`).
CHANNELS = {
    "work": ("claim",),
    "assignments": ("sync",),
    "credentials": ("sync",),
    "config": ("sync", "config"),
    "repositories": ("sync",),
    "worktrees": ("heartbeat",),
    "restart": ("heartbeat",),
    "logins": ("logins",),
    "harness_actions": ("harness",),
    "credential_imports": ("imports",),
    "subscription_refresh": ("refresh",),
}
ALL = ("claim", "sync", "config", "heartbeat", "logins", "harness", "imports", "refresh")


class NotSupported(Exception):
    """The server has no runner stream (an older release)."""


class Ended(Exception):
    """The server ended the stream on purpose: `expired` (the computer was revoked)."""


def connect(client, after=None):
    """Yield (event, id, data) from the open stream; raise NotSupported on 404, OSError when it breaks."""
    query = "?" + urllib.parse.urlencode({"after": after}) if after is not None else ""
    request = urllib.request.Request(client.url + "/api/v2/runners/me/events" + query, headers={
        "Authorization": "Bearer " + client.token, "Accept": "text/event-stream",
        # The runner's own agent, as on every other request: a proxy such as Cloudflare refuses Python's default.
        "User-Agent": "Tico-Client/" + CLIENT_VERSION,
        **({"Last-Event-ID": str(after)} if after is not None else {})})
    try:
        response = client.opener.open(request, timeout=READ_TIMEOUT_S)
    except urllib.error.HTTPError as exc:
        if exc.code in (404, 405):
            raise NotSupported() from exc
        raise OSError(f"HTTP {exc.code}") from exc
    with response:
        event = {}
        for raw in response:
            line = raw.decode("utf-8", "replace").rstrip("\r\n")
            if not line:
                if event.get("event"):
                    yield event["event"], event.get("id"), event.get("data") or {}
                event = {}
            elif line.startswith(":"):
                yield "keepalive", None, {}
            else:
                name, _, value = line.partition(":")
                value = value[1:] if value.startswith(" ") else value
                if name == "data":
                    try:
                        event["data"] = json.loads(value)
                    except ValueError:
                        event["data"] = {}
                elif name in ("event", "id"):
                    event[name] = value


class Events:
    """The stream, read on one thread; `take` and `live` are what the runner's loop asks."""

    def __init__(self, client, connect=connect, clock=time.monotonic, log=None):
        self.client, self.connect, self.clock, self.log = client, connect, clock, log or (lambda line: None)
        self.lock = threading.Lock()
        self.pending = set()
        self.state = "down"        # down | up | legacy
        self.last_id = None
        self.release = None
        self.legacy_until = 0.0
        self.thread = None
        self.stop = threading.Event()
        self.wake = threading.Event()   # set on every ask, so the runner's loop acts now, not at its next 0.25 s tick

    @property
    def live(self):
        return self.state == "up"

    def take(self, channel):
        """True once for each time the server asked for `channel` since it was last taken."""
        with self.lock:
            if channel in self.pending:
                self.pending.discard(channel)
                return True
            return False

    def ask(self, *channels):
        with self.lock:
            self.pending.update(channels)
        self.wake.set()

    def start(self):
        self.thread = threading.Thread(target=self.run, name="runner-events", daemon=True)
        self.thread.start()

    def close(self):
        self.stop.set()

    def run(self):
        delay = 1.0
        while not self.stop.is_set():
            if self.state == "legacy" and self.clock() < self.legacy_until:
                self.stop.wait(min(60.0, self.legacy_until - self.clock()))
                continue
            try:
                clean = self.read_once()
            except NotSupported:
                if self.state != "legacy":
                    self.log("Tico runner: this server has no event stream; polling as before")
                self.state, self.legacy_until = "legacy", self.clock() + LEGACY_RETRY_S
                continue
            except Exception as exc:       # refused, broken, timed out: poll until it is back
                clean = False
                if self.state == "up":
                    self.log(f"Tico runner: event stream lost ({type(exc).__name__}); polling until it is back")
                elif not getattr(self, "said_down", False):
                    # Never up since start (a proxy refusing it, say): said once, so polling is not silent.
                    self.said_down = True
                    self.log(f"Tico runner: event stream unavailable ({type(exc).__name__}: {str(exc)[:120]}); polling")
            if clean:
                delay = 1.0
                continue                   # the server's own lifetime ran out: reconnect at once
            self.state = "down"
            self.stop.wait(delay)
            delay = min(RECONNECT_MAX_S, delay * 2)

    def read_once(self):
        """One connection, to its end. True when the server closed it after its lifetime."""
        recovering = self.state != "up"
        for event, ident, data in self.connect(self.client, self.last_id):
            if self.stop.is_set():
                return True
            if event == "ready":
                release = data.get("release")
                if self.last_id is None:
                    self.last_id = str(data.get("seq", "")) or None
                # Back after an outage or on a new server release: one full pass catches up on anything unsaid.
                if recovering or (self.release is not None and release != self.release):
                    self.ask(*ALL)
                self.release = release
                self.state = "up"
            elif event == "runner":
                self.ask(*CHANNELS.get(data.get("kind"), ALL))
            elif event == "reset":
                self.ask(*ALL)
            elif event == "expired":
                raise Ended()
            if ident:
                self.last_id = ident
        return self.state == "up"

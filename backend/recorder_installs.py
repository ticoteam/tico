"""Which Tico Recorder each person runs, and whether a newer one is out (docs/meetings.md).

The Recorder (recorder/src/network.rs) sends `X-Tico-Recorder-Version` and `X-Tico-Recorder-Install`, a random id
kept in its library folder, on every call it makes to Tico. The request middleware hands them to `Seen.note`, which
writes one row per person and install (`recorder_installs`): at once for a new install or a new version, otherwise
at most every WRITE_EVERY seconds, so a Recorder polling every few seconds costs a dictionary lookup, not a write.

The newest Recorder is the version in the Recorder's own updater feed, `recorder-latest.json` on the latest GitHub
release: the address the Recorder itself reads, so a hosted cell that can reach GitHub for its own release check can
reach this too. It is fetched in the background at most hourly, never on a request's path, and a failure keeps the
last answer (or none) without saying anything. With update checks off (`TICO_UPDATE_CHECK=off`) or in a rehearsal
nothing is fetched and no update is ever shown.

A person sees, on Meetings, the download while none of their copies has been seen lately, and one quiet line when
their most recently used copy is older than the newest. Owners and admins see each person's version in Settings >
People. Nothing else follows from it: no task, no message, no badge.
"""

import logging
import re
import sqlite3
import threading
import time

import httpx

from . import hubdb as H
from . import releases

log = logging.getLogger("tico.recorder_installs")
VERSION_HEADER = "x-tico-recorder-version"
INSTALL_HEADER = "x-tico-recorder-install"
FEED_URL = "https://github.com/ticoteam/tico/releases/latest/download/recorder-latest.json"
DOWNLOAD_URL = "https://github.com/ticoteam/tico/releases/latest/download/Tico-Recorder.dmg"
WRITE_EVERY = 600          # seconds between writes for one install whose version has not changed
RECENT_DAYS = 30           # a copy seen this recently means the person has the Recorder: no download link
KEEP = 20                  # installs kept per person; the least recently seen go first
FETCH_EVERY = 3600
RETRY = 15 * 60
NOTED_MAX = 10_000         # throttle entries held in memory before they are forgotten all at once
VERSION = re.compile(r"[0-9]{1,4}\.[0-9]{1,4}\.[0-9]{1,4}(?:-[0-9A-Za-z.]{1,32})?")
INSTALL = re.compile(r"[0-9a-f]{32}")
# Replaced in tests with an httpx.MockTransport so nothing reaches the network.
TRANSPORT = None


def from_headers(headers):
    """(version, install) from a Recorder's request, or None: absent or malformed headers are ignored."""
    version, install = headers.get(VERSION_HEADER, "").strip(), headers.get(INSTALL_HEADER, "").strip().lower()
    if VERSION.fullmatch(version) and INSTALL.fullmatch(install):
        return version, install
    return None


class Seen:
    """The last write per (person, install) in this process, so most requests skip the database."""

    def __init__(self, store, clock=time.monotonic):
        self.store, self.clock = store, clock
        self.lock = threading.Lock()
        self.noted = {}

    def due(self, who, headers):
        """The row to write for this request, or None. Cheap: no database read."""
        if who.role not in ("owner", "human") or who.via or getattr(who, "support", False):
            return None
        seen = from_headers(headers)
        if seen is None:
            return None
        person, (version, install) = H.actor_id(who.actor), seen
        with self.lock:
            last = self.noted.get((person, install))
            if last and last[0] == version and self.clock() - last[1] < WRITE_EVERY:
                return None
        return person, install, version

    def note(self, person, install, version):
        with self.lock:
            if len(self.noted) >= NOTED_MAX:
                self.noted.clear()
            self.noted[(person, install)] = (version, self.clock())
        try:
            with self.store.transaction() as c:
                at = H.now()
                c.execute("INSERT INTO recorder_installs(person_id,install_id,version,last_seen_at,created_at,updated_at) "
                          "SELECT ?,?,?,?,?,? WHERE EXISTS(SELECT 1 FROM humans WHERE id=?) "
                          "ON CONFLICT(person_id,install_id) DO UPDATE SET version=excluded.version, "
                          "last_seen_at=excluded.last_seen_at, updated_at=excluded.updated_at",
                          (person, install, version, at, at, at, person))
                c.execute("DELETE FROM recorder_installs WHERE person_id=? AND install_id NOT IN (SELECT install_id "
                          "FROM recorder_installs WHERE person_id=? ORDER BY last_seen_at DESC LIMIT ?)",
                          (person, person, KEEP))
        except (sqlite3.Error, OSError) as exc:
            # Telling versions apart is a convenience: a busy or read-only database never fails the request.
            log.debug("Could not note the Recorder version: %s", type(exc).__name__)


class Latest:
    """The newest Recorder version from its updater feed, cached; "" until a fetch has worked."""

    def __init__(self, clock=time.time):
        self.clock = clock
        self.lock = threading.Lock()
        self.version = ""
        self.checked = 0.0
        self.retry_at = 0.0
        self.running = False

    def refresh(self):
        """One fetch. Runs in a background thread; tests call it directly."""
        try:
            # A dedicated public client: no hub credentials, proxies or netrc, and GitHub's redirect to its asset host.
            with httpx.Client(timeout=5, trust_env=False, transport=TRANSPORT, follow_redirects=True) as http:
                r = http.get(FEED_URL, headers={"Accept": "application/json", "User-Agent": "tico-recorder-check"})
                r.raise_for_status()
                body = r.json()
            value = str(body.get("version") or "").strip() if isinstance(body, dict) else ""
            value = re.sub(r"^v(?=\d)", "", value)
            if not VERSION.fullmatch(value):
                raise ValueError("no version")
            self.version, self.checked, self.retry_at = value, self.clock(), 0.0
        except (httpx.HTTPError, ValueError) as exc:
            log.debug("Recorder feed check failed: %s", type(exc).__name__)
            self.retry_at = self.clock() + RETRY
        finally:
            self.running = False

    def get(self):
        """What is cached now; a stale cache starts one background fetch."""
        if not releases.Checker.enabled():
            return ""
        now = self.clock()
        if not (self.running or now < self.retry_at or (self.checked and now - self.checked < FETCH_EVERY)):
            with self.lock:
                if not self.running:
                    self.running = True
                    threading.Thread(target=self.refresh, daemon=True, name="recorder-feed").start()
        return self.version


LATEST = Latest()


def _newest(c, person_ids=None):
    """{person: (version, last_seen_at)} for each person's most recently seen copy."""
    where, args = "", ()
    if person_ids is not None:
        ids = list(person_ids)[:1000]
        if not ids:
            return {}
        where, args = " WHERE person_id IN (%s)" % ",".join("?" * len(ids)), tuple(ids)
    out = {}
    for row in c.execute("SELECT person_id, version, last_seen_at FROM recorder_installs" + where
                         + " ORDER BY last_seen_at", args):
        out[row[0]] = (row[1], row[2])
    return out


def _recent(at):
    seen = H.parse_ts(at)
    return bool(seen) and (time.time() - seen.timestamp()) < RECENT_DAYS * 86400


def mine(c, who):
    """The caller's own Recorder, for Meetings."""
    version, at = _newest(c, [H.actor_id(who.actor)]).get(H.actor_id(who.actor), ("", ""))
    latest = LATEST.get()
    return {"version": version, "last_seen_at": at or None, "recent": _recent(at) if at else False,
            "latest": latest, "update_available": bool(version) and releases.newer(latest, version),
            "download_url": DOWNLOAD_URL}


def everyone(c):
    """{person: {version, last_seen_at, update_available}} for Settings > People."""
    latest = LATEST.get()
    return {pid: {"version": version, "last_seen_at": at, "update_available": releases.newer(latest, version)}
            for pid, (version, at) in _newest(c).items()}


def install(app, store):
    from fastapi import Request
    from .views import human_only

    app.state.recorder_seen = Seen(store)

    @app.get("/api/v2/meetings/recorder")
    def my_recorder(request: Request):
        """Your most recently used Tico Recorder, the newest release's version and the download."""
        who = request.state.identity
        human_only(who)
        with store.read() as c:
            return mine(c, who)

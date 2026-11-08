"""The anonymous usage count: what an install tells Tico HQ when it looks for a new release.

Read PRIVACY.md first; this module is the whole of it. When counting is on, the release check asks
`GET <TICO_HQ_URL>/v1/latest` with exactly five query fields (`payload()`): a random install id, the
version, and three yes/no flags. Nothing else is sent, and nothing is sent at all while counting is off
(`off_reason()`), when the owner has not yet been shown the notice, or in demo mode. Off means the check
goes straight to GitHub with no id, as it did before this existed.

The id is random, made once, stored in this database and never derived from anything. Two flags are
booleans over the last seven days (a person used the app, a bot turn completed); the third says a bot the company
added has ever completed a turn here, so HQ can tell how long an install took to reach its first working bot.
"""
import json
import logging
import os
import time
import uuid

from .replication import rehearsal_on
from .store import H, Problem, encode

log = logging.getLogger("tico.census")

KEY = "usage-count"               # registry_metadata
DEFAULT_HQ = "https://updates.tico.team"
WINDOW_DAYS = 7
NOTE_EVERY = 3600                 # a person's activity is written at most this often
DOC_URL = "https://github.com/ticoteam/tico/blob/main/PRIVACY.md"
NOTICE = ("Tico counts active installs anonymously (a random ID, version, and three yes/no activity flags). "
          "Turn off: Settings > General or TICO_TELEMETRY=off.")
FIELDS = ("install_id", "version", "active_people", "active_bots", "first_result")
_OFF = ("off", "0", "false", "no", "disabled")


def hq_url():
    return os.environ.get("TICO_HQ_URL", "").strip().rstrip("/") or DEFAULT_HQ


def debug():
    return os.environ.get("TICO_TELEMETRY_DEBUG", "").strip().lower() not in ("", "0", "off", "false", "no")


def env_off():
    """The environment's own switches, which the Settings toggle cannot override: TICO_TELEMETRY=off, and the
    DO_NOT_TRACK convention (consoledonottrack.com: any value other than empty or 0 means "do not track"), and a rehearsal."""
    if rehearsal_on():
        return "TICO_REHEARSAL"
    if os.environ.get("TICO_TELEMETRY", "").strip().lower() in _OFF:
        return "TICO_TELEMETRY"
    if os.environ.get("DO_NOT_TRACK", "").strip().lower() not in ("", "0", "false", "no"):
        return "DO_NOT_TRACK"
    return ""


def first_result(c, settings):
    """When a bot the company added first completed a turn, or "" before one has: the Assistant and the other built-in
    bots answer from the first minute, so they say nothing about a working team. Attempts are never deleted and the
    table is in the order they were made, so the first match is the earliest-started result;
    the scan reads only the built-in bots' turns before it (tens of milliseconds at 20,000 Assistant turns)."""
    from .getting_started import built_in
    skip = sorted(built_in(settings))
    row = c.execute("SELECT finished_at FROM attempts WHERE state='completed' AND finished_at IS NOT NULL AND bot_id NOT IN (%s) "
                    "LIMIT 1" % ",".join("?" * len(skip)), skip).fetchone()
    return row[0] if row else ""


def _load(c):
    row = c.execute("SELECT value FROM registry_metadata WHERE key=?", (KEY,)).fetchone()
    value = json.loads(row[0]) if row else {}
    return value if isinstance(value, dict) else {}


def _save(c, value):
    c.execute("INSERT INTO registry_metadata VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
              (KEY, encode(value)))


def install_id_in(c):
    """This install's random ID inside the caller's write transaction, made if there is none yet (Census._id)."""
    record = _load(c)
    if not str(record.get("install_id") or ""):
        record["install_id"] = str(uuid.uuid4())
        _save(c, record)
    return record["install_id"]


class Census:
    def __init__(self, store, settings, clock=H.now):
        self.store, self.settings, self.clock = store, settings, clock
        self._noted = 0.0

    # ------------------------------------------------------------------ whether
    def off_reason(self, c=None):
        """Why nothing is sent: "demo", "TICO_REHEARSAL", "TICO_TELEMETRY", "DO_NOT_TRACK", "setting", or "" when counting is on."""
        if self.settings.demo:
            return "demo"
        if self.settings.rehearsal:
            return "TICO_REHEARSAL"
        env = env_off()
        if env:
            return env
        if c is None:
            with self.store.read() as c:
                return self.off_reason(c)
        return "" if _load(c).get("enabled", True) else "setting"

    def enabled(self, c=None):
        return not self.off_reason(c)

    def ready(self, c):
        """Counting is on and the owner has been shown the notice (as Homebrew does: nothing goes out before it)."""
        return not self.off_reason(c) and bool(_load(c).get("notice"))

    # ------------------------------------------------------------------ the payload
    def payload(self, c, version):
        """The five fields, or None when nothing may be sent. Creates the id on first use."""
        if not self.ready(c):
            return None
        since = H.shift(self.clock(), days=-WINDOW_DAYS)
        record = _load(c)
        people = str(record.get("last_person") or "") >= since
        bots = c.execute("SELECT 1 FROM attempts WHERE state='completed' AND finished_at>=? LIMIT 1",
                         (since,)).fetchone() is not None
        return {"install_id": self._id(record, c), "version": version, "active_people": people, "active_bots": bots,
                "first_result": bool(first_result(c, self.settings))}

    def _id(self, record, c=None):
        value = str(record.get("install_id") or "")
        if value:
            return value
        with self.store.transaction() as w:
            record = _load(w)
            record.setdefault("install_id", str(uuid.uuid4()))
            _save(w, record)
            return record["install_id"]

    def install_id(self):
        """This install's random ID, made if there is none yet. A support request the person chooses to send with it
        uses this, whatever the counting choice is (PRIVACY.md, "Support tickets")."""
        with self.store.read() as c:
            record = _load(c)
        return self._id(record)

    def query(self, version):
        """What the release check adds to its request to HQ, or None to go straight to GitHub. In debug mode the
        exact payload is logged and nothing is sent."""
        with self.store.read() as c:
            data = self.payload(c, version)
        if data is None:
            return None
        if debug():
            log.warning("[usage count] would send GET %s/v1/latest with %s (debug: not sending)", hq_url(),
                        {k: data[k] for k in FIELDS})
            return None
        return {"install_id": data["install_id"], "version": data["version"],
                **{k: str(data[k]).lower() for k in ("active_people", "active_bots", "first_result")}}

    # ------------------------------------------------------------------ activity
    def note_person(self, who):
        """A person used the app: remember the time, at most hourly, and only while counting is on."""
        if who.role not in ("owner", "human") or who.via or who.via_token:
            return
        now = time.monotonic()
        if self._noted and now - self._noted < NOTE_EVERY:
            return
        self._noted = now
        try:
            with self.store.transaction() as c:
                if self.off_reason(c):
                    return
                record = _load(c)
                record["last_person"] = self.clock()
                _save(c, record)
        except Exception as exc:
            log.debug("Could not note activity: %s", type(exc).__name__)

    def person_due(self, who):
        return (who.role in ("owner", "human") and not who.via and not who.via_token
                and (not self._noted or time.monotonic() - self._noted >= NOTE_EVERY))

    # ------------------------------------------------------------------ the owner's controls
    def view(self, c):
        record = _load(c)
        off = self.off_reason(c)
        return {"enabled": not off, "off_by": off, "install_id": str(record.get("install_id") or ""),
                "notice": record.get("notice") or "", "text": NOTICE, "doc": DOC_URL}

    def set_notice(self, state):
        if state not in ("shown", "dismissed"):
            raise Problem("validation", "Unknown notice state", 422)
        with self.store.transaction() as c:
            record = _load(c)
            if record.get("notice") != "dismissed":
                record["notice"] = state
                _save(c, record)

    def set_enabled(self, enabled, actor):
        with self.store.transaction() as c:
            record = _load(c)
            record["enabled"] = bool(enabled)
            record.setdefault("notice", "dismissed")   # they have made the choice themselves
            _save(c, record)
            H.event(c, actor, "usage_count.on" if enabled else "usage_count.off", "")
            return self.view(c)

    def reset_id(self, actor):
        """A new random id: the old one is forgotten here and no longer connects to this install."""
        with self.store.transaction() as c:
            record = _load(c)
            record["install_id"] = str(uuid.uuid4())
            _save(c, record)
            H.event(c, actor, "usage_count.reset_id", "")
            return self.view(c)


def notice_due(c, settings, who):
    """Whether the owner's page prints the one-time notice: counting is on, and it was not dismissed."""
    if who is None or who.role != "owner" or settings.demo or env_off():
        return False
    record = _load(c)
    return record.get("enabled", True) and record.get("notice") != "dismissed"

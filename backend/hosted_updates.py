"""Tico support's scheduled update of a hosted server (Chris, Oct 25: hosted servers "should be able to be remotely
bumped but not 100% automatic").

Only a hosted install (TICO_HOSTED own or invited, which the installer's `--hosted` sets) asks, and only with a Gateway
key, the one credential it already holds for Tico HQ. Every CHECK_S it tells HQ its version at
`<Gateway URL>/servers/update` and reads back the target Tico staff set for this server, if any (ticoteam/tico-services
hosting/updates.py). Nothing happens until staff set one: there is no following of new releases, and HQ never reaches
into the server. A self-run server never asks.

A target newer than the running release waits for a moment no bot turn is running (no leased or running attempt, and no
computer saying it is busy), at most IDLE_WAIT_S, then goes through the standard updater exactly as the owner's Update
now does (`releases.start`: the updater's snapshot, health check and rollback, the `update-pending` marker and the
system.update events). Computers follow their server as always. An update already under way (the owner's) is left to
finish. The outcome (updated, failed, rolled_back, or timed_out after SETTLE_S with no final state from the updater) is
told to HQ at the next check-in, a minute later, and HQ clears the target. Each target staff set has its own
`request_id`, and one request is acted on once: a failed or rolled-back update is never tried again until staff set
the target again. A target the server is already on (or past) is reported `updated_at` without an update.

The owner stays in control: their Update now still works, and while a target waits Health's Version line and the New
version popover say "Tico will update this server to vX when idle" (`notice`).

State is one `registry_metadata` row, KEY: {request_id, target, state (waiting, updating or done), queued_s,
started_s, outcome, message, reported, checked_s}. It survives the update's own restart, so the new release finishes
the record. HQ's answers are read raw, capped at MAX_ANSWER bytes, with no redirects, and must have exactly the fields
the contract names. The key goes only in the request's header, only to a URL `clients/gateway.py safe_base` allows.
"""
import json
import re
import threading
import time
import urllib.error
import urllib.request

from clients import gateway as G

from . import hubdb as H, releases, tico_notices
from .replication import rehearsal_on
from .store import Problem

KEY = "hosted-update"
ACTOR = "system:hosting"
PATH = "/servers/update"
HOSTED = ("own", "invited")
CHECK_S = 15 * 60            # HQ is asked this often; at once after an outcome
IDLE_WAIT_S = 2 * 3600       # a busy server updates anyway after this long, as Update now would
SETTLE_S = 45 * 60           # an update with no final state from the updater after this long is reported timed_out
WAIT = 10                    # seconds for HQ's answer
MAX_ANSWER = 4096
OPENER = None                # tests replace it with a function (request, timeout) -> response: nothing leaves the test
REQUEST_ID = re.compile(r"hu_[0-9a-f]{16}")
STABLE = re.compile(r"[0-9]{1,4}\.[0-9]{1,4}\.[0-9]{1,4}")
UNDER_WAY = ("pulling", "restarting", "uncertain")
_ticking = threading.Lock()


def on(settings):
    """A hosted install that is not a demo or a rehearsal (a copy of real data never updates for HQ), and that Tico does
    not update from outside (TICO_UPDATES=tico). A cell company has an updater of its own (docs/cell.md) and asks."""
    return getattr(settings, "hosted", "") in HOSTED and not getattr(settings, "demo", False) \
        and not getattr(settings, "rehearsal", False) and not rehearsal_on() and not releases.managed()


def load(c):
    row = c.execute("SELECT value FROM registry_metadata WHERE key=?", (KEY,)).fetchone()
    try:
        stored = json.loads(row[0]) if row else {}
    except ValueError:
        stored = {}
    return stored if isinstance(stored, dict) else {}


def save(c, record):
    c.execute("INSERT INTO registry_metadata VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
              (KEY, json.dumps(record, sort_keys=True)))


def scheduled(c, settings):
    """The release Tico support has this server waiting for or updating to, or ''."""
    record = load(c)
    return record.get("target", "") if on(settings) and record.get("state") in ("waiting", "updating") else ""


def notice(c, settings):
    """`releases.notice()` with `scheduled`, for the config and Health. `messages` also holds the notices Tico Team sent
    through the Gateway relay, each once even when `/v1/latest` carries it too (backend/tico_notices.py); `message`
    is the first of them, which the banner shows."""
    out = releases.notice()
    shown = tico_notices.banner(c, out.get("message"))
    return {**out, "message": shown[0] if shown else None, "messages": shown, "scheduled": scheduled(c, settings)}


def reached(current, target):
    """Whether the running release is `target` or newer."""
    return bool(releases.parse(current)) and not releases.newer(target, current)


def busy(c):
    """A bot turn is running: a leased or running attempt, or a computer whose last claim named busy work (a lease that
    lapsed while its worker still runs)."""
    if c.execute("SELECT 1 FROM attempts WHERE state IN ('leased','running') LIMIT 1").fetchone():
        return True
    from .execution import CLAIMED_BUSY, claimed_busy
    return any(claimed_busy(runner)[0] for runner in list(CLAIMED_BUSY))


def record_outcome(c, result):
    """The updater's final state for the update the `update-pending` marker names: its system.update.finished event,
    once (the GET route, the updater's restart and this module may each see it first). A state for another release
    leaves the marker. True when it wrote the event."""
    row = c.execute("SELECT value FROM registry_metadata WHERE key='update-pending'").fetchone()
    if not row:
        return False
    pending = json.loads(row[0])
    if result.get("to") and str(result["to"]).lstrip("v") != pending["to"]:
        return False
    c.execute("DELETE FROM registry_metadata WHERE key='update-pending'")
    H.event(c, pending["actor"], "system.update.finished", pending["to"],
            {"outcome": result["state"], "from": pending["from"], "to": pending["to"],
             "message": result.get("message", "")})
    return True


# ----------------------------------------------------------------------------- HQ
def _ask(key, base, body):
    """(status, JSON object or None) of HQ's answer, or (0, None) when it was not reached."""
    try:
        base = G.safe_base(base)
    except ValueError:
        return 0, None
    request = urllib.request.Request(base + PATH, method="POST", data=json.dumps(body).encode(), headers={
        "Authorization": "Bearer " + key, "Content-Type": "application/json", "Accept": "application/json"})
    try:
        with (OPENER or G._send)(request, timeout=WAIT) as response:
            raw, status = response.read(MAX_ANSWER + 1), getattr(response, "status", 200)
    except urllib.error.HTTPError as error:
        return error.code, None
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        return 0, None
    if len(raw) > MAX_ANSWER:
        return 0, None
    try:
        answer = json.loads(raw)
    except (ValueError, UnicodeDecodeError, RecursionError):
        return 0, None
    return status, answer if isinstance(answer, dict) else None


def _target(answer):
    """HQ's {target}: (version, request_id), None for no target, or ValueError for anything else."""
    if not isinstance(answer, dict) or set(answer) != {"target"}:
        raise ValueError("answer")
    target = answer["target"]
    if target is None:
        return None
    if not isinstance(target, dict) or set(target) != {"version", "request_id"} \
            or not isinstance(target["version"], str) or not STABLE.fullmatch(target["version"]) \
            or not isinstance(target["request_id"], str) or not REQUEST_ID.fullmatch(target["request_id"]):
        raise ValueError("target")
    return target["version"], target["request_id"]


def check_in(store, key, base, now):
    """Tell HQ this server's version (and an outcome not yet told), and take up a new target."""
    with store.read() as c:
        record = load(c)
    body = {"version": releases.version()}
    told = record.get("request_id") if record.get("outcome") and not record.get("reported") else ""
    if told:
        body["done"] = {"request_id": told, "outcome": record["outcome"], "message": record.get("message", "")[:300]}
    status, answer = _ask(key, base, body)
    with store.transaction() as c:
        record = load(c)
        record["checked_s"] = now
        try:
            target = _target(answer) if status == 200 else ValueError
        except ValueError:
            target = ValueError
        if target is ValueError:                   # not reached, refused or not hosted: ask again next time
            save(c, record)
            return
        if told and record.get("request_id") == told:
            record["reported"] = True
        if target is None:
            if record.get("state") == "waiting":   # staff took it back before it started
                H.event(c, ACTOR, "system.update.unscheduled", record["target"], {"to": record["target"]})
                record.update(state="done", outcome="", message="", reported=True)
        elif target[1] != record.get("request_id") and record.get("state") != "updating":
            version, request_id = target
            current = releases.version()
            record = {"request_id": request_id, "target": version, "checked_s": now, "reported": False}
            if reached(current, version):
                record.update(state="done", outcome="updated", message="")
            else:
                record.update(state="waiting", queued_s=now, outcome="", message="")
                H.event(c, ACTOR, "system.update.scheduled", version, {"from": current, "to": version})
        save(c, record)


# ----------------------------------------------------------------------------- the update
def _finish(store, request_id, outcome, message):
    with store.transaction() as c:
        record = load(c)
        if record.get("request_id") != request_id or record.get("state") not in ("waiting", "updating"):
            return
        record.update(state="done", outcome=outcome, message=message[:300], reported=False)
        save(c, record)
        record_outcome(c, {"state": outcome, "to": record["target"], "message": message})


def _status():
    try:
        return releases.status()
    except Problem:                                # the updater not answering, as while it restarts the server
        return {"state": "unanswered"}


def advance(store, now):
    """Move a waiting or updating target one step. Returns the record's state."""
    with store.read() as c:
        record = load(c)
    state, target, request_id = record.get("state", ""), record.get("target", ""), record.get("request_id", "")
    if state not in ("waiting", "updating"):
        return state
    current = releases.version()
    if state == "waiting":
        if reached(current, target):               # the owner updated meanwhile
            _finish(store, request_id, "updated", "")
            return "done"
        if _status().get("state") in UNDER_WAY:    # another update (the owner's) is under way: it finishes first
            return state
        with store.transaction() as c:
            record = load(c)
            if record.get("request_id") != request_id or record.get("state") != "waiting":
                return record.get("state", "")
            if busy(c) and now - record.get("queued_s", now) < IDLE_WAIT_S:
                return state
            record.update(state="updating", started_s=now)
            save(c, record)
            c.execute("INSERT INTO registry_metadata VALUES('update-pending',?) "
                      "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                      (json.dumps({"actor": ACTOR, "from": current, "to": target}),))
            H.event(c, ACTOR, "system.update.started", target, {"from": current, "to": target})
            from .execution import flag_cut
            flag_cut(c, "update", target)    # the runs it cuts resume after it (execution.cut_by_update)
        try:
            releases.start(target)
        except Problem as problem:
            _finish(store, request_id, "failed", problem.detail)
            return "done"
        return "updating"
    result = _status()
    final, to = result.get("state"), str(result.get("to") or "").lstrip("v")
    if final == "healthy" and reached(current, target):
        _finish(store, request_id, "updated", "")
    elif final in ("failed", "rolled_back") and to == target:
        _finish(store, request_id, final, result.get("message") or "")
    elif now - record.get("started_s", now) >= SETTLE_S:
        _finish(store, request_id, "timed_out", "The updater gave no final answer in 45 minutes.")
    else:
        return state
    return "done"


def due(store, now=None):
    """Whether this minute has anything to do: HQ to ask, or a target to move."""
    if not on(store.settings):
        return False
    with store.read() as c:
        record = load(c)
    now = now or time.time()
    return record.get("state") in ("waiting", "updating") or now - record.get("checked_s", 0) >= CHECK_S \
        or bool(record.get("outcome") and not record.get("reported"))


def tick(store, vault, now=None):
    """One minute's step: ask HQ when it is due, then move the target. Returns the record's state."""
    if not on(store.settings) or not _ticking.acquire(blocking=False):
        return ""
    try:
        from . import gateway_connection
        now = now or time.time()
        with store.read() as c:
            record = load(c)
            key, base, _ = gateway_connection.target(c, vault, store.settings)
        if key and (now - record.get("checked_s", 0) >= CHECK_S or (record.get("outcome") and not record.get("reported"))):
            check_in(store, key, base, now)
        return advance(store, now)
    finally:
        _ticking.release()


def tick_later(store):
    """`tick` on its own thread, for the scheduler's minute: a tick never waits on HQ or the updater."""
    from .credentials import Vault

    def run():
        try:
            tick(store, Vault(store))
        except Exception:
            pass                                   # the next minute tries again
    thread = threading.Thread(target=run, name="hosted-update", daemon=True)
    thread.start()
    return thread

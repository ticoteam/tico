"""Sending outgoing webhooks (backend/webhooks.py makes the sends; this module delivers them).

A send is one event for one endpoint, with the exact body it will carry. Four daemon threads take due sends, a
doorbell wakes them after a commit that queued one, and a short poll covers a missed ring. A worker claims a send
(`ready` to `sending`, committed), posts outside any transaction, then records the attempt.

Delivery is at least once. A crash between the post and the record puts the send back in `ready` two minutes later,
so a receiver may see one send twice and drops the repeat by its `webhook-id`, which is the send's id and never
changes. `webhook-timestamp` is new on every attempt and the signature covers it (Standard Webhooks:
`v1,<base64 HMAC-SHA256 of "id.timestamp.body">` under the decoded `whsec_` key; a second `v1,` signature while a
replaced secret is still in its day of grace).

A 2xx answer delivers. `webhook-delivery: abort-message` cancels. Anything else, a redirect included, is tried again
at 5 s, 5 min, 30 min, 2 h, 5 h, 10 h and 10 h (each within 10 %), after which the send is `failed`.
"""
import base64
import hashlib
import hmac
import logging
import random
import threading
import time

from . import egress
from . import hubdb as H

log = logging.getLogger(__name__)

WORKERS = 4
SWEEP = 5.0                       # seconds a worker sleeps without a ring
PER_HOST = 2                      # sends in flight to one host at once
STALE = 120                       # seconds a `sending` row may sit before it returns to `ready`
RETRY_AFTER = (5, 300, 1800, 7200, 18000, 36000, 36000)     # seconds after the 1st, 2nd... failed attempt
MAX_ATTEMPTS = len(RETRY_AFTER) + 1
JITTER = 0.10
GRACE_HOURS = 24
NOTIFY_HOURS = 24
KEEP_DAYS = 30
KEEP_ATTEMPTS = 200
PREFIX = "whsec_"
TERMINAL = ("delivered", "failed", "canceled", "withheld")


def sealed_as(webhook_id, previous=False):
    return f"webhooks:{webhook_id}" + (":prev" if previous else "")


def key_of(secret):
    """The signing key inside a `whsec_` secret: its base64 payload, decoded."""
    return base64.b64decode(secret[len(PREFIX):] if secret.startswith(PREFIX) else secret)


def signature(secret, send_id, timestamp, body):
    """`v1,<signature>` for one attempt. `body` is bytes."""
    signed = f"{send_id}.{timestamp}.".encode() + body
    return "v1," + base64.b64encode(hmac.new(key_of(secret), signed, hashlib.sha256).digest()).decode()


def headers_for(secrets_, send_id, timestamp, body, custom=None):
    """The headers of one attempt. The endpoint's own headers (lowercase names) go under Tico's: they can add or
    replace the user-agent, never the content type or the signing headers (webhooks.clean_headers refuses those)."""
    return {"user-agent": "Tico-Webhooks/1", **(custom or {}), "content-type": "application/json",
            "webhook-id": send_id, "webhook-timestamp": str(timestamp),
            "webhook-signature": " ".join(signature(s, send_id, timestamp, body) for s in secrets_)}


def retry_delay(failed, rng=random):
    """Seconds until the next attempt after `failed` failed attempts (1 after the first), or None when none are left."""
    if failed >= MAX_ATTEMPTS:
        return None
    return RETRY_AFTER[failed - 1] * rng.uniform(1 - JITTER, 1 + JITTER)


# ----------------------------------------------------------------------------------------------------- the workers
class Bell:
    """A doorbell for threads: `ring` wakes every waiter, and one that was busy when it rang does not sleep through it."""

    def __init__(self):
        self.cond, self.rung = threading.Condition(), 0

    def ring(self):
        with self.cond:
            self.rung += 1
            self.cond.notify_all()

    def wait(self, seen, timeout):
        with self.cond:
            if self.rung == seen:
                self.cond.wait(timeout)
            return self.rung


BELL = Bell()
_workers = {"threads": [], "stop": threading.Event(), "lock": threading.Lock(), "recovered": 0.0, "args": None}


def ring():
    BELL.ring()


def start(store, vault, *, rehearsal=False, transport=None, workers=WORKERS):
    """Start (or restart, the ones that died) the delivery threads. Safe to call again on every scheduler tick."""
    with _workers["lock"]:
        _workers["args"] = (store, vault, rehearsal, transport, workers)
        _workers["threads"] = [t for t in _workers["threads"] if t.is_alive()]
        if _workers["stop"].is_set() and not _workers["threads"]:
            _workers["stop"] = threading.Event()
        while len(_workers["threads"]) < workers:
            t = threading.Thread(target=_loop, args=(store, vault, rehearsal, transport, _workers["stop"]),
                                 name=f"webhook-delivery-{len(_workers['threads'])}", daemon=True)
            t.start()
            _workers["threads"].append(t)


def ensure():
    """Restart a worker that died. The scheduler's tick calls it; it does nothing before the first `start`."""
    if _workers["args"] and not _workers["stop"].is_set():
        store, vault, rehearsal, transport, workers = _workers["args"]
        start(store, vault, rehearsal=rehearsal, transport=transport, workers=workers)


def stop():
    _workers["stop"].set()
    BELL.ring()
    for t in list(_workers["threads"]):
        t.join(5)
    _workers["threads"] = []


def _loop(store, vault, rehearsal, transport, halt):
    while not halt.is_set():
        seen = BELL.rung
        try:
            worked = step(store, vault, rehearsal=rehearsal, transport=transport)
        except Exception:
            log.exception("webhook delivery step failed")
            worked = False
        if not worked:
            BELL.wait(seen, SWEEP)


def step(store, vault, *, rehearsal=False, transport=None, now=None):
    """Claim one due send and deliver it. True when it did one (so the caller looks again at once)."""
    stamp = now or H.now()
    if time.monotonic() - _workers["recovered"] > 30:
        _workers["recovered"] = time.monotonic()
        recover(store, stamp)
    if rehearsal:
        withhold(store, stamp)
        return False
    claimed = claim(store, stamp)
    if not claimed:
        return False
    deliver(store, vault, claimed, transport=transport)
    return True


# ------------------------------------------------------------------------------------------------------ state moves
def recover(store, now=None):
    """`sending` rows whose worker never recorded an answer (a crash, a restart) go back to `ready`."""
    now = now or H.now()
    with store.transaction() as c:
        back = c.execute("UPDATE webhook_sends SET state='ready', claimed_at=NULL, next_attempt_at=? "
                         "WHERE state='sending' AND claimed_at<?", (now, H.shift(now, seconds=-STALE))).rowcount
    if back:
        log.warning("%d webhook sends were in flight when their worker stopped; trying them again", back)
        ring()
    return back


def withhold(store, now=None):
    """A rehearsal copy sends nothing: due sends end as `withheld`."""
    now = now or H.now()
    with store.transaction() as c:
        return c.execute("UPDATE webhook_sends SET state='withheld', next_attempt_at=?, "
                         "last_error='Rehearsal: nothing is sent from a copy' "
                         "WHERE state='ready' AND next_attempt_at<=?", (now, now)).rowcount


def claim(store, now):
    """The next due send, now `sending` and committed, or None. Skips a host that already has PER_HOST in flight and
    holds an endpoint past its hourly rate until its hour is over (its sends wait; none is dropped)."""
    with store.transaction() as c:
        rows = c.execute(
            "SELECT s.id, s.webhook_id, w.host, w.rate_per_hour, w.window_start, w.window_count FROM webhook_sends s "
            "JOIN webhooks w ON w.id=s.webhook_id WHERE s.state='ready' AND s.next_attempt_at<=? AND w.deleted_at IS NULL "
            "AND w.enabled=1 ORDER BY s.next_attempt_at LIMIT 100", (now,)).fetchall()
        busy = {}
        for row in rows:
            if row["host"] not in busy:
                busy[row["host"]] = c.execute(
                    "SELECT count(*) FROM webhook_sends s JOIN webhooks w ON w.id=s.webhook_id "
                    "WHERE s.state='sending' AND w.host=?", (row["host"],)).fetchone()[0]
            if busy[row["host"]] >= PER_HOST:
                continue
            start_, count = row["window_start"], row["window_count"]
            if not start_ or start_ <= H.shift(now, hours=-1):
                start_, count = now, 0
            if count >= row["rate_per_hour"]:
                c.execute("UPDATE webhook_sends SET next_attempt_at=? WHERE id=?", (H.shift(start_, hours=1), row["id"]))
                continue
            c.execute("UPDATE webhooks SET window_start=?, window_count=? WHERE id=?", (start_, count + 1, row["webhook_id"]))
            c.execute("UPDATE webhook_sends SET state='sending', claimed_at=? WHERE id=?", (now, row["id"]))
            return row["id"]
    return None


def deliver(store, vault, send_id, *, transport=None):
    """Post one claimed send and record what came back."""
    with store.read() as c:
        send = c.execute("SELECT s.*, w.url, w.allow_http, w.secret, w.secret_prev, w.secret_prev_until, w.deleted_at, "
                         "w.headers "
                         "FROM webhook_sends s JOIN webhooks w ON w.id=s.webhook_id WHERE s.id=?", (send_id,)).fetchone()
        if not send:
            return
        keys = []
        if send["secret"]:
            keys.append(vault.cipher.unseal(c, sealed_as(send["webhook_id"]), send["secret"]))
        if send["secret_prev"] and (send["secret_prev_until"] or "") > H.now():
            keys.append(vault.cipher.unseal(c, sealed_as(send["webhook_id"], True), send["secret_prev"]))
        from . import webhooks
        stored = webhooks.headers_of(send)
        custom = webhooks.open_headers(c, vault, send["webhook_id"], stored)
    if webhooks.needs_value(stored):
        # A secret header whose value was cleared when the address changed: never sent without it. The send waits for
        # the value (the endpoint is paused until then, so it is not claimed again).
        with store.transaction() as c:
            c.execute("UPDATE webhook_sends SET state='ready', claimed_at=NULL WHERE id=? AND state='sending'", (send_id,))
        return
    body = send["body"].encode()
    timestamp = int(time.time())
    started = time.monotonic()
    status, head, error, abort = None, "", "", False
    try:
        answer = egress.post(send["url"], body, headers_for(keys, send_id, timestamp, body, custom),
                             allow_http=bool(send["allow_http"]), transport=transport)
        status, head = answer.status, answer.head
        abort = answer.headers.get("webhook-delivery", "").strip().lower() == "abort-message"
    except egress.Blocked as exc:
        error = exc.reason
    except Exception as exc:                # a network error of any kind is a failed attempt, never a dead worker
        error = f"{type(exc).__name__}: {exc}"[:300]
    record(store, send, timestamp, status, head, error, abort, int((time.monotonic() - started) * 1000))


def record(store, send, timestamp, status, head, error, abort, ms):
    now = H.now()
    with store.transaction() as c:
        n = (c.execute("SELECT max(n) FROM webhook_attempts WHERE send_id=?", (send["id"],)).fetchone()[0] or 0) + 1
        c.execute("INSERT INTO webhook_attempts(send_id,webhook_id,n,created_at,timestamp_sent,status,ms,response_head,error) "
                  "VALUES(?,?,?,?,?,?,?,?,?)", (send["id"], send["webhook_id"], n, now, timestamp, status, ms,
                                                head[:1024], error[:300]))
        if status is not None and 200 <= status < 300:
            c.execute("UPDATE webhooks SET failing_since=NULL WHERE id=? AND failing_since IS NOT NULL", (send["webhook_id"],))
        else:
            c.execute("UPDATE webhooks SET failing_since=? WHERE id=? AND failing_since IS NULL", (now, send["webhook_id"]))
        now_row = c.execute("SELECT state, attempts FROM webhook_sends WHERE id=?", (send["id"],)).fetchone()
        if not now_row or now_row["state"] != "sending":
            return                              # deleted or retried by hand while the post was out; the attempt stays logged
        attempts = now_row["attempts"] + 1
        shown = error or f"HTTP {status}"
        from . import task_trigger_rules
        task_trigger_rules.webhook_result(c, send["event_id"], status)
        if status is not None and 200 <= status < 300:
            c.execute("UPDATE webhook_sends SET state='delivered', attempts=?, last_status=?, last_error='', "
                      "delivered_at=?, next_attempt_at=?, claimed_at=NULL WHERE id=?", (attempts, status, now, now, send["id"]))
        elif abort:
            c.execute("UPDATE webhook_sends SET state='canceled', attempts=?, last_status=?, last_error=?, "
                      "next_attempt_at=?, claimed_at=NULL WHERE id=?",
                      (attempts, status, "The receiver asked not to be sent this message again", now, send["id"]))
        else:
            wait = retry_delay(attempts)
            if wait is None:
                c.execute("UPDATE webhook_sends SET state='failed', attempts=?, last_status=?, last_error=?, "
                          "next_attempt_at=?, claimed_at=NULL WHERE id=?", (attempts, status, shown, now, send["id"]))
                exhausted(c, send, attempts, status, shown, now)
            else:
                at = H.shift(now, seconds=wait)
                c.execute("UPDATE webhook_sends SET state='ready', attempts=?, last_status=?, last_error=?, "
                          "next_attempt_at=?, claimed_at=NULL WHERE id=?", (attempts, status, shown, at, send["id"]))
                c.after_commit.append(ring)


def exhausted(c, send, attempts, status, shown, now):
    """The last attempt failed: one audit row (no payload), and at most one Needs-you line a day per endpoint to the
    person who owns it. Health shows the failure from the rows themselves."""
    H.event(c, H.KEEPER, "webhook.exhausted", send["id"],
            {"webhook": send["webhook_id"], "type": send["type"], "attempts": attempts, "status": status})
    hook = c.execute("SELECT * FROM webhooks WHERE id=?", (send["webhook_id"],)).fetchone()
    if not hook or hook["deleted_at"]:
        return
    if hook["notified_at"] and hook["notified_at"] > H.shift(now, hours=-NOTIFY_HOURS):
        return
    c.execute("UPDATE webhooks SET notified_at=? WHERE id=?", (now, hook["id"]))
    try:
        # One savepoint: a refusal to file the line must not undo the failed send's record.
        c.execute("SAVEPOINT webhook_notice")
        task = H.task_create(c, H.KEEPER, f"Webhook {hook['name']} is not receiving events",
                             f"Tico gave up on a {send['type']} event after {attempts} attempts ({shown}). "
                             f"Check the endpoint, then retry the send from Settings > Webhooks or "
                             f"`hub webhook retry {send['id']}`.", hook["created_by"], lint=False)
        c.execute("UPDATE webhooks SET notify_task=? WHERE id=?", (task["id"], hook["id"]))
        c.execute("RELEASE webhook_notice")
    except Exception:
        c.execute("ROLLBACK TO webhook_notice")
        c.execute("RELEASE webhook_notice")
        log.warning("Could not file the Needs-you line for webhook %s", hook["id"], exc_info=True)


# --------------------------------------------------------------------------------------------------------- retention
def prune(c, now=None):
    """The hourly cleanup: events and finished sends after KEEP_DAYS, attempts to KEEP_ATTEMPTS an endpoint, and a
    deleted endpoint once nothing of it is left."""
    cutoff = H.shift(now or H.now(), days=-KEEP_DAYS)
    c.execute("DELETE FROM webhook_events WHERE created_at<?", (cutoff,))
    c.execute(f"DELETE FROM webhook_sends WHERE state IN ({','.join('?' * len(TERMINAL))}) AND next_attempt_at<?",
              (*TERMINAL, cutoff))
    c.execute("DELETE FROM webhook_attempts WHERE send_id NOT IN (SELECT id FROM webhook_sends)")
    c.execute("DELETE FROM webhook_attempts WHERE rowid IN (SELECT rowid FROM (SELECT rowid, row_number() OVER "
              "(PARTITION BY webhook_id ORDER BY created_at DESC, n DESC) AS rank FROM webhook_attempts) WHERE rank>?)",
              (KEEP_ATTEMPTS,))
    c.execute("DELETE FROM webhooks WHERE deleted_at IS NOT NULL AND deleted_at<? "
              "AND NOT EXISTS(SELECT 1 FROM webhook_sends s WHERE s.webhook_id=webhooks.id)", (cutoff,))

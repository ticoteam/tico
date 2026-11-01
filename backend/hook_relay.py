"""Webhooks through Tico Gateway, the install's side.

A trigger's own address needs this server to be up and reachable. A relay is a second address at Tico HQ
(`https://hooks.tico.dev/<relay_id>/<secret>`, or the older `https://api.tico.dev/v1/hooks/<relay_id>/<secret>`): HQ checks the secret, stores the delivery and answers 202, and
this server pulls what is waiting every 15 seconds and feeds each delivery to `triggers.receive` with `via="gateway"`,
exactly as if the sender had posted it here. A stopped or local server loses nothing; it catches up when it is back.
The trigger's own address keeps working in parallel.

Notices from Tico Team ride the same queue (backend/tico_notices.py): HQ keeps a system relay for the built-in trigger
`_tico`, so a Gateway key alone is enough to receive them. The loop then pulls every minute instead of every 15
seconds. That relay is recorded only from HQ's list of relays (`system: true`, asked at most hourly, and at once when a
delivery names a relay not recorded yet); a delivery naming `_tico` from any other relay is dropped and acked. Physical
mail (backend/postal_mail.py) arrives on the same system relay with the trigger `_mail`, under the same rule.

The pull follows the mail loop (backend/inbound.py): `wait=0`, a cursor kept here, an ack after the work is committed,
off the scheduler thread. Every outcome of `receive` (accepted, duplicate, refused, rate_limited, not_found) is
terminal and is acked, so a refusal never blocks the cursor. The install verifies signatures itself (HQ keeps the
headers and the exact body); HQ only checked the URL secret.

The Gateway key (backend/gateway_connection.py) is the credential. Without one a relay cannot be made, and a key HQ
refuses (401) stops the loop until the key changes. Deleting a trigger forgets its relay (`forget`), and the HQ delete
is finished by the loop, off any transaction. Rehearsal and demo never pull.

A server that is off or asleep can be woken by a delivery that waits (contract C10): the owner gives HQ an address they
run (`wake_url`), and HQ posts a signed `tico.wake` to it when a delivery has waited over a minute. HQ keeps the signing
secret and shows it once; this server keeps only the address, who set it and when. Hosted servers are woken by HQ already
(backend/sleep_idle.py), so this is for installs people run themselves.
"""
import asyncio
import base64
import binascii
import json
import re
import threading
import time
import urllib.parse
from datetime import timezone
from typing import Any

import httpx
from fastapi import Request

from clients import gateway as G
from clients.gateway import user_agent

from . import gateway_connection as GC
from . import hubdb as H
from . import outbound_tls
from . import postal_mail
from . import tico_notices
from .auth import validate_identity
from .models import Contract, Empty
from .store import Problem

TRANSPORT = None                 # tests' fake HQ, never the live one
EVERY = 15                       # seconds between pulls when this server has a relay address of its own
NOTICES_EVERY = 60               # ... and with only a Gateway key, where the pull brings Tico Team's notices
RELAYS_EVERY = 3600              # HQ's list of relays, for its system relay for `_tico`, is asked at most this often
BACKOFF_MAX = 300                # a failing pull waits 15 s, 30 s, ... up to this
PAGE = 20                        # deliveries per pull: a page is at most PAGE x 256 KB (base64)
PAGES = 10                       # pulls in one tick while HQ says there is more
MAX_ANSWER = 12 * 1024 * 1024
TIMEOUT = 15
PENDING_EVERY = 60               # the status view's count from HQ is asked at most this often
FAILING_FOR = 15 * 60            # Health speaks after pulls have failed this long
OLD_PENDING = 3600               # ... or when the oldest waiting delivery is this old
POISON = 5                       # a delivery whose handling raised this many times is skipped, so one cannot block the rest
RELAY = re.compile(r"hr_[0-9a-f]{16}")


def _prefix(address, relay_id):
    """The address up to its relay id: `https://api.tico.dev/v1/hooks/<id>` or the short `https://hooks.tico.dev/<id>`
    (HQ's HQ_HOOKS_HOST). "" when HQ's answer is not an https address that ends in `<id>` or `<id>/<secret>`."""
    parts = urllib.parse.urlsplit(address)
    segments = parts.path.split("/")
    if parts.scheme != "https" or not parts.netloc or parts.query or parts.fragment or relay_id not in segments[-2:]:
        return ""
    return f"https://{parts.netloc}" + "/".join(segments[:segments.index(relay_id, len(segments) - 2) + 1])
WAKE_URL_MAX = 2000
NO_KEY = "Connect the Tico Gateway in Settings > Gateway first: an address through Tico Gateway needs its key."
now = time.time                  # tests' controlled clock


class RelayWake(Contract):
    wake: bool


class WakeUrl(Contract):
    url: str | None


class Stop(Exception):
    """HQ refused the key: nothing more is asked until the key changes."""


class Failed(Exception):
    """HQ did not answer, or answered something this server cannot use. `code` is stable and holds no HQ text."""

    def __init__(self, code: str, status: int = 0, body: object = None) -> None:
        super().__init__(code)
        self.code, self.status = code, status
        self.body: object = body or {}


# ----------------------------------------------------------------------------- state
def state(c):
    row = c.execute("SELECT * FROM hook_relay_state WHERE singleton=1").fetchone()
    return dict(row) if row else {"cursor": 0, "acked": 0, "last_pull_at": None, "failing_since": None,
                                  "last_error": "", "stopped_for": "", "pending": None, "oldest": None,
                                  "pending_at": None, "wake_url": None, "wake_url_set_at": None, "wake_url_set_by": None}


def save(c, **fields):
    c.execute("INSERT OR IGNORE INTO hook_relay_state(singleton) VALUES(1)")
    c.execute("UPDATE hook_relay_state SET " + ",".join(k + "=?" for k in fields) + " WHERE singleton=1",
              tuple(fields.values()))


def live(c):
    """The relay addresses people made, not the one HQ keeps for Tico Team's notices."""
    return [dict(r) for r in c.execute("SELECT * FROM trigger_relays WHERE deleted_at IS NULL AND trigger_id<>? ORDER BY created_at",
                                       (tico_notices.ID,)).fetchall()]


def _row(c, trigger_id):
    return c.execute("SELECT * FROM trigger_relays WHERE trigger_id=? AND deleted_at IS NULL", (trigger_id,)).fetchone()


def public(row):
    """The relay as a trigger shows it. `wake` is whether a delivery starts a sleeping server (HQ's switch, as last
    listed), null until HQ has said."""
    return {"address_prefix": row["address_prefix"], "created_at": row["created_at"],
            "wake": None if row["wake"] is None else bool(row["wake"])} if row else None


def attach(c, items):
    """Each listed trigger gains `relay`: `{address_prefix, created, wake}` or null (backend/triggers.py `listing`)."""
    relays = {r["trigger_id"]: r for r in live(c)}
    for item in items:
        item["relay"] = public(relays.get(item["id"]))


def forget(c, trigger_id):
    """A deleted trigger loses its relay. Marked here, in the trigger's own transaction; HQ is told by the loop, so no
    network call waits inside a write."""
    c.execute("UPDATE trigger_relays SET deleted_at=? WHERE trigger_id=? AND deleted_at IS NULL", (H.now(), trigger_id))


def clean_wake_url(value):
    """The address HQ will be asked to knock on: https, a host, no user or password, one line. None turns it off."""
    if value is None:
        return None
    try:
        parts = urllib.parse.urlsplit(value)
        parts.port
    except ValueError:
        parts = None
    if (not parts or not value or len(value) > WAKE_URL_MAX or parts.scheme != "https" or not parts.hostname
            or "@" in parts.netloc or any(ch.isspace() or ord(ch) < 32 or ch == "\\" for ch in value)):
        raise Problem("url", "The wake address starts with https://, has a host, and no spaces, user or password", 422)
    return value


# ----------------------------------------------------------------------------- talking to HQ
class Link:
    """One Gateway key and base URL, read once from the connection (backend/gateway_connection.py)."""

    def __init__(self, key: str, base: str, fingerprint: str) -> None:
        self.key, self.base, self.fingerprint = key, base, fingerprint

    def call(self, method: str, path: str, body: object = None, idempotency: str | None = None, *,
             area: str = "hooks", transport: object = None, cap: int = MAX_ANSWER) -> dict[str, Any]:
        """One call to `<base>/<area>/<path>`. `area` and `cap` let the sleep client (backend/sleep_idle.py) share the
        key, the pinned client settings and the answer cap; `transport` is a test's fake HQ."""
        headers = {"Authorization": "Bearer " + self.key, "User-Agent": user_agent(), "Accept-Encoding": "identity"}
        if idempotency:
            headers["Idempotency-Key"] = idempotency[:200]
        try:
            with httpx.Client(transport=transport or TRANSPORT, timeout=TIMEOUT, trust_env=False, follow_redirects=False,
                              verify=outbound_tls.context(trust_env=False)) as client:
                with client.stream(method, f"{self.base}/{area}/{path}", json=body, headers=headers) as response:
                    raw = bytearray()
                    for chunk in response.iter_bytes():
                        raw.extend(chunk)
                        if len(raw) > cap:
                            raise Failed("oversized")
                    status = response.status_code
        except httpx.HTTPError:
            raise Failed("unreachable") from None
        try:
            answer = json.loads(bytes(raw) or b"{}")
        except ValueError:
            answer = {}
        answer = answer if isinstance(answer, dict) else {}
        if status == 401:
            raise Stop()
        if status >= 400:
            error = answer.get("error", answer.get("code"))      # {"error": "x"}, {"error": {"code": "x"}} or {"code": "x"}
            error = error.get("code") if isinstance(error, dict) else error
            raise Failed(error if isinstance(error, str) and re.fullmatch(r"[a-z_]{1,40}", error) else f"http_{status}",
                         status, answer)
        return answer


def link_of(c: Any, vault: Any, settings: Any) -> Link | None:
    """The Link for the connected key, or None. A base the key may not be sent to is no link."""
    key, base, _ = GC.target(c, vault, settings)
    if not key:
        return None
    try:
        base = G.safe_base(base)
    except ValueError:
        return None
    return Link(key, base, GC.key_id(c, settings))


class HookRelay:
    def __init__(self, store, settings, app, auth=None):
        self.store, self.settings, self.app, self.auth = store, settings, app, auth
        self.lock = threading.Lock()
        self.failures = 0
        self.next_try = 0.0
        self.poison = {}
        self.interval = EVERY
        self.listed = 0.0            # when HQ's relays were last listed

    @property
    def off(self):
        return bool(self.settings.demo or self.settings.rehearsal)

    def vault(self):
        return getattr(self.app.state, "vault", None)

    def link(self, c):
        vault = self.vault()
        return link_of(c, vault, self.settings) if vault else None

    # ------------------------------------------------------------------------- the pull loop
    def tick(self):
        if self.off or not self.lock.acquire(blocking=False):
            return
        try:
            self._tick()
        finally:
            self.lock.release()

    def _tick(self):
        with self.store.read() as c:
            link = self.link(c)
            current, relays = state(c), live(c)
            deleting = c.execute("SELECT count(*) FROM trigger_relays WHERE deleted_at IS NOT NULL AND hq_gone=0").fetchone()[0]
            owner = self.owner(c)
            complete = tico_notices.complete(c, owner) if link else True
        self.interval = EVERY if relays or deleting else NOTICES_EVERY
        if not link:
            return
        if not complete:                            # the built-in trigger and its rules, from the first pull on
            with self.store.transaction() as c:
                tico_notices.ensure(c, owner)
        if current["stopped_for"] and current["stopped_for"] == link.fingerprint:
            return                                  # HQ refused this key; a new key is a new chance
        if current["stopped_for"]:
            with self.store.transaction() as c:
                save(c, stopped_for="", last_error="", failing_since=None)
        if time.monotonic() < self.next_try:
            return
        try:
            if deleting:
                self.sweep_deleted(link)
            self.pull(link, current)
            if relays:
                self.count(link)
            self.learn_notice_relay(link)           # also finds the system relay before any notice has come
            self.worked()
        except Stop:
            with self.store.transaction() as c:
                save(c, stopped_for=link.fingerprint, last_error="key_revoked",
                     failing_since=current["failing_since"] or now())
        except Failed as failed:
            self.failed(failed.code)

    def worked(self):
        self.failures, self.next_try = 0, 0.0
        with self.store.transaction() as c:
            save(c, last_pull_at=now(), failing_since=None, last_error="")

    def failed(self, code):
        self.failures += 1
        self.next_try = time.monotonic() + min(BACKOFF_MAX, EVERY * 2 ** (self.failures - 1))
        with self.store.transaction() as c:
            before = state(c)
            save(c, last_error=code, failing_since=before["failing_since"] or now())

    def pull(self, link, current):
        cursor = current["cursor"]
        forced = False                              # HQ's relay list is forced at most once per pull
        for _ in range(PAGES):
            try:
                page = link.call("POST", "pull", {"cursor": cursor, "limit": PAGE})
            except Failed as failed:
                if failed.code != "cursor_ahead":
                    raise
                cursor = self.reset_cursor(failed.body)
                continue
            items = page.get("items")
            through = page.get("cursor")
            if not isinstance(items, list) or type(through) is not int or through < cursor:
                raise Failed("bad_answer")
            if not forced and self.unknown_system_relay(items):
                forced = True
                self.learn_notice_relay(link, force=True)
            for item in items:
                cursor = self.take(item, cursor)
            cursor = max(cursor, through)
            with self.store.transaction() as c:
                save(c, cursor=cursor)
            self.ack(link, cursor)
            if not page.get("more"):
                return

    def reset_cursor(self, body):
        """HQ served less than this server holds (a restored backup, a reset queue): start from where HQ says, else
        from the start, where everything already handled is a duplicate and acked again."""
        server = body.get("cursor")
        cursor = server if type(server) is int and server >= 0 else 0
        with self.store.transaction() as c:
            save(c, cursor=cursor, acked=min(cursor, state(c)["acked"]))
        return cursor

    def ack(self, link, cursor):
        with self.store.read() as c:
            done = state(c)["acked"]
        if cursor <= done:
            return                                  # a lost ack is tried again here next time: acked stays behind
        try:
            link.call("POST", "ack", {"through_hq_seq": cursor})
        except Failed as failed:
            if failed.code != "cursor_ahead":
                raise
            self.reset_cursor(failed.body)
            return
        with self.store.transaction() as c:
            save(c, acked=cursor)

    def take(self, item, cursor):
        """One delivery in its own short write transaction; the cursor moves with it. Returns the new cursor."""
        seq = item.get("hq_seq") if isinstance(item, dict) else None
        if type(seq) is not int or seq <= cursor:
            raise Failed("bad_answer")
        try:
            saved = self.deliver(item, seq)
        except Exception as error:
            self.poison[seq] = self.poison.get(seq, 0) + 1
            if self.poison[seq] < POISON:
                raise Failed("receive_failed") from error
            self.poison.pop(seq, None)              # skipped for good, and said so
            with self.store.transaction() as c:
                save(c, cursor=seq)
                H.event(c, "keeper", "trigger.relay_skipped", str(item.get("trigger_id") or ""),
                        {"delivery": str(item.get("delivery_id") or "")[:200], "error": type(error).__name__})
            return seq
        if not saved:                               # an undecodable body: terminal, and the cursor still moves
            with self.store.transaction() as c:
                save(c, cursor=seq)
        return seq

    def deliver(self, item, seq):
        """Hand one delivery to the trigger. The cursor moves in the same transaction as `receive`'s writes, so a crash
        never leaves one without the other. False when the body was not decodable and nothing was written."""
        from . import triggers
        try:
            body = base64.b64decode(str(item.get("body_b64") or ""), validate=True)
        except (binascii.Error, ValueError):
            return False                            # not a body anyone can use: terminal
        headers = item.get("headers") if isinstance(item.get("headers"), dict) else {}
        headers = {str(k): str(v) for k, v in headers.items()}
        trigger_id, relay_id = str(item.get("trigger_id") or ""), str(item.get("relay_id") or "")
        kick = False
        if trigger_id == postal_mail.TRIGGER:
            # A letter (backend/postal_mail.py) rides the system relay too and is only kept here: the download and the
            # model calls run in the mail drain, never inside this transaction.
            with self.store.transaction() as c:
                if tico_notices.accepts(c, relay_id):
                    kick = postal_mail.arrived(c, body, str(item.get("delivery_id") or ""))
                save(c, cursor=seq)
            mail = getattr(self.app.state, "postal_mail", None)
            if kick and mail:
                mail.kick()
            return True
        with self.store.transaction() as c:
            # Only a trigger that has this live relay here is fed: HQ's word alone never reaches another trigger. The
            # one built-in exception is `_tico`, fed only from the system relay HQ's list named (`learn_notice_relay`).
            if trigger_id == tico_notices.ID:
                ok = tico_notices.accepts(c, relay_id)
                if ok:
                    tico_notices.ensure(c, self.owner(c))
            else:
                ok = c.execute("SELECT 1 FROM trigger_relays WHERE relay_id=? AND trigger_id=? AND deleted_at IS NULL",
                               (relay_id, trigger_id)).fetchone()
            if not ok:
                save(c, cursor=seq)
                return True
            # A signed delivery (github, standard, stripe, slack, hmac) is checked here, so it needs the vault; rules and
            # routines need `auth` to run as the people who made them.
            triggers.receive(c, trigger_id, body, headers, secret=None, via="gateway", delivery_id=str(item.get("delivery_id") or ""),
                    received_at=received(item.get("received_at")), vault=self.vault(), auth=self.auth)
            save(c, cursor=seq)
        return True

    def owner(self, c):
        """The owner's actor, or None before the owner is on the roster (the built-in rules wait for them)."""
        try:
            pid = self.auth.owner_id(c) if self.auth else ""
        except Exception:
            return None
        return H.human_actor(pid) if pid else None

    def unknown_system_relay(self, items):
        """Whether a delivery in this page names `_tico` or `_mail` through a relay not recorded here."""
        wanted = {str(i.get("relay_id") or "") for i in items
                  if isinstance(i, dict) and i.get("trigger_id") in (tico_notices.ID, postal_mail.TRIGGER)}
        if not wanted:
            return False
        with self.store.read() as c:
            return wanted != {tico_notices.relay(c)}

    def learn_notice_relay(self, link, force=False):
        """The relay HQ lists with `system: true` for `_tico` is the one that may feed it. Asked at most hourly, or at
        once (`force`) when a delivery names a relay not known yet; a forced list that fails fails the pull, so the
        delivery is tried again rather than dropped."""
        if not force and now() - self.listed < RELAYS_EVERY:
            return
        try:
            answer = link.call("GET", "relays")
        except Failed:
            if force:
                raise
            self.listed = now()                     # asked again in an hour: the pull has its own backoff
            return
        self.listed = now()
        items = answer.get("items")
        found = [i.get("relay_id") for i in items if isinstance(i, dict) and i.get("system") is True
                 and i.get("trigger_id") == tico_notices.ID] if isinstance(items, list) else []
        with self.store.transaction() as c:
            for item in items if isinstance(items, list) else []:
                if isinstance(item, dict) and isinstance(item.get("wake"), bool):
                    c.execute("UPDATE trigger_relays SET wake=? WHERE relay_id=?", (int(item["wake"]), str(item.get("relay_id"))))
            if len(found) == 1 and tico_notices.record_relay(c, found[0]):
                tico_notices.ensure(c, self.owner(c))

    def sweep_deleted(self, link):
        """Tell HQ about relays deleted here, a few at a time; HQ having no such relay is as good as deleted."""
        with self.store.read() as c:
            gone = [r["relay_id"] for r in c.execute(
                "SELECT relay_id FROM trigger_relays WHERE deleted_at IS NOT NULL AND hq_gone=0 LIMIT 10").fetchall()]
        for relay_id in gone:
            try:
                link.call("POST", f"relays/{relay_id}/delete", {}, idempotency="forget:" + relay_id)
            except Failed as failed:
                if failed.status != 404:
                    raise
            with self.store.transaction() as c:
                c.execute("UPDATE trigger_relays SET hq_gone=1 WHERE relay_id=?", (relay_id,))

    def count(self, link):
        with self.store.read() as c:
            at = state(c)["pending_at"]
        if at and now() - at < PENDING_EVERY:
            return
        answer = link.call("GET", "pending")
        pending, oldest = answer.get("pending"), answer.get("oldest_received_at")
        with self.store.transaction() as c:
            save(c, pending=pending if type(pending) is int else None,
                 oldest=oldest if isinstance(oldest, str) else None, pending_at=now())

    async def loop(self, stop):
        while not stop.is_set():
            try:
                await asyncio.to_thread(self.tick)
            except Exception:
                pass                                # the next tick tries again; the error is in the state row
            try:
                await asyncio.wait_for(stop.wait(), self.interval)
            except TimeoutError:
                pass

    # ------------------------------------------------------------------------- relays, for a person
    def gate(self, request, trigger_id, *, create=False):
        """The caller may manage this trigger and the Gateway is connected; returns (link, trigger row)."""
        from . import task_privacy, triggers
        who = request.state.identity
        with self.store.read_transaction() as c:
            validate_identity(c, who)
            task_privacy.guard_write(c, who, request.url.path, self.auth)
            row = triggers._row(c, trigger_id)
            triggers.may_manage(c, who, row)
            if triggers.builtin(trigger_id):
                raise Problem("relay", f"{tico_notices.NAME} notices come through Tico Gateway already; there is no address to make", 422)
            relay = _row(c, trigger_id)
            if create and relay:
                raise Problem("relay", "This trigger already has an address through Tico Gateway", 409)
            if not create and not relay:
                raise Problem("not_found", "This trigger has no address through Tico Gateway", 404)
            link = self.link(c)
        if not link:
            raise Problem("gateway", NO_KEY, 409)
        return who, link, row, relay

    def make(self, request, trigger_id, key):
        if not key:
            raise Problem("idempotency_key", "Provide an Idempotency-Key", 422)
        who, link, trigger, _ = self.gate(request, trigger_id, create=True)
        answer = self.ask(link, "POST", "relays", {"trigger_id": trigger_id, "name": trigger["name"]},
                          "relay-create:" + trigger_id + ":" + key)
        relay_id, secret = str(answer.get("relay_id") or ""), answer.get("secret")
        if not RELAY.fullmatch(relay_id):
            raise Problem("gateway", "The Tico Gateway gave an answer this server cannot use. Try again.", 502)
        prefix = _prefix(str(answer.get("address") or ""), relay_id)
        if not prefix:
            raise Problem("gateway", "The Tico Gateway gave an answer this server cannot use. Try again.", 502)
        extra = None
        with self.store.transaction() as c:
            existing = _row(c, trigger_id)
            if existing and existing["relay_id"] != relay_id:
                extra = relay_id                    # a second request made a different one first: this one goes back
            elif existing:
                pass                                # the same HQ relay (a concurrent retry with the same key): keep it
            else:
                c.execute("INSERT INTO trigger_relays(relay_id,trigger_id,address_prefix,created_by,created_at) VALUES(?,?,?,?,?) "
                          "ON CONFLICT(relay_id) DO UPDATE SET trigger_id=excluded.trigger_id,address_prefix=excluded.address_prefix,"
                          "deleted_at=NULL,hq_gone=0", (relay_id, trigger_id, prefix, who.actor, H.now()))
                H.event(c, who.actor, "trigger.relay_created", trigger_id, {"relay": relay_id})
                tico_notices.ensure(c, self.owner(c))      # HQ made this install's Tico Team relay with its first
        if extra:
            self.ask(link, "POST", f"relays/{extra}/delete", {}, "forget:" + extra, quiet=True)
            raise Problem("relay", "This trigger already has an address through Tico Gateway", 409)
        replayed = not secret
        self.listed = 0.0                           # the next tick lists HQ's relays, which say whether this one wakes
        return {"relay": {"trigger": trigger_id, "relay_id": relay_id, "address_prefix": prefix,
                          "address": prefix if replayed else f"{prefix}/{secret}", "replayed": replayed}}

    def rotate(self, request, trigger_id, key):
        if not key:
            raise Problem("idempotency_key", "Provide an Idempotency-Key", 422)
        who, link, _, relay = self.gate(request, trigger_id)
        answer = self.ask(link, "POST", f"relays/{relay['relay_id']}/rotate", {}, "relay-rotate:" + relay["relay_id"] + ":" + key)
        secret = answer.get("secret")
        with self.store.transaction() as c:
            H.event(c, who.actor, "trigger.relay_rotated", trigger_id, {"relay": relay["relay_id"]})
        return {"relay": {"trigger": trigger_id, "relay_id": relay["relay_id"], "address_prefix": relay["address_prefix"],
                          "address": relay["address_prefix"] if not secret else f"{relay['address_prefix']}/{secret}",
                          "replayed": not secret}}

    def wake(self, request, trigger_id, on, key):
        """Whether a delivery to this address starts a sleeping server (backend/sleep_idle.py); HQ keeps the switch."""
        who, link, _, relay = self.gate(request, trigger_id)
        answer = self.ask(link, "POST", f"relays/{relay['relay_id']}", {"wake": on},
                          f"relay-wake:{relay['relay_id']}:{key or H.new_id()}")
        on = answer["wake"] if isinstance(answer.get("wake"), bool) else on
        with self.store.transaction() as c:
            c.execute("UPDATE trigger_relays SET wake=? WHERE relay_id=?", (int(on), relay["relay_id"]))
            H.event(c, who.actor, "trigger.relay_wake", trigger_id, {"relay": relay["relay_id"], "wake": on})
        return {"relay": {"trigger": trigger_id, "relay_id": relay["relay_id"], "wake": on}}

    def remove(self, request, trigger_id):
        who, link, _, relay = self.gate(request, trigger_id)
        with self.store.transaction() as c:
            forget(c, trigger_id)
            H.event(c, who.actor, "trigger.relay_deleted", trigger_id, {"relay": relay["relay_id"]})
        told = True
        try:
            self.ask(link, "POST", f"relays/{relay['relay_id']}/delete", {}, "forget:" + relay["relay_id"], quiet=True)
            with self.store.transaction() as c:
                c.execute("UPDATE trigger_relays SET hq_gone=1 WHERE relay_id=?", (relay["relay_id"],))
        except Problem:
            told = False                            # the loop tells HQ when it can; the address here is already off
        return {"relay": {"trigger": trigger_id, "deleted": True, "gateway_told": told}}

    # ------------------------------------------------------------------------- the wake address, for the owner
    def wake_gate(self, request, *, write=False):
        """Only the owner reads or changes the wake address: it is a way to start this server."""
        from . import task_privacy
        who = request.state.identity
        with self.store.read_transaction() as c:
            validate_identity(c, who)
            if write:
                task_privacy.guard_write(c, who, request.url.path, self.auth)
        if who.role != "owner":
            raise Problem("forbidden", "Only the owner may see or change the address that wakes this server", 403)
        return who

    def wake_url(self, request):
        self.wake_gate(request)
        with self.store.read() as c:
            current = state(c)
        return {"url": current["wake_url"], "set_at": current["wake_url_set_at"], "set_by": current["wake_url_set_by"]}

    def set_wake_url(self, request, url, key):
        """Tell HQ where to knock (null: nowhere). The answer carries HQ's signing secret once; it is passed on and never kept."""
        who = self.wake_gate(request, write=True)
        url = clean_wake_url(url)
        if self.off:
            raise Problem("rehearsal", "A rehearsal copy never changes the address that wakes a server", 409)
        with self.store.read() as c:
            link = self.link(c)
        if not link:
            raise Problem("gateway", NO_KEY, 409)
        answer = self.ask(link, "POST", "wake", {"url": url, "secret": None}, "wake-url:" + (key or H.new_id()),
                          refused=("invalid_url", "The Tico Gateway cannot use that address. It must be a public https address."))
        if url is not None and isinstance(answer.get("url"), str):
            url = answer["url"]                     # HQ's own spelling of it
        secret = answer.get("secret") if url is not None and isinstance(answer.get("secret"), str) else None
        with self.store.transaction() as c:
            save(c, wake_url=url, wake_url_set_at=H.now() if url else None, wake_url_set_by=who.actor if url else None)
            # The address can carry a token in its path: the audit names the host only.
            H.event(c, who.actor, "hooks.wake_url_set" if url else "hooks.wake_url_cleared", "",
                    {"host": urllib.parse.urlsplit(url).hostname} if url else {})
        return {"url": url, "secret": secret}

    def ask(self, link, method, path, body, idempotency, quiet=False, refused=None):
        try:
            return link.call(method, path, body, idempotency)
        except Stop:
            raise Problem("gateway", "The Tico Gateway does not accept this server's key. Check it in Settings > Gateway.",
                          409) from None
        except Failed as failed:
            if refused and failed.code == refused[0]:
                raise Problem("url", refused[1], 422) from None
            if failed.status == 404 and path.endswith("/delete"):
                return {}
            raise Problem("gateway", "The Tico Gateway did not answer. Try again in a minute."
                          if failed.code == "unreachable" else f"The Tico Gateway refused this ({failed.code}).", 502) from None

    # ------------------------------------------------------------------------- what Settings and Health show
    def status(self, c):
        link = self.link(c) if self.vault() else None
        current, relays = state(c), live(c)
        stopped = bool(link and current["stopped_for"] and current["stopped_for"] == link.fingerprint)
        key = "revoked" if stopped else "connected" if link else "none"
        deleting = c.execute("SELECT count(*) FROM trigger_relays WHERE deleted_at IS NOT NULL AND hq_gone=0").fetchone()[0]
        pulled = current["last_pull_at"]
        return {"key": key, "relays": len(relays), "cursor": current["cursor"],
                "last_pull": _iso(pulled), "last_error": current["last_error"],
                "pending": current["pending"], "oldest": current["oldest"], "deleting": deleting,
                "active": bool(link and not self.off and not stopped and (relays or deleting))}


def received(value):
    """HQ's receipt time in this server's own timestamp format (hubdb.now), which the ledger compares as text. One that
    does not parse, or lies more than 5 minutes ahead, is replaced by now."""
    at = H.parse_ts(value) if isinstance(value, str) else None
    if at is not None and at.tzinfo is None:
        at = at.replace(tzinfo=timezone.utc)
    if at is None or at.timestamp() > now() + 300:
        return H.now()
    return at.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"


def _iso(seconds):
    if not seconds:
        return None
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(seconds))


def health(c, settings=None):
    """Health's row, or None: pulls failing for 15 minutes while a relay exists, or a delivery waiting over an hour."""
    if not live(c):
        return None
    current, at = state(c), now()
    if current["failing_since"] and at - current["failing_since"] >= FAILING_FOR:
        words = ("Tico Gateway does not accept this server's key" if current["last_error"] == "key_revoked"
                 else "this server cannot reach Tico Gateway")
        return {"status": "warn", "summary": f"Webhooks sent to an address through Tico Gateway are waiting: {words}. "
                                             "They are delivered once it works again."}
    oldest = current["oldest"]
    if oldest and current["pending"]:
        waited = H.parse_ts(oldest)
        if waited and waited.tzinfo is None:
            waited = waited.replace(tzinfo=timezone.utc)
        if waited and at - waited.timestamp() >= OLD_PENDING:
            return {"status": "warn", "summary": f"{current['pending']} webhook(s) have waited over an hour at Tico Gateway "
                                                 "and are not coming through."}
    return None


def install(app, settings, store, auth):
    service = HookRelay(store, settings, app, auth)
    app.state.hook_relay = service
    tico_notices.install(app, store)

    def readers(request):
        if request.state.identity.role not in ("human", "owner", "bot"):
            raise Problem("forbidden", "Only people and bots read triggers", 403)

    @app.get("/api/v2/relay")
    def relay_status(request: Request):
        """Webhooks through Tico Gateway: the key, how many addresses, the last pull and what waits at the Gateway."""
        readers(request)
        with store.read() as c:
            return {"relay": service.status(c)}

    @app.get("/api/v2/hooks/wake-url")
    def wake_url_read(request: Request):
        """The address Tico Gateway knocks on to start this server when a webhook waits for it, and who set it and when. Owner only."""
        return service.wake_url(request)

    @app.put("/api/v2/hooks/wake-url")
    def wake_url_set(request: Request, body: WakeUrl):
        """Set the wake address (`url`, https) or turn it off (`url: null`). The answer carries the Standard Webhooks signing
        `secret` once, for the receiver; this server does not keep it. Owner only; needs the Gateway key."""
        return service.set_wake_url(request, body.url, request.headers.get("idempotency-key"))

    @app.post("/api/v2/triggers/{trigger_id}/relay")
    def relay_create(request: Request, trigger_id: str, body: Empty):
        """An address through Tico Gateway for this trigger. The answer's `address` carries the secret and is shown only now."""
        return service.make(request, trigger_id, request.headers.get("idempotency-key"))

    @app.post("/api/v2/triggers/{trigger_id}/relay/rotate")
    def relay_rotate(request: Request, trigger_id: str, body: Empty):
        """A new secret for the Gateway address; the old one stops working at once."""
        return service.rotate(request, trigger_id, request.headers.get("idempotency-key"))

    @app.post("/api/v2/triggers/{trigger_id}/relay/wake")
    def relay_wake(request: Request, trigger_id: str, body: RelayWake):
        """Whether a delivery to the Gateway address starts a sleeping server. HQ keeps the switch."""
        return service.wake(request, trigger_id, body.wake, request.headers.get("idempotency-key"))

    @app.post("/api/v2/triggers/{trigger_id}/relay/delete")
    def relay_delete(request: Request, trigger_id: str, body: Empty):
        """Remove the Gateway address. The trigger's own address is untouched."""
        return service.remove(request, trigger_id)

"""Tico's side of the relay between installs: keys, peerings and envelopes through Tico HQ (ticoteam/tico-services,
federation/). It asks HQ with the install's Gateway key and sends HQ only signed bytes it cannot read as more than
bytes. HTTP never runs inside a database transaction: each step reads, asks, then writes.

Each tick: register the key if HQ does not have it, read the peerings from HQ, send what is waiting, then pull and
apply what was sent to this install, and ack it. A failing tick backs off; the state row says why.
"""
import asyncio
import base64
import calendar
import threading
import time

from . import federation as F
from . import federation_envelope as E
from . import federation_sync as S
from . import hook_relay as HR

AREA = "federation"          # HQ's routes: <root>/federation/... where the root is the Gateway's base without `/gateway`
EVERY = 15                   # seconds between ticks
SYNC_EVERY = 60              # seconds between reads of the peerings from HQ
PULL_LIMIT = 50              # envelopes asked for per pull
PAGES = 10                   # pulls in one tick while HQ says there is more
BACKOFF_MAX = 300
now = time.time              # tests' controlled clock


def root(base):
    base = base.rstrip("/")
    return base[: -len("/gateway")] if base.endswith("/gateway") else base


def stamp():
    return time.strftime("%Y-%m-%dT%H:%M:%S.%f", time.gmtime(now())) + "Z"


def epoch(value):
    """HQ's `received_at` ("2026-11-01T10:00:00Z") as seconds; the clock now when it is not in that form."""
    try:
        return float(calendar.timegm(time.strptime(value, "%Y-%m-%dT%H:%M:%SZ")))
    except (TypeError, ValueError):
        return now()


def status_of(view, pinned):
    """A peering's state here: active only when HQ has both approvals and the peer's key is the one pinned at approval."""
    if view.get("status") == "revoked":
        return "revoked"
    if view.get("status") == "active" and pinned and pinned == view.get("peer_key_id"):
        return "active"
    return "pending"


def record(c, view, pin=None):
    """Keeps one local row per HQ peering in step with HQ's view of it. `pin` = (key id, public key), set at approval."""
    hq_id, peer = view.get("id"), view.get("peer")
    if not (isinstance(hq_id, str) and isinstance(peer, str) and E.INSTALL.fullmatch(peer)):
        return None
    current = stamp()
    row = c.execute("SELECT * FROM federation_peerings WHERE hq_peering_id=?", (hq_id,)).fetchone()
    if row is None:
        live = F.peering(c, peer)
        if live is not None:                              # a newer peering with this install replaces the old one here
            c.execute("UPDATE federation_peerings SET status='revoked', revoked_at=?, updated_at=? WHERE id=?",
                      (current, current, live["id"]))
        c.execute("INSERT INTO federation_peerings(id,hq_peering_id,peer_install,direction,status,approved_at,peer_key_id,"
                  "peer_public_key,paused_at,paused_by,revoked_at,created_at,updated_at) VALUES(?,?,?,?,'pending',NULL,NULL,"
                  "NULL,NULL,NULL,NULL,?,?)",
                  (F.new_id("fp_"), hq_id, peer, "outgoing" if view.get("requested_by_you") else "incoming", current, current))
        row = c.execute("SELECT * FROM federation_peerings WHERE hq_peering_id=?", (hq_id,)).fetchone()
    row = dict(row)
    key_id, public = pin if pin else (row["peer_key_id"], row["peer_public_key"])
    status = status_of(view, key_id)
    you, theirs = bool(view.get("you_paused")), bool(view.get("peer_paused"))
    paused_at = (row["paused_at"] or current) if (you or theirs) else None
    paused_by = ("you" if you else "peer") if (you or theirs) else None
    approved_at = (row["approved_at"] or current) if view.get("you_approved") else None
    revoked_at = (row["revoked_at"] or current) if status == "revoked" else None
    c.execute("UPDATE federation_peerings SET status=?, approved_at=?, peer_key_id=?, peer_public_key=?, paused_at=?, "
              "paused_by=?, revoked_at=?, updated_at=? WHERE id=?",
              (status, approved_at, key_id, public, paused_at, paused_by, revoked_at, current, row["id"]))
    return F.peering_by_id(c, row["id"])


class Relay:
    def __init__(self, store, settings, app):
        self.store, self.settings, self.app = store, settings, app
        self.lock = threading.Lock()
        self.failures = 0
        self.next_try = 0.0
        self.synced = 0.0
        self.interval = EVERY

    @property
    def off(self):
        return bool(self.settings.demo or self.settings.rehearsal)

    def vault(self):
        return getattr(self.app.state, "vault", None)

    def link(self, c):
        """The HQ link at the API root, or None without a Gateway key."""
        vault = self.vault()
        link = HR.link_of(c, vault, self.settings) if vault else None
        return HR.Link(link.key, root(link.base), link.fingerprint) if link else None

    def call(self, link, method, path, body=None, *, idempotency=None, content=None, headers=None):
        return link.call(method, path, body, idempotency, area=AREA, content=content, headers=headers)

    # ------------------------------------------------------------------------- HQ's peering and key calls
    def register(self, link, key):
        answer = self.call(link, "POST", "keys", {"public_key": key["public_key"]})
        with self.store.transaction() as c:
            c.execute("UPDATE federation_keys SET registered_at=? WHERE key_id=?", (stamp(), answer.get("key_id") or key["key_id"]))
        return answer

    def request(self, link, peer, idempotency):
        return self.call(link, "POST", "peerings", {"peer": peer}, idempotency=idempotency)

    def approve(self, link, hq_id, peer_key_id):
        return self.call(link, "POST", f"peerings/{hq_id}/approve", {"peer_key_id": peer_key_id})

    def pause(self, link, hq_id, paused):
        return self.call(link, "POST", f"peerings/{hq_id}/pause", {"paused": bool(paused)})

    def revoke(self, link, hq_id):
        return self.call(link, "POST", f"peerings/{hq_id}/revoke", {})

    def directory(self, link, install):
        """HQ's current public key for an install: {key_id, public_key}, checked against its own id."""
        answer = self.call(link, "GET", f"keys/{install}")
        key_id, public = answer.get("key_id"), answer.get("public_key")
        try:
            raw = base64.b64decode(public, validate=True)
        except (TypeError, ValueError):
            raise HR.Failed("bad_answer") from None
        if not (isinstance(key_id, str) and E.KEY_ID.fullmatch(key_id) and len(raw) == E.PUBLIC_KEY_BYTES
                and E.key_id_of(raw) == key_id):
            raise HR.Failed("bad_answer")
        return {"key_id": key_id, "public_key": public}

    def sync(self, link):
        """HQ's list of this install's peerings, applied here."""
        answer = self.call(link, "GET", "peerings")
        items = answer.get("items") if isinstance(answer.get("items"), list) else []
        with self.store.transaction() as c:
            for view in items:
                if isinstance(view, dict):
                    record(c, view)
            c.execute("UPDATE federation_state SET listed_at=? WHERE singleton=1", (now(),))

    # ------------------------------------------------------------------------- envelopes
    def send(self, link, row, outbound):
        return self.call(link, "POST", "envelopes", content=outbound["body"], headers={
            "x-tico-to": outbound["to"], "x-tico-envelope": outbound["envelope_id"],
            "x-tico-sent-at": str(outbound["sent_at"]), "x-tico-key-id": outbound["key_id"],
            "x-tico-signature": outbound["signature"]})

    def sweep(self, link):
        """Stages what core and the home need sent, then sends each waiting row. A refusal by HQ or the peer that will not
        change is settled as failed; anything else waits and is tried again with a longer wait."""
        with self.store.transaction() as c:
            S.stage(c, self.vault(), now())
        with self.store.read() as c:
            rows = S.due_outbox(c, now())
        for row in rows:
            with self.store.read() as c:
                if F.active(c, row["recipient"]) is None:
                    continue
                outbound = S.envelope(c, self.vault(), row, now())
            if outbound is None:
                with self.store.transaction() as c:
                    S.refused(c, row, "no_longer_due")
                continue
            try:
                self.send(link, row, outbound)
            except HR.Stop:
                raise
            except HR.Failed as failed:
                with self.store.transaction() as c:
                    if failed.status and 400 <= failed.status < 500 and failed.status not in (408, 429):
                        S.refused(c, row, failed.code)
                    else:
                        S.retry(c, row, failed.code, now())
                continue
            with self.store.transaction() as c:
                S.sent(c, row)

    def fetch_authors(self, link, me, sender, body):
        """HQ's keys for the authors of an item from the home, looked up here, outside any transaction."""
        wrapper = E.parse_payload(body)
        if not wrapper or wrapper.get("type") != "item":
            return {}
        author, key_id = wrapper.get("author"), wrapper.get("key_id")
        if not (isinstance(author, str) and isinstance(key_id, str)) or author in (me, sender):
            return {}
        with self.store.read() as c:
            known = c.execute("SELECT 1 FROM federation_directory WHERE install=? AND key_id=?", (author, key_id)).fetchone()
        if known:
            return {}
        try:
            found = self.directory(link, author)
        except HR.Failed:
            return {}                                       # receive refuses an item whose author has no known key
        return {author: found} if found["key_id"] == key_id else {}

    def pull_once(self, link):
        """One page of envelopes from HQ, each applied in its own transaction and then acked. True when HQ has more."""
        with self.store.read() as c:
            cursor = c.execute("SELECT cursor FROM federation_state WHERE singleton=1").fetchone()[0]
            me = F.install(c)
        try:
            answer = self.call(link, "POST", "pull", {"cursor": cursor, "limit": PULL_LIMIT})
        except HR.Failed as failed:
            if failed.code == "cursor_ahead" and isinstance(failed.body.get("cursor"), int):
                with self.store.transaction() as c:
                    c.execute("UPDATE federation_state SET cursor=? WHERE singleton=1", (failed.body["cursor"],))
            raise
        items = answer.get("items") if isinstance(answer.get("items"), list) else []
        through = cursor
        for item in items:
            sender, envelope_id, headers = item.get("from"), item.get("envelope_id"), item.get("headers") or {}
            try:
                body = base64.b64decode(item.get("body_b64") or "", validate=True)
            except ValueError:
                body = None
            if not (isinstance(sender, str) and isinstance(envelope_id, str) and isinstance(headers, dict) and body is not None):
                through = item.get("hq_seq", through)
                continue
            fetched = self.fetch_authors(link, me, sender, body) if me else {}
            with self.store.transaction() as c:
                if F.install(c):
                    S.receive(c, now(), sender, envelope_id, headers, body, epoch(item.get("received_at")), fetched)
            through = item.get("hq_seq", through)
        top = answer.get("cursor") if isinstance(answer.get("cursor"), int) else through
        if top > cursor:
            self.call(link, "POST", "ack", {"through_hq_seq": top})
        with self.store.transaction() as c:
            c.execute("UPDATE federation_state SET cursor=?, acked=?, last_pull_at=? WHERE singleton=1", (top, top, now()))
        return answer.get("more") is True

    # ------------------------------------------------------------------------- the loop
    def tick(self):
        if self.off or not self.lock.acquire(blocking=False):
            return
        try:
            if now() < self.next_try:
                return
            self._tick()
            self.failures, self.next_try = 0, 0.0
            self.note(None)
        except HR.Stop:
            self.trouble("refused")
        except HR.Failed as failed:
            self.trouble(failed.code)
        except Exception:  # noqa: BLE001 - the next tick tries again; the state row keeps the code
            self.trouble("internal")
        finally:
            self.lock.release()

    def _tick(self):
        with self.store.read() as c:
            link = self.link(c)
            key = F.key(c)
        if link is None or key is None:
            return
        if key["registered_at"] is None:
            self.register(link, key)
        if now() - self.synced >= SYNC_EVERY:
            self.sync(link)
            self.synced = now()
        self.sweep(link)
        for _ in range(PAGES):
            if not self.pull_once(link):
                break

    def trouble(self, code):
        self.failures += 1
        self.next_try = now() + min(BACKOFF_MAX, 15 * 2 ** min(self.failures, 6))
        self.note(code)

    def note(self, code):
        with self.store.transaction() as c:
            if code is None:
                c.execute("UPDATE federation_state SET failing_since=NULL, last_error='' WHERE singleton=1")
            else:
                c.execute("UPDATE federation_state SET failing_since=COALESCE(failing_since, ?), last_error=? "
                          "WHERE singleton=1", (now(), code))

    async def loop(self, stop):
        while not stop.is_set():
            try:
                await asyncio.to_thread(self.tick)
            except Exception:  # noqa: BLE001, S110 - the next tick tries again; the error is in the state row
                pass
            try:
                await asyncio.wait_for(stop.wait(), self.interval)
            except TimeoutError:
                pass

    def status(self, c):
        """What the status view shows: the key, the Gateway, and what waits, with no key material."""
        key = F.key(c)
        state = dict(c.execute("SELECT * FROM federation_state WHERE singleton=1").fetchone())
        pending = c.execute("SELECT count(*) FROM federation_outbox").fetchone()[0]
        return {"gateway": self.link(c) is not None, "install": F.install(c) or None,
                "key": {"key_id": key["key_id"], "registered": key["registered_at"] is not None} if key else None,
                "outbox": pending, "last_pull_at": state["last_pull_at"], "failing_since": state["failing_since"],
                "last_error": state["last_error"], "listed_at": state["listed_at"]}

"""Tico's side of the relay between installs: keys, peerings and envelopes through Tico HQ (ticoteam/tico-services,
federation/). It asks HQ with the install's Gateway key and sends HQ only signed bytes it cannot read as more than
bytes. HTTP never runs inside a database transaction: each step reads, asks, then writes.

Each tick: register the key if HQ does not have it, read the peerings from HQ, send what is waiting, then pull and
apply what was sent to this install, and ack it. A failing tick backs off; the state row says why.
"""
import asyncio
import base64
import calendar
import json
import logging
import threading
import time
from urllib.parse import quote

from . import federation as F
from . import federation_envelope as E
from . import federation_sync as S
from . import federation_threads as T
from . import hook_relay as HR
from . import screening

AREA = "federation"          # HQ's routes: <root>/federation/... where the root is the Gateway's base without `/gateway`
EVERY = 15                   # seconds between ticks
SYNC_EVERY = 60              # seconds between reads of the peerings from HQ
SYNC_PAGES = 50              # pages of the peerings, or of their history, read in one sync; past that it is incomplete
PULL_LIMIT = 50              # envelopes asked for per pull
PAGES = 10                   # pulls in one tick while HQ says there is more
BACKOFF_MAX = 300
SWEEP_ROUNDS = 200             # passes over the lane heads in one tick (each pass moves a row on)
DRAIN_ROUNDS = 200           # passes over the held lane heads in one tick
now = time.time              # tests' controlled clock
log = logging.getLogger(__name__)


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


def refusable(failed):
    """A failure that will not change on a retry: a 4xx other than a timeout, a rate limit, or a pause (a pause lifts, so
    the row waits for it). Anything else waits and is tried again."""
    return bool(failed.status) and 400 <= failed.status < 500 and failed.status not in (408, 429) and failed.code != "paused"


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
    company = str(view.get("peer_company") or row["peer_company"] or "").strip()[:120]
    c.execute("UPDATE federation_peerings SET status=?, approved_at=?, peer_key_id=?, peer_public_key=?, paused_at=?, "
              "paused_by=?, revoked_at=?, peer_company=?, updated_at=? WHERE id=?",
              (status, approved_at, key_id, public, paused_at, paused_by, revoked_at, company, current, row["id"]))
    return F.peering_by_id(c, row["id"])


def apply_revoked(c, view):
    """One view from HQ's history, on the row this install has for it. A peering this install never had a row for has
    nothing to stop, and recording it would end a newer peering with the same install."""
    if not isinstance(view, dict) or view.get("status") != "revoked":
        return
    row = c.execute("SELECT status FROM federation_peerings WHERE hq_peering_id=?", (view.get("id"),)).fetchone()
    if row is not None and row["status"] != "revoked":
        record(c, view)


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

    def company(self):
        """This install's display name, sent to HQ with a peering request or approval."""
        return str(getattr(self.settings, "company_name", "") or "").strip()[:120]

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
        return self.call(link, "POST", "peerings", {"peer": peer, "company": self.company()}, idempotency=idempotency)

    def approve(self, link, hq_id, peer_key_id):
        return self.call(link, "POST", f"peerings/{hq_id}/approve", {"peer_key_id": peer_key_id, "company": self.company()})

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
        """HQ's list of this install's peerings, a page at a time, applied here. An HQ from before paging answers `items`
        alone, which is the whole list. What was read is applied even when the rest was not: a view speaks only for its own
        peering, and a peering missing from the list is never changed, so a partial list takes nothing away. Only a whole
        list sets `listed_at`. A failed page is raised after that; running out of pages is logged and the tick goes on."""
        views, cursor, whole = [], None, False
        try:
            for _ in range(SYNC_PAGES):
                answer = self.call(link, "GET", "peerings" + (f"?cursor={quote(cursor, safe='')}" if cursor else ""))
                if not isinstance(answer.get("items"), list):
                    break
                views.extend(answer["items"])
                following = answer.get("next")
                if following is None:
                    whole = True
                    break
                if not isinstance(following, str) or not following or following == cursor:
                    break
                cursor = following
            if not whole:
                log.warning("federation: HQ's list of peerings was not read to its end (%s read)", len(views))
        finally:
            with self.store.transaction() as c:
                for view in views:
                    if isinstance(view, dict):
                        record(c, view)
                if whole:
                    c.execute("UPDATE federation_states SET listed_at=? WHERE singleton=1", (now(),))
        self.sync_revoked(link)

    def sync_revoked(self, link):
        """Peerings HQ has revoked: they leave its working list for its history, newest first, so a local row learns of a
        revocation by the other company here. The read stops at the first page reaching what an earlier read finished
        (`history_through`), so nothing new costs one page. A read cut short, by a failure or by SYNC_PAGES, resumes from
        `history_cursor`, and `history_top` waits until it ends; a stored cursor HQ refuses (a 422) starts over next time,
        since it would otherwise stop every sync. An HQ without this route (a 404) lists revoked peerings in its working
        list instead."""
        with self.store.read() as c:
            state = dict(c.execute("SELECT history_through,history_cursor,history_top FROM federation_states").fetchone())
        through, cursor, top = state["history_through"], state["history_cursor"], state["history_top"]
        views, done = [], False
        try:
            for _ in range(SYNC_PAGES):
                answer = self.call(link, "GET", "peerings/history" + (f"?cursor={quote(cursor, safe='')}" if cursor else ""))
                page = answer.get("items")
                if not isinstance(page, list):
                    break
                views.extend(page)
                stamps = [v["updated"] for v in page if isinstance(v, dict) and type(v.get("updated")) in (int, float)]
                top = max([*stamps, *([top] if top is not None else [])], default=None)
                following = answer.get("next")
                if following is None or (through is not None and any(stamp < through for stamp in stamps)):
                    done = True
                    break
                if not isinstance(following, str) or not following or following == cursor:
                    break
                cursor = following
            if not done:
                log.warning("federation: HQ's revoked peerings were not read to the last one seen (%s read)", len(views))
        except HR.Failed as failed:
            if failed.status == 422:
                cursor = top = None
            elif failed.status != 404:
                raise
        finally:
            with self.store.transaction() as c:
                for view in views:
                    apply_revoked(c, view)
                if done:
                    through = max((v for v in (through, top) if v is not None), default=None)
                    cursor = top = None
                c.execute("UPDATE federation_states SET history_through=?, history_cursor=?, history_top=? WHERE singleton=1",
                          (through, cursor, top))

    # ------------------------------------------------------------------------- envelopes
    def send(self, link, row, outbound):
        return self.call(link, "POST", "envelopes", content=outbound["body"], headers={
            "x-tico-to": outbound["to"], "x-tico-envelope": outbound["envelope_id"],
            "x-tico-sent-at": str(outbound["sent_at"]), "x-tico-key-id": outbound["key_id"],
            "x-tico-signature": outbound["signature"]})

    def sweep(self, link):
        """Stages what core and the home need sent, then sends the head of each lane (one peer and one root). A later row of
        a lane waits until its head is taken or settled, so a root's stream reaches each peer in order. A paused peering,
        or any failure that may pass, waits and is tried again; other lanes are not held back by it."""
        with self.store.transaction() as c:
            F.set_company(c, self.company())
            S.stage(c, self.vault(), now())
        for _ in range(SWEEP_ROUNDS):
            with self.store.read() as c:
                rows = S.due_outbox(c, now())
            moved = False
            for row in rows:
                moved = self.send_row(link, row) or moved
            if not moved:
                return

    def send_row(self, link, row):
        """Sends one waiting row. True when it was taken, settled, or put back to wait. False when its peering is not
        active, so it is left alone (and its lane with it)."""
        with self.store.transaction() as c:
            if F.active(c, row["recipient"]) is None:
                return False
            outbound = S.envelope(c, self.vault(), row, now())
            if outbound is None:                 # no longer to be sent: a waiting delivery goes moot
                S.dropped(c, row)
                return True
        try:
            self.send(link, row, outbound)
        except HR.Stop:
            raise
        except HR.Failed as failed:
            with self.store.transaction() as c:
                if refusable(failed):
                    S.refused(c, row, failed.code)
                else:
                    S.retry(c, row, failed.code, now())
            return True
        with self.store.transaction() as c:
            S.sent(c, row, outbound)
        return True

    def screen_ahead(self, sender, body, me):
        """The org-rule and injection verdicts for the carried texts an envelope brings, made before its write: the model
        calls run outside any transaction. Keyed by S.screen_key; the write reads them through gate()."""
        with self.store.read() as c:
            texts = S.carried_texts(c, me, sender, body)
        judge = getattr(self.app.state, "judge", None)
        verdicts = {}
        for text in texts:
            org = T.checked_inbound(self.store, judge, text["actor"], text["text"], text["install"], text["origin_id"],
                                    text["thread_id"])
            injection = screening.screen(self.store, "federation", text["text"],
                                         key=f"{text['install']}:{text['origin_id']}:{text['revision']}", primary=judge)
            verdicts[S.screen_key(text["install"], text["actor"], text["origin_id"], text["revision"], text["text"])] = (
                org, injection)
        return verdicts

    def gate(self, verdicts):
        """The checker a write asks for each carried text. A text with no verdict, or whose org-rule check did not run
        (unchecked, for an outside text), defers the envelope: nothing is written and nothing is acked for it."""
        def check(actor, text, install, origin_id, revision, thread_id):
            found = verdicts.get(S.screen_key(install, actor, origin_id, revision, text))
            if found is None:
                raise S.Deferred("unscreened")
            org, injection = found
            if org and org.get("outcome") == "unchecked":
                raise S.Deferred("rules_unchecked")
            return org, injection
        return check

    def fetch_authors(self, link, me, sender, body):
        """HQ's keys for the authors of an item from the home, looked up here, outside any transaction."""
        wrapper = E.parse_payload(body)
        if not wrapper or wrapper.get("type") != "item":
            return {}
        author, key_id = wrapper.get("author"), wrapper.get("key_id")
        if not (isinstance(author, str) and isinstance(key_id, str)) or author in (me, sender):
            return {}
        with self.store.read() as c:
            known = c.execute("SELECT 1 FROM federation_directory_entries WHERE install=? AND key_id=?", (author, key_id)).fetchone()
        if known:
            return {}
        try:
            found = self.directory(link, author)
        except HR.Failed as failed:
            if failed.status and 400 <= failed.status < 500:
                return {}                                   # no such install in HQ: receive refuses the item
            raise                                           # a transient failure: the pull waits and tries again
        return {author: found} if found["key_id"] == key_id else {}

    def pull_once(self, link):
        """One page of envelopes from HQ. Each is applied now, or held here when it cannot be applied yet (a rule check has
        not run, the peering is paused), or when its root already has a held envelope from that peer. A held envelope is
        kept by this install and acked, so the mailbox moves on. True when HQ has more."""
        with self.store.read() as c:
            cursor = c.execute("SELECT cursor FROM federation_states WHERE singleton=1").fetchone()[0]
        try:
            answer = self.call(link, "POST", "pull", {"cursor": cursor, "limit": PULL_LIMIT})
        except HR.Failed as failed:
            if failed.code == "cursor_ahead" and isinstance(failed.body.get("cursor"), int):
                with self.store.transaction() as c:
                    c.execute("UPDATE federation_states SET cursor=? WHERE singleton=1", (failed.body["cursor"],))
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
            received, lane = epoch(item.get("received_at")), S.lane_of(body)
            with self.store.read() as c:
                waiting = S.lane_waiting(c, sender, lane)
            if waiting:
                self.hold(sender, lane, envelope_id, headers, body, received, "behind_earlier")
            else:
                self.receive_or_hold(link, sender, lane, envelope_id, headers, body, received)
            through = item.get("hq_seq", through)
        top = answer.get("cursor") if isinstance(answer.get("cursor"), int) else through
        if top > cursor:
            self.call(link, "POST", "ack", {"through_hq_seq": top})
        with self.store.transaction() as c:
            c.execute("UPDATE federation_states SET cursor=?, acked=?, last_pull_at=? WHERE singleton=1", (top, top, now()))
        return answer.get("more") is True

    def apply(self, link, sender, envelope_id, headers, body, received):
        """One envelope applied in its own transaction. The lookups and model checks it needs run first, outside it. Raises
        S.Deferred for a check that has not run or a peering that is paused, and HR.Failed for a lookup that did not answer."""
        with self.store.read() as c:
            me = F.install(c)
        fetched = self.fetch_authors(link, me, sender, body) if me else {}
        verdicts = self.screen_ahead(sender, body, me) if me else {}
        with self.store.transaction() as c:
            if F.install(c):
                S.receive(c, now(), sender, envelope_id, headers, body, received, fetched, gate=self.gate(verdicts),
                          vault=self.vault())

    def receive_or_hold(self, link, sender, lane, envelope_id, headers, body, received):
        try:
            self.apply(link, sender, envelope_id, headers, body, received)
        except HR.Stop:
            raise
        except (S.Deferred, HR.Failed) as deferred:
            self.hold(sender, lane, envelope_id, headers, body, received, deferred.code)

    def hold(self, sender, lane, envelope_id, headers, body, received, reason):
        with self.store.transaction() as c:
            S.hold(c, now(), sender, lane, envelope_id, headers, body, received, reason)

    def drain(self, link):
        """Held envelopes that are due, tried in each lane's order. A lane stops at its first one that still cannot be
        applied; the other lanes go on, and that one is tried again when it is due."""
        for _ in range(DRAIN_ROUNDS):
            with self.store.read() as c:
                heads = S.held_heads(c, now())
            if not heads:
                return
            for held in heads:
                try:
                    self.apply(link, held["peer_install"], held["envelope_id"], json.loads(held["headers"]),
                               bytes(held["body"]), held["received_at"])
                except HR.Stop:
                    raise
                except (S.Deferred, HR.Failed) as deferred:
                    with self.store.transaction() as c:
                        S.held_again(c, held, deferred.code, now())
                    continue
                with self.store.transaction() as c:
                    S.held_done(c, held["id"])

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
        self.drain(link)
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
                c.execute("UPDATE federation_states SET started_failing_at=NULL, last_error='' WHERE singleton=1")
            else:
                c.execute("UPDATE federation_states SET started_failing_at=COALESCE(started_failing_at, ?), last_error=? "
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
        state = dict(c.execute("SELECT * FROM federation_states WHERE singleton=1").fetchone())
        pending = c.execute("SELECT count(*) FROM federation_outbox_items").fetchone()[0]
        held = c.execute("SELECT count(*) FROM federation_deferrals").fetchone()[0]
        return {"gateway": self.link(c) is not None, "install": F.install(c) or None,
                "key": {"key_id": key["key_id"], "registered": key["registered_at"] is not None} if key else None,
                "outbox": pending, "held": held, "last_pull_at": state["last_pull_at"], "started_failing_at": state["started_failing_at"],
                "last_error": state["last_error"], "listed_at": state["listed_at"]}

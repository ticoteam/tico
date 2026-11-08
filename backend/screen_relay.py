"""Private Rust relay control plane; temporary screen/session authority belongs to the Rust process.

The only durable lifecycle records are existing audit events. SQLite write transactions deduplicate
close reports and reconcile old session opens after a restart. No credentials or tickets are persisted.
"""
import asyncio
import hmac
import json
import logging
import threading
import time
import uuid
from types import SimpleNamespace

import httpx
from fastapi import Request

from .store import H, Problem

PRIVATE_PATH = "/_tico/screen/v1/authorize"
log = logging.getLogger(__name__)


def private_request(request, settings):
    return (bool(settings.screen_relay_key) and request.method == "POST" and request.url.path == PRIVATE_PATH
            and hmac.compare_digest(request.headers.get("authorization", ""), "Bearer " + settings.screen_relay_key))


def install_policy(app, store, auth, execution, settings):
    @app.post(PRIVATE_PATH, include_in_schema=False)
    def authorize(request: Request, body: dict):
        if not private_request(request, settings):
            raise Problem("forbidden", "Private relay key required", 403)
        if body.get("kind") == "exit_link":
            from .browser_exit import authorize_link
            return authorize_link(store, auth, execution, body)
        if body.get("kind") == "browser":
            origin = body.get("origin")
            if origin is not None and (not isinstance(origin, str) or not settings.allows_origin(origin)):
                raise Problem("forbidden", "Cross-site screens are not allowed", 403)
            return {"allowed": True}
        rid, sid, token = body.get("runner_id"), body.get("session_id"), body.get("authorization")
        if (body.get("kind") != "runner" or not all(isinstance(v, str) for v in (rid, sid, token))
                or len(rid) > 128 or len(sid) > 128 or len(token) > 8192 or type(body.get("control")) is not bool):
            raise Problem("forbidden", "Invalid relay authorization", 403)
        who = auth.authenticate({"authorization": token}, f"/api/v2/runners/me/screen/{sid}/ws", "GET")
        from .screen import _allows
        with store.read() as c:
            row = execution.runner(c, who) if who.role == "runner" and who.runner_id == rid else None
            if row is None or not _allows(row["screen"], body["control"]):
                raise Problem("forbidden", "A registered computer with this screen mode is required", 403)
        return {"allowed": True}


# Usage batches collected in one tick at most: 16 x 2,048 rows is twice the relay's whole backlog (relay-rs exit.rs).
EXIT_BATCHES_PER_TICK = 16


class RemoteRegistry:
    def __init__(self, store):
        self.store = store
        self.lock = threading.RLock()
        self.http = httpx.Client(base_url=(store.settings.screen_relay_url or "http://127.0.0.1:1").rstrip("/"), timeout=2, trust_env=False,
                                 headers={"Authorization": "Bearer " + store.settings.screen_relay_key})
        self.lease, self.boot, self.sequence = "", "", 0
        self.generations, self.acks = {}, []
        # Browser exits (backend/browser_exit.py): the policy and bot-selection generations this lease's relay has
        # (pushed again after a new lease, and until a push succeeds), and the usage batch to acknowledge.
        self.exit_pushed, self.bot_pushed, self.exit_acks = {}, {}, []
        self.pruned = 0.0
        self.healthy, self.superseded = False, False

    def _request(self, path, body=None):
        try:
            response = self.http.get(path) if body is None else self.http.post(path, json=body)
            if response.status_code == 429:
                raise Problem("screen_busy", "This computer's screen is open too many times. Close one first.", 429)
            if response.status_code in (403, 409):
                raise Problem("screen_off", "The screen permission changed. Open a fresh screen.", 403)
            response.raise_for_status()
            return response.json()
        except Problem:
            raise
        except (httpx.HTTPError, ValueError):
            self.healthy = False
            raise Problem("screen_relay_unavailable", "The screen relay is unavailable. Try again shortly.", 503) from None

    def _command(self, op, /, **fields):
        if not self.healthy:
            raise Problem("screen_relay_unavailable", "The screen relay is unavailable. Try again shortly.", 503)
        self.sequence += 1
        try:
            answer = self._request("/control/v1/command", {"lease": self.lease, "sequence": self.sequence, "op": op, **fields})
            if answer.get("boot") != self.boot:
                self.healthy = False
                raise Problem("screen_relay_unavailable", "The screen relay restarted. Open a fresh screen.", 503)
            return answer
        except Problem as exc:
            # A stale lease must stop renewal. Admission-policy errors do not retire unrelated sessions.
            if op in ("heartbeat", "mode") or exc.status == 503:
                self.healthy = False
            raise

    def start(self, initial=True):
        with self.lock:
            hello = self._request("/control/v1/hello")
            if not initial and hello["boot"] == self.boot and hello.get("lease") not in (None, self.lease):
                # A replacement Python process owns this boot now. The old process may never reclaim it.
                self.superseded = True
                return
            lease = uuid.uuid4().hex
            answer = self._request("/control/v1/start", {"challenge": hello["challenge"], "lease": lease})
            self.lease, self.boot, self.sequence = lease, answer["boot"], 0
            self.generations.clear()
            self.exit_pushed.clear()    # a new lease clears the relay's exit policies and bot generations too
            self.bot_pushed.clear()
            self.healthy = True
        self._reconcile()

    def admit(self, runner_id, actor, control, width, height, mode):
        with self.lock:
            sid = H.new_id()
            reply = self._command("grant", id=sid, runner_id=runner_id, actor=actor, control=control,
                                  generation=self.generations.get(runner_id, 0), mode=mode)
            return SimpleNamespace(id=sid, runner_id=runner_id, actor=actor, control=control,
                                   relay_lease=self.lease), reply["ticket"]

    def publish(self, session):
        with self.lock:
            self._command("activate", id=session.id)

    def end(self, session, code, reason):
        try:
            with self.lock:
                self._command("end", id=session.id, code=code, reason=reason)
        except Problem:
            # A lost control connection is fenced by lease expiry; no Python socket fallback is started.
            self.healthy = False
        return True

    def set_mode(self, runner_id, mode, reason):
        # Store -> control lock is the same order as admit inside Store.write. Re-read under the write
        # lock: a delayed/replayed request must never republish an older durable permission.
        with self.store.transaction() as c:
            row = c.execute("SELECT screen,revoked_at FROM runners WHERE id=?", (runner_id,)).fetchone()
            mode = row["screen"] if row and not row["revoked_at"] else "off"
            with self.lock:
                generation = self.generations[runner_id] = self.generations.get(runner_id, 0) + 1
                try:
                    return self._command("mode", runner_id=runner_id, generation=generation, mode=mode,
                                         reason=reason)["closed"]
                except Problem:
                    self.healthy = False
                    return 0  # Durable mode/revocation succeeds; the lease supplies the bounded socket cutoff.

    def pending(self, runner_id):
        with self.lock:
            return self._command("pending", runner_id=runner_id)["sessions"]

    # ------------------------------------------------------------------ browser exits (relay-rs/src/exit.rs)
    EXIT_REFUSALS = ("exit_offline", "exit_not_shared", "exit_over_cap", "exit_busy", "exit_stale",
                     "stale exit policy")

    def _exit_command(self, op, /, **fields):
        """An exit operation: its answer, or the relay's refusal code as a string. Anything else is the relay
        unavailable (503)."""
        if not self.healthy:
            raise Problem("browser_exit_unavailable", "The relay is unavailable. Try again shortly.", 503)
        self.sequence += 1
        try:
            response = self.http.post("/control/v1/command",
                                      json={"lease": self.lease, "sequence": self.sequence, "op": op, **fields})
            answer = response.json()
        except (httpx.HTTPError, ValueError):
            self.healthy = False
            raise Problem("browser_exit_unavailable", "The relay is unavailable. Try again shortly.", 503) from None
        if response.status_code != 200:
            code = answer.get("error") if isinstance(answer, dict) else None
            if code in self.EXIT_REFUSALS:
                return code
            if code in ("stale permission lease", "stale control sequence"):
                self.healthy = False
            raise Problem("browser_exit_unavailable", "The relay refused the request. Try again shortly.", 503)
        if answer.get("boot") != self.boot:
            self.healthy = False
            raise Problem("browser_exit_unavailable", "The relay restarted. Try again shortly.", 503)
        return answer

    def exit_epoch(self):
        """The relay lease a grant is read under (see exit_grant)."""
        with self.lock:
            return self.lease

    def push_exit_policy(self, runner_id):
        """Send the exit computer's stored policy and its generation; the relay keeps the newest. A push that fails is
        logged and sent again by the next tick (_exit_reconcile); a grant carries the stored policy meanwhile."""
        from . import browser_exit
        with self.store.read() as c:
            p = browser_exit.policy(c, runner_id)
        with self.lock:
            epoch = self.lease
            try:
                answer = self._exit_command("exit_policy", runner_id=runner_id, policy={
                    "generation": p["generation"], "sharing": p["sharing"], "bots": p["bots"],
                    "cap": p["daily_cap_bytes"]})
            except Problem:
                log.warning("browser exit policy for %s not pushed to the relay; retrying", runner_id)
                return 0
            if answer == "stale exit policy" or isinstance(answer, dict):     # the relay has this one or newer
                if self.lease == epoch:
                    self.exit_pushed[runner_id] = max(self.exit_pushed.get(runner_id, 0), p["generation"])
                return answer.get("closed", 0) if isinstance(answer, dict) else 0
            log.warning("browser exit policy for %s refused by the relay (%s); retrying", runner_id, answer)
            return 0

    def push_bot_generation(self, bot, reason):
        """Tell the relay the bot's selection changed (its exit, or the computer it runs on): grants read before are
        refused and its tunnels opened under them close. Retried by the next tick when it fails."""
        from . import browser_exit
        with self.store.read() as c:
            generation = browser_exit.bot_generation(c, bot)
        with self.lock:
            epoch = self.lease
            try:
                answer = self._exit_command("exit_close", bot=bot, bot_generation=generation, reason=reason)
            except Problem:
                log.warning("browser exit change for %s not pushed to the relay; retrying", bot)
                return 0
            if isinstance(answer, dict) and self.lease == epoch:
                self.bot_pushed[bot] = max(self.bot_pushed.get(bot, 0), generation)
            return answer.get("closed", 0) if isinstance(answer, dict) else 0

    def _exit_reconcile(self, batch=32):
        """Push what the relay is missing: generations committed but not pushed under this lease (a failed push, a
        new lease, a bot moved to another computer)."""
        from . import browser_exit
        with self.store.read() as c:
            exits, bots = browser_exit.generations(c)
        with self.lock:
            exits = [rid for rid, g in exits.items() if g > self.exit_pushed.get(rid, 0)][:batch]
            bots = [bot for bot, g in bots.items() if g > self.bot_pushed.get(bot, 0)][:batch]
        for rid in exits:
            self.push_exit_policy(rid)
        for bot in bots:
            self.push_bot_generation(bot, "browser exit changed")

    def exit_grant(self, grant, epoch):
        """The relay's answer to one tunnel ({"ticket"}), or its refusal code. `epoch` is the lease the grant was read
        under: after a new lease the relay's generations start again, so an older read is read again (exit_stale)."""
        with self.lock:
            if self.lease != epoch:
                return "exit_stale"
            try:
                return self._exit_command("exit_grant", grant=grant)
            except Problem:
                return "unavailable"

    def exit_close(self, bot=None, exit=None, reason=""):
        with self.lock:
            try:
                answer = self._exit_command("exit_close", bot=bot, exit=exit, reason=reason)
                return answer.get("closed", 0) if isinstance(answer, dict) else 0
            except Problem:
                return 0

    @staticmethod
    def _record(c, report):
        sid, rid = report["id"], report["runner_id"]
        # These writes share SQLite's writer lock with open and revocation, including retried reports.
        if c.execute("SELECT 1 FROM events WHERE action='runner.screen_close' AND target=? "
                     "AND json_extract(detail,'$.session_id')=?", (rid, sid)).fetchone():
            return
        opened = c.execute("SELECT actor,detail FROM events WHERE action='runner.screen_open' AND target=? "
                           "AND json_extract(detail,'$.session_id')=?", (rid, sid)).fetchone()
        if not opened:  # A prepared grant whose opening transaction rolled back has nothing to audit.
            return
        detail = json.loads(opened["detail"])
        if (detail.get("relay_lease") != report["lease"] or opened["actor"] != report["actor"]
                or bool(detail.get("control")) != report["control"]):
            raise RuntimeError("Relay report does not match its committed grant")
        H.event(c, opened["actor"], "runner.screen_close", rid,
                {"session_id": sid, "control": report["control"], "seconds": report["seconds"], "code": report["code"]})

    def _reconcile(self):
        reconcile_sessions(self.store, self.lease)

    def tick(self):
        if self.superseded:
            return
        if not self.healthy:
            self.start(initial=False)
            if not self.healthy:
                return
        # Browser-exit usage the relay holds beyond the batch in hand is collected in this tick, up to a limit, so no
        # backlog builds up: a relay that stops on its own loses at most about one tick's bytes.
        from . import browser_exit
        for _ in range(EXIT_BATCHES_PER_TICK):
            with self.lock:
                answer = self._command("heartbeat", ack=self.acks, exit_ack=self.exit_acks)
                reports, usage, boot = answer["reports"], answer.get("exit_usage"), self.boot
                self.acks, self.exit_acks = [], []
            # Never take the store lock while holding the control lock: admission takes them in the other order.
            prune = time.monotonic() - self.pruned > 3600
            with self.store.transaction() as c:
                for report in reports:
                    self._record(c, report)
                recorded = browser_exit.record_batch(c, boot, usage)
                if prune:
                    browser_exit.prune(c)
            if prune:
                self.pruned = time.monotonic()
            with self.lock:
                self.acks.extend(report["id"] for report in reports)
                if recorded:
                    self.exit_acks.append(recorded)
            in_hand = len(usage.get("rows") or []) if isinstance(usage, dict) else 0
            if not recorded or int(answer.get("exit_backlog") or 0) <= in_hand:
                break
        self._exit_reconcile()

    async def run(self, stop):
        try:
            while not stop.is_set():
                try:
                    await asyncio.to_thread(self.tick)
                except Exception:
                    self.healthy = False
                    log.warning("screen relay control unavailable; sockets are fenced by the permission lease")
                try:
                    await asyncio.wait_for(stop.wait(), timeout=2)
                except TimeoutError:
                    pass
        finally:
            try:
                await asyncio.to_thread(self.stop_and_drain)
            except Problem:
                pass
            self.http.close()

    def stop_and_drain(self, deadline=5.0):
        """Stop: the relay closes every socket, which counts the open tunnels' last bytes, and keeps the lease for
        heartbeats until Python has collected the browser-exit usage it holds. Collected here until none is left or
        the deadline passes; what's left stays in the relay for the next lease."""
        from . import browser_exit
        with self.lock:
            if not self.healthy:
                return
            answer = self._command("stop")
        until = time.monotonic() + deadline
        while (answer.get("exit_usage") or answer.get("exit_backlog")) and time.monotonic() < until:
            with self.lock:
                answer = self._command("heartbeat", ack=[], exit_ack=self.exit_acks)
                usage, boot, self.exit_acks = answer.get("exit_usage"), self.boot, []
            if usage:
                with self.store.transaction() as c:
                    recorded = browser_exit.record_batch(c, boot, usage)
                with self.lock:
                    if recorded:
                        self.exit_acks.append(recorded)
        self.healthy = False


def reconcile_sessions(store, lease=""):
    while True:
        with store.transaction() as c:
            rows = c.execute("SELECT o.target,o.actor,o.detail FROM events o WHERE o.action='runner.screen_open' "
                             "AND json_extract(o.detail,'$.relay_lease') IS NOT NULL "
                             "AND json_extract(o.detail,'$.relay_lease')<>? AND NOT EXISTS "
                             "(SELECT 1 FROM events x WHERE x.action='runner.screen_close' AND x.target=o.target "
                             "AND json_extract(x.detail,'$.session_id')=json_extract(o.detail,'$.session_id')) "
                             "LIMIT 256", (lease,)).fetchall()
            for row in rows:
                detail = json.loads(row["detail"])
                RemoteRegistry._record(c, {"id": detail["session_id"], "runner_id": row["target"], "actor": row["actor"],
                                 "control": bool(detail["control"]), "lease": detail["relay_lease"], "seconds": 0, "code": 1012})
        if len(rows) < 256:
            break


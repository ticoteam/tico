"""Private Rust relay control plane; temporary screen/session authority belongs to the Rust process.

The only durable lifecycle records are existing audit events. SQLite write transactions deduplicate
close reports and reconcile old session opens after a restart. No credentials or tickets are persisted.
"""
import asyncio
import hmac
import json
import logging
import threading
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


class RemoteRegistry:
    def __init__(self, store):
        self.store = store
        self.lock = threading.RLock()
        self.http = httpx.Client(base_url=(store.settings.screen_relay_url or "http://127.0.0.1:1").rstrip("/"), timeout=2, trust_env=False,
                                 headers={"Authorization": "Bearer " + store.settings.screen_relay_key})
        self.lease, self.boot, self.sequence = "", "", 0
        self.generations, self.acks = {}, []
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

    def _command(self, op, **fields):
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

    @staticmethod
    def _record(c, report):
        sid, rid = report["id"], report["runner_id"]
        # These writes share SQLite's writer lock with open and revocation, including retried reports.
        if c.execute("SELECT 1 FROM events WHERE action='runner.screen_close' AND target=? "
                     "AND json_extract(detail_json,'$.session_id')=?", (rid, sid)).fetchone():
            return
        opened = c.execute("SELECT actor,detail_json FROM events WHERE action='runner.screen_open' AND target=? "
                           "AND json_extract(detail_json,'$.session_id')=?", (rid, sid)).fetchone()
        if not opened:  # A prepared grant whose opening transaction rolled back has nothing to audit.
            return
        detail = json.loads(opened["detail_json"])
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
        with self.lock:
            reports = self._command("heartbeat", ack=self.acks)["reports"]
            self.acks = []
        # Never take the store lock while holding the control lock: admission takes them in the other order.
        with self.store.transaction() as c:
            for report in reports:
                self._record(c, report)
        with self.lock:
            self.acks.extend(report["id"] for report in reports)

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
            def shutdown():
                with self.lock:
                    if self.healthy:
                        self._command("stop")
            try:
                await asyncio.to_thread(shutdown)
            except Problem:
                pass
            self.http.close()


def reconcile_sessions(store, lease=""):
    while True:
        with store.transaction() as c:
            rows = c.execute("SELECT o.target,o.actor,o.detail_json FROM events o WHERE o.action='runner.screen_open' "
                             "AND json_extract(o.detail_json,'$.relay_lease') IS NOT NULL "
                             "AND json_extract(o.detail_json,'$.relay_lease')<>? AND NOT EXISTS "
                             "(SELECT 1 FROM events x WHERE x.action='runner.screen_close' AND x.target=o.target "
                             "AND json_extract(x.detail_json,'$.session_id')=json_extract(o.detail_json,'$.session_id')) "
                             "LIMIT 256", (lease,)).fetchall()
            for row in rows:
                detail = json.loads(row["detail_json"])
                RemoteRegistry._record(c, {"id": detail["session_id"], "runner_id": row["target"], "actor": row["actor"],
                                 "control": bool(detail["control"]), "lease": detail["relay_lease"], "seconds": 0, "code": 1012})
        if len(rows) < 256:
            break


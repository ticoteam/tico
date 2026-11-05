"""A person's opt-in alerts through HQ paging; no phone or alert body enters a shared event.

Admission reserves each $1 before sending, under both monthly caps. An uncertain request keeps its reservation and
retries with the same HQ alert/idempotency key after restart. Consent, visibility and quiet hours are checked again
at dispatch; revocation stops queued work. The scheduler owns one bounded worker, never a request's write lock.
"""
import hashlib
import re
import logging
import threading
from datetime import timedelta
from urllib.parse import quote
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx
from fastapi import Request
from pydantic import Field, StrictBool, StrictInt

from clients import gateway as G
from . import gateway_connection, outbound_tls
from .auth import validate_identity
from .models import Contract
from .store import H, Problem
from clients.gateway import user_agent

TRANSPORT = None
MAX_RESPONSE = 32768
KINDS = ("urgent_needs", "legal_mail", "bot_stuck")
TEXTS = {"urgent_needs": "Tico: an overdue task needs you. Open Needs you in Tico.",
         "legal_mail": "Tico: legal mail has arrived. Open Inboxes in Tico.",
         "bot_stuck": "Tico: a bot you manage has a stalled task. Open Health in Tico.",
         "test": "Tico: your test alert. Your company pays $1 for this accepted alert."}
CLOCK = re.compile(r"(?:[01][0-9]|2[0-3]):[0-5][0-9]")


class Preferences(Contract):
    urgent_needs: StrictBool | None = None
    legal_mail: StrictBool | None = None
    bot_stuck: StrictBool | None = None
    urgent_calls: StrictBool | None = None
    timezone: str | None = Field(default=None, max_length=80)
    quiet_start: str | None = Field(default=None, max_length=5)
    quiet_end: str | None = Field(default=None, max_length=5)
    emergency_exception: StrictBool | None = None
    monthly_cap: StrictInt | None = Field(default=None, ge=0, le=10000)


class Phone(Contract):
    phone: str = Field(min_length=10, max_length=40)
    sms_consent: StrictBool
    call_consent: StrictBool = False


class Confirmation(Contract):
    code: str = Field(pattern=r"^[0-9]{6}$")


class CompanyCap(Contract):
    monthly_cap: StrictInt = Field(ge=0, le=100000)


class TestAlert(Contract):
    accept_charge: StrictBool


def person(who):
    if who.role not in ("owner", "human") or who.via or who.task_actor or getattr(who, "support", None):
        raise Problem("person_only", "Open Inboxes settings yourself to manage your own alerts", 403)
    return H.actor_id(who.actor)


def reference(ident):
    return hashlib.sha256(("personal-alerts:" + ident).encode()).hexdigest()


def normalize(raw):
    value = re.sub(r"[\s().-]", "", raw)
    if len(value) == 10 and value.isascii() and value.isdigit():
        value = "+1" + value
    elif len(value) == 11 and value.startswith("1"):
        value = "+" + value
    if not re.fullmatch(r"\+1[2-9][0-9]{2}[2-9][0-9]{6}", value):
        raise Problem("invalid_phone", "Use a US or Canadian phone number", 422)
    return value


def prefs(c, ident):
    row = c.execute("SELECT * FROM alert_preferences WHERE person_id=?", (ident,)).fetchone()
    if row:
        return dict(row)
    return {"person_id": ident, "phone": None, "phone_version": "", "verified_at": None, "revocation_pending": 0, "stall_after": "", "call_consent": 0,
            **dict.fromkeys(KINDS, 0), "urgent_calls": 0, "timezone": "UTC", "quiet_start": "22:00",
            "quiet_end": "07:00", "emergency_exception": 0, "monthly_cap": 10, "updated_at": H.now()}


def save(c, ident, values):
    row = {**prefs(c, ident), **values, "updated_at": H.now()}
    keys = list(row)
    c.execute("INSERT INTO alert_preferences(" + ",".join(keys) + ") VALUES(" + ",".join("?" * len(keys)) +
              ") ON CONFLICT(person_id) DO UPDATE SET " + ",".join(k + "=excluded." + k for k in keys[1:]),
              [row[k] for k in keys])
    return row


def update(c, ident, body):
    values = body.model_dump(exclude_none=True)
    if "timezone" in values:
        try:
            ZoneInfo(values["timezone"])
        except (ZoneInfoNotFoundError, ValueError):
            raise Problem("timezone", "Choose an IANA timezone", 422) from None
    for key in ("quiet_start", "quiet_end"):
        if key in values and values[key] and not CLOCK.fullmatch(values[key]):
            raise Problem("quiet_hours", "Quiet hours use HH:MM, or empty for none", 422)
    after = {**prefs(c, ident), **values}
    if bool(after["quiet_start"]) != bool(after["quiet_end"]):
        raise Problem("quiet_hours", "Set both quiet hours, or clear both", 422)
    if after["urgent_calls"] and not after["call_consent"]:
        raise Problem("call_consent_required", "Verify your phone with call consent first", 422)
    save(c, ident, values)
    c.execute("UPDATE personal_alerts SET next_attempt_at=NULL WHERE person_id=? AND state='pending'", (ident,))
    H.event(c, "human:" + ident, "alerts.preferences", ident, {"fields": sorted(values)})
    return local_view(c, ident)


def overdue(task, at):
    due = H.parse_ts(task.get("due_at"))
    if due and due.tzinfo is None:
        due = due.replace(tzinfo=ZoneInfo("America/Los_Angeles"))
    return bool(due and due <= at)


def quiet(row, at, emergency):
    if emergency and row["emergency_exception"]:
        return False
    start, end = row["quiet_start"], row["quiet_end"]
    if not start or start == end:
        return False
    hour = at.astimezone(ZoneInfo(row["timezone"])).strftime("%H:%M")
    return start <= hour < end if start < end else hour >= start or hour < end


def quiet_end(row, at, emergency):
    # Walk UTC minutes: the first local minute outside the window also handles skipped/repeated DST times.
    minute = at.replace(second=0, microsecond=0)
    for offset in range(1, 26 * 60 + 1):
        candidate = minute + timedelta(minutes=offset)
        if not quiet(row, candidate, emergency):
            return candidate.strftime("%Y-%m-%dT%H:%M:%SZ")
    return H.shift(H.now(), hours=24)


def next_month(at):
    return ((at.replace(day=1) + timedelta(days=32)).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            .strftime("%Y-%m-%dT%H:%M:%SZ"))


def future_deadlines(c, at):
    """Personal task deadlines wake a sleeping install so its next collection can page or defer to quiet-hours end."""
    rows = c.execute("SELECT DISTINCT t.due_at FROM tasks t JOIN alert_preferences p ON "
                     "(t.owner_id='human:'||p.person_id OR t.waiting_on='human:'||p.person_id) "
                     "WHERE p.verified_at IS NOT NULL AND p.urgent_needs=1 AND p.monthly_cap>0 "
                     "AND t.status IN ('open','doing','waiting') AND t.due_at IS NOT NULL "
                     "AND NOT EXISTS (SELECT 1 FROM personal_alerts a WHERE a.person_id=p.person_id "
                     "AND a.incident_key='due:'||t.id||':'||t.due_at) "
                     "AND (SELECT monthly_cap FROM alert_company_settings WHERE id=1)>0 ORDER BY t.due_at LIMIT 100")
    out = []
    for (value,) in rows:
        due = H.parse_ts(value)
        if due and due.tzinfo is None:
            due = due.replace(tzinfo=ZoneInfo("America/Los_Angeles"))
        if due and due.timestamp() > at:
            out.append(due.timestamp())
    return out


def spend(c, ident=None, month=None):
    month = month or H.now()[:7]
    return c.execute("SELECT coalesce(sum(reserved),0) FROM personal_alerts WHERE (month=? OR state='sending')" +
                     (" AND person_id=?" if ident else ""), (month, ident) if ident else (month,)).fetchone()[0]


def local_view(c, ident):
    row = prefs(c, ident)
    return {"phone": row["phone"], "status": "removal_pending" if row["revocation_pending"] else "verified" if row["verified_at"] else "code_sent" if row["phone"] else "none",
            "verified_at": row["verified_at"], "call_consent": bool(row["call_consent"]),
            "preferences": {k: bool(row[k]) if k in (*KINDS, "urgent_calls", "emergency_exception") else row[k]
                            for k in Preferences.model_fields},
            "monthly_spend": spend(c, ident), "company_monthly_cap": c.execute(
                "SELECT monthly_cap FROM alert_company_settings WHERE id=1").fetchone()[0],
            "company_monthly_spend": spend(c), "price_usd": 1, "paid_by": "company",
            "recent": [dict(r) for r in c.execute("SELECT id,kind,state,error,created_at FROM personal_alerts "
                                                 "WHERE person_id=? ORDER BY created_at DESC,id DESC LIMIT 10", (ident,))]}


def enqueue(c, ident, kind, subject, incident, emergency=False):
    row = prefs(c, ident)
    if not row["verified_at"] or (kind != "test" and not row[kind]):
        return None
    # The stable incident key remains after delivery, failure or cancellation; repeated facts never page again.
    alert = "pa_" + hashlib.sha256((ident + ":" + incident).encode()).hexdigest()[:48]
    c.execute("INSERT OR IGNORE INTO personal_alerts(id,person_id,incident_key,kind,subject_id,emergency,phone_version,"
              "created_at) VALUES(?,?,?,?,?,?,?,?)", (alert, ident, incident, kind, subject, int(emergency),
                                                     row["phone_version"], H.now()))
    return alert


class Alerts:
    def __init__(self, app, store, settings, auth):
        self.app, self.store, self.settings, self.auth = app, store, settings, auth
        self.lock = threading.RLock()
        self.worker_lock = threading.Lock()
        self.cursor = ""

    def gateway(self, method, path, body=None, key=None):
        if self.settings.demo or self.settings.rehearsal:
            raise Problem("unavailable", "Alerts send nothing in demo or rehearsal mode", 409)
        with self.store.read() as c:
            secret, base, _ = gateway_connection.target(c, self.app.state.vault, self.settings)
        if not secret:
            raise Problem("no_gateway_key", "Connect the company Gateway first", 409)
        try:
            url = G.safe_base(base) + "/paging/" + path
            headers = {"Authorization": "Bearer " + secret, "User-Agent": user_agent()}
            if key:
                headers["Idempotency-Key"] = key
            with httpx.Client(transport=TRANSPORT, timeout=12, trust_env=False, follow_redirects=False,
                              verify=outbound_tls.context(trust_env=False)) as client:
                with client.stream(method, url, json=body, headers=headers) as response:
                    raw = bytearray()
                    for chunk in response.iter_bytes():
                        raw.extend(chunk)
                        if len(raw) > MAX_RESPONSE:
                            raise Problem("gateway_unavailable", "Paging returned too much data", 502)
                    import json
                    data = json.loads(raw)
                    if response.status_code >= 400:
                        code = data.get("error") if isinstance(data, dict) else None
                        known = {"opted_out", "wrong_code", "code_expired", "too_many_tries", "too_many_codes",
                                 "phone_in_use", "invalid_phone", "unverified_phone", "not_found", "consent_required",
                                 "call_consent_required", "opt_out_unconfigured", "out_of_credit", "too_many_alerts",
                                 "cap_reached", "rate_limited", "provider_error", "unconfigured"}
                        status = response.status_code if code in known else 502
                        code = code if code in known else "gateway_unavailable"
                        raise Problem(code, "Paging: " + code.replace("_", " "), status)
                    if not isinstance(data, dict):
                        raise ValueError()
                    return data
        except (httpx.HTTPError, ValueError):
            raise Problem("gateway_unavailable", "Paging is unavailable; try again later", 502) from None

    def status(self, ident, row):
        data = self.gateway("GET", "phones?person_ref=" + reference(ident) + "&limit=100")
        if data.get("person_ref") != reference(ident) or data.get("opt_out_sync") is not True:
            raise Problem("opt_out_unconfigured", "Personal alerts need HQ opt-out synchronization", 503)
        return next((p for p in data.get("phones", []) if p.get("phone") == row["phone"]), None)

    def view(self, ident):
        with self.store.read() as c:
            row, out = prefs(c, ident), local_view(c, ident)
        if row["verified_at"] and not self.settings.demo:
            try:
                current = self.status(ident, row)
                out["status"] = "opted_out" if current and current.get("opted_out") else "verified" if current else "unverified"
                out["call_consent"] = bool(current and current.get("call_consent"))
            except Problem as exc:
                out.update(status="unavailable", error=exc.code)
        return out

    def phone(self, ident, body):
        if not body.sms_consent:
            raise Problem("consent_required", "Agree to receive alert texts before verification", 422)
        phone = normalize(body.phone)
        with self.lock:
            with self.store.transaction() as c:
                old = prefs(c, ident)
                if old["revocation_pending"]:
                    raise Problem("removal_pending", "Finish removing your phone before verifying again", 409)
                if old["phone"] and old["phone"] != phone:
                    raise Problem("remove_phone_first", "Remove your current phone before adding another", 409)
                claimed = c.execute("SELECT 1 FROM alert_preferences WHERE phone=? AND person_id<>? AND verified_at IS NOT NULL", (phone, ident)).fetchone()
                if claimed:
                    raise Problem("phone_in_use", "That phone is already connected", 409)
                save(c, ident, {"phone": phone, "phone_version": old["phone_version"] or H.new_id()})
            result = self.gateway("POST", "phones", {"phone": phone, "person_ref": reference(ident),
                                                       "sms_consent": True, "call_consent": body.call_consent})
            # Even an HQ shortcut cannot assign local verification: only confirm binds our version and consent.
            return {"status": result.get("status", "code_sent"), "expires_in": result.get("expires_in", 600)}

    def confirm(self, ident, body):
        with self.lock:
            with self.store.read() as c:
                row = prefs(c, ident)
            if row["revocation_pending"]:
                raise Problem("removal_pending", "Finish removing your phone before verifying again", 409)
            if not row["phone"]:
                raise Problem("phone_required", "Add your phone first", 409)
            self.gateway("POST", "phones/confirm", {"phone": row["phone"], "code": body.code, "person_ref": reference(ident)})
            current = self.status(ident, row)
            if not current or current.get("opted_out"):
                raise Problem("opted_out", "Reply START to the paging number before enabling alerts", 422)
            with self.store.transaction() as c:
                if c.execute("SELECT 1 FROM alert_preferences WHERE phone=? AND person_id<>? AND verified_at IS NOT NULL",
                             (row["phone"], ident)).fetchone():
                    raise Problem("phone_in_use", "That phone is already connected", 409)
                save(c, ident, {"verified_at": current["verified_at"], "call_consent": int(bool(current.get("call_consent")))})
                H.event(c, "human:" + ident, "alerts.verified", ident, {})
            return self.view(ident)

    def remove(self, ident):
        with self.lock:
            with self.store.transaction() as c:
                row = prefs(c, ident)
                # Stop local dispatch immediately, even when HQ is unavailable. Keep the number for retrying revoke.
                save(c, ident, {"verified_at": None, "urgent_calls": 0, "call_consent": 0, "revocation_pending": 1})
                c.execute("UPDATE personal_alerts SET state='canceled' WHERE person_id=? AND state IN ('pending','sending')", (ident,))
            if row["phone"]:
                try:
                    self.gateway("DELETE", "phones/" + quote(row["phone"], safe="") + "?person_ref=" + reference(ident))
                except Problem as exc:
                    if exc.code != "not_found":
                        raise
            with self.store.transaction() as c:
                save(c, ident, {"phone": None, "phone_version": "", "revocation_pending": 0})
                H.event(c, "human:" + ident, "alerts.removed", ident, {})
            return self.view(ident)

    def eligible(self, c, alert, row):
        if not row["verified_at"] or row["phone_version"] != alert["phone_version"]:
            return False
        try:
            who = self.auth.identity_for_actor(c, "human:" + row["person_id"])
        except Problem:
            return False
        if alert["kind"] == "test":
            return True
        if not row[alert["kind"]]:
            return False
        try:
            task = self.auth.task(c, who, alert["subject_id"])
        except Problem:
            return False
        if task["status"] not in H.ACTIVE_STATUSES or H.TS.snoozed(task):
            return False
        if alert["kind"] == "legal_mail":
            return True
        if alert["kind"] == "urgent_needs":
            return overdue(task, H.parse_ts(H.now())) and who.actor in (task["owner_id"], task.get("waiting_on"))
        return bool(H.is_bot(task["owner_id"]) and self.auth.bot_manager(c, who, H.actor_id(task["owner_id"]))
                    and H.stalled_tasks(c, task_ids=[task["id"]]))

    def dispatch(self, alert_id):
        with self.lock:
            with self.store.transaction() as c:
                found = c.execute("SELECT * FROM personal_alerts WHERE id=?", (alert_id,)).fetchone()
                if not found:
                    raise Problem("not_found", "Alert not found", 404)
                alert = dict(found)
                if alert["state"] not in ("pending", "sending"):
                    return {"id": alert_id, "status": alert["state"], "error": alert["error"]}
                row = prefs(c, alert["person_id"])
                if not self.eligible(c, alert, row):
                    c.execute("UPDATE personal_alerts SET state='canceled' WHERE id=?", (alert_id,))
                    return {"id": alert_id, "status": "canceled"}
                if quiet(row, H.parse_ts(H.now()), alert["emergency"]) and alert["kind"] != "test":
                    c.execute("UPDATE personal_alerts SET attempted_at=?,next_attempt_at=? WHERE id=?",
                              (H.now(), quiet_end(row, H.parse_ts(H.now()), alert["emergency"]), alert_id))
                    return {"id": alert_id, "status": "quiet_hours"}
                month = H.now()[:7]
                company_cap = c.execute("SELECT monthly_cap FROM alert_company_settings WHERE id=1").fetchone()[0]
                extra = 0 if alert["reserved"] else 1
                if spend(c, row["person_id"], month) + extra > row["monthly_cap"] or spend(c, month=month) + extra > company_cap:
                    c.execute("UPDATE personal_alerts SET attempted_at=?,next_attempt_at=? WHERE id=?",
                              (H.now(), next_month(H.parse_ts(H.now())), alert_id))
                    return {"id": alert_id, "status": "spend_cap"}
                channel = "call" if alert["emergency"] and row["urgent_calls"] and row["call_consent"] else "sms"
                if alert["state"] == "sending":
                    channel = alert["channel"]
                    if channel == "call" and not row["urgent_calls"]:
                        c.execute("UPDATE personal_alerts SET state='canceled' WHERE id=?", (alert_id,))
                        return {"id": alert_id, "status": "canceled"}
                c.execute("UPDATE personal_alerts SET state='sending',reserved=1,month=?,channel=?,attempted_at=?,next_attempt_at=? WHERE id=?",
                          (month, channel, H.now(), H.shift(H.now(), seconds=60), alert_id))
            try:
                current = self.status(row["person_id"], row)
                if not current or current.get("opted_out"):
                    raise Problem("opted_out" if current else "unverified_phone", "Phone is not eligible for alerts", 422)
                if channel == "call" and not current.get("call_consent"):
                    raise Problem("call_consent_required", "Call consent is no longer active", 403)
                sent = self.gateway("POST", "alerts/" + alert_id + "/sends",
                                    {"phone": row["phone"], "person_ref": reference(row["person_id"]),
                                     "channel": channel, "text": TEXTS[alert["kind"]]}, key=alert_id)
                if not sent.get("send_id") or sent.get("status") == "sending":
                    raise Problem("gateway_unavailable", "Paging acceptance is not confirmed yet", 502)
            except Problem as exc:
                certain = exc.code == "provider_error" or (alert["state"] == "pending" and exc.status < 500
                                                           and exc.code != "gateway_unavailable")
                with self.store.transaction() as c:
                    c.execute("UPDATE personal_alerts SET state=?,reserved=?,error=? WHERE id=?",
                              ("failed" if certain else "sending", 0 if certain else 1, exc.code, alert_id))
                if alert["kind"] == "test":
                    raise
                return {"id": alert_id, "status": "failed" if certain else "sending", "error": exc.code}
            with self.store.transaction() as c:
                c.execute("UPDATE personal_alerts SET state='accepted',send_id=?,error='' WHERE id=?", (sent["send_id"], alert_id))
                H.event(c, "human:" + row["person_id"], "alerts.accepted", alert_id, {"kind": alert["kind"], "price_usd": 1})
            return {"id": alert_id, "status": "accepted", "price_usd": 1, "paid_by": "company"}

    def collect(self):
        with self.store.transaction() as c:
            people = c.execute("SELECT person_id FROM alert_preferences WHERE verified_at IS NOT NULL AND person_id>? "
                               "ORDER BY person_id LIMIT 20", (self.cursor,)).fetchall()
            self.cursor = people[-1][0] if len(people) == 20 else ""
            for (ident,) in people:
                row = prefs(c, ident)
                try:
                    who = self.auth.identity_for_actor(c, "human:" + ident)
                except Problem:
                    continue
                if row["urgent_needs"]:
                    tasks = c.execute("SELECT * FROM tasks WHERE (owner_id=? OR waiting_on=?) AND status IN ('open','doing','waiting') "
                                      "AND due_at IS NOT NULL AND due_at<=? AND NOT EXISTS (SELECT 1 FROM personal_alerts a "
                                      "WHERE a.person_id=? AND a.incident_key='due:'||tasks.id||':'||tasks.due_at) "
                                      "ORDER BY due_at LIMIT 100",
                                      (who.actor, who.actor, H.shift(H.now(), hours=24), ident)).fetchall()
                    for task in tasks:
                        if overdue(dict(task), H.parse_ts(H.now())) and not H.TS.snoozed(dict(task)):
                            enqueue(c, ident, "urgent_needs", task["id"], "due:" + task["id"] + ":" + task["due_at"], True)
                if row["bot_stuck"]:
                    ids = [r[0] for r in c.execute("SELECT id FROM tasks WHERE id>? AND owner_id LIKE 'bot:%' "
                                                   "AND status IN ('open','doing','waiting') ORDER BY id LIMIT 100",
                                                   (row["stall_after"],))]
                    save(c, ident, {"stall_after": ids[-1] if len(ids) == 100 else ""})
                    stalled = H.stalled_tasks(c, task_ids=ids) if ids else []
                    for task in stalled:
                        if self.auth.bot_manager(c, who, H.actor_id(task["owner_id"])):
                            enqueue(c, ident, "bot_stuck", task["id"], "stuck:" + task["id"] + ":" + task["updated_at"])

    def run(self):
        try:
            self.collect()
            with self.store.read() as c:
                ids = [r[0] for r in c.execute("SELECT id FROM personal_alerts WHERE state IN ('pending','sending') "
                                              "AND coalesce(next_attempt_at,'')<=? ORDER BY coalesce(attempted_at,''),created_at LIMIT 20",
                                              (H.now(),))]
            for ident in ids:
                self.dispatch(ident)
        except Exception:
            logging.getLogger("tico.alerts").exception("Personal alert worker failed")
        finally:
            self.worker_lock.release()

    def tick(self):
        if self.settings.demo or self.settings.rehearsal:
            return
        if self.worker_lock.acquire(blocking=False):
            threading.Thread(target=self.run, name="personal-alerts", daemon=True).start()


def legal_mail(c, auth, task_id, mail_id):
    if not task_id:
        return
    for (ident,) in c.execute("SELECT person_id FROM alert_preferences WHERE legal_mail=1 AND verified_at IS NOT NULL"):
        try:
            who = auth.identity_for_actor(c, "human:" + ident)
            auth.task(c, who, task_id)
        except Problem:
            continue
        enqueue(c, ident, "legal_mail", task_id, "legal-mail:" + mail_id, True)


def install(app, store, settings, auth, mutate):
    service = Alerts(app, store, settings, auth)
    app.state.personal_alerts = service

    def own(request):
        who = request.state.identity
        ident = person(who)
        with store.read() as c:
            validate_identity(c, who)
        return ident

    @app.get("/api/v2/alerts/me")
    def get_me(request: Request):
        return service.view(own(request))

    @app.put("/api/v2/alerts/me")
    def put_me(request: Request, body: Preferences):
        ident = own(request)
        with service.lock:
            return mutate(request, body, lambda c: update(c, ident, body))

    @app.post("/api/v2/alerts/me/phone")
    def add_phone(request: Request, body: Phone):
        return service.phone(own(request), body)

    @app.post("/api/v2/alerts/me/phone/confirm")
    def confirm_phone(request: Request, body: Confirmation):
        return service.confirm(own(request), body)

    @app.delete("/api/v2/alerts/me/phone")
    def delete_phone(request: Request):
        return service.remove(own(request))

    @app.post("/api/v2/alerts/me/test")
    def test(request: Request, body: TestAlert):
        ident = own(request)
        if not body.accept_charge:
            raise Problem("charge_required", "A test costs the company $1 when accepted", 422)
        key = request.headers.get("Idempotency-Key", "")
        if not re.fullmatch(r"[\x21-\x7e]{1,200}", key):
            raise Problem("idempotency_key_required", "Send an Idempotency-Key for a paid test", 422)
        with service.lock:
            with store.transaction() as c:
                alert = enqueue(c, ident, "test", "", "test:" + key)
                if not alert:
                    raise Problem("unverified_phone", "Verify your own phone first", 409)
            return service.dispatch(alert)

    def admin(request):
        own(request)
        if auth.company_role(request.state.identity) not in ("owner", "admin"):
            raise Problem("forbidden", "Only an owner or admin changes the company alert cap", 403)

    @app.get("/api/v2/alerts/company")
    def get_company(request: Request):
        admin(request)
        with store.read() as c:
            return {"monthly_cap": c.execute("SELECT monthly_cap FROM alert_company_settings WHERE id=1").fetchone()[0],
                    "monthly_spend": spend(c), "price_usd": 1}

    @app.put("/api/v2/alerts/company")
    def put_company(request: Request, body: CompanyCap):
        admin(request)
        def write(c):
            c.execute("UPDATE alert_company_settings SET monthly_cap=? WHERE id=1", (body.monthly_cap,))
            c.execute("UPDATE personal_alerts SET next_attempt_at=NULL WHERE state='pending'")
            H.event(c, request.state.identity.actor, "alerts.company_cap", "", {"monthly_cap": body.monthly_cap})
            return {"monthly_cap": body.monthly_cap, "monthly_spend": spend(c), "price_usd": 1}
        with service.lock:
            return mutate(request, body, write)

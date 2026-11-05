"""Personal paging's privacy, opt-out, durable reservations and actual event producers against a fake HQ."""
import json
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest

from backend import hubdb as H, personal_alerts as A
from backend.auth import Identity
from backend.tests.test_api import api, get, post, put, headers  # noqa: F401

PHONE = "+14155550123"
OTHER = "+14155550124"
NOW = "2026-11-04T18:00:00Z"


class HQ:
    def __init__(self):
        self.phones, self.pending, self.sends = {}, {}, {}
        self.calls, self.accepted = [], []
        self.stopped, self.timeout, self.down, self.old = False, False, False, False

    def __call__(self, req):
        path = req.url.path
        data = json.loads(req.content) if req.content else {}
        person = data.get("person_ref") or req.url.params.get("person_ref")
        self.calls.append((req.method, path, data))
        if self.down:
            return httpx.Response(503, json={"error": "unconfigured"})
        if path.endswith("/phones") and req.method == "GET":
            return httpx.Response(200, json={"phones": [{**r, "opted_out": self.stopped}
                                                       for r in self.phones.values() if r["person_ref"] == person],
                                              "person_ref": person if not self.old else None, "opt_out_sync": not self.old})
        if path.endswith("/phones") and req.method == "POST":
            self.pending[person] = data
            return httpx.Response(200, json={"status": "code_sent", "expires_in": 600})
        if path.endswith("/phones/confirm"):
            if data["code"] != "123456":
                return httpx.Response(422, json={"error": "wrong_code"})
            self.phones[person] = {**self.pending[person], "verified_at": NOW}
            return httpx.Response(200, json={"status": "verified", "verified_at": NOW})
        if req.method == "DELETE":
            self.phones.pop(person, None)
            return httpx.Response(200, json={"ok": True})
        if path.endswith("/sends"):
            key = req.headers["Idempotency-Key"]
            if self.stopped:
                return httpx.Response(422, json={"error": "opted_out"})
            if key not in self.sends:
                self.sends[key] = {"send_id": "ps_" + str(len(self.sends)), "status": "queued"}
                self.accepted.append(data)
            if self.timeout:
                self.timeout = False
                raise httpx.ReadTimeout("accepted but receipt lost")
            return httpx.Response(201, json=self.sends[key])
        return httpx.Response(404, json={"error": "not_found"})


@pytest.fixture
def hq(api, monkeypatch):
    fake = HQ()
    monkeypatch.setattr(A, "TRANSPORT", httpx.MockTransport(fake))
    monkeypatch.setattr(A.gateway_connection, "target", lambda *args: ("tgw_test", "https://hq.acme.example/v1/gateway", "test"))
    monkeypatch.setattr(H, "now", lambda: NOW)
    return fake


def connect(api, phone=PHONE, token="ana-test", call=True):
    post(api, "alerts/me/phone", {"phone": phone, "sms_consent": True, "call_consent": call}, token)
    return post(api, "alerts/me/phone/confirm", {"code": "123456"}, token)


def test_only_the_person_reads_changes_and_removes_their_phone(api, hq):
    connect(api)
    assert get(api, "alerts/me")["phone"] == PHONE
    assert get(api, "alerts/me", "ben-test")["phone"] is None
    assert post(api, "alerts/me/phone", {"phone": PHONE, "sms_consent": True}, "ben-test", expected=409)["error"]["code"] == "phone_in_use"
    api.delete("/api/v2/alerts/me/phone", headers=headers("ben-test")).raise_for_status()
    assert get(api, "alerts/me")["status"] == "verified"
    # Unknown SQL tables are refused even to the owner; generic SQL cannot reveal another person's number.
    response = api.post("/api/v2/sql", json={"sql": "SELECT phone FROM alert_preferences"}, headers=headers("ben-test"))
    assert response.status_code == 403, response.text
    assert PHONE not in response.text
    with api.app.state.store.read() as c:
        assert PHONE not in " ".join(r[0] for r in c.execute("SELECT detail FROM events"))
    for who in (Identity("bot:botops", "bot"), Identity("human:ana", "owner", via="botops"),
                Identity("human:ana", "owner", via="assistant"), Identity("runner:r", "runner")):
        with pytest.raises(A.Problem):
            A.person(who)


def test_verification_requires_sms_and_preserves_call_choice(api, hq):
    post(api, "alerts/me/phone", {"phone": PHONE, "sms_consent": False}, expected=422)
    assert hq.calls == []
    post(api, "alerts/me/phone", {"phone": PHONE, "sms_consent": True, "call_consent": False})
    post(api, "alerts/me/phone/confirm", {"code": "000000"}, expected=422)
    assert get(api, "alerts/me")["status"] == "code_sent"
    result = post(api, "alerts/me/phone/confirm", {"code": "123456"})
    assert result["call_consent"] is False
    put(api, "alerts/me", {"urgent_calls": True}, expected=422)
    assert hq.pending[A.reference("ana")]["call_consent"] is False


def test_test_send_is_charged_once_with_the_same_key_and_caps_apply(api, hq):
    connect(api)
    put(api, "alerts/me", {"monthly_cap": 1})
    post(api, "alerts/me/test", {"accept_charge": False}, expected=422)
    head = headers(key="same-paid-test")
    for _ in range(2):
        reply = api.post("/api/v2/alerts/me/test", json={"accept_charge": True}, headers=head)
        assert reply.status_code == 200 and reply.json()["status"] == "accepted"
    assert len(hq.accepted) == 1
    assert get(api, "alerts/me")["monthly_spend"] == 1
    assert post(api, "alerts/me/test", {"accept_charge": True})["status"] == "spend_cap"
    assert len(hq.accepted) == 1
    put(api, "alerts/company", {"monthly_cap": 0})
    assert post(api, "alerts/me/test", {"accept_charge": True})["status"] == "spend_cap"


def test_company_cap_reserves_concurrent_persons_before_sending(api, hq):
    connect(api)
    connect(api, OTHER, "ben-test")
    put(api, "alerts/company", {"monthly_cap": 1})
    service = api.app.state.personal_alerts
    with api.app.state.store.transaction() as c:
        ids = [A.enqueue(c, ident, "test", "", "test:race") for ident in ("ana", "ben")]
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(service.dispatch, ids))
    assert sorted(x["status"] for x in results) == ["accepted", "spend_cap"]
    assert len(hq.accepted) == 1
    with api.app.state.store.read() as c:
        assert A.spend(c) == 1
    put(api, "alerts/company", {"monthly_cap": 4}, "cara-test", expected=403)


def test_restart_retries_uncertain_acceptance_without_a_second_charge(api, hq):
    connect(api)
    put(api, "alerts/me", {"monthly_cap": 1})
    hq.timeout = True
    post(api, "alerts/me/test", {"accept_charge": True}, expected=502)
    with api.app.state.store.read() as c:
        alert = c.execute("SELECT * FROM personal_alerts").fetchone()
        assert alert["state"] == "sending" and A.spend(c, "ana") == 1
    restarted = A.Alerts(api.app, api.app.state.store, api.app.state.store.settings, api.app.state.auth)
    assert restarted.dispatch(alert["id"])["status"] == "accepted"
    assert len(hq.accepted) == 1
    assert get(api, "alerts/me")["monthly_spend"] == 1


def due_task(api, owner="human:ana", private=True):
    with api.app.state.store.transaction() as c:
        return H.task_create(c, "human:ana", "Review the Acme filing", "Review the filing before its deadline.",
                             owner, due=H.shift(NOW, hours=-1), private=private, lint=False, deduplicate=False)


def test_real_overdue_task_is_deduped_and_urgent_call_is_opt_in(api, hq):
    connect(api)
    put(api, "alerts/me", {"urgent_needs": True, "urgent_calls": True, "quiet_start": "", "quiet_end": ""})
    task = due_task(api)
    service = api.app.state.personal_alerts
    service.collect()
    service.collect()
    with api.app.state.store.read() as c:
        rows = c.execute("SELECT * FROM personal_alerts WHERE kind='urgent_needs'").fetchall()
        assert len(rows) == 1 and rows[0]["subject_id"] == task["id"]
    assert service.dispatch(rows[0]["id"])["status"] == "accepted"
    assert hq.accepted[0]["channel"] == "call"
    service.collect()
    assert len(hq.accepted) == 1


def test_quiet_hours_defer_and_closed_tasks_do_not_page(api, hq):
    connect(api)
    put(api, "alerts/me", {"urgent_needs": True, "timezone": "America/Los_Angeles", "quiet_start": "09:00", "quiet_end": "12:00"})
    task = due_task(api)
    service = api.app.state.personal_alerts
    service.collect()
    with api.app.state.store.read() as c:
        alert = c.execute("SELECT id FROM personal_alerts").fetchone()[0]
    assert service.dispatch(alert)["status"] == "quiet_hours"
    assert hq.accepted == []
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE tasks SET status='done' WHERE id=?", (task["id"],))
    put(api, "alerts/me", {"emergency_exception": True})
    assert service.dispatch(alert)["status"] == "canceled"
    assert hq.accepted == []


def test_opt_out_blocks_calls_and_sms_and_old_hq_fails_closed(api, hq):
    connect(api)
    put(api, "alerts/me", {"urgent_needs": True, "urgent_calls": True, "emergency_exception": True})
    due_task(api)
    service = api.app.state.personal_alerts
    service.collect()
    with api.app.state.store.read() as c:
        alert = c.execute("SELECT id FROM personal_alerts").fetchone()[0]
    hq.stopped = True
    assert get(api, "alerts/me")["status"] == "opted_out"
    assert service.dispatch(alert)["error"] == "opted_out"
    assert hq.accepted == []
    hq.stopped, hq.old = False, True
    assert get(api, "alerts/me")["status"] == "unavailable"
    post(api, "alerts/me/test", {"accept_charge": True}, expected=503)
    assert hq.accepted == []


def test_revoke_stops_pending_alerts_even_if_hq_is_down(api, hq):
    connect(api)
    with api.app.state.store.transaction() as c:
        alert = A.enqueue(c, "ana", "test", "", "test:revoke")
    hq.down = True
    response = api.delete("/api/v2/alerts/me/phone", headers=headers())
    assert response.status_code == 503
    assert get(api, "alerts/me")["status"] == "removal_pending"
    post(api, "alerts/me/phone/confirm", {"code": "123456"}, expected=409)
    assert api.app.state.personal_alerts.dispatch(alert)["status"] == "canceled"
    hq.down = False
    assert api.delete("/api/v2/alerts/me/phone", headers=headers()).status_code == 200
    assert get(api, "alerts/me")["phone"] is None
    assert A.reference("ana") not in hq.phones


def test_legal_mail_only_reaches_subscribers_who_can_read_its_task(api, hq):
    connect(api)
    connect(api, OTHER, "ben-test")
    for token in ("ana-test", "ben-test"):
        put(api, "alerts/me", {"legal_mail": True, "quiet_start": "", "quiet_end": ""}, token)
    task = due_task(api)
    with api.app.state.store.transaction() as c:
        A.legal_mail(c, api.app.state.auth, task["id"], "letter-1")
        A.legal_mail(c, api.app.state.auth, task["id"], "letter-1")
        rows = c.execute("SELECT * FROM personal_alerts").fetchall()
        assert len(rows) == 1 and rows[0]["person_id"] == "ana"
    assert api.app.state.personal_alerts.dispatch(rows[0]["id"])["status"] == "accepted"
    assert hq.accepted[0]["channel"] == "sms"


def test_preferences_validate_timezone_quiet_hours_and_strict_caps(api, hq):
    put(api, "alerts/me", {"timezone": "not/a/timezone"}, expected=422)
    put(api, "alerts/me", {"quiet_start": "25:00"}, expected=422)
    put(api, "alerts/me", {"quiet_start": ""}, expected=422)
    put(api, "alerts/me", {"monthly_cap": True}, expected=422)
    put(api, "alerts/me", {"monthly_cap": -1}, expected=422)
    result = put(api, "alerts/me", {"timezone": "America/New_York", "quiet_start": "22:00", "quiet_end": "07:00"})
    row = {**result["preferences"]}
    assert A.quiet(row, datetime.fromisoformat("2026-11-01T05:30:00+00:00"), True)
    assert A.quiet(row, datetime.fromisoformat("2026-11-01T06:30:00+00:00"), True)
    row["emergency_exception"] = True
    assert not A.quiet(row, datetime.fromisoformat("2026-11-01T06:30:00+00:00"), True)
    assert A.quiet(row, datetime.fromisoformat("2026-11-01T06:30:00+00:00"), False)


def test_rehearsal_refuses_verification_and_paid_sends(api, hq, monkeypatch):
    monkeypatch.setattr(api.app.state.store.settings, "rehearsal", True)
    post(api, "alerts/me/phone", {"phone": PHONE, "sms_consent": True}, expected=409)
    assert hq.calls == []


def test_stalled_task_alerts_only_a_manager_and_cancels_when_recovered(api, hq):
    connect(api)
    put(api, "alerts/me", {"bot_stuck": True, "quiet_start": "", "quiet_end": ""})
    task = due_task(api, owner="bot:ops")
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE jobs SET state='completed'")
        c.execute("UPDATE tasks SET updated_at=? WHERE id=?", (H.shift(NOW, hours=-2), task["id"]))
    service = api.app.state.personal_alerts
    service.collect()
    with api.app.state.store.read() as c:
        alert = c.execute("SELECT * FROM personal_alerts WHERE kind='bot_stuck'").fetchone()
        assert alert is not None
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE tasks SET status='done' WHERE id=?", (task["id"],))
    assert service.dispatch(alert["id"])["status"] == "canceled"
    assert hq.accepted == []

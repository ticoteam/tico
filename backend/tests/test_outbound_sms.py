"""The two SMS adapters exercise the outbound gate against their provider's HTTP contract."""

import base64
import ipaddress
import json
from urllib.parse import parse_qs

import httpx
import pytest

from backend import egress, outbound_actions as OA, outbound_sms as SMS, task_asks
from backend.auth import Identity
from backend.entities import store as contacts
from backend.store import H
from backend.tests.helpers.credentials import setup
from backend.tests.helpers.tasks_board import api, bot_token, get, headers, post

ACCOUNT = "AC" + "1" * 32
SMS_ID = "SM" + "2" * 32
ACTIVITY_ID = "acti_synthetic123"
TOKEN = "3" * 32
API_KEY = "SK" + "4" * 32
API_SECRET = "syntheticTwilioKeySecret123456789"
FROM = "+12025550100"
TO = "+12025550101"
BODY = " Hello 👋\n"
LEAD = "lead_synthetic123"


class FakeSMS:
    def __init__(self, channel, tool):
        self.channel = channel
        self.tool = tool
        self.requests = []
        self.accepted = 0
        self.next_status = 201
        self.lose = False
        self.bad_receipt = False
        self.lead = LEAD
        self.auth = f"{ACCOUNT}:{TOKEN}" if channel == "sms" else f"{TOKEN}:"
        self.provider_id = SMS_ID if channel == "sms" else ACTIVITY_ID

    def __call__(self, request):
        self.requests.append(request)
        wanted = "Basic " + base64.b64encode(self.auth.encode()).decode()
        if request.headers.get("authorization") != wanted:
            return httpx.Response(401)
        assert request.method == "POST"
        if self.channel == "sms":
            assert request.headers["host"] == "api.twilio.com"
            assert request.url.path == f"/2010-04-01/Accounts/{ACCOUNT}/Messages.json"
            assert request.headers["content-type"] == "application/x-www-form-urlencoded"
            assert parse_qs(request.content.decode()) == {"From": [FROM], "To": [TO], "Body": [BODY]}
            key = "sid"
        else:
            assert request.headers["host"] == "api.close.com"
            assert request.url.path == "/api/v1/activity/sms/"
            assert request.headers["content-type"] == "application/json"
            assert json.loads(request.content) == {
                "status": "outbox",
                **({"lead_id": self.lead} if self.lead else {}),
                "local_phone": FROM,
                "remote_phone": TO,
                "text": BODY,
            }
            key = "id"
        if self.next_status != 201:
            status, self.next_status = self.next_status, 201
            return httpx.Response(status, headers={"Retry-After": "180"})
        self.accepted += 1
        if self.lose:
            raise httpx.ReadError("reset after acceptance")
        return httpx.Response(201, json={} if self.bad_receipt else {key: self.provider_id})


def _make(api, monkeypatch, channel, *, template=False):
    setup(api)
    vendor = post(api, "api-tools", {"name": "Twilio" if channel == "sms" else "Close CRM"})
    tool = vendor["id"]
    fake = FakeSMS(channel, tool)
    if template:
        post(
            api,
            f"api-tools/{tool}/paste",
            {"text": f"TWILIO_ACCOUNT_SID={ACCOUNT}\nTWILIO_API_KEY={API_KEY}\nTWILIO_API_SECRET={API_SECRET}"},
        )
        fake.auth = f"{API_KEY}:{API_SECRET}"
    else:
        post(
            api,
            f"api-tools/{tool}/keys",
            {
                "name": "Write key",
                "value": TOKEN,
                "username": ACCOUNT if channel == "sms" else "",
            },
        )
    marked = api.patch(f"/api/v2/api-tools/{tool}", json={"record_owner": "outside"}, headers=headers())
    assert marked.status_code == 200
    store = api.app.state.store
    with store.transaction() as c:
        owner = Identity("human:ana", "owner", "ana@acme.example")
        c.execute("UPDATE bot_config SET reports_to='human:ben' WHERE bot='ops'")
        for key in c.execute("SELECT id FROM credentials WHERE tool_id=?", (tool,)).fetchall():
            api.app.state.vault.grant(c, owner, key[0], "bot:ops")
    monkeypatch.setattr(egress, "resolve", lambda host: [ipaddress.ip_address("93.184.215.14")])
    monkeypatch.setattr(SMS, "TRANSPORT", httpx.MockTransport(fake))
    return fake


@pytest.fixture(params=["sms", "close_sms"])
def vendor(api, monkeypatch, request):
    return _make(api, monkeypatch, request.param)


@pytest.fixture
def store(api):
    return api.app.state.store


def _action(vendor, key):
    payload = {"from": FROM, "body": BODY}
    if vendor.channel == "close_sms" and vendor.lead:
        payload["lead_id"] = vendor.lead
    return {
        "channel": vendor.channel,
        "connection_id": vendor.tool,
        "destination": TO,
        "operation": "send",
        "occurrence_key": key,
        "payload": payload,
    }


def _queue(store, vendor, key="send-once"):
    with store.transaction() as c:
        return OA.enqueue(c, store.settings, actor="bot:ops", **_action(vendor, key))[0]


def _row(store, action_id):
    with store.read() as c:
        return OA.get(c, action_id)


def _approve(api, action_id):
    shown = get(api, f"outbound-actions/{action_id}", token="ben-test")
    approved = post(api, f"outbound-actions/{action_id}/approve", {"shown_hash": shown["shown_hash"]}, token="ben-test")
    assert approved["action"]["state"] == "queued"
    return shown["action"]["payload"]


def _policy(api, vendor):
    response = api.put(
        "/api/v2/outbound-actions/policy",
        json={
            "settings": {f"{vendor.channel}:external": "on_their_own"},
        },
        headers=headers(),
    )
    assert response.status_code == 200


def _attempt(store, now=None):
    claimed = OA.claim(store, now)
    assert claimed is not None
    return OA.attempt(store, claimed)


def test_asks_first_and_sends_the_frozen_text_once(api, store, vendor):
    bot = bot_token(api)
    action = _action(vendor, "send-once")
    created = post(api, "outbound-actions", action, token=bot)
    row = created["action"]
    assert created["created"] and (row["audience"], row["state"]) == ("external", "waiting_approval"), row["last_error"]
    assert OA.claim(store) is None and vendor.requests == []
    expected = {**action["payload"], **({"account_sid": ACCOUNT} if vendor.channel == "sms" else {})}
    assert _approve(api, row["id"]) == expected
    claimed = OA.claim(store)
    assert OA.attempt(store, claimed) == "done"
    assert OA.attempt(store, claimed) == "lost"
    final = _row(store, row["id"])
    assert json.loads(final["receipt"]) == {"provider_id": vendor.provider_id}
    assert final["provider_attempted_at"] and final["payload"] == ""
    replay = post(api, "outbound-actions", action, token=bot)
    assert not replay["created"] and replay["action"]["id"] == row["id"]
    assert OA.claim(store) is None and len(vendor.requests) == vendor.accepted == 1


@pytest.mark.parametrize("garbled", [False, True])
def test_an_unproven_answer_asks_the_owner_and_never_resends(api, store, vendor, garbled):
    _policy(api, vendor)
    vendor.lose, vendor.bad_receipt = not garbled, garbled
    row = _queue(store, vendor)
    assert _attempt(store) == "unknown"
    after = _row(store, row["id"])
    with store.read() as c:
        ask = OA.asked(after)["ask"]
        assert c.execute("SELECT person_id FROM requirements WHERE id=?", (ask,)).fetchone()[0] == "human:ben"
    assert after["provider_attempted_at"]
    assert OA.reconcile_pass(store) == 0
    assert OA.claim(store, H.shift(H.now(), days=1)) is None
    assert len(vendor.requests) == vendor.accepted == 1


def test_rate_limit_waits_and_uses_the_same_ok(api, store, vendor):
    row = _queue(store, vendor)
    _approve(api, row["id"])
    vendor.next_status = 429
    start = H.now()
    assert _attempt(store) == "requeued"
    after = _row(store, row["id"])
    assert after["state"] == "queued" and after["provider_attempted_at"] is None
    assert after["next_attempt_at"] >= H.shift(start, seconds=180)
    assert OA.claim(store, H.shift(start, seconds=179)) is None
    assert _attempt(store, after["next_attempt_at"]) == "done"
    with store.read() as c:
        assert task_asks.standing(c, row["approval_id"]) == task_asks.USED
    assert len(vendor.requests) == 2 and vendor.accepted == 1


def test_a_provider_refusal_is_final(api, store, vendor):
    _policy(api, vendor)
    vendor.next_status = 400
    vendor.lead = None
    row = _queue(store, vendor)
    assert _attempt(store) == "failed"
    after = _row(store, row["id"])
    assert after["payload"] == "" and not OA.asked(after).get("ask")
    assert OA.claim(store, H.shift(H.now(), days=1)) is None
    assert len(vendor.requests) == 1 and vendor.accepted == 0


def test_opt_out_blocks_existing_approval_and_new_actions(api, store, vendor):
    row = _queue(store, vendor)
    _approve(api, row["id"])
    _policy(api, vendor)
    with store.transaction() as c:
        now = H.now()
        c.execute(
            "INSERT INTO entities(id,type,name,name_key,created_by,created_at,updated_at) "
            "VALUES('person/acme','person','Acme','acme','human:ana',?,?)",
            (now, now),
        )
        c.execute(
            "INSERT INTO entity_identifiers(entity_id,kind,value,normalized_value,primary_identifier,derived) "
            "VALUES('person/acme','phone','+1 (202) 555-0101','+1 (202) 555-0101',0,0)"
        )
        c.execute(
            "INSERT INTO org_rules(id,kind,text,who,with_scope,if_broken,status,suggested_by,created_at,"
            "updated_at,toward_scope) VALUES('sms-opt-out','external','Opted out of SMS',"
            "'{\"everyone\":true}','{}','block','active','human:ana',?,?,?)",
            (now, now, json.dumps(["entity:person/acme", "channel:sms"])),
        )
    new = _queue(store, vendor, "after-opt-out")
    assert new["state"] == "blocked" and "Opted out" in new["last_error"]
    with store.transaction() as c:
        c.execute(
            "INSERT INTO entities(id,type,name,name_key,created_by,created_at,updated_at) "
            "VALUES('person/acme-kept','person','Acme','acme','human:ana',?,?)",
            (now, now),
        )
        contacts.merge(c, "human:ana", "person/acme-kept", "person/acme")
    assert _attempt(store) == "blocked"
    after = _row(store, row["id"])
    assert after["provider_attempted_at"] is None and "Opted out" in after["last_error"]
    assert vendor.requests == []


def test_the_record_integration_and_write_grant_are_required(api, store, vendor):
    _policy(api, vendor)
    row = _queue(store, vendor)
    with store.transaction() as c:
        c.execute(
            "UPDATE credential_grants SET access_level='read' WHERE subject='bot:ops' AND credential_id IN "
            "(SELECT id FROM credentials WHERE tool_id=?)",
            (vendor.tool,),
        )
    with pytest.raises(OA.Problem) as refused:
        _queue(store, vendor, "only-reads")
    assert refused.value.status == 403
    assert _attempt(store) == "blocked" and vendor.requests == []
    with store.transaction() as c:
        c.execute("UPDATE tools SET record_owner='' WHERE id=?", (vendor.tool,))
    with pytest.raises(OA.Problem) as refused:
        _queue(store, vendor, "not-a-record-integration")
    assert refused.value.status == 422
    assert _row(store, row["id"])["provider_attempted_at"] is None


def test_twilio_template_uses_its_shared_key_pair_and_freezes_the_account(api, store, monkeypatch):
    vendor = _make(api, monkeypatch, "sms", template=True)
    _policy(api, vendor)
    with store.transaction() as c:
        key = c.execute(
            "SELECT id FROM credentials WHERE tool_id=? AND env='TWILIO_API_SECRET'", (vendor.tool,)
        ).fetchone()[0]
        c.execute(
            "UPDATE credential_grants SET access_level='read' WHERE credential_id=? AND subject='bot:ops'", (key,)
        )
    with pytest.raises(OA.Problem) as refused:
        _queue(store, vendor)
    assert refused.value.status == 403
    with store.transaction() as c:
        c.execute(
            "UPDATE credential_grants SET access_level='read_write' WHERE credential_id=? AND subject='bot:ops'", (key,)
        )
    _queue(store, vendor)
    assert _attempt(store) == "done" and vendor.accepted == 1
    row = _queue(store, vendor, "before-account-change")
    with store.transaction() as c:
        c.execute("UPDATE tool_settings SET value=? WHERE tool_id=?", ("AC" + "9" * 32, vendor.tool))
    assert _attempt(store) == "blocked"
    assert _row(store, row["id"])["provider_attempted_at"] is None and len(vendor.requests) == 1

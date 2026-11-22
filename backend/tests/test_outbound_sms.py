"""The two SMS adapters exercise the outbound gate against their provider's HTTP contract."""

import base64
import ipaddress
import json
from dataclasses import replace
from urllib.parse import parse_qs

import httpx
import pytest

from backend import egress, outbound_actions as OA, outbound_sms as SMS, task_asks
from backend.auth import Identity
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


class InterruptedBody(httpx.SyncByteStream):
    def __iter__(self):
        raise httpx.ReadError("reset after the response headers")


class FakeSMS:
    def __init__(self, channel, tool):
        self.channel = channel
        self.tool = tool
        self.requests = []
        self.accepted = 0
        self.next_status = 201
        self.retry_after = [b"180"]
        self.lose = False
        self.bad_receipt = False
        self.body = BODY
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
            assert parse_qs(request.content.decode()) == {"From": [FROM], "To": [TO], "Body": [self.body]}
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
                "text": self.body,
            }
            key = "id"
        if self.next_status != 201:
            status, self.next_status = self.next_status, 201
            if status == 429:
                headers = [(b"X-Provider-Detail", b"\xe9"), *((b"Retry-After", v) for v in self.retry_after)]
                return httpx.Response(status, headers=headers, stream=InterruptedBody())
            return httpx.Response(status)
        self.accepted += 1
        if self.lose:
            raise httpx.ReadError("reset after acceptance")
        record = {} if self.bad_receipt else {"body": self.body, key: self.provider_id}
        return httpx.Response(201, content=json.dumps(record).encode())


def _make(api, monkeypatch, channel, *, template=False, grouped=False):
    setup(api)
    vendor = post(api, "api-tools", {"name": "Twilio" if channel == "sms" else "Close CRM"})
    tool = vendor["id"]
    fake = FakeSMS(channel, tool)
    if template:
        sid = "" if grouped else f"TWILIO_ACCOUNT_SID={ACCOUNT}\n"
        post(
            api,
            f"api-tools/{tool}/paste",
            {
                "text": f"{sid}TWILIO_API_KEY={API_KEY}\nTWILIO_API_SECRET={API_SECRET}",
            },
        )
        if grouped:
            post(
                api,
                f"api-tools/{tool}/keys",
                {"name": "Account SID", "env": "TWILIO_ACCOUNT_SID", "value": ACCOUNT},
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
    payload = {"from": FROM, "body": vendor.body}
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


def _policy(api, vendor, mode="on_their_own"):
    response = api.put(
        "/api/v2/outbound-actions/policy",
        json={
            "settings": {f"{vendor.channel}:external": mode},
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


@pytest.mark.parametrize(
    ("retry_after", "wait"),
    [
        ([b"180", b"5"], 180),
        ([b"1e100"], 86_400),
        ([b"\xff", b"5"], 60),
        ([b"Wed, 21 Oct 2015 07:28:00 +9999999999999999999999999999"], 60),
    ],
)
def test_rate_limit_headers_requeue_with_the_same_ok_even_if_the_body_cannot_be_read(
    api, store, vendor, retry_after, wait
):
    row = _queue(store, vendor)
    _approve(api, row["id"])
    vendor.next_status = 429
    vendor.retry_after = retry_after
    start = H.now()
    assert _attempt(store) == "requeued"
    after = _row(store, row["id"])
    assert after["state"] == "queued" and after["provider_attempted_at"] is None
    assert after["next_attempt_at"] >= H.shift(start, seconds=wait)
    assert after["next_attempt_at"] <= H.shift(H.now(), seconds=wait)
    assert OA.claim(store, H.shift(start, seconds=wait - 1)) is None
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


def _opt_out(api, store):
    post(
        api,
        "entities/types/person/attributes",
        {"key": "mobile", "name": "Mobile", "description": "The person's mobile number.", "value_type": "phone"},
    )
    contact = post(
        api,
        "entities",
        {"type": "person", "match": {"name": "Acme Contact"}, "values": {"mobile": "+1 (202) 555‑0101"}},
    )
    with store.transaction() as c:
        now = H.now()
        c.execute(
            "INSERT INTO org_rules(id,kind,text,who,with_scope,if_broken,status,suggested_by,created_at,"
            "updated_at,toward_scope) VALUES('sms-opt-out','external','Opted out of SMS',"
            "'{\"everyone\":true}','{}','block','active','human:ana',?,?,?)",
            (now, now, json.dumps([f"entity:{contact['id']}", "channel:sms"])),
        )
    return contact["id"]


def test_opt_out_blocks_existing_approval_and_new_actions(api, store, vendor):
    row = _queue(store, vendor)
    _approve(api, row["id"])
    _policy(api, vendor)
    contact = _opt_out(api, store)
    new = _queue(store, vendor, "after-opt-out")
    assert new["state"] == "blocked" and "Opted out" in new["last_error"]
    kept = post(api, "entities", {"type": "person", "match": {"name": "Kept Contact"}})["id"]
    post(api, "entities/merge", {"keep": kept, "drop": contact})
    assert _attempt(store) == "blocked"
    after = _row(store, row["id"])
    assert after["provider_attempted_at"] is None and "Opted out" in after["last_error"]
    assert vendor.requests == []


def test_opt_out_committed_after_the_permit_stops_dispatch(api, store, vendor, monkeypatch):
    row = _queue(store, vendor)
    _approve(api, row["id"])
    adapter = OA.ADAPTERS[vendor.channel]

    def dispatch_after_opt_out(store, permitted):
        assert permitted["provider_attempted_at"]
        _opt_out(api, store)
        return adapter.dispatch(store, permitted)

    monkeypatch.setitem(OA.ADAPTERS, vendor.channel, replace(adapter, dispatch=dispatch_after_opt_out))
    assert _attempt(store) == "blocked"
    after = _row(store, row["id"])
    assert after["provider_attempted_at"] is None and "Opted out" in after["last_error"]
    assert vendor.requests == []


def test_ask_first_committed_after_the_permit_waits_for_approval(api, store, vendor, monkeypatch):
    _policy(api, vendor)
    row = _queue(store, vendor)
    assert row["state"] == "queued" and not row["approval_id"]
    adapter = OA.ADAPTERS[vendor.channel]

    def dispatch_after_policy_change(store, permitted):
        assert permitted["provider_attempted_at"]
        if not permitted["approval_id"]:
            _policy(api, vendor, "ask_first")
        return adapter.dispatch(store, permitted)

    monkeypatch.setitem(OA.ADAPTERS, vendor.channel, replace(adapter, dispatch=dispatch_after_policy_change))
    assert _attempt(store) == "waiting_approval"
    held = _row(store, row["id"])
    assert held["state"] == "waiting_approval" and held["approval_id"]
    assert held["claim_token"] is None and held["provider_attempted_at"] is None and held["attempts"] == 0
    assert OA.claim(store) is None and vendor.requests == []
    assert _approve(api, row["id"]) == json.loads(row["payload"])
    assert _attempt(store) == "done"
    assert len(vendor.requests) == vendor.accepted == 1


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


@pytest.mark.parametrize(
    ("base", "accepted"),
    [("https://API.Close.com", True), ("https://api.close.com.:443", True), ("https://api.close.com:8443", False)],
)
def test_the_queue_takes_the_providers_host_however_spelled_but_only_on_its_own_port(
    api, store, monkeypatch, base, accepted
):
    vendor = _make(api, monkeypatch, "close_sms")
    _policy(api, vendor)
    with store.transaction() as c:
        c.execute("UPDATE tools SET base_url=? WHERE id=?", (base, vendor.tool))
    if accepted:
        _queue(store, vendor, f"spelled-{base}")
    else:
        with pytest.raises(OA.Problem) as refused:
            _queue(store, vendor, f"spelled-{base}")
        assert refused.value.status == 422 and vendor.requests == []


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


def test_twilio_grouped_account_sid_in_credentials_is_frozen_and_used(api, store, monkeypatch):
    vendor = _make(api, monkeypatch, "sms", template=True, grouped=True)
    row = _queue(store, vendor)
    assert _approve(api, row["id"])["account_sid"] == ACCOUNT
    assert _attempt(store) == "done" and vendor.accepted == 1


def test_vault_unavailable_at_dispatch_requeues_the_frozen_text(api, store, monkeypatch):
    vendor = _make(api, monkeypatch, "sms", template=True, grouped=True)
    row = _queue(store, vendor)
    _approve(api, row["id"])

    def unavailable(c, credential):
        raise OA.Problem("vault_unavailable", "synthetic vault error", 503)

    with monkeypatch.context() as patch:
        patch.setattr(api.app.state.vault.cipher, "decrypt", unavailable)
        assert _attempt(store) == "requeued"
    after = _row(store, row["id"])
    assert after["provider_attempted_at"] is None and after["payload"] == row["payload"]
    assert "synthetic vault error" not in after["last_error"] and vendor.requests == []
    assert _attempt(store, after["next_attempt_at"]) == "done" and vendor.accepted == 1


def test_twilio_accepts_a_receipt_echoing_the_largest_text(api, store, monkeypatch):
    vendor = _make(api, monkeypatch, "sms")
    vendor.body = "👋" * 1_600
    _policy(api, vendor)
    row = _queue(store, vendor)
    assert _attempt(store) == "done"
    assert json.loads(_row(store, row["id"])["receipt"]) == {"provider_id": SMS_ID}

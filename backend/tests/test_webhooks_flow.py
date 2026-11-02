"""Webhooks end to end, across the pieces built apart: a real call (direct or through the Gateway relay) reaches a real
rule, the rule wakes a bot, and that bot may then read the stored call."""
import base64
import json
from types import SimpleNamespace

from backend import hook_relay
from backend import hubdb as H
from backend.tests.test_api import api, get, post  # noqa: F401
from backend.tests.test_event_rules import rule as rule_row
from backend.tests.test_triggers import hook
from backend.tests.test_triggers_incoming import as_bot
from backend.tests.test_wake_rules import jobs, setup
from backend.tests.test_webhooks import net  # noqa: F401


def wake_rule(api, **when):
    """A stored `webhook.received` rule that wakes cpo (rules made today live in automations; the engine still reads
    these rows)."""
    return rule_row(api, by="human:ana", then="action", when=when,
                    params={"action": "wake_bot", "bot": "cpo", "text": "Order {{data.order}} ({{type}})"})["rule"]


def test_a_direct_call_wakes_the_bot_a_rule_names_and_that_bot_may_read_the_call(api):
    setup(api)
    made = post(api, "triggers", {"name": "Shop"})["trigger"]
    post(api, "triggers/shop", {"type_from": "/kind"})
    rule = wake_rule(api, trigger="shop", type="order.*")
    with api.app.state.store.transaction() as c:
        c.execute("DELETE FROM jobs")
    got = hook(api, made["url"], b'{"kind": "order.created", "order": 42}', {"Webhook-Id": "w1"})
    assert got.status_code == 202 and got.json()["rules"] == 1 and got.json()["type"] == "order.created"
    with api.app.state.store.read() as c:
        assert jobs(c, "cpo") == 1
        message = c.execute("SELECT * FROM messages WHERE to_actor='bot:cpo' ORDER BY rowid DESC LIMIT 1").fetchone()
    refs = json.loads(message["refs"])
    assert message["from_actor"] == H.KEEPER and "Order 42 (order.created)" in message["body"]
    assert refs["delivery"] == "w1" and refs["rule_id"] == rule["id"] and refs["via"] == "direct"
    as_bot(api, "cpo")
    read = get(api, "triggers/shop/deliveries/w1", token="cpo-test")
    assert "\"order\": 42" in read["delivery"]["content"]
    as_bot(api, "ops")
    get(api, "triggers/shop/deliveries/w1", token="ops-test", expected=404)        # not given this call
    # a refund is no order: the rule's type pattern keeps it quiet, and a retry of w1 wakes nobody again
    hook(api, made["url"], b'{"kind": "refund.created"}', {"Webhook-Id": "w2"})
    assert hook(api, made["url"], b'{"kind": "order.created", "order": 42}', {"Webhook-Id": "w1"}).status_code == 200
    with api.app.state.store.read() as c:
        assert jobs(c, "cpo") == 1


def test_a_call_through_the_gateway_relay_runs_the_same_rule(api):
    setup(api)
    post(api, "triggers", {"name": "Shop"})
    wake_rule(api, trigger="shop")
    with api.app.state.store.transaction() as c:
        c.execute("DELETE FROM jobs")
        c.execute("INSERT INTO trigger_relays(relay_id,trigger_id,address_prefix,created_by,created_at) VALUES(?,?,?,?,?)",
                  ("hr_0123456789abcdef", "shop", "https://api.tico.dev/v1/hooks/hr_0123456789abcdef", "human:ana",
                   H.now()))
    relay = hook_relay.HookRelay(api.app.state.store, api.app.state.store.settings,
                                 SimpleNamespace(state=api.app.state))
    item = {"hq_seq": 1, "relay_id": "hr_0123456789abcdef", "trigger_id": "shop", "delivery_id": "g1",
            "received_at": "2026-10-25T10:00:00+00:00", "headers": {"content-type": "application/json"},
            "body_b64": base64.b64encode(b'{"order": 7}').decode()}
    assert relay.take(item, 0) == 1
    assert relay.take({**item, "hq_seq": 2}, 1) == 2                   # HQ sending it twice wakes once
    with api.app.state.store.read() as c:
        assert jobs(c, "cpo") == 1
        refs = json.loads(c.execute("SELECT refs FROM messages WHERE to_actor='bot:cpo' ORDER BY rowid DESC "
                                    "LIMIT 1").fetchone()[0])
        logged = c.execute("SELECT via, received_at FROM trigger_deliveries WHERE delivery='g1'").fetchone()
    assert refs["via"] == "gateway" and refs["delivery"] == "g1"
    assert get(api, "triggers/shop")["trigger"]["relay"]["address_prefix"].endswith("hr_0123456789abcdef")
    assert logged["via"] == "gateway" and logged["received_at"] == "2026-10-25T10:00:00.000000Z"


def test_a_rule_that_ignores_a_call_heard_it_so_its_maker_gets_no_unheard_note(api):
    made = post(api, "triggers", {"name": "Shop"})["trigger"]
    post(api, "triggers/shop", {"unheard": "note"})
    rule_row(api, by="human:ana", then="ignore", when={"trigger": "shop"})
    assert hook(api, made["url"], b'{"kind": "ping"}', {"Webhook-Id": "p1"}).status_code == 202
    with api.app.state.store.read() as c:
        assert not c.execute("SELECT 1 FROM messages WHERE refs LIKE '%unheard%'").fetchone()


def test_a_call_a_rule_forwards_reaches_the_outgoing_endpoint_signed(api, net):
    from backend import webhook_delivery as D
    from backend.tests.test_webhooks import make, run, verify
    made = post(api, "triggers", {"name": "Shop"})["trigger"]
    endpoint = make(api, events=["custom.*"], token="ana-test")
    rule_row(api, by="human:ana", then="action", when={"trigger": "shop"},
             params={"action": "send_webhook", "webhook": endpoint["id"], "type": "custom.order",
                     "data": {"order": "{{data.order}}", "from": "{{trigger.name}}"}})
    got = hook(api, made["url"], b'{"order": 42}', {"Webhook-Id": "w9"}).json()
    assert got["rules"] == 1
    run(api, net)
    assert len(net.seen) == 1
    sent = net.seen[0]
    body = json.loads(sent.content)
    assert body["type"] == "custom.order" and body["data"] == {"order": "42", "from": "Shop"}
    assert verify(endpoint["secret"], {k.lower(): v for k, v in sent.headers.items()}, sent.content)
    assert hook(api, made["url"], b'{"order": 42}', {"Webhook-Id": "w9"}).status_code == 200    # a retry sends nothing new
    run(api, net)
    assert len(net.seen) == 1 and D.MAX_ATTEMPTS == 8


def test_a_signed_call_through_the_gateway_relay_is_checked_accepted_and_judged_with_the_vault(api):
    """The relay once called receive() with no vault, so every signed preset was refused as vault_unavailable and still
    acknowledged to HQ: valid deliveries never reached a rule."""
    import hashlib
    import hmac
    setup(api)
    post(api, "triggers", {"name": "Shop"})
    key = "k" * 24
    post(api, "triggers/shop/signing", {"preset": "github", "secret": key})
    wake_rule(api, trigger="shop")
    with api.app.state.store.transaction() as c:
        c.execute("DELETE FROM jobs")
        c.execute("INSERT INTO trigger_relays(relay_id,trigger_id,address_prefix,created_by,created_at) VALUES(?,?,?,?,?)",
                  ("hr_0123456789abcdef", "shop", "https://api.tico.dev/v1/hooks/hr_0123456789abcdef", "human:ana", H.now()))
    relay = hook_relay.HookRelay(api.app.state.store, api.app.state.store.settings, SimpleNamespace(state=api.app.state),
                                 api.app.state.auth)
    body = b'{"order": 8}'
    signed = "sha256=" + hmac.new(key.encode(), body, hashlib.sha256).hexdigest()

    def item(seq, delivery, signature):
        return {"hq_seq": seq, "relay_id": "hr_0123456789abcdef", "trigger_id": "shop", "delivery_id": delivery,
                "received_at": H.now(), "body_b64": base64.b64encode(body).decode(),
                "headers": {"content-type": "application/json", "x-hub-signature-256": signature}}
    assert relay.take(item(1, "s1", "sha256=" + "0" * 64), 0) == 1             # a bad signature is still refused
    with api.app.state.store.read() as c:
        assert jobs(c, "cpo") == 0
        assert c.execute("SELECT outcome,reason FROM trigger_deliveries WHERE sent_id='s1' OR delivery='s1'").fetchone()[0] == "refused"
    assert relay.take(item(2, "s2", signed), 1) == 2
    with api.app.state.store.read() as c:
        row = c.execute("SELECT outcome,rules FROM trigger_deliveries WHERE delivery='s2'").fetchone()
        assert (row["outcome"], row["rules"]) == ("accepted", 1) and jobs(c, "cpo") == 1


def test_one_send_rule_for_two_triggers_sends_each_call_even_when_both_carry_the_same_delivery_id(api, net):
    from backend.tests.test_webhooks import make, run
    first = post(api, "triggers", {"name": "Shop"})["trigger"]
    second = post(api, "triggers", {"name": "Blog"})["trigger"]
    endpoint = make(api, events=["custom.*"], token="ana-test")
    rule_row(api, by="human:ana", then="action",
             params={"action": "send_webhook", "webhook": endpoint["id"], "type": "custom.seen",
                     "data": {"from": "{{trigger.name}}"}})
    assert hook(api, first["url"], b"{}", {"Webhook-Id": "1"}).json()["rules"] == 1
    assert hook(api, second["url"], b"{}", {"Webhook-Id": "1"}).json()["rules"] == 1
    run(api, net)
    assert sorted(json.loads(r.content)["data"]["from"] for r in net.seen) == ["Blog", "Shop"]

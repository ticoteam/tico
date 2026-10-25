"""Webhooks v2b: recovering refused calls, the slack preset, routine templates, the create_task and forward rule actions,
endpoint filters and custom headers, replay missing, recover failed, and the automatic turn-off."""
import hashlib
import hmac
import json

import httpx
import pytest

from backend import hubdb as H
from backend import rules as R
from backend import trigger_presets as P
from backend import webhooks as W
from backend.store import Problem
from backend.tests.test_api import api, get, post  # noqa: F401
from backend.tests.test_event_rules import CALL, judge, rule
from backend.tests.test_task_rules import department
from backend.tests.test_triggers import hook, sig
from backend.tests.test_triggers_incoming import as_bot, routine, stored_rows
from backend.tests.test_wake_rules import setup
from backend.tests.test_webhooks import due, make, net, publish, run, sends, verify  # noqa: F401

# Slack's published request-signing example (api.slack.com/authentication/verifying-requests-from-slack): its
# secret, timestamp, form body and signature.
SLACK_SECRET, SLACK_TS = "8f742231b10e8888abcd99yyyzzz85a5", 1531420618
SLACK_BODY = (b"token=xyzz0WbapA4vBCDEFasx0q6G&team_id=T1DC2JH3J&team_domain=testteamnow&channel_id=G8PSS9T3V&channel_name=foobar"
              b"&user_id=U2CERLKJA&user_name=roadrunner&command=%2Fwebhook-collect&text=&response_url=https%3A%2F%2Fhooks.slack.com"
              b"%2Fcommands%2FT1DC2JH3J%2F397700885554%2F96rGlfmibIGlgcZRskXaIFfN&trigger_id=398738663015.47445629121.803a0bc887a14d10d2c447fce8b6703c")
SLACK_V0 = "v0=a2114d57b48eac39b9ad189dd8316235a7b4a8d21a10bd27519666489c69b503"
SLACK_HEADERS = {"x-slack-signature": SLACK_V0, "x-slack-request-timestamp": str(SLACK_TS)}


def slack_headers(secret, body, stamp):
    """Slack's scheme written out again, apart from the code under test."""
    signed = hmac.new(secret.encode(), f"v0:{stamp}:".encode() + body, hashlib.sha256).hexdigest()
    return {"X-Slack-Signature": "v0=" + signed, "X-Slack-Request-Timestamp": str(stamp)}


def test_slack_checks_v0_over_its_example_and_answers_the_challenge_without_counting_a_delivery(api):
    now = SLACK_TS + 10
    assert P.verify("slack", SLACK_HEADERS, SLACK_BODY, SLACK_SECRET, now) == (True, "")
    assert slack_headers(SLACK_SECRET, SLACK_BODY, SLACK_TS)["X-Slack-Signature"] == SLACK_V0
    assert not P.verify("slack", SLACK_HEADERS, SLACK_BODY + b"x", SLACK_SECRET, now)[0]
    assert not P.verify("slack", SLACK_HEADERS, SLACK_BODY, SLACK_SECRET, SLACK_TS + 301)[0]       # past the five minutes
    assert not P.verify("slack", SLACK_HEADERS, SLACK_BODY, SLACK_SECRET, SLACK_TS - 301)[0]
    assert P.verify("slack", SLACK_HEADERS, SLACK_BODY, SLACK_SECRET, SLACK_TS + 299)[0]
    assert not P.verify("slack", {"x-slack-signature": SLACK_V0}, SLACK_BODY, SLACK_SECRET, now)[0]   # no timestamp

    import time
    made = post(api, "triggers", {"name": "Chat"})["trigger"]
    routine(api, "webhook.chat")
    post(api, "triggers/chat/signing", {"preset": "slack", "secret": "slack-signing-secret-0123"})
    key = "slack-signing-secret-0123"
    stamp = int(time.time())
    handshake = json.dumps({"type": "url_verification", "challenge": "3eZbrw1aBm2rZgRNFdxV2595E9CY3gmdALWMmHkvFXO7tYXAYM8P"}).encode()
    got = hook(api, made["url"], handshake, slack_headers(key, handshake, stamp))
    assert got.status_code == 200 and got.json() == {"challenge": "3eZbrw1aBm2rZgRNFdxV2595E9CY3gmdALWMmHkvFXO7tYXAYM8P"}
    assert hook(api, made["url"], handshake, slack_headers("wrong-secret-0123456789", handshake, stamp)).status_code == 401
    assert stored_rows(api, "SELECT count(*) FROM trigger_seen")[0][0] == 0
    assert [t for t in get(api, "tasks?owner=bot:ops")["tasks"] if t.get("routine_id")] == []
    event = json.dumps({"type": "event_callback", "event": {"type": "app_mention", "text": "hi"}}).encode()
    first = hook(api, made["url"], event, {**slack_headers(key, event, stamp), "X-Slack-Retry-Num": "0"})
    assert first.status_code == 202 and first.json()["type"] == "app_mention" and first.json()["tasks"] == 1
    other = json.dumps({"type": "app_rate_limited"}).encode()
    assert hook(api, made["url"], other, slack_headers(key, other, stamp)).json()["type"] == "app_rate_limited"


def test_recover_runs_refused_calls_again_once_and_the_senders_retry_is_a_duplicate(api):
    made = post(api, "triggers", {"name": "Shop"})["trigger"]
    routine(api, "webhook.shop")
    post(api, "triggers/shop/signing", {"preset": "hmac", "secret": "a" * 20})
    body = b'{"order": 1}'
    typo = {"X-Hub-Signature-256": sig("b" * 20, body), "Webhook-Id": "w1"}
    assert hook(api, made["url"], body, typo).status_code == 401
    assert hook(api, made["url"], b'{"order": 2}', {"X-Hub-Signature-256": sig("c" * 20, b'{"order": 2}'), "Webhook-Id": "w2"}).status_code == 401
    log = {d["sent_id"]: d for d in get(api, "triggers/shop/deliveries")["deliveries"]}       # a refused row is named by the call's own id
    assert log["w1"]["outcome"] == "refused" and log["w1"]["stored"] is True
    shown = get(api, "triggers/shop/deliveries/" + log["w1"]["delivery"])["delivery"]
    assert shown["headers"]["x-hub-signature-256"] == "[redacted]" and '"order": 1' in shown["body"]

    post(api, "triggers/shop/signing", {"preset": "hmac", "secret": "b" * 20})          # Tico had the typo
    as_bot(api, "ops")
    post(api, "triggers/shop/recover", {}, token="ops-test", expected=403)
    post(api, "triggers/shop/recover", {}, token="cara-test", expected=403)
    done = post(api, "triggers/shop/recover", {"since_hours": 24})["recover"]
    assert (done["found"], done["recovered"], done["still_refused"]) == (2, 1, 1)
    assert len([t for t in get(api, "tasks?owner=bot:ops")["tasks"] if t.get("routine_id")]) == 1
    retry = hook(api, made["url"], body, typo)                                            # the sender tries w1 again
    assert retry.status_code == 200 and retry.json()["duplicate"] is True
    assert len([t for t in get(api, "tasks?owner=bot:ops")["tasks"] if t.get("routine_id")]) == 1
    again = post(api, "triggers/shop/recover", {"since_hours": 24})["recover"]
    assert (again["found"], again["recovered"], again["still_refused"]) == (1, 0, 1)      # only w2 is left, and still bad
    assert stored_rows(api, "SELECT detail_json FROM events WHERE action='trigger.recovered' ORDER BY ts")[0][0].count("recovered") == 1
    post(api, "triggers/_tico/recover", {}, expected=422)                                   # the built-in trigger takes no edits


def test_a_routine_playbook_reads_the_call_and_one_without_braces_is_left_alone(api):
    made = post(api, "triggers", {"name": "Shop"})["trigger"]
    post(api, "triggers/shop", {"type_from": "/kind"})
    routine(api, "webhook.shop", key="orders")
    post(api, "bots/ops/routines", {"key": "plain", "title": "Plain", "on": "webhook.shop", "text": "Ship it."})
    post(api, "bots/ops/routines", {"key": "filled", "title": "Filled", "on": "webhook.shop",
                                    "text": "Order {{payload.order}} ({{type}}) from {{trigger.name}}: {{payload.note}} {{nope}}"})
    hook(api, made["url"], b'{"kind": "order.created", "order": 42, "note": "{{trigger.name}}"}', {"Webhook-Id": "t1"})
    bodies = [get(api, "tasks/" + t["id"])["task"]["body"] for t in get(api, "tasks?owner=bot:ops")["tasks"] if t.get("routine_id")]
    filled = next(b for b in bodies if b.startswith("Order 42"))
    assert filled.startswith("Order 42 (order.created) from Shop: {{trigger.name}}\n\nevent: webhook.shop") and '"order": 42' in filled
    assert any(b.startswith("Ship it.") for b in bodies)


def forward_rule(api, endpoint, **more):
    return post(api, "rules", {"scope_kind": "team", "event": "webhook.received", "then": "action", "when": {"trigger": "shop"},
                               "params": {"action": "forward", "webhook": endpoint}, **more})["rule"]


def test_forward_sends_the_incoming_payload_on_untouched_and_signed(api, net):
    made = post(api, "triggers", {"name": "Shop"})["trigger"]
    endpoint = make(api, events=["custom.*"])
    post(api, "rules", {"scope_kind": "team", "event": "status_or_relation", "then": "action",
                        "params": {"action": "forward", "webhook": endpoint["id"]}}, expected=422)          # not a call
    post(api, "rules", {"scope_kind": "team", "event": "release.published", "then": "action",
                        "params": {"action": "forward", "webhook": endpoint["id"]}}, expected=422)           # only webhook.received
    post(api, "rules", {"scope_kind": "team", "event": "webhook.received", "then": "action",
                        "params": {"action": "forward", "webhook": endpoint["id"]}}, token="cara-test", expected=403)
    forward_rule(api, endpoint["id"])
    sent = hook(api, made["url"], b'{"order": 42, "note": "{{data.order}}"}', {"Webhook-Id": "f1"})
    assert sent.status_code == 202 and sent.json()["rules"] == 1
    assert run(api, net) == 1
    seen = net.seen[0]
    envelope = json.loads(seen.content)
    assert envelope["type"] == "custom.forward.shop" and envelope["data"] == {"order": 42, "note": "{{data.order}}"}
    assert verify(endpoint["secret"], {k.lower(): v for k, v in seen.headers.items()}, seen.content)
    assert hook(api, made["url"], b'{"order": 42, "note": "{{data.order}}"}', {"Webhook-Id": "f1"}).status_code == 200
    assert run(api, net) == 0                                                                      # a retry sends nothing


def test_create_task_files_a_templated_task_once_and_is_written_only_by_someone_who_could_assign(api):
    setup(api)
    params = {"action": "create_task", "owner": "cpo", "title": "Order {{data.order}} for {{data.customer.name}}",
              "body": "Look at {{type}}."}
    made = rule(api, then="action", params=params)["rule"]
    done = judge(api)
    assert [(d["rule_id"], d["outcome"], d["target"], d["ok"]) for d in done] == [(made["id"], "create_task", "cpo", True)]
    assert judge(api) == []                                                                       # the same call, once
    tasks = [t for t in get(api, "tasks?owner=bot:cpo")["tasks"] if t["title"].startswith("Order 42")]
    assert len(tasks) == 1 and tasks[0]["title"] == "Order 42 for Buyer"
    body = get(api, "tasks/" + tasks[0]["id"])["task"]["body"]
    assert body == "Look at order.created."                     # nothing was cut: no line about the rest
    assert get(api, "tasks/" + tasks[0]["id"])["task"]["requester"] == "human:ben"          # in the rule maker's name

    base = {"scope_kind": "team", "event": "webhook.received", "then": "action"}
    post(api, "rules", {**base, "params": {**params, "owner": "nobody"}}, expected=404)
    post(api, "rules", {**base, "params": {**params, "owner": "{{data.bot}}"}}, expected=422)       # a bot named now, not a template
    post(api, "rules", {**base, "params": {**params, "title": ""}}, expected=422)
    post(api, "rules", {**base, "event": "status_or_relation", "params": params}, expected=422)       # not for a message
    class Denied:
        def target(self, *args, **kwargs):
            raise Problem("not_found", "Unknown recipient", 404)
    with api.app.state.store.read() as c, pytest.raises(Problem) as refused:
        R._action_writer(c, Denied(), api.app.state.store.settings.test_identities["ben-test"], "action", params)
    assert refused.value.status == 403 and "cpo" in refused.value.detail


def test_an_endpoint_filter_keeps_other_departments_and_bots_out_and_leaves_company_events_alone(api, net):
    department(api)
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE bot_config SET team='engineering',config_json=json_set(config_json,'$.team','engineering') WHERE bot='ops'")
    everyone = make(api, events=["task.created", "custom.*"])
    eng = make(api, events=["task.created", "custom.*"], filter={"departments": ["engineering"]})
    cpo = make(api, events=["task.created"], filter={"bots": ["cpo"]})
    post_w = __import__("backend.tests.test_webhooks", fromlist=["post"]).post
    post_w(api, "webhooks", {"name": "x", "url": "https://hooks.acme.example/x", "filter": {"departments": ["astrology"]}}, expected=422)
    post_w(api, "webhooks", {"name": "x", "url": "https://hooks.acme.example/x", "filter": {"task_types": ["no-such-type"]}}, expected=422)
    assert eng["filter"] == {"departments": ["engineering"], "bots": [], "task_types": []}
    with api.app.state.store.transaction() as c:
        mine = H.task_create(c, "human:ana", "Engineering job", "x", "bot:ops", lint=False)
        other = H.task_create(c, "human:ana", "Other job", "x", "bot:cpo", lint=False)
        W.publish(c, "custom.note", "s1", {"x": 1}, "human:ana")
    heard = {name: {s["subject"] for s in sends(api, hook["id"])} for name, hook in (("all", everyone), ("eng", eng), ("cpo", cpo))}
    assert heard["all"] == {mine["id"], other["id"], "s1"}
    assert heard["eng"] == {mine["id"], "s1"}              # the other department's task is out; a company event passes
    assert heard["cpo"] == {other["id"]}
    updated = post_w(api, f"webhooks/{eng['id']}/update", {"filter": {}})["webhook"]
    assert updated["filter"] == {"departments": [], "bots": [], "task_types": []}


def test_replay_missing_queues_exactly_the_events_an_endpoint_lacks(api, net):
    first = make(api, events=["custom.*"])
    publish(api, "custom.a", "s1", {"n": 1}, "human:ana")
    publish(api, "custom.b", "s2", {"n": 2}, "human:ana")
    publish(api, "custom.a", "s3", {"n": 3}, "human:ana")
    with api.app.state.store.transaction() as c:
        W.publish(c, "custom.a", "s4", {"n": 4}, "human:ana", only=first["id"])            # a one-off to the first endpoint
    late = make(api, events=["custom.a"])
    post_w = __import__("backend.tests.test_webhooks", fromlist=["post"]).post
    assert sends(api, late["id"]) == []
    done = post_w(api, f"webhooks/{late['id']}/replay", {"since": "2020-01-01"})
    assert done["queued"] == 2 and done["more"] is False
    assert sorted(s["subject"] for s in sends(api, late["id"])) == ["s1", "s3"]
    assert post_w(api, f"webhooks/{late['id']}/replay", {"since": "2020-01-01"})["queued"] == 0       # nothing is missing now
    assert len(sends(api, first["id"])) == 4
    post_w(api, f"webhooks/{late['id']}/replay", {"since": "last week"}, expected=422)
    post_w(api, f"webhooks/{late['id']}/pause", {})
    post_w(api, f"webhooks/{late['id']}/replay", {"since": "2020-01-01"}, expected=409)
    run(api, net)


def test_replay_missing_moves_past_events_the_endpoint_does_not_want_so_the_one_it_does_is_reached(api, net, monkeypatch):
    """The 500-event window used to be cut before the subscription test, so enough unwanted events in front of a wanted
    one left every round at queued=0, more=True for good."""
    monkeypatch.setattr(W, "REPLAY_BATCH", 3)
    post_w = __import__("backend.tests.test_webhooks", fromlist=["post"]).post
    make(api, events=["custom.*"])
    for i in range(7):
        publish(api, "custom.b", f"b{i}", {"n": i}, "human:ana")
    publish(api, "custom.a", "wanted", {"n": 1}, "human:ana")
    late = make(api, events=["custom.a"])
    body, queued, rounds = {"since": "2020-01-01"}, 0, 0
    while True:
        done = post_w(api, f"webhooks/{late['id']}/replay", body)
        queued, rounds = queued + done["queued"], rounds + 1
        if not done["more"]:
            break
        assert done["next_since"] and done["next_after"] and rounds < 10
        body = {"since": done["next_since"], "after": done["next_after"]}
    assert queued == 1 and rounds == 3 and [x["subject"] for x in sends(api, late["id"])] == ["wanted"]


def test_since_with_an_offset_means_that_moment_in_utc():
    assert W._since("2026-10-25T12:00:00+09:00") == "2026-10-25T03:00:00.000000Z"
    assert W._since("2026-10-25T12:00:00Z") == "2026-10-25T12:00:00.000000Z"
    assert W._since("2026-10-25T12:00:00") == "2026-10-25T12:00:00.000000Z"       # no zone: UTC, as the stamps are
    assert W._since("2026-10-25") == "2026-10-25T00:00:00.000000Z"


def test_five_days_of_failing_turn_an_endpoint_off_but_thirteen_hours_after_a_quiet_spell_do_not(api, net):
    post_w = __import__("backend.tests.test_webhooks", fromlist=["post"]).post
    hook_ = make(api, events=["custom.*"])
    publish(api, "custom.a", "s1", {"n": 1}, "human:ana")
    send = sends(api, hook_["id"])[0]

    def failing(since, last):
        """The endpoint began failing at `since`; its latest attempt was at `last`."""
        with api.app.state.store.transaction() as c:
            c.execute("DELETE FROM webhook_attempts")
            c.execute("UPDATE webhooks SET failing_since=? WHERE id=?", (since, hook_["id"]))
            c.execute("UPDATE webhook_sends SET state='failed', next_attempt=? WHERE id=?", (since, send["id"]))
            c.execute("INSERT INTO webhook_attempts(send_id,webhook_id,n,at,timestamp_sent,status,ms) VALUES(?,?,1,?,0,500,0)",
                      (send["id"], hook_["id"], last))
            return W.auto_disable(c)
    now = H.now()
    assert failing(H.shift(now, hours=-13), now) == []         # quiet for days, then failing for 13 hours: still on
    assert failing(H.shift(now, days=-5, hours=1), now) == []   # a run just under five days
    assert failing(H.shift(now, days=-5, hours=-1), H.shift(now, days=-5, hours=-1, minutes=1)) == []    # one burst
    assert failing(H.shift(now, days=-5, hours=-1), now) == [hook_["id"]]
    shown = post_w(api, f"webhooks/{hook_['id']}/update", {})["webhook"]
    assert shown["enabled"] is False and shown["disabled_reason"] and shown["disabled_at"]
    with api.app.state.store.transaction() as c:
        assert W.auto_disable(c) == []                          # said once
        assert W.health_summary(c)["disabled"] == ["Acme hook"]
        notes = [t for t in c.execute("SELECT * FROM tasks WHERE title LIKE 'Webhook % was turned off'")]
    assert len(notes) == 1 and notes[0]["owner"] == "human:ana"
    assert sends(api, hook_["id"])[0]["state"] == "failed"
    post_w(api, f"webhooks/{hook_['id']}/recover", {"since": "2020-01-01"}, expected=409)          # on first
    enabled = post_w(api, f"webhooks/{hook_['id']}/enable", {})
    assert enabled["webhook"]["enabled"] is True and enabled["webhook"]["disabled_at"] is None
    assert enabled["recover"]["failed"] == 1
    got = post_w(api, f"webhooks/{hook_['id']}/recover", {"since": enabled["recover"]["since"]})
    assert got["recovered"] == 1
    assert run(api, net) == 1 and sends(api, hook_["id"])[0]["state"] == "delivered"


def test_custom_headers_go_out_and_a_secret_one_is_never_shown_again(api, net):
    post_w = __import__("backend.tests.test_webhooks", fromlist=["post"]).post
    made = make(api, events=["custom.*"], headers=[{"name": "X-Api-Key", "value": "s3cret-value-123", "secret": True},
                                                  {"name": "X-Env", "value": "prod"}])
    assert made["headers"] == [{"name": "x-api-key", "secret": True, "set": True}, {"name": "x-env", "secret": False, "value": "prod"}]
    assert "s3cret-value-123" not in json.dumps(made | {"secret": ""})
    for shown in (post_w(api, "webhooks/" + made["id"] + "/update", {"name": "Renamed"})["webhook"],
                  post_w(api, "webhooks/" + made["id"] + "/update", {"headers": [{"name": "x-api-key", "secret": True},
                                                                               {"name": "x-env", "value": "stage"}]})["webhook"]):
        assert shown["headers"][0] == {"name": "x-api-key", "secret": True, "set": True}
    assert "s3cret-value-123" not in json.dumps(get(api, "webhooks/" + made["id"])) + json.dumps(get(api, "webhooks"))
    with api.app.state.store.read() as c:
        assert "s3cret-value-123" not in c.execute("SELECT headers_json FROM webhooks").fetchone()[0]
        assert "s3cret-value-123" not in " ".join(r["detail_json"] for r in c.execute("SELECT detail_json FROM events"))
    post_w(api, "webhooks/" + made["id"] + "/example", {"type": "custom.example"})
    assert run(api, net) == 1
    wire = {k.lower(): v for k, v in net.seen[0].headers.items()}
    assert wire["x-api-key"] == "s3cret-value-123" and wire["x-env"] == "stage" and wire["content-type"] == "application/json"
    for bad in ("webhook-id", "Webhook-Signature", "Host", "content-length", "Content-Type", "transfer-encoding", "connection", "x bad"):
        post_w(api, "webhooks/" + made["id"] + "/update", {"headers": [{"name": bad, "value": "v"}]}, expected=422)
    post_w(api, "webhooks/" + made["id"] + "/update", {"headers": [{"name": "x-a", "value": "one\r\nx-b: two"}]}, expected=422)
    post_w(api, "webhooks/" + made["id"] + "/update", {"headers": [{"name": "x-a", "secret": True}]}, expected=422)   # no value to keep
    post_w(api, "webhooks/" + made["id"] + "/update", {"headers": [{"name": f"x-{n}", "value": "v"} for n in range(11)]}, expected=422)
    post_w(api, "webhooks/" + made["id"] + "/update", {"headers": []})
    assert get(api, "webhooks/" + made["id"])["webhook"]["headers"] == []


def test_failing_since_starts_at_the_first_failure_and_any_success_or_enabling_clears_it(api, net):
    post_w = __import__("backend.tests.test_webhooks", fromlist=["post"]).post
    hook_ = make(api, events=["custom.*"])

    def since():
        with api.app.state.store.read() as c:
            return c.execute("SELECT failing_since FROM webhooks WHERE id=?", (hook_["id"],)).fetchone()[0]
    net.reply = lambda request: httpx.Response(500)
    publish(api, "custom.a", "s1", {"n": 1}, "human:ana")
    run(api, net)
    first = since()
    assert first
    due(api)
    run(api, net)
    assert since() == first                                      # the later failures do not move it
    net.reply = lambda request: httpx.Response(200)
    due(api)
    run(api, net)
    assert since() is None                                       # a 2xx resets the run
    net.reply = lambda request: httpx.Response(500)
    publish(api, "custom.a", "s2", {"n": 2}, "human:ana")
    run(api, net)
    assert since() and since() != first
    post_w(api, f"webhooks/{hook_['id']}/enable", {})
    assert since() is None                                       # so does turning it on


def test_recovering_a_relayed_call_keeps_the_gateways_delivery_id_so_its_retry_is_still_a_duplicate(api):
    """A relayed Stripe call without an id header is named by HQ; recovery once recomputed the id from the body's evt_ id,
    so a later delivery under HQ's id was accepted a second time."""
    import base64
    import time
    from types import SimpleNamespace
    from backend import hook_relay
    post(api, "triggers", {"name": "Shop"})
    routine(api, "webhook.shop")
    post(api, "triggers/shop/signing", {"preset": "stripe", "secret": "t" * 20})
    with api.app.state.store.transaction() as c:
        c.execute("INSERT INTO trigger_relays(relay_id,trigger_id,address_prefix,created_by,created) VALUES(?,?,?,?,?)",
                  ("hr_0123456789abcdef", "shop", "https://api.tico.dev/v1/hooks/hr_0123456789abcdef", "human:ana", H.now()))
    relay = hook_relay.HookRelay(api.app.state.store, api.app.state.store.settings, SimpleNamespace(state=api.app.state),
                                 api.app.state.auth)
    body = b'{"id": "evt_123", "type": "charge.succeeded"}'
    stamp = str(int(time.time()))

    def item(seq, key):
        signed = hmac.new(key.encode(), f"{stamp}.".encode() + body, hashlib.sha256).hexdigest()
        return {"hq_seq": seq, "relay_id": "hr_0123456789abcdef", "trigger_id": "shop", "delivery_id": "hq-1",
                "received_at": H.now(), "body_b64": base64.b64encode(body).decode(),
                "headers": {"content-type": "application/json", "stripe-signature": f"t={stamp},v1={signed}"}}
    assert relay.take(item(1, "w" * 20), 0) == 1                                         # refused: the secret had a typo
    assert post(api, "triggers/shop/recover", {"since_hours": 24})["recover"]["still_refused"] == 1
    post(api, "triggers/shop/signing", {"preset": "stripe", "secret": "w" * 20})          # fixed
    assert post(api, "triggers/shop/recover", {"since_hours": 24})["recover"]["recovered"] == 1
    assert [r[0] for r in stored_rows(api, "SELECT delivery FROM trigger_seen")] == ["hq-1"]
    assert relay.take(item(2, "w" * 20), 1) == 2                                         # HQ's own retry
    assert len([t for t in get(api, "tasks?owner=bot:ops")["tasks"] if t.get("routine_id")]) == 1

"""Incoming webhooks beyond the basics (backend/triggers.py, backend/trigger_presets.py): verification presets,
the dedupe ledger, stored calls and who may read them, replay, settings, and the Gateway door."""
import base64
import hashlib
import hmac
import json
from datetime import datetime, timezone

from backend import trigger_presets as P
from backend import triggers
from backend.auth import Identity
from backend.tests.test_api import api, get, post  # noqa: F401
from backend.tests.test_triggers import hook, listener, rule_row, sig, turns


def stripe_sig(secret, body, t):
    return f"t={t},v1=" + hmac.new(secret.encode(), f"{t}.".encode() + body, hashlib.sha256).hexdigest()


# Standard Webhooks' published vector (the standardwebhooks libraries' tests).
STD_SECRET, STD_ID, STD_TS = "whsec_MfKQ9r8GKYqrTwjUPD8ILPZIo2LaLaSw", "msg_p5jXN8AQM9LWM0D4loKWxJek", 1614265330
STD_BODY, STD_SIG = b'{"test": 2432232314}', "v1,g0hM9SsE+OTPJTGt/tmIKtSyZlE3uFJELVlNIOLJ1OE="
STD_HEADERS = {"webhook-id": STD_ID, "webhook-timestamp": str(STD_TS), "webhook-signature": STD_SIG}
# Stripe's scheme (t=<unix>,v1=<hex of HMAC-SHA256 over "<t>.<body>">), computed for this secret and body.
STRIPE_SECRET, STRIPE_BODY, STRIPE_TS = "whsec_test_secret_0123456789", b'{"id": "evt_1", "type": "invoice.paid"}', 1700000000
STRIPE_V1 = "9bdb4cc8e8ccb4080468c066403a9c586fb880a241613e05dada839d4a7e7eee"       # fixed, so a change to the scheme in code shows up here


def test_standard_webhooks_accepts_its_vector_and_refuses_changes():
    now = STD_TS + 10
    assert P.verify("standard", STD_HEADERS, STD_BODY, STD_SECRET, now) == (True, "")
    assert not P.verify("standard", STD_HEADERS, STD_BODY + b" ", STD_SECRET, now)[0]
    assert not P.verify("standard", STD_HEADERS, STD_BODY, STD_SECRET, STD_TS + 301)[0]       # too old
    assert not P.verify("standard", STD_HEADERS, STD_BODY, STD_SECRET, STD_TS - 301)[0]       # too new
    assert not P.verify("standard", {**STD_HEADERS, "webhook-id": "msg_other"}, STD_BODY, STD_SECRET, now)[0]
    assert not P.verify("standard", STD_HEADERS, STD_BODY, "whsec_" + base64.b64encode(b"another secret key!!").decode(), now)[0]
    several = {**STD_HEADERS, "webhook-signature": "v1,AAAA v2,zzzz " + STD_SIG}               # any v1 may match
    assert P.verify("standard", several, STD_BODY, STD_SECRET, now)[0]
    assert not P.verify("standard", {k: v for k, v in STD_HEADERS.items() if k != "webhook-id"}, STD_BODY, STD_SECRET, now)[0]


def test_stripe_accepts_a_signed_body_and_refuses_changes():
    t = STRIPE_TS
    header = stripe_sig(STRIPE_SECRET, STRIPE_BODY, t)
    assert header == f"t={t},v1={STRIPE_V1}"
    assert P.verify("stripe", {"stripe-signature": header}, STRIPE_BODY, STRIPE_SECRET, t + 5) == (True, "")
    assert not P.verify("stripe", {"stripe-signature": header}, STRIPE_BODY + b"x", STRIPE_SECRET, t + 5)[0]
    assert not P.verify("stripe", {"stripe-signature": header}, STRIPE_BODY, STRIPE_SECRET, t + 301)[0]
    assert not P.verify("stripe", {"stripe-signature": header.replace(f"t={t}", f"t={t + 1}")}, STRIPE_BODY, STRIPE_SECRET, t)[0]
    two = f"t={t},v1={'0' * 64}," + header.split(",", 1)[1] + ",v0=ignored"                  # several v1; one matches
    assert P.verify("stripe", {"stripe-signature": two}, STRIPE_BODY, STRIPE_SECRET, t)[0]
    assert not P.verify("stripe", {}, STRIPE_BODY, STRIPE_SECRET, t)[0]


def test_github_and_hmac_presets():
    body = b'{"zen": "x"}'
    good = {"x-hub-signature-256": sig("k" * 20, body)}
    assert P.verify("github", good, body, "k" * 20, 0)[0] and P.verify("hmac", good, body, "k" * 20, 0)[0]
    generic = {"x-signature": sig("k" * 20, body)[7:]}
    assert P.verify("hmac", generic, body, "k" * 20, 0)[0] and not P.verify("github", generic, body, "k" * 20, 0)[0]
    assert not P.verify("github", {"x-hub-signature-256": sig("k" * 20, body + b"!")}, body, "k" * 20, 0)[0]


def test_type_extraction_and_header_handling():
    body = b'{"type": "order.paid", "data": {"items": [{"kind": "a"}]}}'
    assert P.type_of("/type", {}, body) == "order.paid" and P.type_of("/data/items/0/kind", {}, body) == "a"
    assert P.type_of("/data/items/9", {}, body) == "" and P.type_of("/type", {}, b"not json") == ""
    assert P.type_of("X-GitHub-Event", {"x-github-event": "push"}, body) == "push"
    assert P.redacted({"x-hub-signature-256": "abc", "authorization": "Bearer s", "x-api-key": "k", "content-type": "a/b",
                       "idempotency-key": "i1", "webhook-id": "w1", "set-cookie": "a=b"}) == {
        "x-hub-signature-256": "[redacted]", "authorization": "[redacted]", "x-api-key": "[redacted]", "content-type": "a/b",
        "idempotency-key": "i1", "webhook-id": "w1", "set-cookie": "[redacted]"}
    assert P.redacted({"X-Access-Key": "a", "x-authorization": "b", "X-Auth-Token": "c", "x-client-secret": "d",
                       "Idempotency-Key": "i2", "webhook-id": "w", "x-request-id": "r"}) == {
        "X-Access-Key": "[redacted]", "x-authorization": "[redacted]", "X-Auth-Token": "[redacted]",
        "x-client-secret": "[redacted]", "Idempotency-Key": "i2", "webhook-id": "w", "x-request-id": "r"}
    kept = P.kept_headers({"cookie": "a=b", "authorization": "Bearer secret1", "cf-ray": "1", "connection": "close",
                           "x-event": "e"}, "secret1")
    assert kept == {"x-event": "e"}


def as_bot(api, slug):
    """The bot itself, as an agent credential (backend/tests/test_company.py bot)."""
    from backend.store import H
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE bots SET state='active' WHERE slug=?", (slug,))
        c.execute("INSERT OR IGNORE INTO agents(bot,harness,token_hash,created_at,created_by) "
                  "VALUES(?,'hermes',?,?,'human:ana')", (slug, "x-" + slug, H.now()))
    api.app.state.store.settings.test_identities[slug + "-test"] = Identity("bot:" + slug, "bot", agent="hermes")


def routine(api, trigger="shop", bot="ops"):
    """An automation on the webhook that hands `bot` each call in a Bot turn (what a routine on it was)."""
    return listener(api, trigger, bot, text="Ship it. Type {{webhook.type}}.", name="Order")


def runs(api, aid):
    return get(api, f"automations/{aid}/runs")["runs"]


def stored_rows(api, sql, *args):
    with api.app.state.store.read() as c:
        return c.execute(sql, args).fetchall()


def test_a_sender_id_stays_a_duplicate_after_the_log_and_the_body_are_gone(api, monkeypatch):
    made = post(api, "triggers", {"name": "Shop"})["trigger"]
    aid = routine(api)
    assert hook(api, made["url"], headers={"Webhook-Id": "w1"}).status_code == 202
    monkeypatch.setattr(triggers, "KEEP", 1)
    for i in range(3):                       # w1's log row is pruned
        assert hook(api, made["url"], headers={"Webhook-Id": f"x{i}"}).status_code == 202
    with api.app.state.store.transaction() as c:      # ...and its body has expired
        c.execute("UPDATE trigger_bodies SET expires_at='2000-01-01T00:00:00.000000Z'")
    triggers.sweep(api.app.state.store)
    assert stored_rows(api, "SELECT count(*) FROM trigger_bodies")[0][0] == 0
    again = hook(api, made["url"], headers={"Webhook-Id": "w1"})
    assert again.status_code == 200 and again.json()["duplicate"] is True
    assert len(runs(api, aid)) == 4          # w1, x0, x1, x2: the retry ran nothing
    with api.app.state.store.transaction() as c:      # the ledger forgets after 30 days
        c.execute("UPDATE trigger_seen SET seen_at='2000-01-01T00:00:00.000000Z'")
    triggers.sweep(api.app.state.store)
    assert hook(api, made["url"], headers={"Webhook-Id": "w1"}).status_code == 202       # now a log row is replaced, not an error


def test_a_bare_retry_in_the_same_minute_is_a_duplicate(api):
    made = post(api, "triggers", {"name": "Shop"})["trigger"]
    assert hook(api, made["url"], b'{"a": 1}').status_code == 202
    assert hook(api, made["url"], b'{"a": 1}').status_code == 200
    assert hook(api, made["url"], b'{"a": 2}').status_code == 202


def test_each_preset_verifies_over_http_and_the_type_is_kept(api):
    made = post(api, "triggers", {"name": "Billing"})["trigger"]
    routine(api, "billing")
    post(api, "triggers/billing/signing", {"preset": "standard", "secret": "not-whsec-at-all-xx"}, expected=422)
    got = post(api, "triggers/billing/signing", {"preset": "standard", "secret": STD_SECRET})["trigger"]
    assert got["preset"] == "standard" and get(api, "triggers/billing")["trigger"]["type_from"] == "/type"
    # The vector's timestamp is old, so a real call is refused; a fresh one signed the same way is accepted.
    assert hook(api, made["url"], STD_BODY, STD_HEADERS).status_code == 401
    import time
    stamp = str(int(time.time()))
    body = b'{"type": "invoice.paid"}'
    key = base64.b64decode(STD_SECRET[6:])
    good = {"webhook-id": "m1", "webhook-timestamp": stamp, "webhook-signature": "v1," + base64.b64encode(
        hmac.new(key, f"m1.{stamp}.".encode() + body, hashlib.sha256).digest()).decode()}
    first = hook(api, made["url"], body, good)
    assert first.status_code == 202 and first.json()["type"] == "invoice.paid" and first.json()["delivery"] == "m1"
    assert hook(api, made["url"], body, good).status_code == 200                        # same webhook-id
    log = get(api, "triggers/billing/deliveries")["deliveries"]
    assert [d["type"] for d in log if d["outcome"] == "accepted"] == ["invoice.paid"]
    task = turns(api, "Order")[0]
    assert task["owner"] == "bot:ops" and 'field="webhook.type">\ninvoice.paid\n' in task["body"]

    stripe = post(api, "triggers/billing/signing", {"preset": "stripe", "secret": STRIPE_SECRET})["trigger"]
    assert stripe["preset"] == "stripe"
    now = int(time.time())
    header = stripe_sig(STRIPE_SECRET, STRIPE_BODY, now)
    assert hook(api, made["url"], STRIPE_BODY, {"Stripe-Signature": header}).json()["type"] == "invoice.paid"
    assert hook(api, made["url"], STRIPE_BODY + b" ", {"Stripe-Signature": header}).status_code == 401
    old = stripe_sig(STRIPE_SECRET, STRIPE_BODY, now - 3600)
    assert hook(api, made["url"], STRIPE_BODY, {"Stripe-Signature": old}).status_code == 401
    off = post(api, "triggers/billing/signing", {"off": True})["trigger"]
    assert off["signed"] is False and get(api, "triggers/billing")["trigger"]["preset"] == "none"


def test_a_signed_trigger_made_before_presets_is_checked_as_hmac():
    from backend import hubdb as H
    from backend.triggers_incoming_schema import SCHEMA
    import sqlite3
    from backend.triggers_schema import SCHEMA as T1, SIGNING_SCHEMA
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    for script in (T1, SIGNING_SCHEMA):
        H._apply(conn, script)
    now = "2026-01-01T00:00:00.000000Z"
    conn.execute("INSERT INTO triggers(id,name,secret_hash,created_by,created_at,updated_at,signing_secret) VALUES('a','A','h','human:ana',?,?,'sealed')", (now, now))
    conn.execute("INSERT INTO triggers(id,name,secret_hash,created_by,created_at,updated_at) VALUES('b','B','h','human:ana',?,?)", (now, now))
    conn.execute("INSERT INTO trigger_deliveries(trigger_id,delivery,received_at,outcome) VALUES('a','d1',?,'accepted')", (now,))
    H._apply(conn, SCHEMA)
    assert [r["preset"] for r in conn.execute("SELECT preset FROM triggers ORDER BY id")] == ["hmac", "none"]
    assert conn.execute("SELECT delivery FROM trigger_seen").fetchone()[0] == "d1"       # old ids stay duplicates


def test_the_gateway_door_skips_the_address_secret_and_the_cap_but_not_the_signature(api, monkeypatch):
    made = post(api, "triggers", {"name": "Shop"})["trigger"]
    aid = routine(api)
    key = post(api, "triggers/shop/signing", {})["trigger"]["signing_secret"]
    store, vault, auth = api.app.state.store, api.app.state.vault, api.app.state.auth
    body = b'{"type": "paid"}'
    monkeypatch.setattr(triggers, "HOURLY", 1)

    def through(headers, delivery, **kw):
        with store.transaction() as c:
            return triggers.receive(c, "shop", body, headers, secret=None, via="gateway", delivery_id=delivery,
                                    auth=auth, vault=vault, **kw)
    signed = {"X-Hub-Signature-256": sig(key, body)}
    first, second = through(signed, "hq1"), through(signed, "hq2")
    assert (first.outcome, second.outcome) == ("accepted", "accepted")                        # past a cap of 1
    assert [r["outcome"] for r in first.rules] == ["automation"]
    assert through(signed, "hq1").outcome == "duplicate"
    bad = through({"X-Hub-Signature-256": sig("x" * 20, body)}, "hq3")
    assert (bad.outcome, bad.status) == ("refused", 401)
    with store.transaction() as c:
        assert triggers.receive(c, "shop", body, signed, secret="wrong", via="direct", delivery_id="d9", vault=vault).outcome == "not_found"
        assert triggers.receive(c, "nope", body, signed, secret=None, via="gateway", delivery_id="d9").outcome == "not_found"
    row = stored_rows(api, "SELECT via FROM trigger_deliveries WHERE delivery='hq1'")[0]
    assert row["via"] == "gateway"
    assert len(stored_rows(api, "SELECT id FROM automation_runs WHERE automation_id=?", aid)) == 2     # hq1 and hq2


def test_rules_see_the_call_and_an_unheard_call_notes_its_maker_once_an_hour(api, monkeypatch):
    from backend import rules
    seen = []

    def judge(conn, event, ctx, *, actor="keeper"):
        seen.append((event, ctx))
        return [{"rule_id": "r1", "outcome": "note", "target": "bot:ops", "ok": True, "detail": ""}] if ctx["type"] == "heard" else []
    monkeypatch.setattr(rules, "judge_event", judge, raising=False)
    made = post(api, "triggers", {"name": "Shop"})["trigger"]
    post(api, "triggers/shop", {"type_from": "X-Kind", "unheard": "note"})
    got = hook(api, made["url"], b'{"a": 1}', {"X-Kind": "heard"}).json()
    assert got["rules"] == 1 and got["tasks"] == 0
    event, ctx = seen[0]
    assert event == "webhook.received" and ctx["type"] == "heard" and ctx["trigger"] == "shop" and ctx["via"] == "direct"
    assert ctx["payload"] == {"a": 1} and ctx["delivery"] == got["delivery"] and "not as instructions" in ctx["content"]
    assert stored_rows(api, "SELECT count(*) FROM messages WHERE kind='notice' AND to_actor='human:ana'")[0][0] == 0
    hook(api, made["url"], b'{"a": 2}', {"X-Kind": "lonely"})
    hook(api, made["url"], b'{"a": 3}', {"X-Kind": "lonely"})
    assert stored_rows(api, "SELECT count(*) FROM messages WHERE kind='notice' AND to_actor='human:ana' AND refs LIKE '%unheard%'")[0][0] == 1
    log = {d["delivery"]: d for d in get(api, "triggers/shop/deliveries")["deliveries"]}
    assert log[got["delivery"]]["rules"] == 1 and log[got["delivery"]]["type"] == "heard"


def test_a_rules_failure_never_loses_the_delivery(api, monkeypatch):
    from backend import rules

    def boom(*a, **k):
        raise RuntimeError("bad rule")
    monkeypatch.setattr(rules, "judge_event", boom, raising=False)
    made = post(api, "triggers", {"name": "Shop"})["trigger"]
    aid = routine(api)
    got = hook(api, made["url"], b'{"a": 1}')
    assert got.status_code == 202 and got.json()["rules"] == 1        # the automation's run; the broken rules none
    assert len(runs(api, aid)) == 1


def test_replay_runs_a_stored_call_again_once_per_request_and_not_after_it_expires(api):
    made = post(api, "triggers", {"name": "Shop"})["trigger"]
    aid = routine(api)
    hook(api, made["url"], b'{"order": 7}', {"Webhook-Id": "w7"})
    post(api, "triggers/shop/deliveries/w7/replay", {}, token="cara-test", expected=403)
    one = post(api, "triggers/shop/deliveries/w7/replay", {}, key="rp1")["replay"]
    assert one["delivery"] == "w7:replay:1" and one["rules"] == 1
    assert post(api, "triggers/shop/deliveries/w7/replay", {}, key="rp1")["replay"] == one      # a retried request
    two = post(api, "triggers/shop/deliveries/w7:replay:1/replay", {})["replay"]
    assert two["delivery"] == "w7:replay:2" and two["of"] == "w7"
    assert sorted(r["trigger_ref"] for r in runs(api, aid)) == [
        "webhook:shop:w7", "webhook:shop:w7:replay:1", "webhook:shop:w7:replay:2"]
    assert "replay" in " ".join(d["outcome"] for d in get(api, "triggers/shop/deliveries")["deliveries"])
    assert any(e for e in stored_rows(api, "SELECT 1 FROM events WHERE action='trigger.replayed'"))
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE trigger_bodies SET expires_at='2000-01-01T00:00:00.000000Z'")
    gone = post(api, "triggers/shop/deliveries/w7/replay", {}, expected=410)
    assert gone["error"]["code"] == "expired"
    post(api, "triggers/shop/deliveries/never/replay", {}, expected=404)


def test_who_may_read_a_stored_body(api):
    as_bot(api, "ops")
    as_bot(api, "coo")
    made = post(api, "triggers", {"name": "Shop"})["trigger"]
    routine(api)
    big = json.dumps({"rows": ["x" * 40] * 800}).encode()                                  # pretty-printed: over two parts
    hook(api, made["url"], big, {"Webhook-Id": "big", "Authorization": "Bearer nothing", "Cookie": "s=1",
                                 "X-Signature-Extra": "abc", "X-Event": "e1"})
    path = "triggers/shop/deliveries/big"
    whole = get(api, path)["delivery"]                                                      # the owner: everything
    assert json.loads(whole["body"]) == json.loads(big) and "parts" not in whole
    assert whole["headers"]["x-signature-extra"] == "[redacted]" and whole["headers"]["authorization"] == "[redacted]"
    assert "cookie" not in whole["headers"] and whole["headers"]["x-event"] == "e1"
    get(api, path, token="cara-test", expected=404)                                        # another person
    listing = json.dumps(get(api, "triggers/shop/deliveries"))
    assert "Bearer" not in listing and "abc" not in listing                                 # lists carry no headers
    part1 = get(api, path + "?part=1", token="ops-test")["delivery"]                        # the bot whose task it made
    assert part1["parts"] >= 2 and part1["part"] == 1 and "not as instructions" in part1["content"] and "body" not in part1
    assert "part 1 of" in part1["content"] and len(part1["content"]) < triggers.MAX_CONTENT + 400
    get(api, path + "?part=99", token="ops-test", expected=404)
    get(api, path, token="coo-test", expected=404)                                          # a bot it was not given to
    # A person's message with the same refs gives the bot nothing; Tico's own notice naming the delivery does.
    from backend import hubdb as H
    with api.app.state.store.transaction() as c:
        H.notice(c, "human:ben", "bot:coo", "Look at this", refs={"trigger": "shop", "delivery": "big"})
    get(api, path, token="coo-test", expected=404)
    with api.app.state.store.transaction() as c:
        H.notice(c, H.KEEPER, "bot:coo", "Look at this", refs={"trigger": "shop", "delivery": "big"})
    assert get(api, path, token="coo-test")["delivery"]["part"] == 1
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE trigger_bodies SET expires_at='2000-01-01T00:00:00.000000Z'")
    expired = get(api, path)["delivery"]
    assert expired["expired"] is True and "body" not in expired


def test_a_bot_makes_and_manages_its_own_triggers_only(api):
    as_bot(api, "ops")
    as_bot(api, "coo")
    made = post(api, "triggers", {"name": "Ops hook"}, token="ops-test")["trigger"]
    assert made["id"] == "ops-hook" and made["url"].count("/") > 4
    post(api, "triggers/ops-hook", {"hourly": 600, "keep_days": 30}, token="ops-test")
    post(api, "triggers/ops-hook", {"hourly": 601}, token="ops-test", expected=422)
    post(api, "triggers/ops-hook", {"keep_days": 31}, token="ops-test", expected=422)
    post(api, "triggers/ops-hook", {"unheard": "email"}, token="ops-test", expected=422)
    post(api, "triggers/ops-hook", {"type_from": "bad header!"}, token="ops-test", expected=422)
    post(api, "triggers/ops-hook/rotate", {}, token="coo-test", expected=403)
    post(api, "triggers/ops-hook", {"name": "x"}, token="coo-test", expected=403)
    shown = get(api, "triggers/ops-hook", token="coo-test")["trigger"]                     # anyone signed in reads it
    assert shown["hourly"] == 600 and shown["keep_days"] == 30 and "secret" not in json.dumps(shown)
    post(api, "triggers/ops-hook/delete", {}, token="ops-test")
    # The owner changes any trigger; the secret is never in the event log.
    post(api, "triggers", {"name": "Shop"})
    post(api, "triggers/shop/rotate", {}, token="ops-test", expected=403)
    for row in stored_rows(api, "SELECT detail FROM events WHERE action LIKE 'trigger.%'"):
        assert made["url"].rsplit("/", 1)[1] not in row[0]


def test_the_hourly_cap_is_per_trigger_and_a_retry_after_it_is_still_a_duplicate(api):
    made = post(api, "triggers", {"name": "Shop"})["trigger"]
    post(api, "triggers/shop", {"hourly": 2})
    codes = [hook(api, made["url"], b"{}", {"Webhook-Id": f"c{i}"}).status_code for i in range(3)]
    assert codes == [202, 202, 429]
    assert hook(api, made["url"], b"{}", {"Webhook-Id": "c0"}).status_code == 200            # known before the cap is asked


def test_a_sender_id_that_looks_like_a_replay_still_counts_toward_the_hourly_cap_and_replays_skip_it(api):
    made = post(api, "triggers", {"name": "Shop"})["trigger"]
    post(api, "triggers/shop", {"hourly": 1})
    codes = [hook(api, made["url"], b"{}", {"Webhook-Id": f"caller:replay:{i}"}).status_code for i in range(3)]
    assert codes == [202, 429, 429]
    # a person's own replay is exempt, and its number skips an id the sender already used
    assert hook(api, made["url"], b"{}", {"Webhook-Id": "x"}).status_code == 429
    again = post(api, "triggers/shop/deliveries/caller:replay:0/replay", {})["replay"]
    assert again["of"] == "caller:replay:0" and again["delivery"] == "caller:replay:0:replay:1"
    assert stored_rows(api, "SELECT replay FROM trigger_seen WHERE delivery=?", again["delivery"])[0][0] == 1


def test_the_hub_tools_cover_the_whole_life_of_a_trigger(api):
    from backend.tests.test_mcp import call
    bad, made = call(api, "hub_trigger_create", {"name": "Shop"})
    assert not bad and made["id"] == "shop"
    routine(api)
    assert not call(api, "hub_trigger_update", {"id": "shop", "hourly": 5, "type_from": "/kind", "unheard": "note"})[0]
    assert call(api, "hub_trigger_show", {"id": "shop"})[1]["hourly"] == 5
    signed = call(api, "hub_trigger_sign", {"id": "shop", "preset": "github"})[1]
    assert signed["preset"] == "github" and len(signed["signing_secret"]) == 64
    body = b'{"kind": "k1"}'
    assert hook(api, made["url"], body, {"X-Hub-Signature-256": sig(signed["signing_secret"], body),
                                         "X-GitHub-Delivery": "a/b c"}).status_code == 202
    rows = call(api, "hub_trigger_deliveries", {"id": "shop"})[1]["result"]
    assert rows[0]["delivery"] == "a/b c" and rows[0]["type"] == "k1" and rows[0]["stored"] is True
    shown = call(api, "hub_trigger_delivery", {"id": "shop", "delivery": "a/b c"})[1]
    assert json.loads(shown["body"]) == {"kind": "k1"}
    assert call(api, "hub_trigger_replay", {"id": "shop", "delivery": "a/b c"})[1]["delivery"] == "a/b c:replay:1"
    assert call(api, "hub_trigger_list")[1]["result"][0]["preset"] == "github"
    assert call(api, "hub_trigger_rotate", {"id": "shop"})[1]["url"] != made["url"]
    assert call(api, "hub_trigger_delete", {"id": "shop"})[1]["deleted"] is True


def test_a_cap_above_the_log_size_still_holds(api):
    made = post(api, "triggers", {"name": "Shop"})["trigger"]
    post(api, "triggers/shop", {"hourly": 250})
    codes = [hook(api, made["url"], b"{}", {"Webhook-Id": f"c{i}"}).status_code for i in range(251)]
    assert codes[:250] == [202] * 250 and codes[250] == 429


def test_a_stripe_event_id_in_the_body_makes_a_resend_across_minutes_a_duplicate(api):
    made = post(api, "triggers", {"name": "Billing"})["trigger"]
    post(api, "triggers/billing/signing", {"preset": "stripe", "secret": STRIPE_SECRET})
    secret, vault = made["url"].rsplit("/", 1)[1], api.app.state.vault

    def send(body, minutes):
        at = datetime.fromtimestamp(STRIPE_TS + minutes * 60, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        with api.app.state.store.transaction() as c:
            return triggers.receive(c, "billing", body, {"stripe-signature": stripe_sig(STRIPE_SECRET, body, STRIPE_TS)},
                                    secret=secret, received_at=at, vault=vault)
    assert P.body_id("stripe", b"not json") == "" and P.body_id("hmac", STRIPE_BODY) == ""
    first, again = send(STRIPE_BODY, 0), send(STRIPE_BODY, 2)
    assert (first.outcome, again.outcome, first.delivery) == ("accepted", "duplicate", "evt_1")


def test_an_admin_manages_a_trigger_someone_else_made_and_a_plain_person_does_not(api):
    post(api, "triggers", {"name": "Caras"}, token="cara-test")
    post(api, "triggers/caras/rotate", {}, token="ben-test")                  # ben is an admin
    post(api, "triggers", {"name": "Bens"}, token="ben-test")
    post(api, "triggers/bens/rotate", {}, token="cara-test", expected=403)


def test_rules_counts_only_the_rules_that_worked_and_the_delivery_shows_why_the_rest_did_not(api):
    made = post(api, "triggers", {"name": "Shop"})["trigger"]
    rule_row(api, {"action": "wake_bot", "bot": "cpo", "text": "Order {{type}}"})
    rule_row(api, {"action": "wake_bot", "bot": "ops", "text": "Order {{type}}"})
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE bots SET state='planned' WHERE slug='cpo'")
        c.execute("UPDATE bots SET state='active' WHERE slug='ops'")
    got = hook(api, made["url"], b'{"order": 1}', {"Idempotency-Key": "d1"}).json()
    assert got["rules"] == 1                                   # the planned bot's wake did not work
    assert get(api, "triggers/shop/deliveries")["deliveries"][0]["rules"] == 1
    detail = get(api, "triggers/shop/deliveries/d1")["delivery"]
    assert detail["rules"] == 1
    by_bot = {r["target"]: r for r in detail["rule_results"]}
    assert by_bot["ops"]["ok"] is True and by_bot["cpo"]["ok"] is False and "planned" in by_bot["cpo"]["detail"]
    replayed = post(api, "triggers/shop/deliveries/d1/replay", {})["replay"]
    assert replayed["rules"] == 1                              # a replay counts the same way


def test_a_refused_call_keeps_the_type_its_header_names_and_its_server_id(api):
    made = post(api, "triggers", {"name": "Repo"})["trigger"]
    post(api, "triggers/repo/signing", {"preset": "github", "secret": "k" * 20})
    body = b'{"zen": "x"}'
    refused = hook(api, made["url"], body, {"X-GitHub-Event": "push", "X-GitHub-Delivery": "g1", "X-Hub-Signature-256": "sha256=00"})
    assert refused.status_code == 401
    row = get(api, "triggers/repo/deliveries")["deliveries"][0]
    assert row["outcome"] == "refused" and row["type"] == "push"
    assert row["sent_id"] == "g1" and row["delivery"] != "g1"          # the row's handle is Tico's, the sender's id is sent_id
    assert get(api, "triggers/repo/deliveries/" + row["delivery"])["delivery"]["type"] == "push"
    good = hook(api, made["url"], body, {"X-GitHub-Event": "push", "X-GitHub-Delivery": "g1", "X-Hub-Signature-256": sig("k" * 20, body)})
    assert good.status_code == 202 and good.json()["type"] == "push"        # the refusal did not enter the dedupe ledger

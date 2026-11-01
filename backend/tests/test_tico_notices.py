"""Notices from Tico Team through the Gateway relay (backend/tico_notices.py), against the fake HQ. The built-in trigger is
`_tico`; HQ's system relay for it is listed with `system: true`, and nothing else may feed it."""
import json

import pytest

from backend import hook_relay, rules
from backend import hubdb as H
from backend.tests.test_api import api, get, post  # noqa: F401
from backend.tests.test_hook_relay import hq, relay_for, service  # noqa: F401

TICO = "hr_00000000000000a1"


def notice(n="n1", kind="info", body="Version 1.4.1 fixes a mail sync bug.", **more):
    return json.dumps({"type": "tico.notice", "id": n, "kind": kind, "body": body, "url": "https://tico.team/n/" + n,
                       "sent_at": "2026-10-25T09:00:00Z", **more}).encode()


def send(hq, n="n1", relay=TICO, system=True, **more):
    hq.relays.setdefault(relay, {"id": relay, "trigger_id": "_tico", "key": "hq", "system": system})
    hq.deliver(relay, "notice:" + n, body=notice(n, **more),
               headers={"content-type": "application/json", "x-tico-notice": n})


def botops_messages(api):
    with api.app.state.store.read() as c:
        return [dict(r) for r in c.execute("SELECT * FROM messages WHERE to_actor='bot:botops' ORDER BY rowid")]


@pytest.fixture(autouse=True)
def botops(api):
    with api.app.state.store.transaction() as c:
        c.execute("INSERT INTO bots(slug,display_name,runtime,model,effort,cwd,host,state,created_at) "
                  "VALUES('botops','BotOps','fake','','','','keeper','active',?)", (H.now(),))
        c.execute("INSERT INTO bot_config(bot,config,team,operator) VALUES('botops','{}',NULL,'ana')")


@pytest.fixture
def judged(monkeypatch):
    calls = []
    real = rules.judge_event

    def spy(conn, event, ctx, **more):
        if event == "tico.notice":
            calls.append(ctx)
        return real(conn, event, ctx, **more)
    monkeypatch.setattr(rules, "judge_event", spy)
    return calls


def test_a_relayed_notice_reaches_the_rules_once_and_shows_once_beside_latest(api, hq, judged, monkeypatch):
    relay_for(api, hq)                                   # the first relay makes the built-in trigger and its rules
    listed = {t["id"]: t for t in get(api, "triggers")["triggers"]}
    assert listed["_tico"]["builtin"] is True and listed["_tico"]["relay"] is None and listed["_tico"]["url"] == ""
    with api.app.state.store.read() as c:
        kept = {r[0] for r in c.execute("SELECT id FROM rules WHERE scope_kind='team' AND enabled=1")}
    assert kept >= {"tico-notice-security-wake", "tico-notice-billing-ask"}

    send(hq)
    service(api).tick()
    send(hq)                                             # HQ serving the same notice again does nothing
    hq.queue[-1]["hq_seq"] = 2
    service(api).tick()
    assert len(judged) == 1
    assert judged[0]["notice_id"] == "n1" and judged[0]["kind"] == "info" and judged[0]["type"] == "info"
    assert judged[0]["trigger"] == "_tico" and judged[0]["via"] == "gateway"
    assert [m["refs"] for m in botops_messages(api) if '"wake": "tico.notice"' in m["refs"]]    # the info note

    from backend import releases
    monkeypatch.setattr(releases.CHECKER, "extras", {"notices": [{"id": "n1", "versions": "0.4", "body": "x", "url": ""}],
                                                      "known_issues": []})
    with api.app.state.store.read() as c:
        from backend import tico_notices
        again = tico_notices.banner(c, {"id": 7, "versions": "0.4", "body": "Update", "url": ""})
    assert [m["id"] for m in again] == [7]               # n1 is /v1/latest's too: shown once
    monkeypatch.setattr(releases.CHECKER, "extras", None)
    shown = get(api, "notices")["notices"]
    assert [(n["id"], n["source"], n["kind"]) for n in shown] == [("n1", "relay", "info")]


def test_a_security_notice_wakes_botops_and_asks_the_owner(api, hq, judged):
    relay_for(api, hq)
    send(hq, "s1", kind="security", body="Rotate your Acme keys today.")
    service(api).tick()
    wakes = [m for m in botops_messages(api) if '"wake": "tico.notice"' in m["refs"]]
    assert len(wakes) == 1 and "Rotate your Acme keys today." in wakes[0]["body"] and wakes[0]["from_actor"] == H.KEEPER
    assert json.loads(wakes[0]["refs"])["rule_id"] == "tico-notice-security-wake"
    with api.app.state.store.read() as c:
        asked = c.execute("SELECT count(*) FROM events WHERE action='rule.event' AND target='tico-notice-security-ask'").fetchone()[0]
    assert asked == 1


def test_nobody_posts_to_tico_directly_and_it_cannot_be_deleted_or_changed(api, hq):
    relay_for(api, hq)
    for path in ("api/v2/hooks/_tico", "api/v2/hooks/_tico/anything"):
        assert api.post("/" + path, content=notice(), headers={"authorization": "Bearer anything"}).status_code == 404
    post(api, "triggers/_tico/delete", {}, expected=422)
    post(api, "triggers/_tico/rotate", {}, expected=422)
    post(api, "triggers/_tico/signing", {"preset": "hmac"}, expected=422)
    post(api, "triggers/_tico", {"name": "Mine"}, expected=422)
    post(api, "triggers/_tico/relay", {}, expected=422)
    assert post(api, "triggers", {"name": "Tico"})["trigger"]["id"] == "tico"      # a user's own id, no clash
    post(api, "triggers", {"name": "x", "id": "_tico"}, expected=422)
    assert post(api, "triggers/_tico", {"keep_days": 3})["trigger"]["keep_days"] == 3


def test_a_delivery_naming_tico_from_another_relay_is_ignored(api, hq, judged):
    relay_for(api, hq)
    send(hq, "n1")                                       # the relay HQ lists as its system relay
    service(api).tick()
    send(hq, "n2", relay="hr_00000000000000b2", system=False)
    service(api).tick()
    assert [c["notice_id"] for c in judged] == ["n1"] and hq.acks[-1] == 2
    with api.app.state.store.read() as c:
        assert c.execute("SELECT relay_id FROM trigger_relays WHERE trigger_id='_tico'").fetchall()[0][0] == TICO


def test_with_only_a_gateway_key_the_loop_pulls_every_minute_learns_the_system_relay_and_notices_arrive(api, hq, judged):
    send(hq, "k1", kind="billing", body="Your Acme card expires.")
    service(api).tick()
    assert hq.paths().count("pull") == 1 and hq.paths().count("relays") == 1       # the list was forced by the notice
    assert service(api).interval == hook_relay.NOTICES_EVERY == 60
    assert "pending" not in hq.paths() and [c["notice_id"] for c in judged] == ["k1"]
    assert [t["id"] for t in get(api, "triggers")["triggers"]] == ["_tico"]
    relay_for(api, hq)
    service(api).tick()
    assert service(api).interval == hook_relay.EVERY


def test_only_the_relay_hq_lists_as_system_is_recorded_and_the_list_is_asked_hourly(api, hq, monkeypatch):
    hq.relays[TICO] = {"id": TICO, "trigger_id": "_tico", "key": "hq", "system": True}
    hq.relays["hr_00000000000000b2"] = {"id": "hr_00000000000000b2", "trigger_id": "_tico", "key": "x"}   # not system
    service(api).listed = 0.0                            # the app's own first tick, before the key was set, may have asked
    service(api).tick()
    service(api).tick()
    assert hq.paths().count("relays") == 1               # listed once this hour
    with api.app.state.store.read() as c:
        from backend import tico_notices
        assert tico_notices.relay(c) == TICO


def test_a_delivery_naming_the_tico_trigger_from_a_relay_hq_does_not_list_as_system_is_dropped_and_acked(api, hq, judged):
    relay_for(api, hq)
    send(hq, "x1", relay="hr_00000000000000c3", system=False, kind="security")
    service(api).tick()
    assert judged == [] and hq.acks[-1] == 1 and botops_messages(api) == []
    with api.app.state.store.read() as c:
        from backend import tico_notices
        assert tico_notices.relay(c) is None and c.execute("SELECT count(*) FROM tico_notices").fetchone()[0] == 0
    assert hq.paths().count("relays") >= 1               # HQ was asked before the delivery was dropped


def test_an_automation_on_the_tico_trigger_fires_and_webhook_received_is_not_judged(api, hq, judged, monkeypatch):
    relay_for(api, hq)
    aid = post(api, "automations", {"name": "A notice", "owner": "person:ana", "trigger": {"type": "webhook", "id": "_tico"},
                                    "steps": [{"id": "read", "type": "bot_turn", "bot": "ops",
                                               "prompt": "Read it. {{webhook.content}}"}]})["id"]
    seen = []
    real = rules.judge_event
    monkeypatch.setattr(rules, "judge_event", lambda c, event, ctx, **more: (seen.append(event), real(c, event, ctx, **more))[1])
    send(hq, "r1", kind="info")
    service(api).tick()
    assert seen == ["tico.notice"] and len(judged) == 1                        # judged once, as tico.notice only
    api.app.state.store.automations.drain()
    assert [r["trigger_ref"] for r in get(api, f"automations/{aid}/runs")["runs"]] == ["webhook:_tico:notice:r1"]
    with api.app.state.store.read() as c:
        tasks = c.execute("SELECT owner FROM tasks WHERE title='A notice'").fetchall()
    assert [t[0] for t in tasks] == ["bot:ops"]

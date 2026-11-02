"""Rules on events without a task (`rules.judge_event`), the wake_bot and send_webhook actions, and the catalog's
listing. Rows are written as an older install has them: rules made today live in automations."""
import json
import sys
import types

import pytest

from backend import hubdb as H
from backend import rules as R
from backend.tests.test_api import api, get, post  # noqa: F401
from backend.tests.test_wake_rules import jobs, setup

CALL = {"trigger": "shop", "trigger_name": "Acme shop", "type": "order.created", "delivery": "d1",
        "payload": {"order": 42, "customer": {"name": "Buyer"}}, "via": "direct"}


def rule(api, by="human:ben", scope_kind="team", scope_id="", event="webhook.received", then="wake", when=None,
         params=None):
    """A row in the rules table, as the engine reads it."""
    row = {"id": "rule_" + H.new_id()[:8], "scope_kind": scope_kind, "scope_id": scope_id, "event": event,
           "condition": json.dumps(when or {}), "then_outcome": then, "params": json.dumps(params or {}),
           "created_by": by, "created_at": H.now(), "updated_at": H.now()}
    with api.app.state.store.transaction() as c:
        c.execute(f"INSERT INTO rules({','.join(row)}) VALUES({','.join('?' * len(row))})", list(row.values()))
    return {"rule": {**row, "params": params or {}}}


def wake_rule(api, bot="cpo", text="New {{type}}: order {{data.order}} from {{data.customer.name}}", **more):
    return rule(api, then="action", params={"action": "wake_bot", "bot": bot, "text": text}, **more)["rule"]


def judge(api, ctx=None, **more):
    with api.app.state.store.transaction() as c:
        return R.judge_event(c, "webhook.received", {**CALL, **(ctx or {}), **more})


def last_message(c, bot):
    return c.execute("SELECT * FROM messages WHERE recipient_id=? ORDER BY rowid DESC LIMIT 1", (H.bot_actor(bot),)).fetchone()


def test_a_team_wake_rule_queues_a_job_with_the_rendered_text_and_the_call_in_its_refs(api):
    setup(api)
    made = wake_rule(api)
    with api.app.state.store.transaction() as c:
        c.execute("DELETE FROM jobs")
        done = R.judge_event(c, "webhook.received", CALL)
        assert [(d["rule_id"], d["outcome"], d["target"], d["ok"]) for d in done] == [
            (made["id"], "wake_bot", "cpo", True)]
        assert jobs(c, "cpo") == 1
        message = last_message(c, "cpo")
        refs = json.loads(message["refs"])
        assert "New order.created: order 42 from Buyer" in message["body"]
        assert {k: refs[k] for k in ("trigger", "delivery", "type", "rule_id", "wake")} == {
            "trigger": "shop", "delivery": "d1", "type": "order.created", "rule_id": made["id"],
            "wake": "webhook.received"}


def test_nothing_matches_nothing_happens_and_the_same_delivery_wakes_once(api):
    setup(api)
    assert judge(api) == []                                                       # no rows at all
    wake_rule(api, when={"trigger": "shop", "type": "order.*"})
    assert judge(api, trigger="other") == [] and judge(api, type="refund.created", delivery="d0") == []
    with api.app.state.store.transaction() as c:
        c.execute("DELETE FROM jobs")
    assert len(judge(api)) == 1 and judge(api) == []
    assert len(judge(api, delivery="d2")) == 1
    with api.app.state.store.transaction() as c:
        assert jobs(c, "cpo") == 2


def test_a_bot_scope_ignore_silences_the_team_wake_for_that_bot_only(api):
    setup(api)
    wake_rule(api, bot="cpo")
    wake_rule(api, bot="ops")
    rule(api, scope_kind="bot", scope_id="cpo", then="ignore")
    with api.app.state.store.transaction() as c:
        c.execute("DELETE FROM jobs")
    done = judge(api)
    assert [d["target"] for d in done if d["outcome"] == "wake_bot"] == ["ops"]
    with api.app.state.store.transaction() as c:
        assert jobs(c, "cpo") == 0 and jobs(c, "ops") == 1


def test_a_bare_star_ignore_for_messages_does_not_silence_webhooks_and_the_first_row_per_bot_wins(api):
    setup(api)
    rule(api, event="*", then="ignore")
    first = wake_rule(api, text="first")
    wake_rule(api, text="second")
    done = judge(api)
    assert [d["rule_id"] for d in done] == [first["id"]]
    with api.app.state.store.transaction() as c:
        assert "first" in last_message(c, "cpo")["body"]


def test_an_error_does_nothing_and_leaves_a_rule_event_error(api, monkeypatch):
    setup(api)
    wake_rule(api)
    with api.app.state.store.transaction() as c:
        c.execute("DELETE FROM jobs")

    def broken(*args, **kwargs):
        raise RuntimeError("boom")
    monkeypatch.setattr(R, "_tell_bot", broken)
    assert judge(api) == []
    with api.app.state.store.transaction() as c:
        assert jobs(c, "cpo") == 0
        err = c.execute("SELECT detail FROM events WHERE action='rule.event_error'").fetchone()
        assert json.loads(err[0])["delivery"] == "d1"
        assert not c.execute("SELECT 1 FROM rule_events_seen").fetchone()          # a retry may try again
    monkeypatch.undo()
    assert len(judge(api)) == 1


def test_a_note_row_leaves_a_quiet_note_and_a_missing_bot_is_reported_not_raised(api):
    setup(api)
    rule(api, then="note", params={"bot_id": "cpo"})
    rule(api, then="wake", params={"bot_id": "ops"}, when={"type": "order.created"})
    with api.app.state.store.transaction() as c:
        c.execute("DELETE FROM jobs")
    done = judge(api)
    assert [d["ok"] for d in done] == [True, True]
    with api.app.state.store.transaction() as c:
        assert jobs(c, "cpo") == 0 and jobs(c, "ops") == 1
        assert "Acme shop" in last_message(c, "cpo")["body"]


@pytest.fixture
def hooks(monkeypatch):
    """A stand-in for backend/webhooks.py (built beside this): publish records its calls."""
    module = types.ModuleType("backend.webhooks")
    module.calls = []

    class Refused(Exception):
        reason = "That endpoint is off."

    def publish(conn, kind, subject, data, by, *, only=None, revision=None):
        if only == "off":
            raise Refused()
        module.calls.append((kind, subject, data, by, only, revision))
        return 1
    module.publish, module.Refused = publish, Refused
    module.check_usable = lambda *args: None
    monkeypatch.setitem(sys.modules, "backend.webhooks", module)
    monkeypatch.setattr(sys.modules["backend"], "webhooks", module, raising=False)
    return module


def send_rule(api, hook="crm", **more):
    return rule(api, then="action", params={"action": "send_webhook", "webhook": hook, "type": "custom.order-seen",
                                            "data": {"order": "{{data.order}}", "who": ["{{data.customer.name}}"]}},
                **more)["rule"]


def test_send_webhook_publishes_the_rendered_data_as_the_rules_maker(api, hooks):
    made = send_rule(api)
    done = judge(api)
    assert done == [{"rule_id": made["id"], "outcome": "send_webhook", "target": "crm", "ok": True,
                     "detail": "1 queued"}]
    kind, subject, data, by, only, revision = hooks.calls[0]
    assert (kind, subject, data, only, revision) == ("custom.order-seen", "d1", {"order": "42", "who": ["Buyer"]},
                                                     "crm", f"{made['id']}:webhook.received:shop")     # rule, event, trigger
    assert by.startswith("human:")


def test_a_refused_or_missing_outgoing_webhook_is_reported_in_the_result(api, hooks, monkeypatch):
    send_rule(api, hook="off")
    assert judge(api)[0]["ok"] is False and judge(api, delivery="d2")[0]["detail"] == "That endpoint is off."
    monkeypatch.delitem(sys.modules, "backend.webhooks")
    monkeypatch.setattr(sys.modules["backend"], "webhooks", None, raising=False)
    res = judge(api, delivery="d3")[0]
    assert res["ok"] is False and res["detail"] == "Outgoing webhooks are not available"


def test_a_woken_bot_is_told_where_to_read_the_rest_only_when_the_body_was_cut():
    values = {"type": "order.created"}
    call = {"trigger": "shop", "delivery": "d1", "content": "{...}"}
    assert "--part 2" not in R._wake_text({"text": "New {{type}}"}, values, call, "x")
    assert "hub trigger delivery shop d1 --part 2" in R._wake_text({"text": "New {{type}}"}, values, {**call, "cut": True}, "x")


def test_wake_bot_also_runs_as_the_action_of_a_message_event_rule(api):
    setup(api)
    made = rule(api, event="status_or_relation", then="action",
                params={"action": "wake_bot", "bot_id": "ops", "text": "{{task.title}} moved: {{event}}"})["rule"]
    with api.app.state.store.transaction() as c:
        task = H.task_create(c, "bot:ops", "Plain work", "Do it", "bot:cpo")
        c.execute("DELETE FROM jobs")
        msg = H._wake(c, H.task(c, task["id"]), "bot:cpo", "Step moved: In Progress -> Review 1")
        assert msg["refs"]["rule"] == made["id"] and msg["refs"]["quiet"] is True
        assert jobs(c, "ops") == 1 and "Plain work moved" in last_message(c, "ops")["body"]


def test_the_ledger_forgets_a_delivery_after_thirty_days(api):
    setup(api)
    wake_rule(api)
    judge(api)
    with api.app.state.store.transaction() as c:
        assert R.prune_events(c) == 0
        c.execute("UPDATE rule_events_seen SET seen_at=?", (H.shift(H.now(), days=-31),))
        assert R.prune_events(c) == 1


def test_the_listing_endpoint_names_the_catalog(api):
    setup(api)
    out = get(api, "event-types")["items"]
    assert {"webhook.received", "task.done", "release.published"} <= {e["name"] for e in out}
    assert [e["name"] for e in get(api, "event-types?group=approval")["items"]] == [
        "approval.requested", "approval.decided", "approval.withdrawn"]

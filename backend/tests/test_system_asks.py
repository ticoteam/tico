"""System-filed work asks a real decision owner, and old dead letters reach them once."""
import json

import pytest

from backend import ask_routing, health, migrations, task_holds
from backend.store import H, Problem
from backend.tests.test_api import api, headers, post  # noqa: F401
from backend.tests.test_ask_delivery import Api
from backend.tests.test_tasks_board import bot_token
from clients import hubtools


def system_task(c, manager="human:ben", private=False):
    c.execute("UPDATE bot_config SET reports_to=?,team=NULL WHERE bot='ops'", (manager,))
    return H.task_create(c, H.KEEPER, "Document the release", "Write the public changes.", "bot:ops",
                         private=private, lint=False)


@pytest.mark.parametrize("manager,recipient", [("human:ben", "human:ben"), ("cpo", "bot:cpo"), (None, "human:ana")])
def test_system_requester_asks_the_manager_or_owner(api, monkeypatch, manager, recipient):
    monkeypatch.delenv("TICO_REACH_DEFAULT", raising=False)
    with api.app.state.store.transaction() as c:
        task = system_task(c, manager)
        msg = H.task_ask(c, "bot:ops", task["id"], "Should this cover the beta?")
        assert msg["recipient_id"] == recipient
        assert msg["refs"]["rerouted_from"] == H.KEEPER
        assert msg["rerouted"] == f"keeper is the system; sent to {H.actor_name(c, recipient)}"
        assert H.task(c, task["id"])["blocked_reason"].startswith("Waiting on " + task_holds.name(c, recipient))
        if H.is_bot(recipient):
            assert c.execute("SELECT bot_id FROM jobs WHERE message_id=?", (msg["id"],)).fetchone()[0] == "cpo"
        H.answer(c, recipient, msg["id"], "Yes, cover it.")
        assert H.task(c, task["id"])["status"] == "open"


def test_task_ask_api_routes_a_system_requester(api):
    token = bot_token(api)
    with api.app.state.store.transaction() as c:
        task = system_task(c)
    msg = post(api, f"tasks/{task['id']}/ask", {"text": "Should this cover the beta?"}, token=token)
    assert msg["recipient_id"] == "human:ben" and "keeper is the system; sent to" in msg["rerouted"]


@pytest.mark.parametrize("recipient", [None, "keeper"])
def test_structured_comment_routes_the_system_requester_or_explicit_keeper(api, recipient):
    token = bot_token(api)
    with api.app.state.store.transaction() as c:
        task = system_task(c)
    result = post(api, f"tasks/{task['id']}/comments", {"text": "Should this cover the beta?", "ask": {
        "who": recipient, "questions": [{"id": "scope", "header": "Scope", "question": "Should this cover the beta?",
                                         "options": []}]}}, token=token)
    assert result["comment"]["rerouted"].startswith("keeper is the system; sent to ")
    with api.app.state.store.read() as c:
        msg, = H.open_task_asks(c, H.task(c, task["id"]))
        assert msg["recipient_id"] == "human:ben" and msg["refs"]["rerouted_from"] == H.KEEPER


@pytest.mark.parametrize("manager,recipient", [("human:ben", "human:ben"), (None, "human:ana")])
def test_direct_keeper_ask_reaches_a_real_inbox_and_reports_the_route(api, monkeypatch, manager, recipient):
    token = bot_token(api)
    monkeypatch.delenv("TICO_REACH_DEFAULT", raising=False)
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE bot_config SET reports_to=? WHERE bot='ops'", (manager,))
    result = hubtools.ask(Api(api, token), {"bots": ["keeper"], "text": "Should this cover the beta?", "wait_s": 0})
    assert result["keeper"]["recipient_id"] == recipient
    assert result["keeper"]["rerouted"].startswith("keeper is the system; sent to ")
    with api.app.state.store.read() as c:
        assert H.message(c, result["keeper"]["message_id"])["recipient_id"] == recipient


def test_inactive_manager_is_skipped_up_the_chain_when_reachable(api, monkeypatch):
    monkeypatch.delenv("TICO_REACH_DEFAULT", raising=False)
    with api.app.state.store.transaction() as c:
        system_task(c, "cpo")
        c.execute("UPDATE bots SET state='paused' WHERE id='cpo'")
        c.execute("UPDATE bot_config SET reports_to='human:ben' WHERE bot='cpo'")
        c.execute("INSERT INTO reach_lines(id,sender_id,receiver_id,created_at,updated_at,created_by_id,updated_by_id) "
                  "VALUES('manager-line','bot:ops','human:ben',?,?,'human:ana','human:ana')", (H.now(), H.now()))
        msg = H.say(c, "bot:ops", H.KEEPER, "Should this cover the beta?", kind="ask")
        assert msg["recipient_id"] == "human:ben"


def test_routing_respects_nearby_and_private_sources(api, monkeypatch):
    monkeypatch.delenv("TICO_REACH_DEFAULT", raising=False)
    with api.app.state.store.transaction() as c:
        system_task(c, "cpo")
        secret = H.task_create(c, "human:cara", "Review payroll", "Private figures.", "human:cara",
                               private=True, lint=False)
        # A manager cannot read unrelated private sources carried by a question.
        with pytest.raises(Problem):
            H.say(c, "bot:ops", H.KEEPER, "Which figures should I publish?", kind="ask", refs={"task": secret["id"]})
        assert not H.open_task_asks(c, secret)
        c.execute("UPDATE bots SET state='paused' WHERE id='cpo'")
        c.execute("UPDATE bot_config SET reports_to='human:ben' WHERE bot='cpo'")
        with pytest.raises(Problem) as refused:
            H.say(c, "bot:ops", H.KEEPER, "Should this cover the beta?", kind="ask")
        assert refused.value.code == "reach_closed"


def legacy_ask(c, task, *, direct=False):
    conv = H._pair_conversation(c, "bot:ops", H.KEEPER, "ask") if direct else H.conversation(c, task["conversation_id"])
    old = H._write_message(c, "bot:ops", "human:ben", "Should this cover the beta?", conv, "ask",
                           {"task": task["id"]}, None, None)
    c.execute("UPDATE messages SET recipient_id='keeper',created_at=? WHERE id=?",
              (H.shift(H.now(), days=-10), old["id"]))
    H.task_update(c, "bot:ops", task["id"], status="waiting")
    return old


@pytest.mark.parametrize("direct,manager,recipient", [(False, "cpo", "bot:cpo"), (True, None, "human:ana")])
def test_migration_resends_dead_letters_once_and_the_answer_clears_the_hold(api, direct, manager, recipient):
    with api.app.state.store.transaction() as c:
        task = system_task(c, manager)
        old = legacy_ask(c, task, direct=direct)
        c.execute("UPDATE tasks SET waiting_on='keeper' WHERE id=?", (task["id"],))
        task_holds.refresh(c, task["id"])
        assert H.task(c, task["id"])["blocked_reason"].startswith("Waiting on keeper")
        migration = next(m for m in migrations.files() if m.version.endswith("_resend_system_asks"))
        # Exercise the actual migration entry point, including its fresh-install guard.
        import importlib.util
        spec = importlib.util.spec_from_file_location("resend_system_asks", migration.path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        module.up(c)
        asks = H._rows(c.execute("SELECT m.* FROM messages m WHERE json_extract(m.refs,'$.task')=? "
                                 f"AND m.kind='ask' AND {H.OPEN_ASK_SQL}", (task["id"],)))
        assert len(asks) == 1
        msg = H.message(c, asks[0]["id"])
        assert (msg["body"], msg["sender_id"], msg["refs"]["task"]) == (old["body"], old["sender_id"], task["id"])
        assert msg["recipient_id"] == recipient and msg["refs"]["resent_from"] == old["id"]
        assert H.ask_clock(msg) > H.message(c, old["id"])["created_at"]
        original = H.message(c, old["id"])
        assert original["superseded_at"] and original["refs"]["resent_as"] == msg["id"]
        row = H.task(c, task["id"])
        assert row["waiting_on"] == recipient
        assert row["blocked_reason"].startswith("Waiting on " + task_holds.name(c, recipient))
        assert not health.unreadable_waits(c)
        module.up(c)
        assert ask_routing.repair(c) == []
        H.answer(c, recipient, msg["id"], "Yes, cover it.")
        assert H.task(c, task["id"])["status"] == "open"


def test_health_names_a_system_hold_even_with_a_saved_ask_and_hides_private_text(api):
    with api.app.state.store.transaction() as c:
        task = system_task(c, "human:cara")
        legacy_ask(c, task)
    def check(token="ana-test"):
        body = api.get("/api/v2/health", headers=headers(token)).json()
        return next(row for row in body["checks"] if row["id"] == "unreadable_waits")
    found = check()
    assert "Document the release waits on keeper" in found["summary"]
    assert found["fixes"][0]["href"] == "#/task/" + task["id"]
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE tasks SET private=1 WHERE id=?", (task["id"],))
    hidden = check("ben-test")
    assert "Document the release" not in json.dumps(hidden)

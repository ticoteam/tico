"""System-filed work asks a real decision owner, and old dead letters reach them once."""
import json

import pytest

from backend import ask_routing, bot_settings as BS, health, migrations, task_holds
from backend.auth import Identity
from backend.store import H, Problem
from backend.tests.test_api import api, headers, post  # noqa: F401
from backend.tests.test_ask_delivery import Api
from backend.tests.test_tasks_board import bot_token
from clients import hubtools


def system_task(c, manager="human:ben", private=False):
    c.execute("UPDATE bot_config SET reports_to=?,team=NULL WHERE bot='ops'", (manager,))
    return H.task_create(c, H.KEEPER, "Document the release", "Write the public changes.", "bot:ops",
                         private=private, lint=False, deduplicate=False)


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
        assert (msg["sender_id"], msg["refs"]["task"]) == (old["sender_id"], task["id"])
        assert (msg["body"] == old["body"]) is (not direct)
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


@pytest.mark.parametrize("manager", ["human:cara", "bot:cpo"])
@pytest.mark.parametrize("source", ["primary", "additional", "reply"])
def test_routing_checks_every_sources_activity_permissions_for_people_and_bots(api, monkeypatch, manager, source):
    from backend.auth import actor_reads_task
    from backend.tests.test_api import restrict
    monkeypatch.setenv("TICO_REACH_DEFAULT", "open")
    with api.app.state.store.transaction() as c:
        task = system_task(c, manager)
        restricted = H.task_create(c, "human:ana", "Check the ledger", "Read the figures.", "bot:inbox", lint=False)
        restrict(c, "inbox", see={"everyone": True}, read={"bots": ["ops"]}, write={"people": ["ana"]})
        assert actor_reads_task(c, "bot:ops", restricted)
        assert not actor_reads_task(c, manager, restricted)
        refs = {"task": restricted["id"]} if source == "primary" else {"task": [task["id"], restricted["id"]]}
        reply = None
        if source == "reply":
            parent = H._write_message(c, "human:ana", "bot:ops", "Read these figures.",
                                      H.conversation(c, task["conversation_id"]), "say", {"task": restricted["id"]}, None, None)
            refs, reply = {"task": task["id"]}, parent["id"]
        msg = H.say(c, "bot:ops", H.KEEPER, "Should I publish the figures?", kind="ask", refs=refs, in_reply_to=reply)
        assert msg["recipient_id"] == "human:ana"


@pytest.mark.parametrize("standing", [{"sign_in": False}, {"hidden": True}])
def test_people_who_cannot_sign_in_are_skipped(api, monkeypatch, standing):
    monkeypatch.setenv("TICO_REACH_DEFAULT", "open")
    with api.app.state.store.transaction() as c:
        task = system_task(c, "human:cara")
        roster = json.loads(c.execute("SELECT value FROM registry_metadata WHERE key='people'").fetchone()[0])
        next(p for p in roster["people"] if p["id"] == "cara").update(standing)
        c.execute("UPDATE registry_metadata SET value=? WHERE key='people'", (json.dumps(roster),))
        msg = H.task_ask(c, "bot:ops", task["id"], "Should this cover the beta?")
        assert msg["recipient_id"] == "human:ana"


@pytest.mark.parametrize("kind,explicit", [("plain", False), ("comment", False), ("comment", True), ("file", False), ("file", True)])
def test_private_system_task_questions_reach_the_inheriting_manager(api, kind, explicit):
    from backend.tests.test_task_review import ask
    token = bot_token(api)
    with api.app.state.store.transaction() as c:
        task = system_task(c, "human:cara", private=True)
    question = "Should this cover the beta?"
    if kind == "plain":
        result = post(api, f"tasks/{task['id']}/ask", {"text": question}, token=token)
    elif kind == "comment":
        result = post(api, f"tasks/{task['id']}/comments", {"text": question, "ask": ask("keeper" if explicit else None)},
                      token=token)["comment"]
    else:
        post(api, f"tasks/{task['id']}/files", {"name": "release.md", "text": "# Release", "ask": ask("keeper" if explicit else None)},
             token=token)
        with api.app.state.store.read() as c:
            result, = H.open_task_asks(c, H.task(c, task["id"]))
    assert result["recipient_id"] == "human:cara"


@pytest.mark.parametrize("destination", ["loose", "task"])
def test_saved_message_retry_keeps_the_original_recipient_and_receipt(api, monkeypatch, destination):
    monkeypatch.setenv("TICO_REACH_DEFAULT", "open")
    token = bot_token(api)
    with api.app.state.store.transaction() as c:
        task = system_task(c, "human:cara")
    body = {"to": "keeper", "kind": "ask", "text": "Should this cover the beta?"}
    if destination == "task":
        body.update(conversation_id=task["conversation_id"], refs={"task": task["id"]})
    first = post(api, "messages", body, token=token, key="system-question")
    assert first["recipient_id"] == "human:cara"
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE bot_config SET reports_to='human:ben' WHERE bot='ops'")
    assert post(api, "messages", body, token=token, key="system-question") == first


def run_migration(c):
    import importlib.util
    migration = next(m for m in migrations.files() if m.version.endswith("_resend_system_asks"))
    spec = importlib.util.spec_from_file_location("resend_system_asks", migration.path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.up(c)


def test_migrated_direct_task_ask_stays_in_the_managers_decision_queue(api):
    with api.app.state.store.transaction() as c:
        task = system_task(c, "human:cara")
        old = legacy_ask(c, task, direct=True)
        assert H.task(c, task["id"])["waiting_on"] is None
        run_migration(c)
        msg, = H.open_task_asks(c, H.task(c, task["id"]))
        assert msg["conversation_id"] == task["conversation_id"]
        assert msg["refs"]["resent_from"] == old["id"]
        assert task["id"] in [t["id"] for t in H.tasks_asked_of(c, "human:cara")]
    needs = api.get("/api/v2/needs-you", headers=headers("cara-test")).json()
    assert task["id"] in [t["id"] for t in needs["items"]]
    post(api, f"tasks/{task['id']}/comments", {"text": "Yes, cover it."}, token="cara-test")
    with api.app.state.store.read() as c:
        assert not H.open_task_asks(c, H.task(c, task["id"]))
        assert H.task(c, task["id"])["status"] == "open"


@pytest.mark.parametrize("direct,manager", [(True, "human:ben"), (True, "human:cara"), (False, "human:ben")])
def test_repair_keeps_meeting_questions_private_and_only_sends_safe_notices_to_other_rooms(api, direct, manager):
    from backend import task_privacy
    from backend.blobs import register
    from backend.tests.test_sql import query

    meeting = post(api, "live-meetings", {"title": "Restricted planning", "visibility": "private"}, token="ben-test")
    post(api, f"live-meetings/{meeting['id']}/bots", {"bots": ["ops"]}, token="ben-test")
    with api.app.state.store.transaction() as c:
        task = system_task(c, manager)
        old = legacy_ask(c, task, direct=direct)
        c.execute("UPDATE conversations SET room_key=? WHERE id=?", ("live-meeting:" + meeting["id"], old["conversation_id"]))
        c.execute("UPDATE messages SET body='Private meeting figures' WHERE id=?", (old["id"],))
        digest = api.app.state.blobs.put(b"Private meeting attachment", "text/plain")
        blob = register(c, Identity("bot:ops", "bot"), digest, 26, "figures.txt", "text/plain")
        c.execute("INSERT INTO message_assets(message_id,blob_id) VALUES(?,?)", (old["id"], blob["id"]))
        assert not task_privacy.message_readable(c, "human:ana", H.message(c, old["id"]))
        run_migration(c)
        msg, = H.open_task_asks(c, H.task(c, task["id"]))
        assert msg["recipient_id"] == manager
        original = H.message(c, old["id"])
        assert original["conversation_id"] == old["conversation_id"]
        assert original["body"] == "Private meeting figures"
        assert original["refs"]["live_meetings"] == [meeting["id"]]
        assert not task_privacy.message_readable(c, "human:ana", original)
        if direct:
            assert msg["body"] and "Private meeting" not in msg["body"]
            assert set(msg["refs"]) == {"task", "rerouted_from", "resent_from", "asked_again_at"}
            assert not c.execute("SELECT 1 FROM message_assets WHERE message_id=?", (msg["id"],)).fetchone()
        else:
            assert msg["body"] == original["body"]
            assert msg["refs"]["live_meetings"] == [meeting["id"]]
            assert not task_privacy.message_readable(c, "human:ana", msg)
            assert c.execute("SELECT blob_id FROM message_assets WHERE message_id=?", (msg["id"],)).fetchone()[0] == blob["id"]
        assert ask_routing.repair(c) == []
    assert api.get(f"/api/v2/messages/{old['id']}", headers=headers()).status_code == 404
    hidden = [old["id"]] + ([] if direct else [msg["id"]])
    for mid in hidden:
        assert query(api, "SELECT body,refs FROM messages WHERE id=?", params=[mid])["rows"] == []
    if direct:
        assert api.get(f"/api/v2/messages/{msg['id']}", headers=headers()).json()["body"] == msg["body"]


@pytest.mark.parametrize("harness", ["grokbot", "dots", "muse"])
def test_synced_managers_without_ask_delivery_are_skipped_and_health_flags_their_old_questions(api, monkeypatch, harness):
    monkeypatch.setenv("TICO_REACH_DEFAULT", "open")
    with api.app.state.store.transaction() as c:
        task = system_task(c, "cpo")
        BS.write(c, "cpo", {"harness": harness, "runtime": harness})
        c.execute("UPDATE bot_config SET reports_to='human:ben' WHERE bot='cpo'")
        dead = H._write_message(c, "bot:ops", "bot:cpo", "Should this cover the beta?",
                                H.conversation(c, task["conversation_id"]), "ask", {"task": task["id"]}, None, None)
        H.task_update(c, "bot:ops", task["id"], status="waiting")
        assert not c.execute("SELECT 1 FROM jobs WHERE message_id=?", (dead["id"],)).fetchone()
        assert [(r["id"], r["recipient"]) for r in health.unreadable_waits(c)] == [(task["id"], "bot:cpo")]
        second = system_task(c, "cpo")
        msg = H.task_ask(c, "bot:ops", second["id"], "Should this cover the beta?")
        assert msg["recipient_id"] == "human:ben"


@pytest.mark.parametrize("harness", ["hermes", "openclaw"])
def test_independent_external_managers_receive_questions_in_their_inbox(api, harness):
    with api.app.state.store.transaction() as c:
        task = system_task(c, "cpo")
        BS.write(c, "cpo", {"harness": harness, "runtime": harness})
        msg = H.task_ask(c, "bot:ops", task["id"], "Should this cover the beta?")
        assert msg["recipient_id"] == "bot:cpo"
        assert msg["id"] in {m["id"] for m in H.inbox(c, "bot:cpo")["messages"]}
        assert not health.unreadable_waits(c)


def test_synced_manager_counts_while_its_ask_is_delivered_through_its_desktop_queue(api, monkeypatch):
    from backend.tests.test_api import claim
    from backend.tests.test_external_sync import bot_id, deliver, designer, inbox, mac, sync
    monkeypatch.setenv("TICO_REACH_DEFAULT", "open")
    sync(api, designer(), token="cara-test")
    computer = mac(api, bots=["grok-designer"])
    assert deliver(api, computer["runner_id"]).status_code == 200
    recipient = "bot:" + bot_id(api)
    with api.app.state.store.transaction() as c:
        task = system_task(c, recipient)
        msg = H.task_ask(c, "bot:ops", task["id"], "Should this cover the beta?")
        assert msg["recipient_id"] == recipient
        assert c.execute("SELECT bot_id,state FROM jobs WHERE message_id=?", (msg["id"],)).fetchone()["state"] == "queued"
        assert not health.unreadable_waits(c)
    assert inbox(api) == []
    attempt = claim(api, computer)
    assert attempt["message"]["id"] == msg["id"]
    assert deliver(api, None).status_code == 200
    with api.app.state.store.read() as c:
        assert [(r["id"], r["recipient"]) for r in health.unreadable_waits(c)] == [(task["id"], recipient)]


def test_legacy_roomless_message_receipt_replays_without_rerouting_or_accepting_other_content(api):
    from backend import bot_home_merge, models as M
    from backend.store import digest, encode
    from backend.tests.test_bot_home_merge import room

    with api.app.state.store.transaction() as c:
        old_room = room(c, "human:ana", "ops")
        assert not bot_home_merge.merge(c)["errors"]
        home = bot_home_merge.home_for(c, old_room)
        assert home
    token = bot_token(api)
    body = {"to": "human:ben", "text": "Please review the release scope."}
    first = post(api, "messages", body, token=token, key="before-upgrade")
    assert first["conversation_id"] == home
    with api.app.state.store.transaction() as c:
        # Main's checks mutated the request before Store hashed it. Reproduce its
        # persisted receipt, rather than generating it with the upgraded handler.
        legacy = M.MessageCreate.model_validate(body).model_copy(update={"conversation_id": first["conversation_id"]})
        c.execute("UPDATE idempotency SET request_hash=? WHERE operation='/api/v2/messages' AND key='before-upgrade'",
                  (digest(encode(legacy.model_dump())),))
        before = c.execute("SELECT count(*) FROM messages").fetchone()[0]
        # Today's Home can differ; retry still belongs to the saved destination.
        c.execute("UPDATE conversations SET closed_at=? WHERE id=?", (H.now(), first["conversation_id"]))
    assert post(api, "messages", body, token=token, key="before-upgrade") == first
    for changed in ({**body, "text": "Different question"}, {**body, "to": "human:cara"},
                    {**body, "conversation_id": "another-room"}):
        refused = post(api, "messages", changed, token=token, key="before-upgrade", expected=409)
        assert refused["error"]["code"] == "idempotency_conflict"
    with api.app.state.store.read() as c:
        assert c.execute("SELECT count(*) FROM messages").fetchone()[0] == before


def test_quarantined_senders_dead_letter_cannot_abort_the_migration(api):
    with api.app.state.store.transaction() as c:
        task = system_task(c, "human:cara")
        room = H._pair_conversation(c, "bot:ops", H.KEEPER, "ask")
        loose = H._write_message(c, "bot:ops", "human:cara", "Should this cover the beta?", room, "ask", {}, None, None)
        c.execute("UPDATE messages SET recipient_id='keeper',created_at=? WHERE id=?", (H.shift(H.now(), days=-20), loose["id"]))
        old = legacy_ask(c, task)
        c.execute("UPDATE bots SET state='quarantined' WHERE id='ops'")
        run_migration(c)
        assert H.message(c, loose["id"])["superseded_at"] is None
        assert H.message(c, old["id"])["superseded_at"]
        assert H.open_task_asks(c, H.task(c, task["id"]))[0]["recipient_id"] == "human:cara"


@pytest.mark.parametrize("failure", [H.Refused("quarantined", "Sender is quarantined"), RuntimeError("broken row")])
def test_each_failed_repair_rolls_back_and_leaves_the_hold_for_health(api, monkeypatch, failure):
    with api.app.state.store.transaction() as c:
        first = system_task(c)
        old = legacy_ask(c, first)
        second = system_task(c)
        good = legacy_ask(c, second)
        c.execute("UPDATE messages SET created_at=? WHERE id=?", (H.shift(H.now(), days=-20), old["id"]))
        carry = ask_routing._carry_links
        def fail_after_writing(c, before, after):
            carry(c, before, after)
            if before == old["id"]:
                raise failure
        monkeypatch.setattr(ask_routing, "_carry_links", fail_after_writing)
        run_migration(c)
        assert not H.message(c, old["id"])["superseded_at"]
        assert not c.execute("SELECT 1 FROM messages WHERE json_extract(refs,'$.resent_from')=?", (old["id"],)).fetchone()
        assert H.message(c, good["id"])["superseded_at"]
        assert {row["id"] for row in health.unreadable_waits(c)} == {first["id"]}


def test_migrated_file_review_answers_the_replacement_and_keeps_attachments(api):
    from backend.models import ReviewAsk
    from backend.tests.test_task_review import ask
    token = bot_token(api)
    with api.app.state.store.transaction() as c:
        task = system_task(c, "human:cara", private=True)
    made = post(api, f"tasks/{task['id']}/files", {"name": "release.md", "text": "# Release"}, token=token)
    with api.app.state.store.transaction() as c:
        old = legacy_ask(c, task)
        refs = {**old["refs"], **ReviewAsk.model_validate(ask()).model_dump(),
                "target": {"file": made["file_id"], "version": 1}}
        c.execute("UPDATE messages SET refs=? WHERE id=?", (H._dump(refs), old["id"]))
        c.execute("INSERT INTO task_file_reviews(file_id,version,ask_message_id,comment_id) VALUES(?,1,?,?) "
                  "ON CONFLICT(file_id,version) DO UPDATE SET ask_message_id=excluded.ask_message_id,comment_id=excluded.comment_id",
                  (made["file_id"], old["id"], old["id"]))
        blob = c.execute("SELECT blob_id FROM bot_file_versions WHERE file_id=? AND version=1", (made["file_id"],)).fetchone()[0]
        c.execute("INSERT INTO message_assets(message_id,blob_id) VALUES(?,?)", (old["id"], blob))
        run_migration(c)
        msg, = H.open_task_asks(c, H.task(c, task["id"]))
        links = c.execute("SELECT ask_message_id,comment_id FROM task_file_reviews WHERE file_id=?", (made["file_id"],)).fetchone()
        assert tuple(links) == (msg["id"], msg["id"])
        assert c.execute("SELECT blob_id FROM message_assets WHERE message_id=?", (msg["id"],)).fetchone()[0] == blob
    answer = post(api, f"tasks/{task['id']}/answers", {"target": {"file": made["file_id"], "version": 1},
                                                     "answers": {"verdict": ["Approve"]}}, token="cara-test")
    with api.app.state.store.read() as c:
        assert H.message(c, answer["comment"]["id"])["in_reply_to"] == msg["id"]
        assert not H.open_task_asks(c, H.task(c, task["id"]))
        assert H.task(c, task["id"])["status"] == "open"


@pytest.mark.parametrize("source", ["private_task", "activity", "additional", "additional_without_cache"])
def test_health_catches_recipients_who_lose_task_or_source_access(api, monkeypatch, source):
    from backend import read_cache
    from backend.tests.test_api import restrict
    with api.app.state.store.transaction() as c:
        task = system_task(c, "human:cara", private=source == "private_task")
        refs = {"task": task["id"]}
        restricted = task
        if source.startswith("additional"):
            restricted = H.task_create(c, "human:ana", "Check the ledger", "Read the figures.", "bot:inbox", lint=False)
            restrict(c, "inbox", everyone=True)
            refs["sources"] = [restricted["id"]]
        msg = H.say(c, "bot:ops", H.KEEPER, "Should this cover the beta?", conversation_id=task["conversation_id"], kind="ask", refs=refs)
        H.task_update(c, "bot:ops", task["id"], status="waiting")
        if source == "additional_without_cache":
            monkeypatch.setattr(read_cache, "cache", lambda c: None)
        assert not health.unreadable_waits(c)
        if source == "private_task":
            c.execute("UPDATE bot_config SET reports_to='human:ben' WHERE bot='ops'")
        else:
            restrict(c, H.actor_id(restricted["owner_id"]), people=["ana"], bots=["ops"])
            if source == "activity":
                c.execute("UPDATE bot_config SET reports_to='human:ben' WHERE bot='ops'")
        found, = health.unreadable_waits(c)
        assert (found["id"], found["recipient"]) == (task["id"], "human:cara")
        assert H.message(c, msg["id"])["recipient_id"] == "human:cara"

"""Another agent's first message after what the person's Assistant asked it for them is relayed into the person's
Assistant chat (backend/assistant_relay.py): matched by the server's marker on the ask, once, with a link that opens the
reply, and an excerpt only for a plain reply in the person's own chat with that agent."""

import json

from backend import task_effects
from backend.store import H
from backend.tests.helpers.api import assign, headers, post, ready, runner
from backend.tests.helpers.assistant import assistant_turn


def ask_ops(api, text="What does Business Development do, in one line?", person="ben", more=(), **extra):
    """Ben's (or `person`'s) Assistant asks ops for them, in their chat with ops (or `conversation_id`); `more` are
    further asks in the same turn. Returns the first message."""
    _, attempt = assistant_turn(api, person + "-test", "Ask ops what BD does")
    first = post(api, "messages", {"to": "ops", "text": text, **extra}, token=attempt["token"])
    for then in more:
        post(api, "messages", {"to": "ops", "text": then, **extra}, token=attempt["token"])
    return first


def ops_replies(api, text):
    """Ops answers in its run, the way the runner completes a turn with its reply."""
    machine = runner(api)
    assign(api, machine, "ops")
    ready(api, machine, ["ops"])
    attempt = post(api, "jobs/claim", {"bot": "ops"}, token=machine["token"])["attempt"]
    assert attempt, "the Assistant's message queued a run for ops"
    post(api, f"attempts/{attempt['id']}/started", {"thread_id": "run-" + attempt["id"]}, token=machine["token"])
    post(
        api,
        f"attempts/{attempt['id']}/complete",
        {"outcome": "completed", "text": text, "last_seq": 0},
        machine["token"],
    )


def ops_writes(api, conversation_id, text, refs=None, to="human:ben"):
    """Ops writes a message in a thread (the one write path every reply takes). Returns it."""
    with api.app.state.store.transaction() as c:
        room = H.conversation(c, conversation_id)
        return H._write_message(c, "bot:ops", to, text, room, "say", refs or {}, None, None)


def relays(api):
    with api.app.state.store.read() as c:
        rows = c.execute(
            "SELECT m.sender_id, m.recipient_id, m.body, m.refs, v.owner_id, v.room_key FROM messages m "
            "JOIN conversations v ON v.id=m.conversation_id WHERE json_extract(m.refs,'$.assistant_relay')=1 "
            "ORDER BY m.rowid"
        ).fetchall()
    return [{**dict(r), "refs": json.loads(r["refs"])} for r in rows]


def latest_from_ops(api):
    with api.app.state.store.read() as c:
        return dict(
            c.execute(
                "SELECT id, conversation_id FROM messages WHERE sender_id='bot:ops' ORDER BY rowid DESC"
            ).fetchone()
        )


def test_the_agents_first_reply_comes_back_once_with_a_link_that_opens_it(api):
    asked = ask_ops(api, needs_action=True)
    assert asked["refs"]["awaiting_reply"] == {
        "person": "human:ben",
        "agent": "bot:ops",
        "root": asked["conversation_id"],
        "thread": asked["conversation_id"],
    }
    ops_replies(api, "BD finds partners who resell TIDY.\n\nMore detail in the doc.")
    [relay] = relays(api)
    assert (relay["sender_id"], relay["recipient_id"], relay["owner_id"], relay["room_key"]) == (
        "bot:assistant",
        "human:ben",
        "human:ben",
        "assistant",
    )
    reply = latest_from_ops(api)
    assert relay["body"] == (
        "ops replied: BD finds partners who resell TIDY. More detail in the doc.\n\n"
        f"[Open the reply](#/thread/{reply['conversation_id']}?m={reply['id']})"
    )
    assert relay["refs"]["quiet"] and relay["refs"]["no_mentions"]
    with api.app.state.store.read() as c:
        mark = json.loads(c.execute("SELECT refs FROM messages WHERE id=?", (asked["id"],)).fetchone()[0])
    assert mark["awaiting_reply"]["reply_id"] == reply["id"]
    # The marker is spent: a second message is not relayed.
    ops_writes(api, reply["conversation_id"], "One more thing.")
    assert len(relays(api)) == 1


def test_the_answer_to_an_older_ask_is_relayed_after_the_persons_own_message(api):
    asked = ask_ops(api)
    post(api, "messages", {"to": "ops", "text": "Also, thanks."}, token="ben-test")
    ops_writes(api, asked["conversation_id"], "BD finds partners.")
    assert [r["body"].split("\n\n")[0] for r in relays(api)] == ["ops replied: BD finds partners."]


def test_a_reply_in_a_shared_chat_is_relayed_after_someone_elses_post_without_an_excerpt(api):
    with api.app.state.store.transaction() as c:
        shared = H.open_conversation(c, "human:ana", ["human:ben", "bot:ops"], kind="chat", subject="Launch")["id"]
    ask_ops(api, conversation_id=shared)
    post(api, "messages", {"to": "ops", "text": "Me too, please.", "conversation_id": shared}, token="ana-test")
    reply = ops_writes(api, shared, "BD finds partners.", to=None)
    [relay] = relays(api)
    assert relay["body"] == f"ops replied\n\n[Open the reply](#/thread/{shared}?m={reply['id']})"


def test_a_reply_in_a_sub_thread_of_the_ask_is_relayed(api):
    from backend import threads

    asked = ask_ops(api)
    with api.app.state.store.transaction() as c:
        sub = threads.sub_thread(c, "human:ben", asked["id"])["id"]
    reply = ops_writes(api, sub, "BD finds partners.", to=None)
    [relay] = relays(api)
    assert relay["body"].endswith(f"[Open the reply](#/thread/{sub}?m={reply['id']})")


def test_a_person_set_marker_or_via_never_triggers_a_relay(api):
    # The API refuses both keys from a client; a writer that passes them anyway, outside an Assistant turn, sets none.
    forged = {"via": "assistant", "awaiting_reply": {"person": "human:ben", "agent": "bot:ops"}}
    raw = api.post(
        "/api/v2/messages", json={"to": "ops", "text": "What does BD do?", "refs": forged}, headers=headers()
    )
    assert raw.status_code == 422
    with api.app.state.store.transaction() as c:
        room = H.open_conversation(c, "human:ben", ["human:ben", "bot:ops"], kind="chat")
        sent = H._write_message(c, "human:ben", "bot:ops", "What does BD do?", room, "say", forged, None, None)
    assert "awaiting_reply" not in sent["refs"]
    ops_writes(api, sent["conversation_id"], "BD finds partners.")
    assert relays(api) == []


def test_a_relay_that_fails_leaves_the_reply_intact_and_never_reopens_the_marker(api, monkeypatch):
    asked = ask_ops(api)

    def broken(*_):
        raise RuntimeError("the Assistant room is gone")

    monkeypatch.setattr(task_effects, "assistant_line", broken)
    reply = ops_writes(api, asked["conversation_id"], "BD finds partners.")
    with api.app.state.store.read() as c:
        assert c.execute("SELECT body FROM messages WHERE id=?", (reply["id"],)).fetchone()[0] == "BD finds partners."
        mark = json.loads(c.execute("SELECT refs FROM messages WHERE id=?", (asked["id"],)).fetchone()[0])
    assert mark["awaiting_reply"]["reply_id"] == reply["id"]
    monkeypatch.undo()
    ops_writes(api, asked["conversation_id"], "And one more thing.")
    assert relays(api) == []


def test_one_reply_answers_every_waiting_ask_and_tells_each_person_once(api):
    asked = ask_ops(api, more=("And who leads it?",))
    with api.app.state.store.transaction() as c:  # many more open asks than any page of them
        via = H.VIA.set("assistant")
        try:
            room = H.conversation(c, asked["conversation_id"])
            for n in range(60):
                H._write_message(c, "human:ben", "bot:ops", f"Ask {n}", room, "say", {}, None, None)
        finally:
            H.VIA.reset(via)
    ops_writes(api, asked["conversation_id"], "BD finds partners; Dana leads it.")
    assert len(relays(api)) == 1
    with api.app.state.store.read() as c:
        open_marks = c.execute(
            "SELECT count(*) FROM messages WHERE json_extract(refs,'$.awaiting_reply.agent')='bot:ops' "
            "AND json_extract(refs,'$.awaiting_reply.reply_id') IS NULL"
        ).fetchone()[0]
    assert open_marks == 0


def test_a_reply_naming_a_task_or_carrying_files_has_no_excerpt(api, monkeypatch):
    import base64

    from backend.tests.helpers.api import claim
    from backend.tests.helpers.files import publish

    asked = ask_ops(api)
    with api.app.state.store.transaction() as c:
        task = H.task_create(c, "human:ana", "Launch plan", "Public.", "bot:ops", private=False, lint=False)
    ops_writes(api, asked["conversation_id"], "See the plan.", refs={"task_id": task["id"]})
    with api.app.state.store.transaction() as c:  # a second ask in the same chat, as the Assistant writes one
        from backend import hubdb

        via = hubdb.VIA.set("assistant")
        try:
            room = H.conversation(c, asked["conversation_id"])
            H._write_message(c, "human:ben", "bot:ops", "Send the chart?", room, "say", {}, None, None)
        finally:
            hubdb.VIA.reset(via)
    machine = runner(api)
    assign(api, machine, "ops")
    ready(api, machine, ["ops"])
    token = claim(api, machine, "ops")["token"]
    made = publish(
        api,
        {"token": token},
        name="chart.png",
        text=None,
        content_base64=base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"\x00" * 64).decode(),
    )
    assert made.status_code == 200, made.text
    fid = made.json()["file"]["id"]
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE bot_files SET scope=? WHERE id=?", ("conversation:" + asked["conversation_id"], fid))
    post(
        api,
        "messages",
        {
            "to": "human:ben",
            "text": "Here is the chart.",
            "conversation_id": asked["conversation_id"],
            "attachments": [{"ref": f"{fid}@1", "title": "Chart"}],
        },
        token=token,
    )
    assert [r["body"].split("\n\n")[0] for r in relays(api)] == ["ops replied"] * 2


def test_a_native_dm_with_just_the_agent_is_the_persons_own_chat(api):
    from backend import threads
    from backend.auth import Identity

    with api.app.state.store.transaction() as c:
        dm = threads.dm(c, Identity("human:ben", "human"), ["bot:ops"], private=True)["id"]
        assert H.conversation(c, dm)["scope"] == "direct"
    asked = ask_ops(api, conversation_id=dm)
    ops_writes(api, dm, "BD finds partners.")
    assert [r["body"].split("\n\n")[0] for r in relays(api)] == ["ops replied: BD finds partners."]
    # Someone added to it since: no longer their own one-to-one chat, whatever the old participant list says.
    with api.app.state.store.transaction() as c:
        now = H.now()
        c.execute(
            "INSERT INTO thread_members(id,thread_id,actor,role,added_by,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
            (H.new_id(), dm, "human:ana", "member", "human:ben", now, now),
        )
        via = H.VIA.set("assistant")
        try:
            H._write_message(
                c, "human:ben", "bot:ops", "And who leads it?", H.conversation(c, dm), "say", {}, None, None
            )
        finally:
            H.VIA.reset(via)
    ops_writes(api, dm, "Dana leads it.")
    assert [r["body"].split("\n\n")[0] for r in relays(api)][1:] == ["ops replied"]
    assert asked["refs"]["awaiting_reply"]["thread"] == dm


def test_a_person_who_has_left_gets_no_relay(api):
    asked = ask_ops(api)
    with api.app.state.store.transaction() as c:
        people = json.loads(c.execute("SELECT value FROM registry_metadata WHERE key='people'").fetchone()[0])
        for person in people["people"]:
            if person["id"] == "ben":
                person["hidden"] = True
        c.execute("UPDATE registry_metadata SET value=? WHERE key='people'", (json.dumps(people),))
    ops_writes(api, asked["conversation_id"], "BD finds partners.")
    assert relays(api) == []


def test_a_reply_in_a_thread_the_person_no_longer_reads_is_not_told_at_all(api):
    asked = ask_ops(api, person="cara")
    with api.app.state.store.transaction() as c:
        # The chat now belongs to a private task Cara does not read: she no longer reads the thread.
        secret = H.task_create(c, "human:ana", "Plan the acquisition", "Private.", "bot:ops", private=True, lint=False)
        c.execute("UPDATE conversations SET task_id=? WHERE id=?", (secret["id"], asked["conversation_id"]))
    ops_writes(api, asked["conversation_id"], "The buyer is Acme.", to="human:cara")
    assert relays(api) == []


def test_the_migration_clears_markers_written_before_the_server_owned_them(api):
    from pathlib import Path

    asked = ask_ops(api)
    forged = {"awaiting_reply": {"person": "human:ben", "agent": "bot:ops", "root": asked["conversation_id"]}}
    with api.app.state.store.transaction() as c:
        room = H.conversation(c, asked["conversation_id"])
        old = H._write_message(c, "human:ben", "bot:ops", "Older words.", room, "say", {}, None, None)
        c.execute("UPDATE messages SET refs=? WHERE id=?", (json.dumps(forged), old["id"]))
        nulled = H._write_message(c, "human:ben", "bot:ops", "Null mark.", room, "say", {}, None, None)
        c.execute("UPDATE messages SET refs='{\"awaiting_reply\": null}' WHERE id=?", (nulled["id"],))
        c.execute("UPDATE messages SET refs=json_remove(refs,'$.awaiting_reply') WHERE id=?", (asked["id"],))
        for statement in Path("backend/migrations/20261121210139_messages_awaiting_reply.sql").read_text().split(";"):
            if statement.strip():
                c.execute(statement)
        assert json.loads(c.execute("SELECT refs FROM messages WHERE id=?", (old["id"],)).fetchone()[0]) == {}
        assert json.loads(c.execute("SELECT refs FROM messages WHERE id=?", (nulled["id"],)).fetchone()[0]) == {}
    ops_writes(api, asked["conversation_id"], "BD finds partners.")
    assert relays(api) == []


def test_a_post_in_a_sibling_sub_thread_does_not_answer_the_ask(api):
    from backend import threads

    asked = ask_ops(api)
    with api.app.state.store.transaction() as c:
        room = H.conversation(c, asked["conversation_id"])
        other = H._write_message(c, "human:ben", "bot:ops", "Unrelated.", room, "say", {}, None, None)
        sibling = threads.sub_thread(c, "human:ben", other["id"])["id"]
        mine = threads.sub_thread(c, "human:ben", asked["id"])["id"]
        via = H.VIA.set("assistant")
        try:
            H._write_message(
                c, "human:ben", "bot:ops", "Which partners?", H.conversation(c, mine), "say", {}, None, None
            )
        finally:
            H.VIA.reset(via)
    ops_writes(api, sibling, "Something else.", to=None)
    with api.app.state.store.read() as c:
        waiting = c.execute(
            "SELECT count(*) FROM messages WHERE json_extract(refs,'$.awaiting_reply.thread')=? "
            "AND json_extract(refs,'$.awaiting_reply.reply_id') IS NULL",
            (mine,),
        ).fetchone()[0]
    assert waiting == 1
    ops_writes(api, mine, "Acme and Globex.", to=None)
    assert any(r["body"].startswith("ops replied") for r in relays(api))


def test_an_ask_on_a_tasks_chat_is_relayed_too(api):
    _, attempt = assistant_turn(api, "ben-test", "Ask ops on the task")
    with api.app.state.store.transaction() as c:
        task = H.task_create(c, "human:ben", "Launch plan", "Plan it.", "bot:ops", lint=False)
    sent = post(
        api,
        "messages",
        {"to": "ops", "text": "Any update?", "conversation_id": task["conversation_id"]},
        token=attempt["token"],
    )
    assert sent["refs"]["awaiting_reply"]["thread"] == task["conversation_id"]
    ops_writes(api, task["conversation_id"], "Draft is up.", to=None)
    assert [r["body"].split("\n\n")[0] for r in relays(api)] == ["ops replied"]

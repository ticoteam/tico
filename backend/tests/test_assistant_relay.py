"""Another agent's first message after what the person's Assistant asked it for them is relayed into the person's
Assistant chat (backend/assistant_relay.py): matched by the server's marker on the ask, once, with a link that opens the
reply, and an excerpt only for a plain reply in the person's own chat with that agent."""

import json

from backend import task_effects
from backend.store import H
from backend.tests.helpers.api import assign, headers, post, ready, runner
from backend.tests.helpers.assistant import assistant_turn


def ask_ops(api, text="What does Business Development do, in one line?", person="ben", **extra):
    """Ben's (or `person`'s) Assistant asks ops for them, in their chat with ops (or `conversation_id`)."""
    _, attempt = assistant_turn(api, person + "-test", "Ask ops what BD does")
    return post(api, "messages", {"to": "ops", "text": text, **extra}, token=attempt["token"])


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


def test_a_relay_that_fails_leaves_the_reply_intact(api, monkeypatch):
    asked = ask_ops(api)

    def broken(*_):
        raise RuntimeError("the Assistant room is gone")

    monkeypatch.setattr(task_effects, "assistant_line", broken)
    reply = ops_writes(api, asked["conversation_id"], "BD finds partners.")
    with api.app.state.store.read() as c:
        assert c.execute("SELECT body FROM messages WHERE id=?", (reply["id"],)).fetchone()[0] == "BD finds partners."
    assert relays(api) == []


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


def test_a_reply_in_a_thread_the_person_no_longer_reads_has_no_excerpt(api):
    asked = ask_ops(api, person="cara")
    with api.app.state.store.transaction() as c:
        # The chat now belongs to a private task Cara does not read: she no longer reads the thread.
        secret = H.task_create(c, "human:ana", "Plan the acquisition", "Private.", "bot:ops", private=True, lint=False)
        c.execute("UPDATE conversations SET task_id=? WHERE id=?", (secret["id"], asked["conversation_id"]))
    reply = ops_writes(api, asked["conversation_id"], "The buyer is Acme.", to="human:cara")
    [relay] = relays(api)
    assert relay["body"] == f"ops replied\n\n[Open the reply](#/thread/{asked['conversation_id']}?m={reply['id']})"

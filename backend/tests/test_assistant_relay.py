"""Another agent's first message after what the person's Assistant asked it for them is relayed into the person's
Assistant chat (backend/assistant_relay.py): matched by the server's own record of the wait (awaiting_replies, never in a
message), once, as "<agent> replied" with a link that opens the reply, and only when the person can read it."""

import json

from backend import task_effects
from backend.store import H
from backend.tests.helpers.api import assign, get, post, ready, runner
from backend.tests.helpers.assistant import assistant_turn


def ask_ops(api, text="What does Business Development do, in one line?", person="ben", more=(), **extra):
    """Ben's (or `person`'s) Assistant asks ops for them, in their chat with ops (or `conversation_id`); `more` are
    further asks in the same turn. Returns the first message."""
    _, attempt = assistant_turn(api, person + "-test", "Ask ops what BD does")
    first = post(api, "messages", {"to": "ops", "text": text, **extra}, token=attempt["token"])
    for then in more:
        post(api, "messages", {"to": "ops", "text": then, **extra}, token=attempt["token"])
    return first


def assistant_writes(api, conversation_id, text, kind="say"):
    """A further message the Assistant writes to ops for Ben, in this thread (as `_write_message` does on its turn)."""
    with api.app.state.store.transaction() as c:
        via = H.VIA.set("assistant")
        try:
            room = H.conversation(c, conversation_id)
            return H._write_message(c, "human:ben", "bot:ops", text, room, kind, {}, None, None)
        finally:
            H.VIA.reset(via)


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


def ops_writes(api, conversation_id, text, refs=None, to="human:ben", kind="say"):
    """Ops writes a message in a thread (the one write path every reply takes). Returns it."""
    with api.app.state.store.transaction() as c:
        room = H.conversation(c, conversation_id)
        return H._write_message(c, "bot:ops", to, text, room, kind, refs or {}, None, None)


def relays(api):
    """What the Assistant delivered into anyone's Assistant chat."""
    with api.app.state.store.read() as c:
        rows = c.execute(
            "SELECT m.sender_id, m.recipient_id, m.body, m.refs, v.owner_id, v.room_key FROM messages m "
            "JOIN conversations v ON v.id=m.conversation_id WHERE v.scope='personal' AND v.room_key='assistant' "
            "AND m.sender_id='bot:assistant' AND m.delivered_at IS NOT NULL ORDER BY m.rowid"
        ).fetchall()
    return [{**dict(r), "refs": json.loads(r["refs"])} for r in rows]


def waits(api):
    with api.app.state.store.read() as c:
        return [dict(r) for r in c.execute("SELECT * FROM awaiting_replies ORDER BY rowid")]


def latest_from_ops(api):
    with api.app.state.store.read() as c:
        return dict(
            c.execute(
                "SELECT id, conversation_id FROM messages WHERE sender_id='bot:ops' ORDER BY rowid DESC"
            ).fetchone()
        )


def test_the_agents_first_reply_comes_back_once_with_a_link_that_opens_it(api):
    asked = ask_ops(api, needs_action=True)
    [wait] = waits(api)
    assert (wait["message_id"], wait["person_id"], wait["agent_id"], wait["thread_id"]) == (
        asked["id"],
        "human:ben",
        "bot:ops",
        asked["conversation_id"],
    )
    ops_replies(api, "BD finds partners who resell TIDY.\n\nMore detail in the doc.")
    [relay] = relays(api)
    assert (relay["sender_id"], relay["recipient_id"], relay["owner_id"], relay["room_key"]) == (
        "bot:assistant",
        "human:ben",
        "human:ben",
        "assistant",
    )
    reply = latest_from_ops(api)
    assert relay["body"] == f"ops replied\n\n[Open the reply](#/thread/{reply['conversation_id']})"
    assert relay["refs"]["quiet"] and relay["refs"]["no_mentions"]
    assert waits(api) == []
    # The wait is spent: a second message is not relayed.
    ops_writes(api, reply["conversation_id"], "One more thing.")
    assert len(relays(api)) == 1


def test_no_reader_of_the_ask_sees_the_wait_before_or_after_it_is_answered(api):
    asked = ask_ops(api)
    shown = lambda: json.dumps(get(api, f"messages/{asked['id']}", "ben-test"))  # noqa: E731
    before = shown()
    ops_writes(api, asked["conversation_id"], "BD finds partners.")
    after = shown()
    for text in (before, after):
        assert "awaiting" not in text and "reply_id" not in text


def test_the_answer_to_an_older_ask_is_relayed_after_the_persons_own_message(api):
    asked = ask_ops(api)
    post(api, "messages", {"to": "ops", "text": "Also, thanks."}, token="ben-test")
    ops_writes(api, asked["conversation_id"], "BD finds partners.")
    assert [r["body"].split("\n\n")[0] for r in relays(api)] == ["ops replied"]


def test_a_reply_in_a_shared_chat_is_relayed_after_someone_elses_post(api):
    with api.app.state.store.transaction() as c:
        shared = H.open_conversation(c, "human:ana", ["human:ben", "bot:ops"], kind="chat", subject="Launch")["id"]
    ask_ops(api, conversation_id=shared)
    post(api, "messages", {"to": "ops", "text": "Me too, please.", "conversation_id": shared}, token="ana-test")
    ops_writes(api, shared, "BD finds partners.", to=None)
    [relay] = relays(api)
    assert relay["body"] == f"ops replied\n\n[Open the reply](#/thread/{shared})"


def test_a_reply_in_a_sub_thread_of_the_ask_is_relayed(api):
    from backend import threads

    asked = ask_ops(api)
    with api.app.state.store.transaction() as c:
        sub = threads.sub_thread(c, "human:ben", asked["id"])["id"]
    ops_writes(api, sub, "BD finds partners.", to=None)
    [relay] = relays(api)
    assert relay["body"].endswith(f"[Open the reply](#/thread/{sub})")


def test_a_post_in_a_sibling_sub_thread_does_not_answer_the_ask(api):
    from backend import threads

    asked = ask_ops(api)
    with api.app.state.store.transaction() as c:
        room = H.conversation(c, asked["conversation_id"])
        other = H._write_message(c, "human:ben", "bot:ops", "Unrelated.", room, "say", {}, None, None)
        sibling = threads.sub_thread(c, "human:ben", other["id"])["id"]
        mine = threads.sub_thread(c, "human:ben", asked["id"])["id"]
    assistant_writes(api, mine, "Which partners?")
    ops_writes(api, sibling, "Something else.", to=None)
    assert [w["thread_id"] for w in waits(api)].count(mine) == 1
    ops_writes(api, mine, "Acme and Globex.", to=None)
    assert mine not in [w["thread_id"] for w in waits(api)]
    assert relays(api)


def test_a_steer_asks_and_answers_and_an_fyi_notice_asks_nothing(api):
    asked = ask_ops(api)
    ops_writes(api, asked["conversation_id"], "Answered.")
    assistant_writes(api, asked["conversation_id"], "FYI: the doc moved.", kind="notice")
    assert waits(api) == []
    assistant_writes(api, asked["conversation_id"], "Stop and check the numbers first.", kind="steer")
    assert len(waits(api)) == 1
    ops_writes(api, asked["conversation_id"], "Checked: they hold.", kind="steer")
    assert len(relays(api)) == 2


def test_a_person_set_via_never_makes_a_wait(api):
    forged = {"via": "assistant", "awaiting_reply": {"person": "human:ben", "agent": "bot:ops"}}
    with api.app.state.store.transaction() as c:
        room = H.open_conversation(c, "human:ben", ["human:ben", "bot:ops"], kind="chat")
        sent = H._write_message(c, "human:ben", "bot:ops", "What does BD do?", room, "say", forged, None, None)
    assert waits(api) == []
    ops_writes(api, sent["conversation_id"], "BD finds partners.")
    assert relays(api) == []


def test_a_relay_that_fails_leaves_the_reply_intact_and_never_reopens_the_wait(api, monkeypatch):
    asked = ask_ops(api)

    def broken(*_):
        raise RuntimeError("the Assistant room is gone")

    monkeypatch.setattr(task_effects, "assistant_line", broken)
    reply = ops_writes(api, asked["conversation_id"], "BD finds partners.")
    with api.app.state.store.read() as c:
        assert c.execute("SELECT body FROM messages WHERE id=?", (reply["id"],)).fetchone()[0] == "BD finds partners."
    assert waits(api) == []
    monkeypatch.undo()
    ops_writes(api, asked["conversation_id"], "And one more thing.")
    assert relays(api) == []


def test_one_reply_answers_every_waiting_ask_and_tells_each_person_once(api):
    asked = ask_ops(api, more=("And who leads it?",))
    for n in range(60):  # many more open asks than any page of them
        assistant_writes(api, asked["conversation_id"], f"Ask {n}")
    ops_writes(api, asked["conversation_id"], "BD finds partners; Dana leads it.")
    assert len(relays(api)) == 1
    assert waits(api) == []


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


def test_an_ask_on_a_tasks_chat_is_relayed_too(api):
    _, attempt = assistant_turn(api, "ben-test", "Ask ops on the task")
    with api.app.state.store.transaction() as c:
        task = H.task_create(c, "human:ben", "Launch plan", "Plan it.", "bot:ops", lint=False)
    post(
        api,
        "messages",
        {"to": "ops", "text": "Any update?", "conversation_id": task["conversation_id"]},
        token=attempt["token"],
    )
    assert [w["thread_id"] for w in waits(api)] == [task["conversation_id"]]
    ops_writes(api, task["conversation_id"], "Draft is up.", to=None)
    assert [r["body"].split("\n\n")[0] for r in relays(api)] == ["ops replied"]


def test_a_right_taken_away_between_the_ask_and_the_reply_sends_nothing(api):
    """The read check is the one GET /api/v2/messages/{id} makes: Cara leaves the shared thread after the ask."""
    with api.app.state.store.transaction() as c:
        shared = H.open_conversation(c, "human:ana", ["human:cara", "bot:ops"], kind="chat", subject="Launch")["id"]
    ask_ops(api, person="cara", conversation_id=shared)
    with api.app.state.store.transaction() as c:
        room = H.conversation(c, shared)
        c.execute(
            "UPDATE conversations SET participants=? WHERE id=?",
            (json.dumps([p for p in room["participants"] if p != "human:cara"]), shared),
        )
        c.execute("DELETE FROM thread_members WHERE thread_id=? AND actor='human:cara'", (shared,))
    reply = ops_writes(api, shared, "BD finds partners.", to=None)
    get(api, f"messages/{reply['id']}", "cara-test", expected=404)
    assert relays(api) == []


def test_a_rolled_back_reply_leaves_no_relay_owed_on_its_connection(api):
    asked = ask_ops(api)
    store = api.app.state.store
    held = None
    try:
        with store.transaction() as c:
            held = c
            H._write_message(
                c,
                "bot:ops",
                "human:ben",
                "Half a reply.",
                H.conversation(c, asked["conversation_id"]),
                "say",
                {},
                None,
                None,
            )
            assert c.relay_pending
            raise RuntimeError("the request failed after the reply was written")
    except RuntimeError:
        pass
    assert held is not None and held.relay_pending == []
    assert len(waits(api)) == 1 and relays(api) == []
    ops_writes(api, asked["conversation_id"], "The whole reply.")
    assert len(relays(api)) == 1

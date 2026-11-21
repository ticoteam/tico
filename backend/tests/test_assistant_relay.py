"""Another agent's first reply to what the person's Assistant asked it for them is relayed into the person's Assistant
chat (backend/assistant_relay.py): once, as a short summary with a link to the full reply."""

import json

from backend.store import H
from backend.tests.helpers.api import assign, post, ready, runner
from backend.tests.helpers.assistant import assistant_turn


def ask_ops(api, text="What does Business Development do, in one line?", **extra):
    """Ben's Assistant asks ops for him, in Ben's chat with ops. Returns the message."""
    _, attempt = assistant_turn(api, "ben-test", "Ask ops what BD does")
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


def relays(api):
    with api.app.state.store.read() as c:
        rows = c.execute(
            "SELECT m.sender_id, m.recipient_id, m.body, m.refs, v.owner_id, v.room_key FROM messages m "
            "JOIN conversations v ON v.id=m.conversation_id WHERE json_extract(m.refs,'$.assistant_relay')=1 "
            "ORDER BY m.rowid"
        ).fetchall()
    return [{**dict(r), "refs": json.loads(r["refs"])} for r in rows]


def test_the_agents_first_reply_comes_back_to_the_assistant_chat_once(api):
    ask_ops(api, needs_action=True)
    ops_replies(api, "BD finds partners who resell TIDY.\n\nMore detail in the doc.")
    [relay] = relays(api)
    assert (relay["sender_id"], relay["recipient_id"], relay["owner_id"], relay["room_key"]) == (
        "bot:assistant",
        "human:ben",
        "human:ben",
        "assistant",
    )
    with api.app.state.store.read() as c:
        reply = c.execute(
            "SELECT id, conversation_id FROM messages WHERE sender_id='bot:ops' AND recipient_id='human:ben' "
            "ORDER BY rowid DESC LIMIT 1"
        ).fetchone()
    assert relay["body"] == (
        "ops replied: BD finds partners who resell TIDY. More detail in the doc.\n\n"
        f"[Open the reply](#/thread/{reply['conversation_id']}?m={reply['id']})"
    )
    assert relay["refs"]["quiet"] and relay["refs"]["no_mentions"]
    # A second reply in the same chat is not relayed: only the first answers the ask.
    with api.app.state.store.transaction() as c:
        H._write_message(
            c,
            "bot:ops",
            "human:ben",
            "One more thing.",
            H.conversation(c, reply["conversation_id"]),
            "say",
            {},
            None,
            None,
        )
    assert len(relays(api)) == 1


def test_a_reply_to_the_persons_own_message_is_not_relayed(api):
    post(api, "messages", {"to": "ops", "text": "What does BD do?"}, token="ben-test")
    ops_replies(api, "BD finds partners.")
    assert relays(api) == []

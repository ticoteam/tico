"""Turn annotations accept SQLite task rows and preserve task visibility."""

import pytest

from backend.store import H
from backend.tests.test_api import api, as_member, assign, claim, get, post, ready, runner  # noqa: F401


@pytest.mark.parametrize("reader", ["ana-test", "ben-test"])
def test_messages_include_readable_tasks_created_during_the_turn(api, reader):
    as_member(api, "ben@acme.example")
    r = runner(api)
    assign(api, r, "ops")
    ready(api, r, ["ops"])
    with api.app.state.store.transaction() as c:
        room = H.open_conversation(c, "human:ana", ["human:ana", "human:ben", "bot:ops"], subject="Launch")
    msg = post(api, "messages", {"to": "ops", "text": "Please plan the launch", "conversation_id": room["id"]})
    attempt = claim(api, r)
    post(api, f"attempts/{attempt['id']}/started", {"thread_id": "task-turn"}, token=r["token"])
    with api.app.state.store.transaction() as c:
        visible = H.task_create(c, "bot:ops", "Draft the launch post", "For the team.", "bot:ops", lint=False)
        private = H.task_create(c, "bot:ops", "Review private mail", "Private.", "bot:inbox", lint=False)
        child = H.task_create(c, "bot:ops", "Draft the budget", "For the launch.", "bot:finance",
                              parent_id=visible["id"], lint=False)
        # A party files the subtask; ordinary read alone does not permit filing it.
        H.say(c, "bot:ops", "human:ana", "Filed the work.", conversation_id=msg["conversation_id"],
              in_reply_to=msg["id"], refs={"turn_id": attempt["id"], "tasks": [visible["id"], private["id"], child["id"]]})
    token = reader
    for path in ("messages", "snapshot"):
        result = get(api, f"conversations/{msg['conversation_id']}/{path}", token)
        messages = result["messages"] if path == "snapshot" else result
        reply = next(m for m in messages if m["from_actor"] == "bot:ops")
        created = {item["task_id"] for item in reply["run"]["did"] if item["kind"] == "task"}
        assert visible["id"] in created
        assert child["id"] in reply["ref_tasks"]
        assert (private["id"] in created) == (reader != "ben-test")
        assert (private["id"] in reply["ref_tasks"]) == (reader != "ben-test")


def test_a_loose_message_says_what_kind_of_sender_started_its_run(api):
    """A bot that relays another bot's work to a person mid-run is marked as prompted by a bot, so the chat can fold it;
    one sent during a run a person started stays the person's. Only the kind of sender is said, never who."""
    r = runner(api)
    assign(api, r, "ops")
    ready(api, r, ["ops"])
    with api.app.state.store.transaction() as c:
        H.say(c, "bot:finance", "bot:ops", "The census is delivered; please tell Ana.", refs={"needs_action": True})
    attempt = claim(api, r)
    post(api, f"attempts/{attempt['id']}/started", {"thread_id": "relay"}, token=r["token"])
    with api.app.state.store.transaction() as c:
        relay = H.say(c, "bot:ops", "human:ana", "From Finance: the census is delivered.")
    post(api, f"attempts/{attempt['id']}/complete", {"outcome": "completed", "text": "", "last_seq": 0}, token=r["token"])
    asked = post(api, "chat/ops", {"text": "What is next?"})
    attempt = claim(api, r)
    post(api, f"attempts/{attempt['id']}/started", {"thread_id": "own"}, token=r["token"])
    with api.app.state.store.transaction() as c:
        own = H.say(c, "bot:ops", "human:ana", "Starting the outreach now.", conversation_id=asked["conversation_id"])
    messages = {m["id"]: m for m in get(api, f"conversations/{asked['conversation_id']}/snapshot")["messages"]}
    assert messages[relay["id"]]["prompted_by"] == "bot"
    assert messages[own["id"]]["prompted_by"] == "person"
    assert "prompted_by" not in messages[asked["id"]]


def test_a_run_that_failed_before_starting_does_not_claim_later_messages(api):
    """An attempt that failed on its computer before it started has no `finished_at`; it must not stay open and fold
    every later message from that bot."""
    r = runner(api)
    assign(api, r, "ops")
    ready(api, r, ["ops"])
    with api.app.state.store.transaction() as c:
        H.say(c, "bot:finance", "bot:ops", "Please tell Ana the census is delivered.", refs={"needs_action": True})
    failed = claim(api, r)
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE attempts SET state='failed', started_at=NULL, finished_at=NULL WHERE id=?", (failed["id"],))
        loose = H.say(c, "bot:ops", "human:ana", "The census is delivered.")
    asked = post(api, "chat/ops", {"text": "What is next?"})
    attempt = claim(api, r)
    post(api, f"attempts/{attempt['id']}/started", {"thread_id": "own"}, token=r["token"])
    with api.app.state.store.transaction() as c:
        own = H.say(c, "bot:ops", "human:ana", "Starting the outreach now.", conversation_id=asked["conversation_id"])
    messages = {m["id"]: m for m in get(api, f"conversations/{asked['conversation_id']}/snapshot")["messages"]}
    assert "prompted_by" not in messages.get(loose["id"], {})
    assert messages[own["id"]]["prompted_by"] == "person"

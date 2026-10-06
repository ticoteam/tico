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

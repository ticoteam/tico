"""The Assistant works with its person's rights, so its replies can link tasks only the person reads. Its next turn in
that person's Assistant room is claimed with the person's rights too: before, the claim was checked as the Assistant bot
and refused (403) on every retry, so the chat stopped answering (TIDY, Nov 18)."""

import json

from backend import hubdb as H
from backend import task_privacy
from backend.tests.helpers.api import post
from backend.tests.helpers.assistant import assistant_turn, say, turn_message


def private_task_only_ana_reads(api):
    with api.app.state.store.transaction() as c:
        tid = H.task_create(c, "bot:ops", "Send an email", "To the landlord.", "bot:ops", private=True)["id"]
        c.execute("UPDATE tasks SET readers=? WHERE id=?", (json.dumps(["human:ana"]), tid))
    with api.app.state.store.read() as c:
        assert task_privacy.readable(c, "human:ana", {tid})
        assert not task_privacy.readable(c, "bot:assistant", {tid})
    return tid


def test_next_assistant_turn_is_claimed_after_a_reply_links_a_task_only_the_person_reads(api):
    machine, first = assistant_turn(api, "ana-test", "Is my email working?")
    tid = private_task_only_ana_reads(api)
    post(api, f"attempts/{first['id']}/started", {"thread_id": "turn-1"}, token=machine["token"])
    with api.app.state.store.transaction() as c:
        room = c.execute("SELECT conversation_id FROM messages WHERE id=?", (turn_message(api, first),)).fetchone()[0]
        H.say(c, "bot:assistant", "human:ana", f"Two replies wait for you: [Send an email](#/task/{tid})", room)
    post(api, f"attempts/{first['id']}/complete", {"outcome": "completed", "last_seq": 0}, token=machine["token"])
    say(api, "Does anything need fixing?", "ana-test")
    claimed = post(api, "jobs/claim", {}, token=machine["token"], key="claim:second")["attempt"]
    assert claimed is not None
    # The same key replays the lease (a computer retries a lost answer with it).
    again = post(api, "jobs/claim", {}, token=machine["token"], key="claim:second")["attempt"]
    assert again["id"] == claimed["id"]


def test_another_persons_message_is_still_read_as_the_assistant_bot(api):
    with api.app.state.store.read() as c:
        message = {"recipient_id": "bot:assistant", "sender_id": "human:ben", "refs": {"assistant": True}}
        assert task_privacy.assistant_room_actor(c, "bot:assistant", message) == "bot:assistant"

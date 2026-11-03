"""Which jobs a message write queues: a characterization through the real writers.

Each scenario writes messages the way the product does (chat, a bot's message to a bot, a task comment, an ask and its
answer, a command, a quiet hand-off, a thread with members, a group room, a paused bot, a bot to itself, live meeting
lines, an external agent's inbox, an imported transcript) and records, for every message it wrote, who sent it to whom
and whether a job was queued for it. The expected rows were recorded while the `queue_bot_message` trigger did the
queueing; `backend/enqueue.py` must queue exactly the same jobs.
"""
import json

import pytest

from backend.store import H, encode
from backend.tests.test_api import api, post, setup_attempt  # noqa: F401


def _mark(c):
    return c.execute("SELECT coalesce(max(rowid),0) FROM messages").fetchone()[0]


def _written(api, since):
    """(sender, recipient, kind, queued) for every message written after `since`, in write order."""
    with api.app.state.store.read() as c:
        rows = c.execute("SELECT m.sender_id, m.recipient_id, m.kind, "
                         "EXISTS(SELECT 1 FROM jobs j WHERE j.message_id=m.id AND j.id=m.id "
                         "AND j.bot_id=substr(m.recipient_id,5)) AS queued, "
                         "(SELECT count(*) FROM jobs j WHERE j.message_id=m.id) AS jobs "
                         "FROM messages m WHERE m.rowid>? ORDER BY m.rowid", (since,)).fetchall()
    for r in rows:
        assert r["jobs"] == r["queued"], dict(r)          # a job is the message's own: id = message id, its bot
    return [(r["sender_id"], r["recipient_id"], r["kind"], bool(r["queued"])) for r in rows]


def _since(api):
    with api.app.state.store.read() as c:
        return _mark(c)


def _bot(api, slug):
    with api.app.state.store.read() as c:
        return "bot:" + H.resolve_bot(c, slug)["id"]


def test_a_person_chatting_with_a_bot_queues_its_turn(api):
    at = _since(api)
    post(api, "chat/ops", {"text": "Please look at the invoices."})
    assert ("human:ana", _bot(api, "ops"), "say", True) in _written(api, at)


def test_a_message_to_a_person_queues_nothing(api):
    ops = _bot(api, "ops")
    at = _since(api)
    with api.app.state.store.transaction() as c:
        H.say(c, ops, "human:ben", "The invoices are filed.")
    assert _written(api, at) == [(ops, "human:ben", "say", False)]


def test_a_bot_to_bot_message_and_an_ask_with_its_answer(api):
    ops, coo = _bot(api, "ops"), _bot(api, "coo")
    at = _since(api)
    with api.app.state.store.transaction() as c:
        H.say(c, ops, coo, "FYI the invoices are filed.")
        asked = H.say(c, ops, coo, "Which bank pays payroll?", kind="ask")
    sent = [w for w in _written(api, at) if w[0] == ops]
    assert sent == [(ops, coo, "say", False), (ops, coo, "ask", True)]
    at = _since(api)
    with api.app.state.store.transaction() as c:
        H.answer(c, coo, asked["id"], "Acme Bank.")
    assert [w for w in _written(api, at) if w[2] == "answer"] == [(coo, ops, "answer", True)]


def test_a_task_for_a_bot_and_a_comment_on_it(api):
    ops = _bot(api, "ops")
    at = _since(api)
    task = post(api, "tasks", {"owner": "ops", "title": "File the invoices", "body": "All of October."})
    assert (ops, True) in {(w[1], w[3]) for w in _written(api, at)}
    at = _since(api)
    post(api, f"tasks/{task['id']}/comments", {"text": "Include the late ones too."})
    assert ("human:ana", ops, "say", True) in _written(api, at)


def test_a_command_queues_its_turn(api):
    ops = _bot(api, "ops")
    at = _since(api)
    with api.app.state.store.transaction() as c:
        conv = H.open_conversation(c, "human:ana", ["human:ana", ops], kind="chat")
        H._write_message(c, "human:ana", ops, "/compact", conv, "say", {"command": True}, None, None)
    assert _written(api, at) == [("human:ana", ops, "say", True)]


def test_quiet_live_and_self_messages_queue_nothing(api):
    ops = _bot(api, "ops")
    at = _since(api)
    with api.app.state.store.transaction() as c:
        conv = H.open_conversation(c, "human:ana", ["human:ana", ops], kind="chat")
        for refs in ({"quiet": True}, {"live": {"kind": "transcript"}}, {"live": {"kind": "decision"}},
                     {"live": {"kind": "tool"}}, {"live": {"kind": "ask"}}, {"quiet": False}, {"quiet": 0}):
            H._write_message(c, "human:ana", ops, "words", conv, "say", refs, None, None)
        H._write_message(c, ops, ops, "a note to self", conv, "say", {}, None, None)
    assert [w[3] for w in _written(api, at)] == [False, False, False, False, True, True, True, False]


def test_a_paused_bot_still_gets_its_job_queued(api):
    """The queue does not read the bot's state: a held bot's job waits for it (claims skip the bot)."""
    ops = _bot(api, "ops")
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE bots SET state='paused' WHERE slug='ops'")
    at = _since(api)
    with api.app.state.store.transaction() as c:
        H.say(c, "human:ana", ops, "Something to read when you are back.")
    assert _written(api, at) == [("human:ana", ops, "say", True)]
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE bots SET state='archived' WHERE slug='ops'")
    at = _since(api)
    with pytest.raises(H.Refused):              # an archived bot is not written to at all
        with api.app.state.store.transaction() as c:
            H.say(c, "human:ana", ops, "Still there?")
    assert _written(api, at) == []


def test_a_thread_with_members_queues_the_mentioned_bot_through_its_carrier(api):
    ops, coo = _bot(api, "ops"), _bot(api, "coo")
    thread = post(api, "threads", {"title": "Launch", "audience": "members",
                                   "members": ["human:ben", ops, coo]})
    at = _since(api)
    post(api, f"threads/{thread['id']}/messages", {"text": "@ops take this one"})
    written = _written(api, at)
    assert ("human:ana", None, "say", False) in written or ("human:ana", "", "say", False) in written
    assert (H.KEEPER, ops, "notice", True) in written
    assert not [w for w in written if w[1] == coo and w[3]]


def test_a_group_room_queues_the_addressed_bot(api):
    ops, coo = _bot(api, "ops"), _bot(api, "coo")
    at = _since(api)
    with api.app.state.store.transaction() as c:
        conv = H.open_conversation(c, "human:ana", ["human:ana", "human:ben", ops, coo], kind="chat")
        H._write_message(c, "human:ana", coo, "Can you two split this?", conv, "say", {}, None, None)
    # The room's other bot hears it through a keeper carrier notice, which queues its own turn.
    assert _written(api, at) == [("human:ana", coo, "say", True), (H.KEEPER, ops, "notice", True)]


def _external(api, harness="hermes", computer=None):
    with api.app.state.store.transaction() as c:
        config = {"name": "ops", "runtime": "fake", "status": "active", "harness": harness}
        if computer:
            config["app_delivery"] = {"computer": computer}
        c.execute("UPDATE bot_config SET config=? WHERE bot='ops'", (encode(config),))


def test_an_external_agents_bot_gets_no_job_unless_a_registered_computer_delivers_it(api):
    ops = _bot(api, "ops")
    r, _, _attempt = setup_attempt(api)
    for computer, revoked, queued in ((None, False, False), (r["runner_id"], False, True),
                                      ("no-such-computer", False, False), (r["runner_id"], True, False)):
        _external(api, computer=computer)
        if revoked:
            with api.app.state.store.transaction() as c:
                c.execute("UPDATE runners SET revoked_at=? WHERE id=?", (H.now(), r["runner_id"]))
        at = _since(api)
        with api.app.state.store.transaction() as c:
            conv = H.open_conversation(c, "human:ana", ["human:ana", ops], kind="chat")
            H._write_message(c, "human:ana", ops, "Hello", conv, "say", {}, None, None)
        assert _written(api, at) == [("human:ana", ops, "say", queued)], (computer, revoked)


def test_an_imported_transcript_queues_nothing(api):
    from backend import external_sync
    ops = _bot(api, "ops")

    class Provider:
        id, key, label = "grokbot", "grok", "Grok Bot"

    class Line:
        def __init__(self, role, text, at):
            self.role, self.text, self.at, self.id, self.images = role, text, at, None, []

    at = _since(api)
    with api.app.state.store.transaction() as c:
        external_sync.import_messages(c, Provider(), "ana", "ops", "ext-1",
                                      [Line("user", "Draft the post.", "2026-01-01T00:00:00Z"),
                                       Line("assistant", "Here it is.", "2026-01-01T00:00:01Z")])
    assert _written(api, at) == [("human:ana", ops, "say", False), (ops, "human:ana", "say", False)]


def test_a_bot_hand_off_held_as_a_note_queues_nothing(api):
    """A message a rule turns quiet (a held bot-to-bot wake, a parked task's news) leaves a note, never a job."""
    ops, coo = _bot(api, "ops"), _bot(api, "coo")
    at = _since(api)
    with api.app.state.store.transaction() as c:
        H.say(c, ops, coo, "FYI the invoices are filed.")
    sent = [w for w in _written(api, at) if w[0] == ops]
    with api.app.state.store.read() as c:
        refs = json.loads(c.execute("SELECT refs FROM messages WHERE sender_id=? ORDER BY rowid DESC LIMIT 1",
                                    (ops,)).fetchone()[0])
    assert sent == [(ops, coo, "say", False)] and refs.get("quiet")

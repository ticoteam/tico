"""A bot's turn in a native thread, from the claim to the reply (docs/dev/threads-contract.md sections 4.4, 8, 11).

A mention in a thread starts the bot's run through a carrier, a keeper notice that names the message it carries; the
turn answers that message, in its thread (a sub-thread included), as the bot under its turn's grant. The claim names
the thread's root and audience class, which key the runner's session, and a completion can only echo the sub-thread
parent the claim handed out. A runner claims and completes turns over the attempt endpoints, as runner-rs does.
"""

from backend import execution, org_rule_check, threads
from backend.auth import Identity
from backend.store import H
from backend.tests.test_api import api, assign, claim, post, ready, runner  # noqa: F401  (the api fixture)

ANA = Identity("human:ana", "owner", "ana@acme.example")


def bot_runner(api, bot="cpo"):
    r = runner(api)
    assign(api, r, bot)
    ready(api, r, [bot])
    return r


def write(api, fn):
    store = api.app.state.store
    with store.transaction() as c:
        return fn(c, threads.viewer(api.app.state.auth, ANA))


def make_thread(api, **kw):
    return write(api, lambda c, v: threads.create(c, v, **kw))["id"]


def say(api, thread_id, text, parent=None):
    return write(api, lambda c, v: threads.post(c, v, thread_id, text, parent_message_id=parent))


def finish(api, r, attempt, text, **extra):
    aid = attempt["id"]
    post(api, f"attempts/{aid}/started", {"thread_id": "local-thread"}, token=r["token"])
    event = {"events": [{"seq": 1, "kind": "message", "payload": {"text": text, "final": True}}]}
    post(api, f"attempts/{aid}/events", event, token=r["token"])
    return post(api, f"attempts/{aid}/complete", {"outcome": "completed", "text": text, "last_seq": 1, **extra},
                token=r["token"])


def rows(api, sql, args=()):
    with api.app.state.store.read() as c:
        return [dict(row) for row in c.execute(sql, args)]


def replies(api, bot="cpo"):
    return rows(api, "SELECT id, conversation_id, to_actor, in_reply_to, body FROM messages WHERE from_actor=? "
                     "ORDER BY rowid", ("bot:" + bot,))


def test_a_mention_in_a_sub_thread_is_answered_in_that_sub_thread_to_the_asker(api):
    r = bot_runner(api)
    tid = make_thread(api, title="Launch", members=["bot:cpo"])
    parent = say(api, tid, "Kickoff notes")
    asked = say(api, tid, "@cpo list the risks", parent=parent["id"])
    sub = asked["conversation_id"]
    assert sub != tid
    attempt = claim(api, r, "cpo")
    assert attempt is not None, "a mention in a sub-thread starts the bot's run"
    assert (attempt["message"]["refs"] or {}).get("carries") == asked["id"], "the run starts from the carrier"
    assert attempt["conversation"]["root_id"] == tid and attempt["conversation"]["audience_class"] == "private"
    finish(api, r, attempt, "Risks: pricing, timing")
    [reply] = replies(api)
    assert reply["conversation_id"] == sub, "the answer lands in the sub-thread that asked, never the root"
    assert reply["to_actor"] == "human:ana" and reply["in_reply_to"] == asked["id"]


def test_a_carrier_turns_reply_is_withheld_when_the_bot_can_no_longer_post_there(api):
    r = bot_runner(api)
    tid = make_thread(api, title="Board prep", members=["bot:cpo"])
    say(api, tid, "@cpo draft the board note")
    attempt = claim(api, r, "cpo")
    assert attempt is not None
    with api.app.state.store.transaction() as c:      # archived mid-turn: nobody posts in it any more
        c.execute("UPDATE conversations SET archived_at=? WHERE id=?", (H.now(), tid))
    finish(api, r, attempt, "Here is the note")
    assert replies(api) == [], "the write door refuses the reply; nothing is posted"
    [run] = rows(api, "SELECT state, final_text FROM attempts WHERE id=?", (attempt["id"],))
    assert run == {"state": "completed", "final_text": "Here is the note"}, "the run itself is still done"
    assert rows(api, "SELECT 1 FROM events WHERE action='reply.withheld' AND target=?", (attempt["id"],))


def test_a_carried_message_taken_back_mid_turn_gets_no_reply(api):
    r = bot_runner(api)
    tid = make_thread(api, title="Pricing", members=["bot:cpo"])
    asked = say(api, tid, "@cpo check the tiers")
    attempt = claim(api, r, "cpo")
    assert attempt is not None
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE messages SET deleted_at=? WHERE id=?", (H.now(), asked["id"]))
    finish(api, r, attempt, "Tiers look fine")
    assert replies(api) == []


def test_a_completion_cannot_move_its_reply_under_another_message(api):
    # The runner echoes the parent its claim handed out; a parent it makes up is ignored, so no sub-thread is started
    # under a message the turn was never asked about.
    r = bot_runner(api)
    tid = make_thread(api, title="Roadmap", members=["bot:cpo"])
    other = say(api, tid, "Unrelated planning notes")
    # Addressed to the bot: its own job, not a carrier, so the completion's parent is all that could move the reply.
    write(api, lambda c, v: threads.post(c, v, tid, "Summarise the roadmap", to_actor="bot:cpo"))
    attempt = claim(api, r, "cpo")
    assert not (attempt["message"]["refs"] or {}).get("carries")
    assert attempt["message"].get("parent_message_id") is None, "a root message hangs off nothing"
    finish(api, r, attempt, "Summary", parent_message_id=other["id"])
    [reply] = replies(api)
    assert reply["conversation_id"] == tid
    assert rows(api, "SELECT id FROM conversations WHERE parent_message_id=?", (other["id"],)) == []


def test_public_and_private_threads_claim_different_audience_classes(api):
    r = bot_runner(api)
    public = make_thread(api, title="Handbook", audience="company", members=["bot:cpo"])
    private = make_thread(api, title="Board prep", members=["bot:cpo"])
    say(api, public, "@cpo update the handbook intro")
    first = claim(api, r, "cpo")
    finish(api, r, first, "Done with the handbook")
    say(api, private, "@cpo draft the board note")
    second = claim(api, r, "cpo")
    assert (first["conversation"]["root_id"], second["conversation"]["root_id"]) == (public, private)
    assert (first["conversation"]["audience_class"], second["conversation"]["audience_class"]) == ("public", "private")


def test_a_carrier_turns_reply_is_rule_checked_as_going_to_the_carried_sender(api, monkeypatch):
    r = bot_runner(api)
    tid = make_thread(api, title="Launch", members=["bot:cpo"])
    say(api, tid, "@cpo what is left?")
    attempt = claim(api, r, "cpo")
    checked = []
    monkeypatch.setattr(org_rule_check.R, "any_active", lambda c: True)
    monkeypatch.setattr(org_rule_check, "preflight",
                        lambda store, judge, who, operation, text, recipients, **kw: checked.append(recipients))
    finish(api, r, attempt, "Two items")
    assert checked == [["human:ana"]], "a carrier reply is never posted unchecked"


def test_only_the_keepers_notice_is_a_carrier():
    msg = {"id": "m1", "conversation_id": "t1", "from_actor": "human:ana", "refs": {"carries": "m0"}}
    assert execution.reply_source(None, msg) is msg, "a carries ref on a person's message is ignored"

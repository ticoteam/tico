"""Token saving (Dev 149): a bot's message to another bot waits for that bot's next run unless it needs action now.

Builder sample, Oct 18: 74 of 168 wakes were QA updates, a bot telling another bot something that could wait.
"""

from backend import bot_to_bot as B
from backend import hubdb as H
from backend.tests.test_api import api, assign, get, post, ready, runner  # noqa: F401


def claim(api, r, bot=None):
    return post(api, "jobs/claim", {"next_run": True, **({"bot": bot} if bot else {})}, token=r["token"])["attempt"]


def setup(api):
    r = runner(api)
    assign(api, r, "ops")
    assign(api, r, "cpo")
    ready(api, r, ["ops", "cpo"])
    return r


def jobs(api, bot):
    with api.app.state.store.read() as c:
        return c.execute("SELECT count(*) FROM jobs WHERE bot=?", (bot,)).fetchone()[0]


def ops_token(api, r):
    """A run of ops, so ops can write as a bot."""
    post(api, "chat/ops", {"text": "Run your check"})
    attempt = claim(api, r, "ops")
    assert attempt and attempt["bot"] == "ops"
    return attempt["token"]


def message(api, mid):
    with api.app.state.store.read() as c:
        return dict(c.execute("SELECT * FROM messages WHERE id=?", (mid,)).fetchone())


def test_a_bots_fyi_to_a_bot_starts_no_run_and_its_next_run_carries_it(api):
    r = setup(api)
    token = ops_token(api, r)
    sent = post(api, "messages", {"to": "cpo", "text": "QA update: the release candidate passed group 3."},
                token=token)
    assert jobs(api, "cpo") == 0
    assert sent["refs"].get(B.MARK) == B.RULE and message(api, sent["id"])["read_at"] is None
    # Any trigger: here a person's chat to cpo, in another room.
    post(api, "chat/cpo", {"text": "Morning, anything for me?"})
    attempt = claim(api, r, "cpo")
    notes = [n for n in attempt["notes"] if n.get("message") == sent["id"]]
    assert len(notes) == 1 and "the release candidate passed group 3" in notes[0]["text"]
    assert notes[0]["from"] == "bot:ops" and "in a direct message" in notes[0]["text"]
    assert message(api, sent["id"])["read_at"] is not None


def test_the_same_message_flagged_needs_action_wakes_the_bot_at_once(api):
    r = setup(api)
    token = ops_token(api, r)
    sent = post(api, "messages", {"to": "cpo", "text": "QA update: the release candidate passed group 3.",
                                  "needs_action": True}, token=token)
    assert jobs(api, "cpo") == 1
    assert B.MARK not in sent["refs"] and sent["refs"]["needs_action"] is True
    attempt = claim(api, r, "cpo")
    assert attempt["message"]["id"] == sent["id"] and not [n for n in attempt["notes"] if n.get("message")]


def test_a_persons_message_always_wakes_the_bot(api):
    setup(api)
    post(api, "messages", {"to": "cpo", "text": "Can you look at the roadmap today?"})
    assert jobs(api, "cpo") == 1


def test_a_quiet_message_is_never_lost_a_failed_run_hands_it_to_the_next_even_days_later(api):
    r = setup(api)
    token = ops_token(api, r)
    sent = post(api, "messages", {"to": "cpo", "text": "FYI: the docs PR merged."}, token=token)
    with api.app.state.store.transaction() as c:
        old = H.shift(H.now(), days=-3)
        c.execute("UPDATE messages SET created=? WHERE id=?", (old, sent["id"]))
        c.execute("UPDATE notes SET created=? WHERE message_id=?", (old, sent["id"]))
    post(api, "chat/cpo", {"text": "First"})
    first = claim(api, r, "cpo")
    assert [n["message"] for n in first["notes"] if n.get("message")] == [sent["id"]]
    post(api, f"attempts/{first['id']}/started", {"thread_id": "t1"}, token=r["token"])
    post(api, f"attempts/{first['id']}/complete", {"outcome": "failed", "text": "boom", "last_seq": 0}, token=r["token"])
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE jobs SET state='cancelled' WHERE state='uncertain'")
    post(api, "chat/cpo", {"text": "Second"})
    second = claim(api, r, "cpo")
    carried = [n for n in second["notes"] if n.get("message")]
    assert [n["message"] for n in carried] == [sent["id"]] and carried[0]["sent"] == old


def test_several_waiting_messages_reach_the_next_run_in_the_order_sent(api):
    r = setup(api)
    token = ops_token(api, r)
    ids = [post(api, "messages", {"to": "cpo", "text": f"Update {i}"}, token=token)["id"] for i in range(3)]
    assert jobs(api, "cpo") == 0
    post(api, "chat/cpo", {"text": "Go"})
    assert [n["message"] for n in claim(api, r, "cpo")["notes"] if n.get("message")] == ids


def test_a_bots_comment_to_a_bot_on_a_task_waits_and_that_tasks_next_run_reads_it_in_its_history(api):
    r = setup(api)
    with api.app.state.store.transaction() as c:
        t = H.task_create(c, "bot:ops", "Ship the export", "Build it", "bot:cpo")
    before = jobs(api, "cpo")
    with api.app.state.store.transaction() as c:
        msg = H.task_comment(c, "bot:ops", t["id"], "QA: groups 1-3 pass on the candidate.")
    assert jobs(api, "cpo") == before and msg["refs"][B.MARK] == B.RULE
    with api.app.state.store.transaction() as c:
        notes = H._rows(c.execute("SELECT * FROM notes WHERE message_id=?", (msg["id"],)))
    assert len(notes) == 1 and f"on task Ship the export ({t['id']})" in notes[0]["body"]
    # The task's next run, whatever starts it, reads the held comment in the task's own history, so the note is
    # marked carried and the message read without repeating it.
    attempt = claim(api, r, "cpo")
    assert attempt["task"]["id"] == t["id"]
    assert not [n for n in attempt["notes"] if n.get("message") == msg["id"]]
    assert message(api, msg["id"])["read_at"] is not None
    with api.app.state.store.read() as c:
        assert c.execute("SELECT carried_by FROM notes WHERE message_id=?", (msg["id"],)).fetchone()[0] == attempt["id"]


def test_a_bots_comment_flagged_needs_action_wakes_the_task_owner(api):
    setup(api)
    with api.app.state.store.transaction() as c:
        t = H.task_create(c, "bot:ops", "Ship the export", "Build it", "bot:cpo")
        before = c.execute("SELECT count(*) FROM jobs WHERE bot='cpo'").fetchone()[0]
        H.task_comment(c, "bot:ops", t["id"], "The release is blocked on your fix.", extra_refs={"needs_action": True})
        assert c.execute("SELECT count(*) FROM jobs WHERE bot='cpo'").fetchone()[0] == before + 1


def test_never_delayed_asks_answers_gate_verdicts_approvals_and_awaited_replies(api):
    setup(api)
    sha = "a" * 40
    with api.app.state.store.transaction() as c:
        ask = H.say(c, "bot:cpo", "bot:ops", "Which release has the fix?", kind="ask")
        start = c.execute("SELECT count(*) FROM jobs WHERE bot='ops'").fetchone()[0]
        H.say(c, "bot:cpo", "bot:ops", "Is the export done?", kind="ask")
        H.say(c, "bot:cpo", "bot:ops", f"Review done.\nVERDICT: changes PR #12 head {sha}")
        H.say(c, "bot:cpo", "bot:ops", "Please spend this", refs={"approval": "a1"})
        flagged = H.say(c, "bot:cpo", "bot:ops", "Need the build now", refs={"needs_action": True})
        assert c.execute("SELECT count(*) FROM jobs WHERE bot='ops'").fetchone()[0] == start + 4
        before = c.execute("SELECT count(*) FROM jobs WHERE bot='cpo'").fetchone()[0]
        # Replies the asker waits on: to its ask and to its needs-action message.
        H.say(c, "bot:ops", "bot:cpo", "v0.3.62", conversation_id=ask["conversation_id"], in_reply_to=ask["id"])
        H.say(c, "bot:ops", "bot:cpo", "Built", conversation_id=flagged["conversation_id"], in_reply_to=flagged["id"])
        assert c.execute("SELECT count(*) FROM jobs WHERE bot='cpo'").fetchone()[0] == before + 2


def test_the_rule_fails_open_and_never_touches_a_persons_or_the_keepers_messages():
    class Conn:
        def execute(self, *a):
            raise RuntimeError("no database")
    conn = Conn()
    assert B.can_wait(conn, "bot:a", "bot:b", "say", {}, "fyi") is True
    assert B.can_wait(conn, "bot:a", "bot:b", "say", None, "fyi") is False
    assert B.can_wait(conn, "bot:a", "bot:b", "say", {"needs_action": "yes"}, "fyi") is False
    assert B.can_wait(conn, "bot:a", "bot:b", "say", {"needs_action": None}, "fyi") is False
    assert B.can_wait(conn, "bot:a", "bot:b", "say", {}, "fyi", in_reply_to="m1") is False
    assert B.can_wait(conn, "bot:a", "bot:b", "say", {"something_new": 1}, "fyi") is False
    assert B.can_wait(conn, "human:ana", "bot:b", "say", {}, "fyi") is False
    assert B.can_wait(conn, H.KEEPER, "bot:b", "notice", {}, "New task") is False
    assert B.can_wait(conn, "bot:a", "human:ana", "say", {}, "fyi") is False
    for kind in ("ask", "answer", "steer"):
        assert B.can_wait(conn, "bot:a", "bot:b", kind, {}, "fyi") is False


def test_a_flagged_wake_counts_in_the_weekly_table_and_a_waiting_one_does_not(api):
    # Dev 139's run_weekly: bot-to-bot wakes are its `comment` row, so the drop shows there week on week.
    r = setup(api)
    token = ops_token(api, r)
    fyi = post(api, "messages", {"to": "cpo", "text": "FYI only"}, token=token)
    post(api, "messages", {"to": "cpo", "text": "Act now", "needs_action": True}, token=token)
    with api.app.state.store.read() as c:
        queued = [dict(row) for row in c.execute(
            "SELECT m.body FROM jobs j JOIN messages m ON m.id=j.message_id WHERE j.bot='cpo'")]
    assert [row["body"] for row in queued] == ["Act now"]
    attempt = claim(api, r, "cpo")
    assert attempt["message"]["body"] == "Act now"
    # The FYI is in the same room, which the run reads: it is read now, not repeated as a note.
    assert not [n for n in attempt["notes"] if n.get("message")] and message(api, fyi["id"])["read_at"]

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
        c.execute("UPDATE messages SET created_at=? WHERE id=?", (old, sent["id"]))
        c.execute("UPDATE notes SET created_at=? WHERE message_id=?", (old, sent["id"]))
    post(api, "chat/cpo", {"text": "First"})
    first = claim(api, r, "cpo")
    assert [n["message"] for n in first["notes"] if n.get("message")] == [sent["id"]]
    post(api, f"attempts/{first['id']}/started", {"thread_id": "t1"}, token=r["token"])
    post(api, f"attempts/{first['id']}/complete", {"outcome": "failed", "text": "boom", "last_seq": 0}, token=r["token"])
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE jobs SET state='cancelled' WHERE state='uncertain'")
        c.execute("UPDATE run_backoff SET wait_until=NULL")        # the fast failure's wait is over
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


def test_a_bots_comment_to_a_bot_on_a_task_waits_and_that_tasks_fresh_session_gets_it_in_its_prompt(api):
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
    # The task's first run is a fresh session, which reads no room history: the held comment still reaches its
    # prompt as a note (the runner puts every carried note in the prompt), and is read then.
    attempt = claim(api, r, "cpo")
    assert attempt["task"]["id"] == t["id"]
    assert [n["message"] for n in attempt["notes"] if n.get("message")] == [msg["id"]]
    assert message(api, msg["id"])["read_at"] is not None
    # That run fails; the retry, a fresh session in the same room again, gets the comment again.
    post(api, f"attempts/{attempt['id']}/started", {"thread_id": "t1"}, token=r["token"])
    post(api, f"attempts/{attempt['id']}/complete", {"outcome": "failed", "text": "boom", "last_seq": 0}, token=r["token"])
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE jobs SET state='queued',attempt_id=NULL WHERE state='uncertain'")
        c.execute("UPDATE run_backoff SET wait_until=NULL")        # the fast failure's wait is over
    retry = claim(api, r, "cpo")
    assert retry and [n["message"] for n in retry["notes"] if n.get("message")] == [msg["id"]]


def test_a_backlog_past_one_runs_notes_gets_the_next_run_and_nothing_is_left(api):
    r = setup(api)
    with api.app.state.store.transaction() as c:
        for i in range(3):
            H.note_create(c, "human:ana", "bot:cpo", f"Ordinary note {i}")
        # Three senders: one room takes at most 20 bot-to-bot messages an hour.
        ids = [H.say(c, ("bot:ops", "bot:coo", "bot:finance")[i % 3], "bot:cpo", f"Update {i}")["id"] for i in range(55)]
    assert jobs(api, "cpo") == 0
    post(api, "chat/cpo", {"text": "Go"})
    seen, runs = [], 0
    while True:
        attempt = claim(api, r, "cpo")
        if not attempt:
            break
        runs += 1
        seen += [n["message"] for n in attempt["notes"] if n.get("message")]
        post(api, f"attempts/{attempt['id']}/started", {"thread_id": f"t{runs}"}, token=r["token"])
        post(api, f"attempts/{attempt['id']}/complete", {"outcome": "completed", "text": "ok", "last_seq": 0},
             token=r["token"])
        assert runs < 5
    assert seen == ids and runs == 2
    assert all(message(api, mid)["read_at"] for mid in ids)


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
    assert [n["message"] for n in attempt["notes"] if n.get("message")] == [fyi["id"]] and message(api, fyi["id"])["read_at"]


def test_a_task_comment_says_whether_it_woke_the_bot(api):
    r = setup(api)
    token = ops_token(api, r)
    with api.app.state.store.transaction() as c:
        t = H.task_create(c, "bot:ops", "Ship the export", "Build it", "bot:cpo")
    plain = post(api, f"tasks/{t['id']}/comments", {"text": "FYI: docs merged."}, token=token)
    flagged = post(api, f"tasks/{t['id']}/comments", {"text": "Blocked on you.", "needs_action": True}, token=token)
    assert plain["woke"] is False and flagged["woke"] is True


def test_needs_action_wakes_the_owner_of_a_snoozed_task_where_a_plain_comment_does_not(api):
    r = setup(api)
    token = ops_token(api, r)
    with api.app.state.store.transaction() as c:
        t = H.task_create(c, "bot:ops", "Ship the export", "Build it", "bot:cpo")
        c.execute("UPDATE tasks SET snoozed_until=? WHERE id=?", (H.shift(H.now(), days=2), t["id"]))
        c.execute("DELETE FROM jobs WHERE bot='cpo'")
    plain = post(api, f"tasks/{t['id']}/comments", {"text": "FYI: docs merged."}, token=token)
    assert plain["woke"] is False and jobs(api, "cpo") == 0
    flagged = post(api, f"tasks/{t['id']}/comments", {"text": "Blocked on you.", "needs_action": True}, token=token)
    assert flagged["woke"] is True and jobs(api, "cpo") == 1
    with api.app.state.store.transaction() as c:
        direct = H.task_comment(c, "bot:ops", t["id"], "Still blocked.", wake=False, extra_refs={"needs_action": True})
        assert not direct["refs"].get("quiet")


def test_a_held_message_or_comment_tells_its_sender_how_to_wake_the_bot_now(api):
    # Dev 328: on Oct 21 Support's escalations to DevOps were held (no needs_action) and read as delivered.
    r = setup(api)
    token = ops_token(api, r)
    with api.app.state.store.transaction() as c:
        t = H.task_create(c, "bot:ops", "Open the window", "x", "bot:cpo")
        c.execute("DELETE FROM jobs WHERE bot='cpo'")
    dm = post(api, "messages", {"to": "cpo", "text": "Deadline 14:10Z"}, token=token)
    comment = post(api, f"tasks/{t['id']}/comments", {"text": "Deadline 14:10Z"}, token=token)
    for sent in (dm, comment):
        assert sent["woke"] is False and jobs(api, "cpo") == 0
        assert "held for cpo's next run" in sent["held"] and "--needs-action" in sent["held"]
    flagged = post(api, "messages", {"to": "cpo", "text": "Deadline 14:10Z", "needs_action": True}, token=token)
    woke = post(api, f"tasks/{t['id']}/comments", {"text": "Deadline 14:10Z", "needs_action": True}, token=token)
    assert "held" not in flagged and "held" not in woke and woke["woke"] is True
    assert "held" not in post(api, "messages", {"to": "cpo", "text": "Morning"})     # a person's always wakes


def test_a_held_message_with_a_file_keeps_its_hold_and_tells_its_sender(api):
    # Dev 328 Review 1: adding the file rewrote the message's references and dropped the hold mark.
    import base64
    from backend.tests.test_files import publish
    r = setup(api)
    token = ops_token(api, r)
    with api.app.state.store.transaction() as c:
        c.execute("DELETE FROM jobs WHERE bot='cpo'")
    first = post(api, "messages", {"to": "cpo", "text": "Chart follows"}, token=token)
    made = publish(api, {"token": token}, name="chart.png", text=None,
                   content_base64=base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"\x00" * 64).decode())
    assert made.status_code == 200, made.text
    fid = made.json()["file"]["id"]
    with api.app.state.store.transaction() as c:   # a file of the ops-cpo conversation
        c.execute("UPDATE bot_files SET scope=? WHERE id=?", ("conversation:" + first["conversation_id"], fid))
    sent = post(api, "messages", {"to": "cpo", "text": "Deadline 14:10Z", "conversation_id": first["conversation_id"],
                                  "attachments": [{"ref": f"{fid}@1", "title": "Window"}]}, token=token)
    assert sent["refs"]["attachments"] and jobs(api, "cpo") == 0
    assert sent["woke"] is False and "held for cpo's next run" in sent["held"] and "--needs-action" in sent["held"]
    assert sent["refs"].get(B.MARK) == B.RULE and sent["refs"].get("quiet") is True


def test_the_cli_prints_why_a_message_was_held(monkeypatch, capsys):
    from types import SimpleNamespace
    from clients import remotecli
    held = "held for cpo's next run; nothing runs it now. Add --needs-action ..."
    monkeypatch.setattr(remotecli, "run", lambda *_: {"id": "m1", "woke": False, "held": held})
    assert remotecli.main(SimpleNamespace(cmd="message", fn="message send")) == 0
    assert capsys.readouterr().err.strip() == "hub: " + held


# Guards, not the fails-first test (Dev 328 C): they pass on main. Escalations that ask for action reach a bot whose
# task waits on a blocked_by relation: each starts a run that bot can claim.
def waiting_on_a_hold(api):
    with api.app.state.store.transaction() as c:
        hold = H.task_create(c, "bot:ops", "Hold: no merges until released", "x", "bot:ops")
        t = H.task_create(c, "bot:ops", "Open the window", "x", "bot:cpo")
    post(api, "tasks/" + t["id"] + "/relations", {"task": hold["id"], "kind": "blocked_by"})
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE tasks SET status='waiting' WHERE id=?", (t["id"],))
        c.execute("DELETE FROM jobs WHERE bot='cpo'")
    return t


def test_guard_needs_action_comment_wakes_a_bot_waiting_on_a_blocked_by(api):
    r = setup(api)
    token = ops_token(api, r)
    t = waiting_on_a_hold(api)
    out = post(api, f"tasks/{t['id']}/comments", {"text": "Deadline 14:10Z", "needs_action": True}, token=token)
    assert out["woke"] is True and jobs(api, "cpo") == 1
    attempt = claim(api, r, "cpo")
    assert attempt and attempt["task"]["id"] == t["id"]


def test_guard_needs_action_message_wakes_a_bot_waiting_on_a_blocked_by(api):
    r = setup(api)
    token = ops_token(api, r)
    t = waiting_on_a_hold(api)
    post(api, "messages", {"to": "cpo", "text": "Deadline 14:10Z", "needs_action": True, "refs": {"task": t["id"]}},
         token=token)
    assert jobs(api, "cpo") == 1 and claim(api, r, "cpo")


def test_guard_a_tracked_ask_wakes_a_bot_waiting_on_a_blocked_by(api):
    r = setup(api)
    token = ops_token(api, r)
    t = waiting_on_a_hold(api)
    post(api, "messages", {"to": "cpo", "text": "Is the window open?", "kind": "ask", "wait_s": 30,
                           "refs": {"task": t["id"]}}, token=token)
    assert jobs(api, "cpo") == 1 and claim(api, r, "cpo")

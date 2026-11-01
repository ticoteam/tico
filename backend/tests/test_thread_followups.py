"""Follow-ups: an ask to a teammate, from a person or a bot, unanswered past the threshold, gets one nudge in its
thread. The watcher is plain code: it never queues a model run, a quiet run writes nothing, and an answered ask is never nudged."""

from datetime import datetime, timedelta, timezone

from backend import hubdb as H
from backend import scheduler, threads, thread_followups
from backend.tests.test_api import api, headers, setup_attempt  # noqa: F401


def later(hours):
    return H.shift(H.now(), hours=hours)


def bot_thread(api, task_id=None):
    with api.app.state.store.transaction() as c:
        return threads.create(c, "bot:finance", title="Books", audience="members", members=["human:ana"],
                              task_id=task_id)["id"]


def ask(api, thread_id, text="Can you confirm the Q3 invoice?"):
    with api.app.state.store.transaction() as c:
        return threads.post(c, "bot:finance", thread_id, text, kind="ask", to_actor="human:ana")


def sweep(api, at):
    with api.app.state.store.transaction() as c:
        return thread_followups.sweep(c, at)


def counts(api):
    with api.app.state.store.read() as c:
        return tuple(c.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                     for table in ("jobs", "messages", "thread_followups"))


def test_an_unanswered_ask_is_nudged_once_in_its_thread(api):
    setup_attempt(api, "finance")
    tid = bot_thread(api)
    message = ask(api, tid)
    assert sweep(api, H.now()) == []                     # not yet: the threshold is a day
    done = sweep(api, later(25))
    assert [d["message"] for d in done] == [message["id"]]
    with api.app.state.store.read() as c:
        nudge = c.execute("SELECT body FROM messages WHERE conversation_id=? AND kind='notice' "
                          "AND to_actor='human:ana'", (tid,)).fetchone()
    assert nudge is not None and "Can you confirm" in nudge["body"]
    assert sweep(api, later(48)) == []                   # one nudge, however often the watcher runs


def test_a_quiet_run_writes_nothing_and_queues_no_model(api):
    setup_attempt(api, "finance")
    ask(api, bot_thread(api))
    before = counts(api)
    assert sweep(api, H.now()) == []
    assert counts(api) == before


def test_an_answered_ask_is_never_nudged(api):
    setup_attempt(api, "finance")
    tid = bot_thread(api)
    ask(api, tid)
    with api.app.state.store.transaction() as c:
        threads.post(c, "human:ana", tid, "Yes, confirmed.")
    assert sweep(api, later(25)) == []


def test_a_task_thread_ask_is_left_to_its_card(api):
    setup_attempt(api, "finance")
    with api.app.state.store.transaction() as c:
        task = H.task_create(c, "human:ana", "Confirm the invoice", "Confirm it.", "human:ana")
    tid = bot_thread(api, task_id=task["id"])
    ask(api, tid)
    assert sweep(api, later(25)) == []


def test_a_nudge_in_a_direct_message_never_queues_the_bot(api):
    """A DM's members are the bot and the person, so a notice there reaches the bot by the DM rule: the nudge must not
    start a run for the bot that asked (a nudge is for the person)."""
    setup_attempt(api, "finance")
    with api.app.state.store.transaction() as c:
        dm = threads.dm(c, "bot:finance", ["human:ana"], title="Invoice")["id"]
    with api.app.state.store.transaction() as c:      # control: a person's message here does wake the bot
        said = threads.post(c, "human:ana", dm, "Checking now.")
        woke = c.execute("SELECT COUNT(*) FROM message_deliveries WHERE message_id=? AND recipient='bot:finance' "
                         "AND mode IN ('steer','queue')", (said["id"],)).fetchone()[0]
    assert woke == 1
    ask(api, dm)                                       # asked after her message, so it is still unanswered
    sweep(api, later(25))
    with api.app.state.store.read() as c:
        nudge = c.execute("SELECT nudge_message_id FROM thread_followups").fetchone()["nudge_message_id"]
        queued = c.execute("SELECT COUNT(*) FROM message_deliveries WHERE message_id=? AND recipient LIKE 'bot:%' "
                           "AND mode IN ('steer','queue')", (nudge,)).fetchone()[0]
    assert queued == 0


def test_the_scheduled_run_nudges_through_the_same_sweep(api):
    setup_attempt(api, "finance")
    tid = bot_thread(api)
    message = ask(api, tid)
    at = datetime.now(timezone.utc) + timedelta(hours=25)
    scheduler.run_thread_followups(api.app.state.store, api.app.state.auth, at)
    with api.app.state.store.read() as c:
        rows = c.execute("SELECT message_id FROM thread_followups").fetchall()
    assert [r["message_id"] for r in rows] == [message["id"]]


def test_a_legacy_ask_stays_hidden_from_a_reader_it_was_hidden_from_in_its_nudge(api):
    """A migrated ask carries its old room's audience. Ana reads it through that room; Cara, a member of the thread,
    does not. Her view of the nudge must match her view of the ask, and the nudge must not quote it."""
    setup_attempt(api, "finance")
    with api.app.state.store.transaction() as c:
        tid = threads.create(c, "bot:finance", title="Books", audience="members",
                             members=["human:ana", "human:cara"])["id"]
    message = ask(api, tid, "Can you confirm the Q3 invoice?")
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE messages SET legacy_audience='shared:finance' WHERE id=?", (message["id"],))
    sweep(api, later(25))
    with api.app.state.store.transaction() as c:
        cara = threads.actor_viewer(c, "human:cara")
        nudge = c.execute("SELECT id, body, legacy_audience FROM messages WHERE conversation_id=? AND kind='notice' "
                          "AND to_actor='human:ana'", (tid,)).fetchone()
        assert nudge is not None
        assert nudge["legacy_audience"] == "shared:finance"
        assert threads.readable(c, cara, message["id"]) is False
        assert threads.readable(c, cara, nudge["id"]) is False
        assert "Can you confirm" not in nudge["body"]


def test_an_ask_whose_recipient_lost_access_is_retired_not_nudged(api):
    setup_attempt(api, "finance")
    tid = bot_thread(api)
    ask(api, tid)
    with api.app.state.store.transaction() as c:
        c.execute("DELETE FROM thread_members WHERE thread_id=? AND actor='human:ana'", (tid,))
    assert sweep(api, later(25)) == []
    assert sweep(api, later(48)) == []
    with api.app.state.store.read() as c:
        rows = c.execute("SELECT nudge_message_id FROM thread_followups").fetchall()
    assert [r["nudge_message_id"] for r in rows] == [None]    # retired: the row stays so it is never reconsidered


def test_one_failed_follow_up_does_not_stop_the_others(api, monkeypatch):
    setup_attempt(api, "finance")
    first, second = bot_thread(api), bot_thread(api)
    ask(api, first, "First question?")
    ask(api, second, "Second question?")
    real_post = threads.post

    def failing_post(c, principal, thread_id, body, **kw):
        if thread_id == first and kw.get("kind") == "notice":
            raise RuntimeError("the write for this one fails")
        return real_post(c, principal, thread_id, body, **kw)
    monkeypatch.setattr(threads, "post", failing_post)
    done = sweep(api, later(25))
    assert [d["thread"] for d in done] == [second]
    with api.app.state.store.read() as c:
        nudged = c.execute("SELECT conversation_id, nudge_message_id FROM thread_followups "
                           "ORDER BY conversation_id").fetchall()
    assert {r["conversation_id"]: r["nudge_message_id"] is not None for r in nudged} == {
        first: False, second: True}


def test_a_persons_ask_to_a_person_and_to_a_bot_are_each_nudged_once_and_a_bot_is_not_woken(api):
    setup_attempt(api, "finance")
    with api.app.state.store.transaction() as c:
        tid = threads.create(c, "human:ana", title="Books", audience="members", members=["human:ben", "bot:finance"])["id"]
        to_person = threads.post(c, "human:ana", tid, "Ben, is the Q3 close done?", kind="ask", to_actor="human:ben")
        to_bot = threads.post(c, "human:ana", tid, "Finance, where is the Q3 invoice?", kind="ask", to_actor="bot:finance")
    jobs = counts(api)[0]
    done = sweep(api, later(25))
    assert {(d["message"], d["to"]) for d in done} == {(to_person["id"], "human:ben"), (to_bot["id"], "bot:finance")}
    assert counts(api)[0] == jobs                        # plain code: the nudge starts no run
    assert sweep(api, later(48)) == []


def test_an_ask_answered_by_a_post_is_not_nudged_and_one_older_than_the_rollout_stays_as_it_is(api):
    with api.app.state.store.transaction() as c:
        tid = threads.create(c, "human:ana", title="Books", audience="members", members=["human:ben"])["id"]
        answered = threads.post(c, "human:ana", tid, "Ben, ready?", kind="ask", to_actor="human:ben")
        threads.post(c, "human:ben", tid, "Yes.")
    assert answered["id"] not in {d["message"] for d in sweep(api, later(25))}
    with api.app.state.store.transaction() as c:
        tid = threads.create(c, "human:ana", title="Old", audience="members", members=["human:ben"])["id"]
        threads.post(c, "human:ana", tid, "Ben, from long ago?", kind="ask", to_actor="human:ben")
    assert sweep(api, later(24 * 8)) == []


def closed_home(api, slug="finance"):
    bot = "bot:" + slug
    setup_attempt(api, slug)
    with api.app.state.store.transaction() as c:
        room = H.open_conversation(c, "human:ana", ["human:ana", bot], kind="chat", subject="Books room", scope="shared",
                                   room_key=slug, allow_held=True)
        H.say(c, "human:ana", bot, "Old close notes", conversation_id=room["id"])
        c.execute("UPDATE messages SET legacy_audience=?, restricted=1 WHERE conversation_id=?", ("shared:" + slug, room["id"]))
        threads._store_shared_homes(c, slug)
    return room["id"]


def earlier_sweep(api):
    with api.app.state.store.transaction() as c:
        return thread_followups.earlier_sweep(c, api.app.state.auth, H.now())


def test_a_manager_hears_once_about_a_closed_history_in_their_dm_with_the_bot_and_no_run_starts(api):
    home = closed_home(api)
    jobs = counts(api)[0]
    done = earlier_sweep(api)
    assert {d["to"] for d in done} == {"human:ana"} and {d["bot"] for d in done} == {"finance"}
    assert earlier_sweep(api) == []
    with api.app.state.store.read() as c:
        rows = c.execute("SELECT m.conversation_id, m.refs_json, m.from_actor, cv.audience, cv.id AS thread FROM messages m "
                         "JOIN conversations cv ON cv.id=m.conversation_id "
                         "WHERE m.kind='notice' AND m.refs_json LIKE '%earlier_messages%'").fetchall()
        assert len(rows) == 1 and rows[0]["conversation_id"] != home, "the Home's readers do not get this notice"
        # Their own chat with the bot, a stored DM or the older personal room already there.
        assert threads.audience_of(threads.thread_row(c, rows[0]["conversation_id"])) == "personal"
        assert rows[0]["from_actor"] == H.KEEPER
        assert H._json(rows[0]["refs_json"], {})["earlier_messages"]["thread_id"] == home
        woken = c.execute("SELECT COUNT(*) FROM message_deliveries WHERE message_id IN (SELECT id FROM messages "
                          "WHERE refs_json LIKE '%earlier_messages%') AND recipient LIKE 'bot:%' AND mode<>'none'").fetchone()[0]
    assert woken == 0 and counts(api)[0] == jobs
    seen = api.get(f"/api/v2/threads/{rows[0]['thread']}/timeline", headers=headers()).json()["messages"]
    assert [m["refs"]["earlier_messages"]["thread_id"] for m in seen if "earlier_messages" in (m.get("refs") or {})] == [home]


def test_nobody_more_is_told_once_the_history_is_open(api):
    closed_home(api)
    with api.app.state.store.transaction() as c:
        threads.open_earlier_messages(c, H.KEEPER, "finance")
    assert earlier_sweep(api) == []

"""Earlier messages: a manager hears once, in its own chat with the bot, that a closed history of its room is there to read
(thread_followups.earlier_sweep). The sweep is plain code: it starts no run, and nobody else is told. The old ask nudges are
gone: an unanswered question goes back to the bot that asked on its task roundup (backend/still_needed.py)."""

from backend.tests.slug_ids import bot_ids_are_their_slugs  # noqa: F401  (each bot's id is its slug)
from backend import hubdb as H
from backend import threads, thread_followups
from backend.tests.test_api import api, headers, setup_attempt  # noqa: F401


def counts(api):
    with api.app.state.store.read() as c:
        return tuple(c.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                     for table in ("jobs", "messages", "thread_followups"))


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
        rows = c.execute("SELECT m.conversation_id, m.refs, m.sender_id, cv.audience, cv.id AS thread FROM messages m "
                         "JOIN conversations cv ON cv.id=m.conversation_id "
                         "WHERE m.kind='notice' AND m.refs LIKE '%earlier_messages%'").fetchall()
        assert len(rows) == 1 and rows[0]["conversation_id"] != home, "the Home's readers do not get this notice"
        # Their own chat with the bot, a stored DM or the older personal room already there.
        assert threads.audience_of(threads.thread_row(c, rows[0]["conversation_id"])) == "personal"
        assert rows[0]["sender_id"] == H.KEEPER
        assert H._json(rows[0]["refs"], {})["earlier_messages"]["thread_id"] == home
        woken = c.execute("SELECT COUNT(*) FROM message_deliveries WHERE message_id IN (SELECT id FROM messages "
                          "WHERE refs LIKE '%earlier_messages%') AND recipient LIKE 'bot:%' AND mode<>'none'").fetchone()[0]
    assert woken == 0 and counts(api)[0] == jobs
    seen = api.get(f"/api/v2/threads/{rows[0]['thread']}/timeline", headers=headers()).json()["messages"]
    assert [m["refs"]["earlier_messages"]["thread_id"] for m in seen if "earlier_messages" in (m.get("refs") or {})] == [home]


def test_nobody_more_is_told_once_the_history_is_open(api):
    closed_home(api)
    with api.app.state.store.transaction() as c:
        threads.open_earlier_messages(c, H.KEEPER, "finance")
    assert earlier_sweep(api) == []

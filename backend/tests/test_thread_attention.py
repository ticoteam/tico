"""Needs attention: a thread item is listed for the person it concerns and clears when they act on it (a reply, a read,
a release), a message outside the thread's audience never reaches its person, and a paused thread is listed only to
the people who can manage it."""

from backend import threads
from backend.auth import Identity
from backend import hubdb as H
from backend.tests.test_api import api, get, setup_attempt  # noqa: F401


def members_thread(api, members=("human:ana",), title="Launch"):
    with api.app.state.store.transaction() as c:
        return threads.create(c, "human:cara", title=title, audience="members", members=list(members))["id"]


def say(api, thread_id, text, *, actor="human:cara", **kw):
    with api.app.state.store.transaction() as c:
        return threads.post(c, actor, thread_id, text, **kw)


def needs(api, kind=None, token="ana-test"):
    items = get(api, "needs-you", token=token)["items"]
    return [i for i in items if kind is None or i["kind"] == kind]


def ana_viewer(api):
    return threads.viewer(api.app.state.auth, Identity("human:ana", "owner", "ana@acme.example"))


def test_a_message_to_the_person_is_listed_until_they_reply(api):
    tid = members_thread(api)
    say(api, tid, "Can you look at the lease?", to_actor="human:ana")
    listed = needs(api, "thread_mention")
    assert [i["conversation_id"] for i in listed] == [tid]
    say(api, tid, "On it.", actor="human:ana")
    assert needs(api, "thread_mention") == []


def test_a_question_is_listed_until_the_person_answers_in_the_thread(api):
    tid = members_thread(api)
    say(api, tid, "Which lease, the Main St one?", kind="ask", to_actor="human:ana")
    assert [i["conversation_id"] for i in needs(api, "thread_ask")] == [tid]
    say(api, tid, "The Main St one.", actor="human:ana")
    assert needs(api, "thread_ask") == []


def test_a_thread_outside_the_person_audience_is_not_listed(api):
    tid = members_thread(api, members=())          # cara alone: a mention of ana does not add her
    say(api, tid, "Ana, see this.", to_actor="human:ana")
    assert all(i["conversation_id"] != tid for i in needs(api))


def test_an_unread_dm_is_listed_until_it_is_read(api):
    with api.app.state.store.transaction() as c:
        dm = threads.dm(c, "human:cara", ["human:ana"], title="Lease")["id"]
    say(api, dm, "Call me when you can.")
    assert [i["conversation_id"] for i in needs(api, "thread_dm")] == [dm]
    with api.app.state.store.transaction() as c:
        threads.mark_all_read(c, ana_viewer(api), dm)
    assert needs(api, "thread_dm") == []


def test_a_held_message_replaces_the_mention_until_released(api):
    tid = members_thread(api)
    message = say(api, tid, "Ana, the offer letter.", to_actor="human:ana")
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE message_deliveries SET state='held' WHERE message_id=? AND recipient='human:ana'",
                  (message["id"],))
    assert needs(api, "thread_mention") == []
    assert [i["conversation_id"] for i in needs(api, "thread_held")] == [tid]
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE message_deliveries SET state='delivered' WHERE message_id=? AND recipient='human:ana'",
                  (message["id"],))
    assert needs(api, "thread_held") == []
    assert [i["conversation_id"] for i in needs(api, "thread_mention")] == [tid]


def test_a_paused_thread_is_listed_to_its_manager_only(api):
    tid = members_thread(api)
    with api.app.state.store.transaction() as c:
        now = H.now()
        c.execute("INSERT INTO message_deliveries(id, message_id, recipient, thread_id, root_id, mode, reason, cause, "
                  "state, created_at, updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                  (H.new_id(), H.new_id(), "bot:finance", tid, tid, "queue", "mention", "bot", "capped", now, now))
    assert [i["conversation_id"] for i in needs(api, "thread_capped", token="cara-test")] == [tid]
    assert needs(api, "thread_capped") == []

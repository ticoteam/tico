"""Thread events on the live stream (docs/dev/threads-contract.md §9; backend/thread_events.py, threads_scope in
backend/events.py): a message says which thread it is in, a `?thread=` follow sends that subtree and nothing else, the
`threads` topic says what changed in a thread for the people who may read it, a read mark goes only to its reader, and
an agent token gets only the topics it may read. In-process through events.read, so every answer is that viewer's."""

from backend import events as E
from backend import task_privacy
from backend.auth import Identity
from backend.tests.test_api import api, headers  # noqa: F401  (the fixture and its helper)
from backend.tests.test_automation_api import as_bot


def who(api, token):
    return api.app.state.auth.settings.test_identities[token]


def new_thread(api, title="Budget", members=("human:ben",), body=None, parent_message_id=None):
    payload = {"title": title, "audience": "members", "members": list(members)}
    if body:
        payload["body"] = body
    if parent_message_id:
        payload["parent_message_id"] = parent_message_id
    response = api.post("/api/v2/threads", json=payload, headers=headers("ana-test"))
    assert response.status_code == 200, response.text
    return response.json()


def say(api, thread_id, text, token="ana-test"):
    response = api.post(f"/api/v2/threads/{thread_id}/messages", json={"text": text}, headers=headers(token))
    assert response.status_code == 200, response.text
    return response.json()


def cursor(api):
    with api.app.state.store.read() as c:
        return E.latest(c)


def read_as(api, person, after, topics, threads_scope=()):
    """The events `person` may be sent after `after`: (seq, topic, data) as events.read gives them."""
    auth = api.app.state.auth
    with api.app.state.store.read() as c:
        events, _, _, _ = E.read(c, auth, person, after, topics=topics, threads_scope=threads_scope,
                                 task_views=lambda rows, c, who, visible_sql="1": [], task_view=None)
    return events


def test_a_message_says_its_thread_root_seq_and_revision_and_only_a_restricted_one_asks_the_privacy_check(api, monkeypatch):
    store = api.app.state.store
    thread = new_thread(api, body="Kickoff")
    after = cursor(api)
    plain = say(api, thread["id"], "A plain reply")
    restricted = say(api, thread["id"], "A restricted reply")
    with store.transaction() as c:
        c.execute("UPDATE messages SET restricted=1 WHERE id=?", (restricted["id"],))
    asked, real = [], task_privacy.message_readable
    monkeypatch.setattr(task_privacy, "message_readable", lambda c, p, m: asked.append(m["id"]) or real(c, p, m))
    events = {d["id"]: d for _, topic, d in read_as(api, who(api, "ben-test"), after, ("messages",)) if topic == "messages"}
    assert plain["id"] in events                    # a member is sent the plain reply
    assert events[plain["id"]]["thread_id"] == thread["id"] and events[plain["id"]]["root_id"] == thread["id"]
    assert isinstance(events[plain["id"]]["message_seq"], int) and events[plain["id"]]["revision"] == 0
    assert asked == [restricted["id"]]              # the unrestricted reply never asked the task-privacy check


def test_a_thread_follow_sends_its_subtree_and_nothing_else(api):
    root = new_thread(api, body="Root")
    first = say(api, root["id"], "A topic")
    sub = new_thread(api, title="Sub", members=(), parent_message_id=first["id"])
    say(api, sub["id"], "In the sub-thread")
    other = new_thread(api, title="Other", body="Elsewhere")
    ben = who(api, "ben-test")

    def threads_with_messages(scope):
        return {d["thread_id"] for _, topic, d in read_as(api, ben, 0, ("messages",), threads_scope=scope)
                if topic == "messages"}

    everything = threads_with_messages(())
    assert {root["id"], sub["id"], other["id"]} <= everything
    assert threads_with_messages((root["id"],)) == {root["id"], sub["id"]}
    assert threads_with_messages((sub["id"],)) == {sub["id"]}


def test_a_read_mark_goes_only_to_the_reader_who_made_it(api):
    store = api.app.state.store
    thread = new_thread(api, body="Kickoff")
    last = say(api, thread["id"], "Please review")["seq"]
    after = cursor(api)
    response = api.post(f"/api/v2/threads/{thread['id']}/read", json={"seq": last}, headers=headers("ben-test"))
    assert response.status_code == 200, response.text
    reads = [d for _, topic, d in read_as(api, who(api, "ben-test"), after, ("threads",)) if d.get("kind") == "read"]
    assert [(d["thread_id"], d["root_id"], d["read"]) for d in reads] == [(thread["id"], thread["id"],
                                                                          {"last_read_seq": last})]
    assert not [d for _, topic, d in read_as(api, who(api, "ana-test"), after, ("threads",))
                if d.get("kind") == "read"]


def test_the_threads_topic_says_what_changed_for_the_people_who_may_read_the_thread(api):
    after = cursor(api)
    thread = new_thread(api, title="Launch", body="Plan the launch")
    ben = who(api, "ben-test")
    created = [d for _, topic, d in read_as(api, ben, after, ("threads",)) if d.get("thread_id") == thread["id"]]
    assert [d["kind"] for d in created][:1] == ["created"]
    assert created[0]["thread"]["title"] == "Launch"
    assert any(d["kind"] == "counters" and d["counters"]["last_seq"] >= 1 for d in created)
    after = cursor(api)
    assert api.patch(f"/api/v2/threads/{thread['id']}", json={"title": "Launch day"},
                     headers=headers("ana-test")).status_code == 200
    renamed = [d for _, topic, d in read_as(api, ben, after, ("threads",)) if d.get("thread_id") == thread["id"]]
    assert [d["kind"] for d in renamed if d["kind"] == "renamed"] == ["renamed"]
    assert next(d for d in renamed if d["kind"] == "renamed")["thread"]["title"] == "Launch day"
    after = cursor(api)
    say(api, thread["id"], "One more")
    last_seq = api.get(f"/api/v2/threads/{thread['id']}", headers=headers("ben-test")).json()["last_seq"]
    counted = [d for _, topic, d in read_as(api, ben, after, ("threads",)) if d.get("thread_id") == thread["id"]]
    assert any(d["kind"] == "counters" and d["counters"]["last_seq"] == last_seq == 2 for d in counted)
    cara = who(api, "cara-test")
    assert not [d for _, topic, d in read_as(api, cara, 0, ("threads",)) if d.get("thread_id") == thread["id"]]


def test_a_bot_token_gets_only_the_topics_it_may_read_and_no_thread_it_is_not_in(api):
    token = as_bot(api, "ops")
    assert api.get("/api/v2/events", params={"topics": "tasks"}, headers=headers(token)).status_code == 403
    after = cursor(api)
    new_thread(api, title="Private", body="Only ben")
    bot = Identity("bot:ops", "bot", agent="test")
    assert not [1 for _, topic, _d in read_as(api, bot, after, E.AGENT_TOPICS) if topic in ("messages", "threads")]

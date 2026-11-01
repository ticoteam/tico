"""The live stream's shortcuts decide exactly what the long way decides: threads.readable_ids asked about one thread at
a time (rendering only the terms that thread can take) agrees with C1 in Python, one stored message shared between
streams is still decided per viewer, and a `?thread=` follow's subtree (an index range) is the subtree."""

from backend import events as E
from backend import threads
from backend.tests.test_api import api, headers  # noqa: F401  (the fixture and its helper)
from backend.tests.test_thread_events import cursor, new_thread, say, who
from backend.tests.test_threads_core import made_threads, world  # noqa: F401  (the fixture)


def test_one_thread_at_a_time_the_sse_rendering_agrees_with_c1_for_every_teammate(world):  # noqa: F811
    store, auth, people = world
    with store.transaction() as c:
        made = made_threads(c, auth, people)
    with store.read_transaction() as c:
        ids = [r[0] for r in c.execute("SELECT id FROM conversations WHERE COALESCE(room_key,'') NOT LIKE 'live-meeting:%'")]
        assert set(made.values()) <= set(ids)
        for actor, identity in people.items():
            v = threads.viewer(auth, identity)
            python = {i for i in ids if threads.readable(c, v, i)}
            one_by_one = {i for i in ids if threads.readable_ids(c, threads.viewer(auth, identity), [i])}
            assert one_by_one == python, (actor, sorted(one_by_one ^ python)[:5])


def read_shared(api, person, after, shared, threads_scope=()):
    auth = api.app.state.auth
    with api.app.state.store.read() as c:
        events, _, _, _ = E.read(c, auth, person, after, topics=("messages",), threads_scope=threads_scope,
                                 task_views=lambda rows, c, who, visible_sql="1": [], task_view=None, shared=shared)
    return {d["id"] for _, topic, d in events if topic == "messages"}


def test_a_message_hydrated_once_for_every_stream_is_sent_only_to_its_readers(api):  # noqa: F811
    shared = E.Hydrated()
    after = cursor(api)
    private = new_thread(api, title="Salary bands", members=("human:ben",))
    secret = say(api, private["id"], "Only Ana and Ben read this")
    # The member reads first, so the message is in the shared store before the outsider's read asks for it.
    assert secret["id"] in read_shared(api, who(api, "ben-test"), after, shared)
    assert secret["id"] not in read_shared(api, who(api, "cara-test"), after, shared)
    # And in the other order: an outsider's read first stores nothing a member then misses.
    shared = E.Hydrated()
    assert secret["id"] not in read_shared(api, who(api, "cara-test"), after, shared)
    assert secret["id"] in read_shared(api, who(api, "ben-test"), after, shared)
    # An edit is a new change, so it is hydrated again rather than served stale.
    edited_after = cursor(api)
    response = api.patch(f"/api/v2/messages/{secret['id']}", json={"text": "Edited by Ana"}, headers=headers("ana-test"))
    assert response.status_code == 200, response.text
    auth = api.app.state.auth
    with api.app.state.store.read() as c:
        events, _, _, _ = E.read(c, auth, who(api, "ben-test"), edited_after, topics=("messages",),
                                 task_views=lambda rows, c, who, visible_sql="1": [], task_view=None, shared=shared)
    bodies = [d.get("body") or (d.get("message") or {}).get("body") for _, topic, d in events if topic == "messages"]
    assert "Edited by Ana" in bodies


def test_a_thread_follow_reads_its_whole_subtree_and_no_sibling_whose_id_shares_its_prefix(api):  # noqa: F811
    after = cursor(api)
    root = new_thread(api, title="Launch", body="Kickoff")
    first = say(api, root["id"], "Who takes the press list?")
    sub = new_thread(api, title="Press list", parent_message_id=first["id"])
    deep = say(api, sub["id"], "I do")
    other = new_thread(api, title="Launch notes")
    elsewhere = say(api, other["id"], "Not in the launch thread")
    with api.app.state.store.transaction() as c:
        # A sibling whose path starts with the root's id but is not under it: '/<root id>x/'.
        c.execute("UPDATE conversations SET path=? WHERE id=?", ("/" + root["id"] + "x/", other["id"]))
    got = read_shared(api, who(api, "ben-test"), after, None, threads_scope=(root["id"],))
    assert {first["id"], deep["id"]} <= got
    assert elsewhere["id"] not in got

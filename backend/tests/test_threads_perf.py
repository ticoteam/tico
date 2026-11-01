"""The live stream's shortcuts decide exactly what the long way decides: threads.readable_ids asked about one thread at
a time (rendering only the terms that thread can take) agrees with C1 in Python, one stored message shared between
streams is still decided per viewer, a `?thread=` follow's subtree (an index range) is the subtree, and a round of streams
reads for each what that stream's own read would."""

import asyncio

from backend import events as E
from backend import read_cache, threads
from backend.store import H, Problem, encode
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


def round_of(api, *polls):
    """Run these (identity, cursor, read arguments) as one round of streams, as install's generate() does."""
    auth, store = api.app.state.auth, api.app.state.store
    served = E.Rounds(store)

    def step(person, arguments):
        def run(c, snapshot, after):
            return E.read_in(c, snapshot, auth, person, after, task_views=lambda rows, c, who, visible_sql="1": [],
                             task_view=None, **arguments)
        return run

    async def together():
        waits = [served.poll(check, step(person, arguments), after, arguments.get("threads_scope", ()))
                 for person, after, arguments, check in polls]
        return await asyncio.gather(*waits, return_exceptions=True)
    return asyncio.run(together())


def lone_read(api, person, after, **arguments):
    with api.app.state.store.read() as c:
        return E.read(c, api.app.state.auth, person, after, task_views=lambda rows, c, who, visible_sql="1": [],
                      task_view=None, **arguments)


def test_a_round_reads_for_each_stream_what_its_own_read_would_for_every_filter(api):  # noqa: F811
    after = cursor(api)
    root = new_thread(api, title="Launch", body="Kickoff")
    first = say(api, root["id"], "Who takes the press list?")
    sub = new_thread(api, title="Press list", parent_message_id=first["id"])
    say(api, sub["id"], "I do")
    private = new_thread(api, title="Salary bands", members=("human:ben",))
    say(api, private["id"], "Only Ana and Ben read this")
    prefix = new_thread(api, title="Launch notes")
    say(api, prefix["id"], "Not in the launch thread")
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE conversations SET path=? WHERE id=?", ("/" + root["id"] + "x/", prefix["id"]))
    filters = [{}, {"topics": ("messages",)}, {"topics": ("threads", "needs", "tasks")}, {"limit": 2},
               {"threads_scope": (root["id"],)}, {"threads_scope": (sub["id"], private["id"])},
               {"conversations": (private["id"],)}, {"conversations": (root["id"],), "threads_scope": (root["id"],)},
               {"bots": ("no-such-bot",)}, {"tasks": ("no-such-task",)},
               {"tasks": ("no-such-task",), "parties": ("human:ana",)}]
    for token in ("ana-test", "ben-test", "cara-test"):
        person = who(api, token)
        check = lambda: None
        hydrated = E.Hydrated()              # shared text, here and in the round before: it must read the same
        polls = [(person, after, {**arguments, "shared": hydrated}, check) for arguments in filters]
        for arguments, got in zip(filters, round_of(api, *polls)):
            assert not isinstance(got, Exception), got
            alone = lone_read(api, person, after, **arguments)
            assert [(s, t, dict(d)) for s, t, d in got[0]] == [(s, t, dict(d)) for s, t, d in alone[0]], arguments
            assert got[1:] == alone[1:], arguments


def test_a_stream_far_behind_reads_its_own_changes_and_a_stream_past_the_backlog_is_reset(api, monkeypatch):  # noqa: F811
    monkeypatch.setattr(E, "TAIL_SPAN", 1)
    after = cursor(api)
    room = new_thread(api, title="Launch")
    said = {say(api, room["id"], f"Message {n}")["id"] for n in range(3)}
    person = who(api, "ben-test")
    behind, caught_up, past = round_of(api, (person, after, {"topics": ("messages",)}, lambda: None),
                                       (person, cursor(api), {"topics": ("messages",)}, lambda: None),
                                       (person, cursor(api) + 5, {"topics": ("messages",)}, lambda: None))
    assert said <= {d["id"] for _, _, d in behind[0]} and not behind[2]
    assert caught_up[0] == [] and not caught_up[2]
    assert past[2] is True


def test_one_viewers_answers_never_reach_the_next_stream_of_a_round(api):  # noqa: F811
    after = cursor(api)
    private = new_thread(api, title="Salary bands", members=("human:ben",))
    secret = say(api, private["id"], "Only Ana and Ben read this")
    topics = {"topics": ("messages", "threads")}
    ids = lambda got: {d.get("id") for _, topic, d in got[0] if topic == "messages"}
    ben, cara = who(api, "ben-test"), who(api, "cara-test")
    member_first = round_of(api, (ben, after, topics, lambda: None), (cara, after, topics, lambda: None))
    outsider_first = round_of(api, (cara, after, topics, lambda: None), (ben, after, topics, lambda: None))
    assert secret["id"] in ids(member_first[0]) and secret["id"] not in ids(member_first[1])
    assert secret["id"] not in ids(outsider_first[0]) and secret["id"] in ids(outsider_first[1])
    # What stays in the snapshot for the next stream is stored rows, by name.
    with api.app.state.store.read_transaction() as c:
        values = read_cache.cache(c)
        values[("one", H.CONVERSATION_SQL, (private["id"],))] = "a stored row"
        values[("live_meeting_sources",)] = "one viewer's index"
        values[("tico_chosen", "human:ben", "some-bot")] = "one viewer's answer"
        E._between_viewers(c)
        assert ("one", H.CONVERSATION_SQL, (private["id"],)) in values
        assert ("live_meeting_sources",) not in values and ("tico_chosen", "human:ben", "some-bot") not in values


def test_a_revoked_member_reads_nothing_more_from_the_next_round(api):  # noqa: F811
    after = cursor(api)
    private = new_thread(api, title="Salary bands", members=("human:ben", "human:cara"))
    say(api, private["id"], "Before")
    topics = {"topics": ("messages",)}
    cara = who(api, "cara-test")
    [before] = round_of(api, (cara, after, topics, lambda: None))
    assert before[0]
    response = api.delete(f"/api/v2/threads/{private['id']}/members/human:cara", headers=headers("ana-test"))
    assert response.status_code in (200, 204), response.text
    resumed = before[1]
    say(api, private["id"], "After")
    [after_revoke] = round_of(api, (cara, resumed, topics, lambda: None))
    assert after_revoke[0] == []


def test_a_refused_sign_in_ends_only_that_stream_and_a_stream_that_left_is_skipped(api):  # noqa: F811
    after = cursor(api)
    room = new_thread(api, title="Launch")
    said = say(api, room["id"], "Hello")["id"]

    def refused():
        raise Problem("unauthenticated", "Sign in again", 401)
    topics = {"topics": ("messages",)}
    ana, ben = who(api, "ana-test"), who(api, "ben-test")
    first, second, third = round_of(api, (ana, after, topics, refused), (ben, after, topics, lambda: None),
                                    (ana, after, topics, lambda: None))
    assert isinstance(first, Problem)
    assert said in {d["id"] for _, _, d in second[0]} and said in {d["id"] for _, _, d in third[0]}


def test_shared_event_text_keeps_each_readers_own_actor_and_address(api):  # noqa: F811
    after = cursor(api)
    room = new_thread(api, title="Launch")
    plain = say(api, room["id"], "No file here")["id"]
    carrying = say(api, room["id"], "A file here")["id"]
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE messages SET refs_json=? WHERE id=?", ('{"attachments":[{"name":"plan.txt"}]}', carrying))
    seen = []

    def visuals(c, who, messages):               # what gets each reader's own address: only a message with a file
        seen.extend(m["id"] for m in messages)
    shared = E.Hydrated()
    got = lone_read(api, who(api, "ben-test"), after, topics=("messages",), visuals=visuals, shared=shared)
    assert {d["id"] for _, _, d in got[0]} >= {plain, carrying}
    assert carrying in seen and plain not in seen
    row = {"seq": 7, "at": "2026-11-01T00:00:00Z"}
    event = E._SameForAll(room["id"], {"root_id": room["id"]}, {"id": plain}, {})
    shown, hidden = event.event(row, "bot:acme", shared), event.event(row, None, shared)
    assert shown["actor"] == "bot:acme" and hidden["actor"] is None and shown.encoded != hidden.encoded
    assert event.event(row, "bot:acme", shared) is shown


def test_shared_event_text_never_names_someone_this_reader_may_not_see(api, monkeypatch):  # noqa: F811
    from backend import teammate_access
    after = cursor(api)
    made = api.post("/api/v2/threads", json={"title": "Launch", "audience": "company"}, headers=headers("ana-test"))
    room = made.json()
    message = say(api, room["id"], "The date moved")
    said, author = message["id"], message["from_actor"]
    real = teammate_access.unseen
    monkeypatch.setattr(teammate_access, "unseen", lambda c, viewer, auth=None: (
        {author.split(":", 1)[1]} if viewer.actor == "human:cara" else real(c, viewer, auth)[0], set()))
    shared = E.Hydrated()                        # Ben reads first, so his text is the one a shortcut would reuse
    seen = {}
    for token in ("ben-test", "cara-test"):
        events = lone_read(api, who(api, token), after, topics=("messages",), shared=shared)[0]
        seen[token] = next(d for _, _, d in events if d.get("id") == said)
    assert seen["ben-test"]["message"]["from_actor"] == author and seen["ben-test"]["actor"] == author
    assert seen["cara-test"]["message"]["from_actor"] == "" and seen["cara-test"]["actor"] is None
    assert author not in encode(seen["cara-test"]), seen["cara-test"]

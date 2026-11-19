"""The live stream's shortcuts decide exactly what the long way decides: threads.readable_ids asked about one thread at
a time (rendering only the terms that thread can take) agrees with C1 in Python, one stored message shared between
streams is still decided per viewer, a `?thread=` follow's subtree (an index range) is the subtree, and a round of streams
reads for each what that stream's own read would."""

import asyncio

from backend.tests.slug_ids import bot_ids_are_their_slugs  # noqa: F401  (each bot's id is its slug)
from backend import events as E
from backend import read_cache, threads
from backend.store import H, Problem, encode
from backend.tests.helpers.api import headers  # noqa: F401  (the fixture and its helper)
from backend.tests.helpers.thread_events import cursor, new_thread, say, who
from backend.tests.helpers.threads_core import demo_template, made_threads, world  # noqa: F401  (the fixtures)


def no_views(rows, c, who, visible_sql="1"):
    """The task views these reads build: none (one function, as the app installs one for every stream)."""
    return []


def test_one_thread_at_a_time_the_sse_rendering_agrees_with_c1_for_every_teammate(world):  # noqa: F811
    store, auth, people = world
    with store.transaction() as c:
        made = made_threads(c, auth, people)
    with store.read_transaction() as c:
        ids = [
            r[0]
            for r in c.execute("SELECT id FROM conversations WHERE COALESCE(room_key,'') NOT LIKE 'live-meeting:%'")
        ]
        assert set(made.values()) <= set(ids)
        checked = set()
        for actor, identity in people.items():
            v = threads.viewer(auth, identity)
            python = {i for i in ids if threads.readable(c, v, i)}
            if actor.startswith("bot:"):
                # Every person is checked. Bots alike in group chain and in the threads they read give the one-by-one
                # rendering the same input, so the first of each class stands for it.
                names = sorted(v.member_names(c))
                members = frozenset(
                    r[0]
                    for r in c.execute(
                        "SELECT thread_id FROM thread_members WHERE actor IN ({})".format(",".join("?" * len(names))),
                        names,
                    )
                )
                kind = (tuple(v.chain(c)), frozenset(python), members, tuple(sorted(v.hidden_read(c) or ())))
                if kind in checked:
                    continue
                checked.add(kind)
            one_by_one = {i for i in ids if threads.readable_ids(c, threads.viewer(auth, identity), [i])}
            assert one_by_one == python, (actor, sorted(one_by_one ^ python)[:5])


def read_shared(api, person, after, shared, threads_scope=()):
    auth = api.app.state.auth
    with api.app.state.store.read() as c:
        events, _, _, _ = E.read(
            c,
            auth,
            person,
            after,
            topics=("messages",),
            threads_scope=threads_scope,
            task_views=no_views,
            task_view=None,
            shared=shared,
        )
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
    response = api.patch(
        f"/api/v2/messages/{secret['id']}", json={"text": "Edited by Ana"}, headers=headers("ana-test")
    )
    assert response.status_code == 200, response.text
    auth = api.app.state.auth
    with api.app.state.store.read() as c:
        events, _, _, _ = E.read(
            c,
            auth,
            who(api, "ben-test"),
            edited_after,
            topics=("messages",),
            task_views=no_views,
            task_view=None,
            shared=shared,
        )
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
            return E.read_in(
                c,
                snapshot,
                auth,
                person,
                after,
                task_views=no_views,
                task_view=None,
                **arguments,
            )

        return run

    async def together():
        waits = [
            served.poll(check, step(person, arguments), after, arguments.get("threads_scope", ()))
            for person, after, arguments, check in polls
        ]
        return await asyncio.gather(*waits, return_exceptions=True)

    return asyncio.run(together())


def lone_read(api, person, after, **arguments):
    with api.app.state.store.read() as c:
        return E.read(
            c,
            api.app.state.auth,
            person,
            after,
            task_views=no_views,
            task_view=None,
            **arguments,
        )


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
    filters = [
        {},
        {"topics": ("messages",)},
        {"topics": ("threads", "needs", "tasks")},
        {"limit": 2},
        {"threads_scope": (root["id"],)},
        {"threads_scope": (sub["id"], private["id"])},
        {"conversations": (private["id"],)},
        {"conversations": (root["id"],), "threads_scope": (root["id"],)},
        {"bots": ("no-such-bot",)},
        {"tasks": ("no-such-task",)},
        {"tasks": ("no-such-task",), "parties": ("human:ana",)},
    ]
    for token in ("ana-test", "ben-test", "cara-test"):
        person = who(api, token)
        check = lambda: None
        hydrated = E.Hydrated()  # shared text, here and in the round before: it must read the same
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
    behind, caught_up, past = round_of(
        api,
        (person, after, {"topics": ("messages",)}, lambda: None),
        (person, cursor(api), {"topics": ("messages",)}, lambda: None),
        (person, cursor(api) + 5, {"topics": ("messages",)}, lambda: None),
    )
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
    first, second, third = round_of(
        api, (ana, after, topics, refused), (ben, after, topics, lambda: None), (ana, after, topics, lambda: None)
    )
    assert isinstance(first, Problem)
    assert said in {d["id"] for _, _, d in second[0]} and said in {d["id"] for _, _, d in third[0]}


def test_shared_event_text_keeps_each_readers_own_actor_and_address(api):  # noqa: F811
    after = cursor(api)
    room = new_thread(api, title="Launch")
    plain = say(api, room["id"], "No file here")["id"]
    carrying = say(api, room["id"], "A file here")["id"]
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE messages SET refs=? WHERE id=?", ('{"attachments":[{"name":"plan.txt"}]}', carrying))
    seen = []

    def visuals(c, who, messages):  # what gets each reader's own address: only a message with a file
        seen.extend(m["id"] for m in messages)

    shared = E.Hydrated()
    got = lone_read(api, who(api, "ben-test"), after, topics=("messages",), visuals=visuals, shared=shared)
    assert {d["id"] for _, _, d in got[0]} >= {plain, carrying}
    assert carrying in seen and plain not in seen
    row = {"seq": 7, "created_at": "2026-11-01T00:00:00Z"}
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
    said, author = message["id"], message["sender_id"]
    real = teammate_access.unseen
    monkeypatch.setattr(
        teammate_access,
        "unseen",
        lambda c, viewer, auth=None: (
            {author.split(":", 1)[1]} if viewer.actor == "human:cara" else real(c, viewer, auth)[0],
            set(),
        ),
    )
    shared = E.Hydrated()  # Ben reads first, so his text is the one a shortcut would reuse
    seen = {}
    for token in ("ben-test", "cara-test"):
        events = lone_read(api, who(api, token), after, topics=("messages",), shared=shared)[0]
        seen[token] = next(d for _, _, d in events if d.get("id") == said)
    assert seen["ben-test"]["message"]["sender_id"] == author and seen["ben-test"]["actor"] == author
    assert seen["cara-test"]["message"]["sender_id"] == "" and seen["cara-test"]["actor"] is None
    assert author not in encode(seen["cara-test"]), seen["cara-test"]


def test_one_persons_streams_of_a_round_decide_each_thread_once_and_read_what_each_would_alone(api, monkeypatch):  # noqa: F811
    after = cursor(api)
    open_room = new_thread(api, title="Launch", body="Kickoff")
    say(api, open_room["id"], "Who takes the press list?")
    private = new_thread(api, title="Salary bands", members=("human:ben",))
    say(api, private["id"], "Only Ana and Ben read this")
    asked = []
    real = threads.readable_ids

    def counted(c, v, ids):
        asked.extend(ids)
        return real(c, v, ids)

    monkeypatch.setattr(threads, "readable_ids", counted)
    topics = {"topics": ("messages", "threads")}
    ben, cara = who(api, "ben-test"), who(api, "cara-test")
    got = round_of(api, *[(ben, after, topics, lambda: None)] * 5, (cara, after, topics, lambda: None))
    per_viewer = {}
    for cid in asked:
        per_viewer[cid] = per_viewer.get(cid, 0) + 1
    assert all(n <= 2 for n in per_viewer.values()), per_viewer  # once for Ben's five streams, once for Cara's
    alone_ben = lone_read(api, ben, after, **topics)
    alone_cara = lone_read(api, cara, after, **topics)
    for result in got[:5]:
        assert [(s, t, dict(d)) for s, t, d in result[0]] == [(s, t, dict(d)) for s, t, d in alone_ben[0]]
    assert [(s, t, dict(d)) for s, t, d in got[5][0]] == [(s, t, dict(d)) for s, t, d in alone_cara[0]]
    assert private["id"] not in str(got[5][0]) and private["id"] in str(got[0][0])


def test_one_persons_streams_of_a_round_build_needs_you_once_and_each_keeps_its_own_marks(api, monkeypatch):  # noqa: F811
    from backend import views

    after = cursor(api)
    with api.app.state.store.transaction() as c:
        c.execute(
            "INSERT INTO changes(topic,kind,subject_id,created_at) VALUES('needs','thread','human:ben',?)", (H.now(),)
        )
    builds = []
    real = views.needs_items

    def counted(c, auth, person, *args, **kwargs):
        builds.append(person.actor)
        return real(c, auth, person, *args, **kwargs)

    monkeypatch.setattr(views, "needs_items", counted)
    ben, cara = who(api, "ben-test"), who(api, "cara-test")
    held = [{} for _ in range(3)]
    polls = [(ben, after, {"topics": ("needs",), "needs_held": mine}, lambda: None) for mine in held]
    got = round_of(api, *polls, (cara, after, {"topics": ("needs",)}, lambda: None))
    assert builds == ["human:ben"]  # Cara has no needs change of her own in this round
    assert all(not isinstance(result, Exception) for result in got), got
    for result, mine in zip(got[:3], held, strict=True):
        assert [t for _, t, _ in result[0]] == ["needs"] and mine["version"] == held[0]["version"]
    assert got[3][0] == []


def test_one_persons_streams_of_a_round_build_each_topic_once_and_read_what_each_would_alone(api, monkeypatch):  # noqa: F811
    import json

    from backend.tests.helpers.api import post, setup_attempt

    after = cursor(api)
    root = new_thread(api, title="Launch", body="Kickoff")
    say(api, root["id"], "Who takes the press list?")
    post(api, "tasks", {"owner": "ben", "title": "Draft the agenda", "body": "Please."})
    _, _, attempt = setup_attempt(api)
    with api.app.state.store.transaction() as c:
        for seq, text in ((1000, "notes"), (1001, "more notes")):
            c.execute(
                "INSERT INTO attempt_events(attempt_id,seq,kind,payload,created_at) VALUES(?,?,'delta',?,?)",
                (attempt["id"], seq, json.dumps({"delta_kind": "text", "text": text}), H.now()),
            )
    calls = []
    for name in ("_tasks", "_threads", "_runs", "_bots"):
        real = getattr(E, name)
        monkeypatch.setattr(E, name, lambda *a, _real=real, _name=name, **k: calls.append(_name) or _real(*a, **k))
    steps = []
    real_rows = E._Snapshot.step_rows
    monkeypatch.setattr(
        E._Snapshot,
        "step_rows",
        lambda self, refs: steps.extend(r for r in refs if str(r) not in self.steps) or real_rows(self, refs),
    )
    topics = {"topics": ("tasks", "threads", "runs", "bots", "messages")}
    ana, ben = who(api, "ana-test"), who(api, "ben-test")
    got = round_of(api, *[(ana, after, topics, lambda: None)] * 5, (ben, after, topics, lambda: None))
    assert all(not isinstance(result, Exception) for result in got), got
    for name in ("_tasks", "_threads", "_runs"):
        assert calls.count(name) == 2, calls  # once for Ana's five streams, once for Ben's
    assert len(steps) == len(set(steps)) == 2  # each run step read once for both people
    calls.clear()
    alone = {person.actor: lone_read(api, person, after, **topics) for person in (ana, ben)}
    for result, person in zip(got, [ana] * 5 + [ben], strict=True):
        assert [(s, t, dict(d)) for s, t, d in result[0]] == [(s, t, dict(d)) for s, t, d in alone[person.actor][0]]
    assert {t for _, t, _ in got[0][0]} >= {"tasks", "threads", "runs"}


def test_a_page_access_change_resets_a_round_stream_reading_across_it_as_a_lone_read_does(api):  # noqa: F811
    after = cursor(api)
    new_thread(api, title="Launch", body="Kickoff")
    with api.app.state.store.transaction() as c:
        c.execute("INSERT INTO changes(topic,kind,ref,created_at) VALUES('tasks','bulk','page_access',?)", (H.now(),))
    moved = cursor(api)
    new_thread(api, title="Press list", body="Who takes it?")
    ben = who(api, "ben-test")
    got = round_of(api, (ben, after, {}, lambda: None), (ben, moved, {}, lambda: None))
    assert [result[2] for result in got] == [True, False]  # (events, cursor, reset, more)
    assert [lone_read(api, ben, at)[2] for at in (after, moved)] == [True, False]

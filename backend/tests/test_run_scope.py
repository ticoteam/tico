"""Run traffic scoped to what a stream is on screen for (Perf I48): `conversation=` and `thread=` narrow `runs` as they
narrow `messages`, to those conversations and those threads' subtrees, in the shared tail and in the SQL reader alike;
a stream naming neither keeps every readable run; and a `bot_status` row carries its root's bots only to a stream
following a thread under that root. Fictional people only: ana (owner), ben (human)."""

import itertools

from backend.tests.slug_ids import bot_ids_are_their_slugs  # noqa: F401  (each bot's id is its slug)
from backend import events as E
from backend import thread_bot_status
from backend.store import H
from backend.tests.helpers.api import assign, claim, headers, ready, runner  # noqa: F401  (fixture, helpers)
from backend.tests.helpers.thread_events import read_as, who
from backend.tests.helpers.thread_halfway import new_thread, say


def machine_for(api, bots):
    machine = runner(api)
    for bot in bots:
        assign(api, machine, bot)
    ready(api, machine, list(bots))
    return machine


def sub_thread(api, parent_message_id, members=("human:ben", "bot:ops")):
    r = api.post(
        "/api/v2/threads",
        json={"title": "Side", "audience": "members", "members": list(members), "parent_message_id": parent_message_id},
        headers=headers(),
    )
    assert r.status_code == 200, r.text
    return r.json()


def log(c, topic, conversation_id, n=1):
    for _ in range(n):
        c.execute(
            "INSERT INTO changes(topic,kind,subject_id,conversation_id,bot,created_at) VALUES(?,?,?,?,?,?)",
            (topic, "output" if topic == "runs" else "insert", "x", conversation_id, "ops", H.now()),
        )


def test_the_tail_and_the_sql_reader_scope_runs_and_messages_to_the_same_rows(api):
    """AC-1 to AC-5 at the change rows, and AC-4: both readers give the same rows for every scope."""
    a = new_thread(api, title="A")
    top = say(api, a["id"], "A topic")
    s = sub_thread(api, top["id"])
    b, chat = new_thread(api, title="B"), new_thread(api, title="Chat")
    store = api.app.state.store
    with store.read() as c:
        after = E.latest(c)
    with store.transaction() as c:
        for cid in (a["id"], s["id"], b["id"], chat["id"], None):
            log(c, "runs", cid)
            log(c, "messages", cid)
    ben = who(api, "ben-test")
    topics = ("messages", "runs")
    expected = {
        ((), ()): {a["id"], s["id"], b["id"], chat["id"], None},  # AC-5: a page following nothing keeps every run
        ((), (a["id"],)): {a["id"], s["id"]},  # AC-1, AC-2: the thread and its sub-thread, nothing from B
        ((), (s["id"],)): {s["id"]},
        ((chat["id"],), ()): {chat["id"]},
        ((chat["id"],), (a["id"],)): {chat["id"], a["id"], s["id"]},  # AC-3: the union, not B
    }
    for (conversations, scope), want in expected.items():
        with store.read() as c:
            snapshot = E._Snapshot(c, [E._Poll(None, None, after, scope)])
            assert snapshot.tail_from == after and snapshot.tail  # the shared tail serves this stream
            args = (ben, after, 100, topics, conversations, (), (), (), scope)
            tail, sql = snapshot.changes(*args), snapshot._read_changes(*args)
        assert [r["seq"] for r in tail] == [r["seq"] for r in sql], (conversations, scope)
        for topic in topics:
            got = {r["conversation_id"] for r in tail if r["topic"] == topic}
            assert got == (want - {None} if (conversations or scope) else want), (topic, conversations, scope)


def test_the_two_readers_agree_on_every_scope_over_mixed_rows(api):
    """AC-4 over every combination of the conversations and threads named, on one set of rows."""
    a = new_thread(api, title="A")
    s = sub_thread(api, say(api, a["id"], "Topic")["id"])
    b = new_thread(api, title="B")
    sb = sub_thread(api, say(api, b["id"], "Topic")["id"])
    ids = [a["id"], s["id"], b["id"], sb["id"]]
    store = api.app.state.store
    with store.read() as c:
        after = E.latest(c)
    with store.transaction() as c:
        for i, cid in enumerate(ids * 3 + [None, "no-such-conversation"]):
            log(c, ("runs", "messages", "bots", "tasks")[i % 4], cid)
    ben = who(api, "ben-test")
    picks = [()] + [tuple(p) for n in (1, 2) for p in itertools.combinations(ids, n)]
    for conversations, scope in itertools.product(picks, picks):
        with store.read() as c:
            snapshot = E._Snapshot(c, [E._Poll(None, None, after, scope)])
            args = (ben, after, 100, ("messages", "runs", "bots", "tasks"), conversations, (), (), (), scope)
            tail, sql = snapshot.changes(*args), snapshot._read_changes(*args)
        assert [r["seq"] for r in tail] == [r["seq"] for r in sql], (conversations, scope)


def test_a_thread_viewer_is_sent_the_runs_of_its_subtree_and_no_other(api):
    """AC-1, AC-2, AC-5 end to end: real runs, as `events.read` sends them."""
    machine = machine_for(api, ("ops", "cpo"))
    a = new_thread(api, title="A", members=("human:ben", "bot:ops"))
    b = new_thread(api, title="B", members=("human:ben", "bot:cpo"))
    with api.app.state.store.read() as c:
        after = E.latest(c)
    say(api, a["id"], "@ops draft it")
    say(api, b["id"], "@cpo check it")
    assert claim(api, machine, "ops") and claim(api, machine, "cpo")
    ben = who(api, "ben-test")

    def runs_from(scope):
        return {d["conversation_id"] for _, topic, d in read_as(api, ben, after, ("runs",), scope) if topic == "runs"}

    assert runs_from(()) == {a["id"], b["id"]}  # a page following nothing: every readable run, as before
    assert runs_from((a["id"],)) == {a["id"]}
    assert runs_from((b["id"],)) == {b["id"]}


def test_bot_status_is_built_only_for_the_roots_a_stream_views(api, monkeypatch):
    """AC-6: the status rows are built for the viewed root alone; another root's row goes out without them."""
    machine_for(api, ("ops",))
    a, b = new_thread(api, title="A"), new_thread(api, title="B")
    top = say(api, a["id"], "A topic")
    s = sub_thread(api, top["id"])
    with api.app.state.store.read() as c:
        after = E.latest(c)
    say(api, a["id"], "@ops can you check this?")
    say(api, b["id"], "@ops and this?")
    built = []
    real = thread_bot_status.status
    monkeypatch.setattr(thread_bot_status, "status", lambda c, v, row: built.append(row["id"]) or real(c, v, row))
    ben = who(api, "ben-test")

    def sent(scope):
        built.clear()
        rows = {
            d["thread_id"]: d
            for _, topic, d in read_as(api, ben, after, ("threads",), scope)
            if topic == "threads" and d.get("kind") == "bot_status"
        }
        return rows, set(built)

    rows, calls = sent((a["id"],))
    assert set(rows) == {a["id"], b["id"]}  # both rows still say their root changed
    assert rows[a["id"]]["bots"][0]["actor"] == "bot:ops" and "bots" not in rows[b["id"]]
    assert calls == {a["id"]}
    rows, calls = sent((s["id"],))  # a sub-thread on screen shows its root's bots
    assert rows[a["id"]]["bots"] and "bots" not in rows[b["id"]] and calls == {a["id"]}
    rows, calls = sent(())  # following no thread: nothing on screen shows bot status
    assert set(rows) == {a["id"], b["id"]} and all("bots" not in d for d in rows.values()) and calls == set()

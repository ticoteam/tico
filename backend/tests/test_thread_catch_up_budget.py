"""Catch up shows every thread with a new message the reader may read, however many unread they have (spec P530).

Every new message that can hide (restricted, as every production message is now) takes the per-message check. The walk
behind the badge only asks whether a root has one: it checks each root's oldest first, every root getting its turn,
within CATCH_UP_HIDDEN for the whole walk. The cards a page shows and the rail's Unread rows then get their whole count
within CATCH_UP_COUNTED, marked `new_count_partial` when it stops there. Fictional people only: ana writes, ben reads."""

import pytest

from backend import thread_rail, threads
from backend.auth import Identity
from backend.store import H
from backend.tests.helpers.api import as_member, get, headers  # noqa: F401  (the fixture and its helpers)
from backend.tests.helpers.task_privacy_reads import traced_get
from backend.tests.slug_ids import bot_ids_are_their_slugs  # noqa: F401  (each bot's id is its slug)

BEN = Identity("human:ben", "human", "ben@acme.example")
HIDE = "UPDATE messages SET restricted=1, legacy_audience='shared:nobody' WHERE id IN ({})"


@pytest.fixture(autouse=True)
def plain_member(api):
    as_member(api, "ben@acme.example")  # the fixture makes Ben an admin; the per-message check is about membership


def seed(api, sizes, *, title="T", audience="members", hidden=0):
    """One root per size, each with that many new messages for ben from ana, every one restricted (it takes the
    per-message check and passes it); the first `hidden` of each are made unreadable to ben. Returns the root ids."""
    roots = []
    with api.app.state.store.transaction() as c:
        for i, size in enumerate(sizes):
            root = threads.create(c, "human:ana", title=f"{title}{i}", audience=audience, members=["human:ben"])
            ids = add(c, root["id"], size)
            if hidden:
                c.execute(HIDE.format(",".join("?" * hidden)), ids[:hidden])
            roots.append(root["id"])
    return roots


def add(c, root_id, size, actor="human:ana"):
    """`size` new restricted messages from `actor` in this thread: their ids."""
    ids = [threads.post(c, actor, root_id, f"m{n}")["id"] for n in range(size)]
    for part in H.chunks(ids):
        c.execute(f"UPDATE messages SET restricted=1 WHERE id IN ({','.join('?' * len(part))})", part)
    return ids


def catch_up_all(api, limit=50):
    """Every Catch up item over all its pages, and each page's `count`."""
    items, counts, cursor = [], [], None
    while True:
        path = f"threads/catch-up?limit={limit}" + (f"&cursor={cursor}" if cursor else "")
        page = get(api, path, "ben-test")
        items += page["items"]
        counts.append(page["count"])
        cursor = page["next_cursor"]
        if not cursor:
            return items, counts


def by_root(items):
    return {it["thread"]["id"]: it for it in items}


def test_every_thread_shows_with_its_exact_count_past_200_unread(api):
    """AC-1, AC-2: the reviewer's probe, 530 unread over 4 threads. Before, the last two in thread order showed 0."""
    roots = seed(api, [200, 20, 60, 250])
    items, counts = catch_up_all(api)
    assert {r: it["new_count"] for r, it in by_root(items).items()} == dict(zip(roots, [200, 20, 60, 250]))
    assert not any(it["new_count_partial"] for it in items)
    assert counts == [4]
    assert get(api, "threads/rail", "ben-test")["catch_up"] == 4


def test_the_rails_unread_rows_hold_the_same_threads_and_counts(api):
    """AC-3: two 1:1 DMs (the rail does not hold them, so they are Unread rows) beside two threads Ben is in."""
    seed(api, [200, 20])
    dms = []
    with api.app.state.store.transaction() as c:
        for actor, size in (("human:ana", 250), ("human:cara", 60)):
            dm = threads.dm(c, actor, ["human:ben"])
            dms.append(dm["id"])
            add(c, dm["id"], size, actor)
    items, _ = catch_up_all(api)
    rail = get(api, "threads/rail", "ben-test")
    unread = {r["id"]: r["new_count"] for r in rail["unread"]}
    assert unread == {dms[0]: 250, dms[1]: 60}
    assert unread == {r: it["new_count"] for r, it in by_root(items).items() if r in dms}
    assert rail["catch_up"] == 4 and not any(r["new_count_partial"] for r in rail["unread"])


def test_a_thread_whose_new_messages_are_all_hidden_does_not_show(api):
    """AC-4: the per-message check still decides; a root with nothing Ben may read is neither shown nor counted."""
    [hidden] = seed(api, [5], title="Hidden", hidden=5)
    [shown] = seed(api, [3], title="Shown")
    items, counts = catch_up_all(api)
    assert list(by_root(items)) == [shown] and counts == [1]
    assert hidden not in get(api, "threads/rail", "ben-test")["catch_up_ids"]


def test_a_count_leaves_out_the_messages_the_reader_may_not_read(api):
    """AC-5: 30 new, the oldest 10 hidden from Ben (so the walk's first checks fail before one passes): 20 new."""
    [root] = seed(api, [30], hidden=10)
    [item] = catch_up_all(api)[0]
    assert item["thread"]["id"] == root and item["new_count"] == 20 and not item["new_count_partial"]


def test_a_hundred_threads_past_the_budget_all_show(api, monkeypatch):
    """AC-6: 100 threads of 25 (2,500 that can hide) with the walk's budget at 150: every root still has a turn and
    shows, none with 0. The budget is lowered so the test stays small; the shape is the same at 2,000."""
    monkeypatch.setattr(thread_rail, "CATCH_UP_HIDDEN", 150)
    roots = seed(api, [25] * 100)
    items, counts = catch_up_all(api, limit=50)
    assert set(by_root(items)) == set(roots) and set(counts) == {99}
    assert all(it["new_count"] > 0 for it in items)


def test_a_root_still_unproved_when_the_walks_budget_runs_out_is_logged(api, monkeypatch, caplog):
    """Step 2: past CATCH_UP_HIDDEN a root whose checks so far all failed is left out, and the walk says so in the log
    (`catch_up_unchecked`) so a reading in production can see it happen."""
    monkeypatch.setattr(thread_rail, "CATCH_UP_HIDDEN", 4)
    seed(api, [3] * 6, hidden=2)  # each root's two oldest are hidden from Ben: round 1 proves none
    with caplog.at_level("WARNING", logger="backend.thread_rail"):
        items, _ = catch_up_all(api)
    assert len(items) < 6
    assert any("catch_up_unchecked" in r.getMessage() for r in caplog.records)


def test_a_count_past_the_page_budget_is_marked_partial_never_zero(api, monkeypatch):
    """AC-7: with CATCH_UP_COUNTED at 40 over 4 threads of 30, each card shows what was counted, marked partial."""
    monkeypatch.setattr(thread_rail, "CATCH_UP_COUNTED", 40)
    roots = seed(api, [30, 30, 30, 30])
    items, _ = catch_up_all(api)
    assert set(by_root(items)) == set(roots)
    assert all(it["new_count_partial"] and 0 < it["new_count"] < 30 for it in items), items
    assert sum(it["new_count"] for it in items) <= 40


def test_pages_show_each_thread_once_and_agree_on_the_count(api):
    """AC-8: the probe's 4 threads, 2 a page."""
    roots = seed(api, [200, 20, 60, 250])
    items, counts = catch_up_all(api, limit=2)
    assert sorted(it["thread"]["id"] for it in items) == sorted(roots) and counts == [4, 4]


def _statements(api, path):
    if path == "badge":  # the walk alone, as the sidebar's badge runs it
        seen = []
        with api.app.state.store.read_transaction() as c:
            v = threads.viewer(api.app.state.auth, BEN)
            c.set_trace_callback(seen.append)
            try:
                thread_rail.catch_up_count(c, v)
            finally:
                c.set_trace_callback(None)
        return len(seen)
    return traced_get(api, path, "ben-test")[1]


@pytest.mark.parametrize("path", ["threads/catch-up", "threads/rail", "badge"])
def test_statements_stay_flat_as_a_threads_messages_grow(api, path):
    """AC-9 (the P09 rule), messages: one thread of 50 restricted messages then 500 adds at most 15 statements."""
    [root] = seed(api, [50])
    small = _statements(api, path)
    with api.app.state.store.transaction() as c:
        add(c, root, 450)
    assert _statements(api, path) - small <= 15


@pytest.mark.parametrize("path", ["threads/catch-up?limit=1", "badge"])
def test_statements_stay_flat_as_threads_grow(api, path):
    """AC-9, threads: 10 then 100 threads holding restricted messages add at most 15 statements to the walk and to a
    page of one card. (The rail's own page reads each thread it lists as a row; that is its listing, not Catch up.)"""
    seed(api, [2] * 10, title="Ten")
    ten = _statements(api, path)
    seed(api, [2] * 90, title="Hundred")
    assert _statements(api, path) - ten <= 15


def test_each_batch_of_roots_costs_a_fixed_number_of_statements(api):
    """AC-9, batches: roots whose new messages are all hidden make the walk read on, 100 roots a batch. Each batch
    costs the same fixed number of statements, however many of its roots hold messages to check."""
    seed(api, [1], title="Shown")
    seed(api, [1] * 99, title="H", hidden=1)  # 100 roots: one batch
    one = _statements(api, "badge")
    seed(api, [1] * 200, title="M", hidden=1)  # 300 roots: three batches
    three = _statements(api, "badge")
    print(f"walk: 1 batch {one}, 3 batches {three} statements")
    assert (three - one) / 2 <= 20, (one, three)


@pytest.mark.slow
def test_bench_a_thousand_roots_and_2500_hidden_unread(api):
    """AC-10 (bench): one reader, 1,000 roots, 2,500 new messages that can hide, walked to the end (the walk behind
    the badge stops at 99 items; a later page walks on). Prints its time and statements for the PR: about 150 ms on a
    two-CPU test machine, most of it the per-message check itself (one a root). The ceiling is twice that, so the bench
    catches a walk that checks far more than one message a root without failing on a busy machine."""
    import time

    seed(api, [3] * 500 + [2] * 500, title="B")
    with api.app.state.store.read_transaction() as c:
        v = threads.viewer(api.app.state.auth, BEN)
        seen = []
        c.set_trace_callback(seen.append)
        started = time.perf_counter()
        items = list(thread_rail._walk(c, v, thread_rail._flags(c, v)))
        ms = (time.perf_counter() - started) * 1000
        c.set_trace_callback(None)
    print(f"walk: {ms:.0f} ms, {len(seen)} statements, {len(items)} items")
    assert len(items) == 1000 and ms < 300 and len(seen) < 150

"""The Threads rail's first page costs a flat number of SQL statements however many bot replies wait in the reader's
bot Homes: each reply names the run that wrote it, and the per-message check reads those runs, their inputs and the
messages they reply to together, never one reply at a time. Priming changes no row or count. Fictional people only:
ana (owner), ben (bot admin) and cara."""

from backend import read_cache, thread_rail, threads
from backend.store import H
from backend.tests.slug_ids import bot_ids_are_their_slugs  # noqa: F401  (each bot's id is its slug)
from backend.tests.helpers.api import get, headers  # noqa: F401  (the fixture and its helper)
from backend.tests.helpers.files import turn
from backend.tests.helpers.pages import page
from backend.tests.helpers.task_privacy_reads import traced_get
from backend.tests.helpers.thread_rail_batches import home as _home, runner, seed_replies  # noqa: F401  (fixture)


def _first_page(api, token="ben-test", path="threads/rail"):
    page, statements = traced_get(api, path, token)
    page.pop("seq", None)
    return page, statements


def test_the_rails_first_page_stays_flat_as_bot_replies_grow(api):
    """AC-1 (spec P09): 5 to 50 replies, each from its own run, add at most 15 statements to the first page, on the
    first load after they arrive (each run's provenance worked out afresh) and on the next (provenance known)."""
    home = _home(api, "bot:ops")
    cold, warm = {}, {}
    done = 0
    for size in (5, 50):
        seed_replies(api, home["id"], size - done, f"s{size}")
        done = size
        page, cold[size] = _first_page(api)
        again, warm[size] = _first_page(api)
        assert again == page
        [row] = page["unread"]
        assert row["id"] == home["id"] and row["new_count"] == size, row
    print(f"rail first page: cold {cold[5]} -> {cold[50]}, warm {warm[5]} -> {warm[50]} statements")
    assert cold[50] - cold[5] <= 15 and warm[50] - warm[5] <= 15, (cold, warm)


def test_catch_up_stays_flat_as_bot_replies_grow(api):
    """Catch up shares the rail's walk and shows each card's new messages, at most CATCH_UP_MESSAGES a card: once a card
    is full (50 replies), 150 add at most 15 statements."""
    home = _home(api, "bot:ops")
    counts = {}
    done = 0
    for size in (50, 150):
        seed_replies(api, home["id"], size - done, f"c{size}")
        done = size
        page, counts[size] = _first_page(api, path="threads/catch-up")
        assert [i["new_count"] for i in page["items"]] == [size], page["items"]
    assert counts[150] - counts[50] <= 15, counts


def test_a_bots_home_replies_are_checked_once_a_load(api, monkeypatch):
    """The walk counts a bot's Home by `_home_replies` alone, so it does not check the same replies again (and spend
    CATCH_UP_HIDDEN on them) for a count it would throw away."""
    home = _home(api, "bot:ops")
    seed_replies(api, home["id"], 30, "once")
    checked = []
    real = threads.message_readable

    def spy(c, v, message, **kw):
        checked.append(dict(message)["id"])
        return real(c, v, message, **kw)

    monkeypatch.setattr(threads, "message_readable", spy)
    assert _home_count(api, home["id"]) == 30
    repeats = [mid for mid in set(checked) if checked.count(mid) > 1]
    assert not repeats, f"{len(repeats)} messages checked more than once"


def test_batched_checks_return_the_same_rail_catch_up_and_badge(api, monkeypatch):
    """AC-2 (spec P09): with and without priming, every row, count and Catch up item is the same, for a reader who may
    read a reply's run and for one who may not."""
    with api.app.state.store.transaction() as c:
        secret = H.task_create(c, "human:ana", "Private", "Secret", "human:ana", private=True, lint=False)
    home = _home(api, "bot:ops")
    seed_replies(api, home["id"], 4, "eq", private_task=secret["id"])
    # A mention in a members thread and a 1:1 DM from the bot, beside the Home.
    made = api.post(
        "/api/v2/threads",
        json={"title": "Budget", "audience": "members", "members": ["human:ben", "human:cara"]},
        headers=headers("ana-test"),
    ).json()
    api.post(
        f"/api/v2/threads/{made['id']}/messages",
        json={"text": "@ben totals?", "to_actor": "human:ben"},
        headers=headers("ana-test"),
    )
    dm = api.get("/api/v2/threads/dm", params={"with": "bot:ops"}, headers=headers("ben-test")).json()
    with api.app.state.store.transaction() as c:
        threads.post(c, "bot:ops", dm["id"], "Deploy finished")
    paths = ("threads/rail", "threads/catch-up", "threads/catch-up?limit=1")
    readers = ("ben-test", "ana-test", "cara-test")
    batched = {(p, r): get(api, p, r) for p in paths for r in readers}
    monkeypatch.setattr(read_cache, "cache", lambda c: None)
    for (path, reader), page in batched.items():
        alone = get(api, path, reader)
        page.pop("seq", None), alone.pop("seq", None)
        assert alone == page, f"priming changed {path} for {reader}"
    # Ben may not read the private task, so the reply whose run read its comment stays hidden from him.
    [row] = [r for r in batched["threads/rail", "ben-test"]["unread"] if r["id"] == home["id"]]
    assert row["new_count"] == 3, row


def _home_count(api, home_id, token="ben-test"):
    rows = [r for r in get(api, "threads/rail", token)["unread"] if r["id"] == home_id]
    return rows[0]["new_count"] if rows else 0


def test_a_reply_whose_run_read_a_refused_page_stays_hidden_on_every_load(api, monkeypatch):
    """AC-2 (spec P09): a reply inherits the file refusal of what its run read (task_privacy.attempt_pages_readable).
    With the page deleted after the run, that reply is hidden on the first load after a provenance change (runs primed
    whole) and on the loads after it (only the runs' messages primed), the same as with no priming at all; and again
    once the Home holds shared-room history, where every message takes the whole check."""
    _, attempt = turn(api)
    fid = page(api, attempt).json()["page"]["file_id"]
    home = _home(api, "bot:ops")
    seed_replies(api, home["id"], 3, "pg")
    with api.app.state.store.transaction() as c:
        named = threads.post(c, "human:ben", home["id"], "See the page", refs={"target": {"file": fid}})
        c.execute("INSERT INTO attempt_inputs(attempt_id,message_id) VALUES('run-pg-0',?)", (named["id"],))
        c.execute("UPDATE bot_files SET state='deleted' WHERE id=?", (fid,))
    primed = thread_rail._prime

    def loads():
        with api.app.state.store.transaction() as c:
            c.execute("UPDATE provenance_clock SET value=value+1 WHERE id=1")
        return [_home_count(api, home["id"]) for _ in range(3)]

    for legacy in (False, True):
        if legacy:
            with api.app.state.store.transaction() as c:
                c.execute("UPDATE messages SET legacy_audience='shared:ops', restricted=1 WHERE id=?", (named["id"],))
        monkeypatch.setattr(thread_rail, "_prime", primed)
        batched = loads()
        monkeypatch.setattr(thread_rail, "_prime", lambda c, rows: None)
        alone = loads()
        assert batched == alone, (legacy, batched, alone)
        assert batched == [2, 2, 2], (legacy, batched)  # the reply whose run read the deleted page is hidden

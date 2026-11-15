"""The Threads rail's first page costs a flat number of SQL statements however many bot replies wait in the reader's
bot Homes: each reply names the run that wrote it, and the per-message check reads those runs, their inputs and the
messages they reply to together, never one reply at a time. Priming changes no row or count. Fictional people only:
ana (owner), ben (bot admin) and cara."""

from backend import read_cache, threads
from backend.store import H
from backend.tests.slug_ids import bot_ids_are_their_slugs  # noqa: F401  (each bot's id is its slug)
from backend.tests.helpers.api import get, headers  # noqa: F401  (the fixture and its helper)
from backend.tests.helpers.task_privacy_reads import traced_get
from backend.tests.helpers.thread_rail_batches import home as _home, runner, seed_replies  # noqa: F401  (fixture)


def _first_page(api, token="ben-test"):
    page, statements = traced_get(api, "threads/rail", token)
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

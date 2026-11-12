"""GET /api/v2/threads/home/{actor} reads an existing Home under a snapshot and takes the writer only to make a missing
one (perf audit I50). Fictional people and bots only: ana (owner) and the ops bot."""

import sys
import threading
import time

from backend.tests.helpers.api import headers  # noqa: F401  (the fixture and its helper)

HOLD_S = 0.3


def home(api, actor, token="ana-test"):
    return api.get(f"/api/v2/threads/home/{actor}", headers=headers(token))


def count_writers(store, monkeypatch):
    """How many times the Home route itself opens the writer."""
    taken = []
    original = store.transaction

    def counted(*args, **kwargs):
        if sys._getframe(1).f_code.co_name == "home_thread":  # the route's own; a person's first-seen note is not
            taken.append(1)
        return original(*args, **kwargs)

    monkeypatch.setattr(store, "transaction", counted)
    return taken


def test_a_missing_home_takes_the_writer_once_and_an_existing_one_never(api, monkeypatch):
    taken = count_writers(api.app.state.store, monkeypatch)
    made = home(api, "bot:ops")
    assert made.status_code == 200, made.text
    assert len(taken) == 1  # made on first use, under the writer
    again = home(api, "bot:ops")
    assert again.status_code == 200 and again.json()["id"] == made.json()["id"]
    assert len(taken) == 1  # read from the snapshot


def test_an_existing_home_is_served_while_another_request_holds_the_writer(api):
    store = api.app.state.store
    first = home(api, "bot:ops").json()["id"]
    holding, release = threading.Event(), threading.Event()

    def hold():
        with store.transaction():
            holding.set()
            release.wait(HOLD_S)

    holder = threading.Thread(target=hold)
    holder.start()
    assert holding.wait(5)
    started = time.monotonic()
    got = home(api, "bot:ops")
    elapsed = time.monotonic() - started
    release.set()
    holder.join()
    assert got.status_code == 200 and got.json()["id"] == first
    # Before I50 this waited out the whole hold (about HOLD_S); now it does not wait on the writer at all.
    assert elapsed < HOLD_S / 2, f"Home read took {elapsed * 1000:.0f} ms under a {HOLD_S * 1000:.0f} ms writer hold"


def test_an_unknown_teammate_is_still_the_same_404_and_makes_nothing(api):
    for actor in ("bot:nobody", "human:nobody", "group:nobody"):
        got = home(api, actor)
        assert got.status_code == 404, (actor, got.text)
    with api.app.state.store.read() as c:
        assert c.execute("SELECT count(*) FROM conversations WHERE owner_id LIKE '%nobody'").fetchone()[0] == 0

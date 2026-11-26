"""One app open's Needs you, before and after it is built once per change (backend/needs_share.py) and its per-task
reviews are read together (task_requirements.prime_reviews), on the hosted company's shape (needs-you-per-person.py
`seed_tidy`, fictional rows) with a linked GitHub PR Tico has read on every open task.

    .venv/bin/python -m pytest bench/needs-you-shared-build.py -n 0 -s -q

It prints, for the company's person: the statements of one build as main reads it (no batched reviews) and as this
change reads it, and the builds and statements of one app open (needs-you twice, four live-stream starts, the company
floor and two place reads) without sharing and with it. Both lists must be the same.
"""

import importlib.util
import pathlib

from backend import events, needs_share
from backend import task_requirements as R
from backend import views as V
from backend.item_sources import github as GS
from backend.store import H
from backend.tests.helpers.api import Identity, headers


def _tidy():
    path = pathlib.Path(__file__).with_name("needs-you-per-person.py")
    spec = importlib.util.spec_from_file_location("needs_you_per_person", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _build(store, auth, who):
    seen: list[str] = []
    with store.read_transaction() as c:
        c.set_trace_callback(seen.append)
        items = V.needs_items(c, auth, who)
        c.set_trace_callback(None)
    return items, len(seen)


def _open(api, store, auth, who):
    """The eight reads of one app open, counted: (builds, statements)."""
    seen: list[str] = []
    take, give = store._take, store._give

    def counting_take():
        c = take()
        c.set_trace_callback(seen.append)
        return c

    def quiet_give(c):
        c.set_trace_callback(None)
        give(c)

    store._take, store._give = counting_take, quiet_give
    before = needs_share.builds
    try:
        for url in ("needs-you", "needs-you?count=1"):
            assert api.get(f"/api/v2/{url}", headers=headers("ana-test")).status_code == 200
        for _ in range(4):
            with store.read_transaction() as c:
                events.needs_baseline(c, auth, who, None)
        for url in ("company/floor", "places/dashboard"):
            assert api.get(f"/api/v2/{url}", headers=headers("ana-test")).status_code == 200
    finally:
        store._take, store._give = take, give
    return needs_share.builds - before, len(seen)


def test_one_open_builds_needs_you_once(api, monkeypatch):
    from backend.blobs import Blobs

    tidy = _tidy()
    store = api.app.state.store
    store.blobs = store.blobs or Blobs(store.settings)
    tidy.seed_tidy(store, 400)
    with store.transaction() as c:
        ids = [r[0] for r in c.execute("SELECT id FROM tasks WHERE status='open' ORDER BY rowid")]
        for k, task_id in enumerate(ids):
            H.task_link(c, "human:ana", task_id, f"https://github.com/acme/app/pull/{1000 + k}")
            GS.save_pr(c, "acme/app", 1000 + k, {"title": f"PR {k}", "state": "open", "head_sha": "a" * 40}, 1)
    auth = api.app.state.auth
    who = Identity(tidy.ANA, "owner", "ana@acme.example")

    with monkeypatch.context() as main:
        main.setattr(needs_share, "mark", lambda c: None)
        main.setattr(R, "prime_reviews", lambda conn, ids: None)
        before, before_sql = _build(store, auth, who)
        main_builds, main_open_sql = _open(api, store, auth, who)
    needs_share.clear()
    after, after_sql = _build(store, auth, who)
    needs_share.clear()
    builds, open_sql = _open(api, store, auth, who)
    assert after == before
    print(f"\n  Ana has {len(after)} items")
    print(f"  one build      main {before_sql:6d} statements   now {after_sql:6d}")
    print(
        f"  one app open   main {main_builds:2d} builds {main_open_sql:6d} statements   now {builds:2d} builds {open_sql:6d}"
    )
    assert builds == 1

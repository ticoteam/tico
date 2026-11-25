"""GET /api/v2/places/{place} reads each pin, its card or page and what a card shows in batches, so the statements
it takes do not grow with how many pins a place has (or how many cards of the same kind it shows)."""

import contextlib

from backend.tests.helpers import api as T
from backend.tests.helpers.api_app import api_client
from backend.tests.helpers.api import post, put  # noqa: F401  (the api fixture)
from backend.tests.helpers.cards import page, patch, task

# Card kinds a Dashboard can take, each read per card.
KINDS = [
    ("tasks_by_status", {}),
    ("done_per_week", {"weeks": 4}),
    ("needs_you", {}),
    ("my_tasks", {"count": 3}),
    ("updates", {"count": 3}),
    ("unread_threads", {"count": 3}),
    ("latest_pages", {"count": 3}),
    ("needs_attention", {}),
    ("automation_runs", {}),
    ("spend", {}),
    ("tasks_by_stage", {"pipeline": "general"}),
]


def place_statements(api, monkeypatch, path, token="ana-test"):
    """GET `path` as `token`: the body, and the statements it took. Each request starts from cold roster and row
    caches, as a poll from a new process would."""
    from backend.tests.helpers.statement_scope import measuring, recorder

    store, seen = api.app.state.store, []
    store.roster_rows = {}
    store.row_cache = type(store.row_cache)()

    def traced(opened):
        @contextlib.contextmanager
        def borrowed(*args, **kwargs):
            with opened(*args, **kwargs) as c:
                c.set_trace_callback(recorder(seen))
                try:
                    yield c
                finally:
                    c.set_trace_callback(None)

        return borrowed

    with monkeypatch.context() as patched:
        for name in ("read", "read_transaction", "transaction"):
            patched.setattr(store, name, traced(getattr(store, name)))
        with measuring(seen):
            response = api.get("/api/v2/places/" + path, headers=T.headers(token))
    assert response.status_code == 200, response.text
    return response.json(), len(seen)


def fill(api, count):
    """`count` more pins of each kind on the Dashboard: a page and a card of every kind, read by the owner. A place
    holds 60 pins (PLACE_MAX), the four defaults among them."""
    put(api, "places/dashboard", {})
    for k in range(count):
        task(api, "bot:ops", f"Ops work {k}")
        fid = page(api, "ops", f"Weekly numbers {k}")
        post(api, "pins", {"place": "dashboard", "item": "page:" + fid})
        for template, settings in KINDS:
            post(api, "cards", {"template": template, "settings": settings, "place": "dashboard"})


def test_the_dashboard_takes_the_same_statements_however_many_pins_it_shows(tmp_path, monkeypatch):
    counted = {}
    for name, count in (("small", 1), ("big", 4)):
        (tmp_path / name).mkdir()
        install = api_client(tmp_path / name, None)
        api = next(install)
        try:
            fill(api, count)
            body, counted[name] = place_statements(api, monkeypatch, "dashboard")
            assert len(body["pins"]) == 4 + count * (1 + len(KINDS)), len(body["pins"])
        finally:
            install.close()
    few, many = counted["small"], counted["big"]
    assert few > 0 and many > 0, (few, many)
    assert many - few <= 4, (
        f"GET /api/v2/places/dashboard takes {many} statements with 4 pins a kind against {few} with 1: a read "
        "runs once per pin again instead of once for the whole place. Batch it (see cards.prime_pins)."
    )


def test_tasks_by_stage_takes_the_same_statements_however_many_stages_have_tasks(api, monkeypatch):
    """One read for the counts and the first titles of every stage, not one per stage: a pipeline with a task in each
    of 12 stages takes what one with 2 does."""
    from backend import task_engine as E
    from backend.store import H
    from backend.tests.helpers.engine import pipeline

    with api.app.state.store.transaction() as c:
        for pid, n in (("few", 2), ("many", 12)):
            names = [f"Stage {k}" for k in range(n)]
            pipeline(c, pid, pid.title(), stages=[{"name": name, "owner": "assignee"} for name in names])
            for name in names:
                tid = H.task_create(c, "human:ana", f"{pid} {name}", "Details.", "bot:ops", pipeline=pid, lint=False)[
                    "id"
                ]
                if name != names[0]:
                    E.move(c, tid, name, "human:ana", "for the test")
    card = post(api, "cards", {"template": "tasks_by_stage", "settings": {"pipeline": "few"}, "place": "bot:ops"})
    counted = {}
    for pid, n in (("few", 2), ("many", 12)):
        patch(api, "cards/" + card["card"]["id"], {"settings": {"pipeline": pid}})
        body, counted[pid] = place_statements(api, monkeypatch, "bot:ops")
        shown = next(p["card"] for p in body["pins"] if "card" in p)
        assert len(shown["data"]["stages"]) == n, shown
    assert counted["many"] == counted["few"], counted

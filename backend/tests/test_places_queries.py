"""GET /api/v2/places/{place} reads each pin, its card or page and what a card shows in batches, so the statements
it takes do not grow with how many pins a place has (or how many cards of the same kind it shows)."""
import contextlib

from backend.tests import test_api as T
from backend.tests.test_api import api, post, put  # noqa: F401  (the api fixture)
from backend.tests.test_cards import page, task

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
]


def place_statements(api, monkeypatch, path, token="ana-test"):
    """GET `path` as `token`: the body, and the statements it took. Each request starts from cold roster and row
    caches, as a poll from a new process would."""
    store, seen = api.app.state.store, []
    store.roster_rows = {}
    store.row_cache = type(store.row_cache)()

    def traced(opened):
        @contextlib.contextmanager
        def borrowed(*args, **kwargs):
            with opened(*args, **kwargs) as c:
                c.set_trace_callback(seen.append)
                try:
                    yield c
                finally:
                    c.set_trace_callback(None)
        return borrowed
    with monkeypatch.context() as patched:
        for name in ("read", "read_transaction", "transaction"):
            patched.setattr(store, name, traced(getattr(store, name)))
        response = api.get("/api/v2/places/" + path, headers=T.headers(token))
    assert response.status_code == 200, response.text
    return response.json(), len(seen)


def fill(api, count):
    """`count` more pins of each kind on the Dashboard: a page and a card of every kind, read by the owner. A place
    holds 60 pins (PLACE_MAX), the four defaults among them."""
    put(api, "places/dashboard", {})
    for k in range(count):
        task(api, "bot:ops", f"Ops work {k}", status="doing" if k % 2 else "open")
        fid = page(api, "ops", f"Weekly numbers {k}")
        post(api, "pins", {"place": "dashboard", "item": "page:" + fid})
        for template, settings in KINDS:
            post(api, "cards", {"template": template, "settings": settings, "place": "dashboard"})


def test_the_dashboard_takes_the_same_statements_however_many_pins_it_shows(tmp_path, monkeypatch):
    counted = {}
    for name, count in (("small", 1), ("big", 5)):
        (tmp_path / name).mkdir()
        install = T.api.__wrapped__(tmp_path / name, None)
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
        f"GET /api/v2/places/dashboard takes {many} statements with 5 more pins a kind against {few} with 1: a read "
        "runs once per pin again instead of once for the whole place. Batch it (see cards.prime_pins).")

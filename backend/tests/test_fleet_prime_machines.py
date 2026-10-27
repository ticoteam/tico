"""fleet_snapshot primes machine() rows once for readable bots."""

from backend.auth import Identity
from backend.tests.test_api import api, assign, claim, post, ready, runner  # noqa: F401
from backend.tests.test_views import as_member
from backend import views
from backend import read_cache


def test_fleet_snapshot_primes_assigned_sql_for_readable_bots(api):
    as_member(api, "ben@acme.example")
    post(api, "tasks", {"title": "Fleet prime", "body": "Run", "owner": "coo"}, "ben-test")
    machine = runner(api)
    assign(api, machine, "coo")
    ready(api, machine, ["coo"])
    assert claim(api, machine)
    who = Identity("human:ben", "human", email="ben@acme.example")
    with api.app.state.store.read_transaction() as c:
        views.fleet_snapshot(c, api.app.state.auth, who, lambda row, **k: row)
        values = read_cache.cache(c)
        key = ("one", views.ASSIGNED_SQL, ("coo",))
        assert values is not None and key in values

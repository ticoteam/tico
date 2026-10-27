"""Operations listing builds every computer's bot_connections in one pass."""

from backend.tests.test_api import api, assign, runner  # noqa: F401
from backend import subscriptions


def test_connections_by_runner_matches_per_computer(api):
    a, b = runner(api, "ana", "A Mac"), runner(api, "ana", "B Mac")
    assign(api, a, "coo")
    assign(api, b, "cpo")
    with api.app.state.store.read() as c:
        ctx = subscriptions.context(c, api.app.state.store.settings)
        by = subscriptions.connections_by_runner(c, api.app.state.store.settings, ctx)
        assert by.get(a["runner_id"]) == subscriptions.connections(
            c, a["runner_id"], api.app.state.store.settings, ctx)
        assert by.get(b["runner_id"]) == subscriptions.connections(
            c, b["runner_id"], api.app.state.store.settings, ctx)
        assert {row["bot"] for row in by[a["runner_id"]]} == {"coo"}
        assert {row["bot"] for row in by[b["runner_id"]]} == {"cpo"}

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


def test_connections_by_runner_follows_an_unplaced_original_and_lists_a_revoked_computer(api):
    import json
    a, gone = runner(api, "ana", "A Mac"), runner(api, "ana", "Old Mac")
    assign(api, a, "cpo")
    assign(api, gone, "coo")
    with api.app.state.store.transaction() as c:
        # cpo is a branch of ops, which runs on codex and sits on no computer.
        for bot, config in (("ops", {"runtime": "codex", "harness": "codex"}), ("cpo", {"shared_from": "ops"})):
            c.execute("INSERT INTO bot_config(bot,config,operator) VALUES(?,?,'ana') "
                      "ON CONFLICT(bot) DO UPDATE SET config=excluded.config", (bot, json.dumps(config)))
        c.execute("UPDATE runners SET revoked_at=? WHERE id=?", ("2026-01-01T00:00:00Z", gone["runner_id"]))
    settings = api.app.state.store.settings
    with api.app.state.store.read() as c:
        ctx = subscriptions.context(c, settings)
        by = subscriptions.connections_by_runner(c, settings, ctx)
        for r in (a, gone):
            assert by.get(r["runner_id"]) == subscriptions.connections(c, r["runner_id"], settings, ctx)
        assert by[a["runner_id"]][0]["runtime"] == "codex"
        assert by[gone["runner_id"]][0]["bot"] == "coo"

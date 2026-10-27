"""computer_details loads active assignments once per snapshot, not once per computer."""

from backend.tests.test_api import api, assign, runner  # noqa: F401
from backend import views


def test_active_assigned_by_runner_matches_per_computer_and_reuses_the_map(api):
    a, b = runner(api, "ana", "A Mac"), runner(api, "ana", "B Mac")
    assign(api, a, "coo")
    assign(api, b, "cpo")
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE bots SET state='archived' WHERE slug='coo'")
    with api.app.state.store.read_transaction() as c:
        by = views.active_assigned_by_runner(c)
        assert by is views.active_assigned_by_runner(c)
        assert "coo" not in by.get(a["runner_id"], set())
        assert by[b["runner_id"]] == {"cpo"}
        per_a = {r[0] for r in c.execute(
            "SELECT a.bot FROM assignments a JOIN bots b ON b.slug=a.bot "
            "WHERE a.runner_id=? AND b.state<>'archived'", (a["runner_id"],))}
        per_b = {r[0] for r in c.execute(
            "SELECT a.bot FROM assignments a JOIN bots b ON b.slug=a.bot "
            "WHERE a.runner_id=? AND b.state<>'archived'", (b["runner_id"],))}
        assert by.get(a["runner_id"], set()) == per_a
        assert by[b["runner_id"]] == per_b

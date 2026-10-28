"""Operations and Computers listing load every computer's assignments in one pass."""

from backend.tests.test_api import api, assign, get, runner  # noqa: F401
from backend import views


def test_assignments_by_runner_matches_per_computer_lookups(api):
    a, b = runner(api, "ana", "A Mac"), runner(api, "ana", "B Mac")
    assign(api, a, "coo")
    assign(api, b, "cpo")
    with api.app.state.store.transaction() as c:
        # Archive one bot on A so active_placed drops it while ordered still lists it.
        c.execute("UPDATE bots SET state='archived' WHERE slug='coo'")
    with api.app.state.store.read() as c:
        placed, active = views.assignments_by_runner(c)
        assert placed[a["runner_id"]] == [r[0] for r in c.execute(
            "SELECT bot FROM assignments WHERE runner_id=? ORDER BY bot", (a["runner_id"],))]
        assert active.get(a["runner_id"], set()) == {r[0] for r in c.execute(
            "SELECT a.bot FROM assignments a JOIN bots b ON b.slug=a.bot "
            "WHERE a.runner_id=? AND b.state<>'archived'", (a["runner_id"],))}
        assert "coo" in placed[a["runner_id"]]
        assert "coo" not in active.get(a["runner_id"], set())
        assert placed[b["runner_id"]] == ["cpo"]
        assert active[b["runner_id"]] == {"cpo"}
    machines = {m["id"]: m for m in get(api, "operations")["machines"]}
    assert "coo" in machines[a["runner_id"]]["bots"]  # archived still listed on the computer
    assert "cpo" in machines[b["runner_id"]]["bots"]
    assert "coo" not in machines[a["runner_id"]]["readiness"].get("bots", {})

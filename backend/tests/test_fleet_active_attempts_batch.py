"""fleet_snapshot loads live attempts once, not once per writable bot."""

from backend.tests.test_api import api, assign, claim, get, post, ready, runner  # noqa: F401
from backend.tests.test_views import as_member
from backend.tests.test_task_privacy_reads import traced


def test_fleet_snapshot_reports_active_attempt_from_batched_live_map(api):
    as_member(api, "ben@acme.example")
    post(api, "tasks", {"title": "Fleet active check", "body": "Run it", "owner": "coo"}, "ben-test")
    machine = runner(api)
    assign(api, machine, "coo")
    ready(api, machine, ["coo"])
    attempt = claim(api, machine)
    assert attempt
    fleet, statements = traced(api, lambda: get(api, "tico/fleet", attempt["token"]))
    coo = next(bot for bot in fleet["bots"] if bot["slug"] == "coo")
    assert coo["active_runs"] >= 1
    assert coo["active_run"]["id"] == attempt["id"]
    # The same four fields as before the batch, which also reads each attempt's bot and created.
    assert set(coo["active_run"]) == {"id", "state", "started_at", "lease_until"}
    assert not [s for s in statements if "FROM attempts WHERE bot_id=" in s]

"""fleet_snapshot primes machine() rows once for readable bots."""

from backend.tests.test_api import api, assign, claim, get, post, ready, runner  # noqa: F401
from backend.tests.test_task_privacy_reads import traced
from backend.tests.test_views import as_member


def test_fleet_snapshot_reads_no_assignment_per_bot(api):
    as_member(api, "ben@acme.example")
    post(api, "tasks", {"title": "Fleet prime", "body": "Run", "owner_id": "coo"}, "ben-test")
    machine = runner(api)
    assign(api, machine, "coo")
    ready(api, machine, ["coo"])
    attempt = claim(api, machine)
    fleet, statements = traced(api, lambda: get(api, "tico/fleet", attempt["token"]))
    assert next(bot for bot in fleet["bots"] if bot["slug"] == "coo")["online"]
    # machine() for each shown bot reads the primed row, not its own assignment SELECT.
    assert not [s for s in statements if "JOIN runners r ON r.id=a.runner_id WHERE a.bot_id=" in s]

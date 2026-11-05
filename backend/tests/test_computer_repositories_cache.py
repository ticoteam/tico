"""computer_details loads repository status once per snapshot, not once per computer."""

import json

from backend.tests.test_api import api, runner  # noqa: F401
from backend import repositories


def test_computer_repositories_reuses_the_map_within_one_read_snapshot(api):
    a, b = runner(api, "ana", "A Mac"), runner(api, "ana", "B Mac")
    with api.app.state.store.transaction() as c:
        c.execute("INSERT INTO registry_metadata VALUES(?,?)",
                  ("computer-repositories:" + a["runner_id"], json.dumps({"repositories": "ready"})))
        c.execute("INSERT INTO registry_metadata VALUES(?,?)",
                  ("computer-repositories:" + b["runner_id"], json.dumps({"repositories": "syncing"})))
    with api.app.state.store.read_transaction() as c:
        first = repositories.computer_repositories(c)
        assert first is repositories.computer_repositories(c)
        assert first[a["runner_id"]] == "ready"
        assert first[b["runner_id"]] == "syncing"
        assert first[a["runner_id"]] == repositories.metadata(
            c, "computer-repositories:" + a["runner_id"]).get("repositories", "unknown")

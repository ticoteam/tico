"""Operations listing loads every computer's repository status in one pass."""

import json

from backend.tests.test_api import api, runner  # noqa: F401
from backend import repositories


def test_computer_repositories_batch_matches_per_runner_metadata(api):
    a, b = runner(api, "ana", "A Mac"), runner(api, "ana", "B Mac")
    with api.app.state.store.transaction() as c:
        c.execute("INSERT INTO registry_metadata VALUES(?,?)",
                  ("computer-repositories:" + a["runner_id"], json.dumps({"repositories": "ready"})))
        c.execute("INSERT INTO registry_metadata VALUES(?,?)",
                  ("computer-repositories:" + b["runner_id"], json.dumps({"repositories": "syncing"})))
    with api.app.state.store.read() as c:
        by = repositories.computer_repositories(c)
        assert by[a["runner_id"]] == repositories.metadata(
            c, "computer-repositories:" + a["runner_id"]).get("repositories", "unknown")
        assert by[b["runner_id"]] == "syncing"
        assert by[a["runner_id"]] == "ready"

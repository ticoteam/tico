"""Operations reads each computer's harness, sign-in, repository, key and assignment rows once for every computer."""

import json

from backend.tests.test_api import api, get, runner  # noqa: F401
from backend.tests.test_task_privacy_reads import traced

PER_COMPUTER = ("runner_harness_choices", "runner_harness_actions", "model_login_cleared", "computer_keys",
                "assignments", "computer-repositories:", "subscription:")


def counted(api, token):
    page, statements = traced(api, lambda: get(api, "operations", token=token))
    return page, {name: sum(name in s for s in statements) for name in PER_COMPUTER}


def seed(api, labels):
    made = [runner(api, "ana", label) for label in labels]
    with api.app.state.store.transaction() as c:
        for r in made:
            c.execute("INSERT INTO registry_metadata VALUES(?,?)", ("computer-repositories:" + r["runner_id"],
                                                                     json.dumps({"repositories": "ready"})))
            c.execute("INSERT INTO model_login_cleared(runner_id,runtime,profile,cleared_at,cleared_by,clear_key) "
                      "VALUES(?,?,?,?,?,1)", (r["runner_id"], "claude", "", "2026-01-01T00:00:00Z", "human:ana"))
    return made


def test_operations_reads_per_computer_rows_once_however_many_computers(api):
    seed(api, ["A Mac", "B Mac"])
    few = {token: counted(api, token) for token in ("ana-test", "ben-test")}
    seed(api, ["C Mac", "D Mac", "E Mac", "F Mac"])
    many = {token: counted(api, token) for token in ("ana-test", "ben-test")}
    for token in few:
        page = many[token][0]
        assert len(page["machines"]) == 6
        assert {m["repositories"] for m in page["machines"]} == {"ready"}
        assert all(m["key_cleared"] == ["claude"] for m in page["machines"])
        assert many[token][1] == few[token][1], (token, few[token][1], many[token][1])

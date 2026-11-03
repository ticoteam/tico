"""Why an item is on someone's list: its cause, its siblings and the commands that would stop it."""

import shlex

from backend import hubdb as H
from backend.tests.test_api import api, get, headers, post, setup_attempt  # noqa: F401


def test_a_bot_filed_task_names_the_bot_and_stop_commands_parse(api):
    with api.app.state.store.transaction() as c:
        task = H.task_create(c, "bot:finance", "Give finance the payroll connector", "x", "human:ana")
    origin = get(api, "origins/" + task["id"])

    assert origin["kind"] == "task" and origin["id"] == task["id"]
    assert origin["cause"]["type"] == "bot"
    assert origin["siblings"]["made"] >= 1 and origin["siblings"]["open"] <= origin["siblings"]["made"]
    assert 1 <= len(origin["stop"]) <= 3
    from clients import hubcli
    cli = hubcli.parser()
    for stop in origin["stop"]:
        words = shlex.split(stop["command"])
        assert words[0] == "hub"
        cli.parse_args(words[1:])  # every stop is a command the CLI really has


def test_a_ref_the_caller_cannot_read_is_not_found(api):
    r = api.get("/api/v2/origins/zzzzzzzzzzzz", headers=headers("ben-test"))
    assert r.status_code == 404


def test_payload_is_small(api):
    with api.app.state.store.transaction() as c:
        task = H.task_create(c, "bot:finance", "Give finance the payroll connector", "x", "human:ana")
    r = api.get("/api/v2/origins/" + task["id"], headers=headers())
    assert r.status_code == 200 and len(r.content) < 8192

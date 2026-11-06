"""Why an item is on someone's list: its cause, its siblings and the commands that would stop it."""

import shlex

from backend import hubdb as H
from backend.tests.test_api import api, get, headers, post, setup_attempt  # noqa: F401
from backend.tests.test_automation_api import make  # noqa: F401


def test_a_bot_filed_task_names_the_bot_and_stop_commands_parse_and_the_payload_is_small(api):
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
    r = api.get("/api/v2/origins/" + task["id"], headers=headers())
    assert r.status_code == 200 and len(r.content) < 8192


def test_an_automation_or_parent_task_is_named_with_a_stop_that_parses(api):
    from clients import hubcli
    cli = hubcli.parser()
    made = make(api)
    aid = made["automation"]["id"] if "automation" in made else made["id"]
    with api.app.state.store.transaction() as c:
        task = H.task_create(c, "bot:finance", "Morning check for Ana", "x", "human:ana")
        c.execute("UPDATE tasks SET occurrence=? WHERE id=?", (aid + ":2026-11-03", task["id"]))
        other = H.task_create(c, "bot:finance", "Morning check, last week", "x", "human:ana")
        # A saved run message whose task ref is a list (what a bot sends): its first task is the one it made.
        H.say(c, "bot:finance", "human:ana", "Morning check ran.", refs={"automation": aid, "task": [other["id"]]})
        parent = H.task_create(c, "human:ana", "Parent work", "x", "human:ana")
        child = H.task_create(c, "human:ana", "Child work", "x", "human:ana", parent_id=parent["id"])
    origin = get(api, "origins/" + task["id"])

    assert origin["cause"]["type"] == "automation" and origin["cause"]["id"] == aid
    assert origin["siblings"]["made"] == 1  # a list task ref on another run message does not break it
    assert origin["cause"]["name"] == "Morning check"
    assert origin["stop"][0]["command"] == f"hub automation update {aid} --disable"
    for stop in origin["stop"]:
        cli.parse_args(shlex.split(stop["command"])[1:])

    origin = get(api, "origins/" + child["id"])
    assert origin["cause"] == {"type": "parent_task", "id": parent["id"], "title": "Parent work"}
    assert origin["stop"][0]["command"] == f"hub task close {parent['id']}"
    assert origin["siblings"]["made"] == 1


def test_an_item_the_caller_cannot_read_is_a_404_with_no_text_and_a_hidden_parent_is_not_named(api):
    r = api.get("/api/v2/origins/zzzzzzzzzzzz", headers=headers("ben-test"))
    assert r.status_code == 404
    private = post(api, "tasks", {"title": "Ben's private plan", "body": "x", "owner": "human:ben", "private": True},
                   token="ben-test")
    assert get(api, "origins/" + private["id"], "ben-test")["id"] == private["id"]
    r = api.get("/api/v2/origins/" + private["id"], headers=headers("cara-test"))
    assert r.status_code == 404

    with api.app.state.store.transaction() as c:
        msg = H.say(c, "human:ben", "human:ana", "Ana, the payroll number is private", kind="say")
        parent = H.task_create(c, "human:ana", "Ana private parent", "x", "human:ana", private=True)
        child = H.task_create(c, "human:ana", "Ben's child", "x", "human:ben", parent_id=parent["id"])
    r = api.get("/api/v2/origins/" + msg["id"], headers=headers("cara-test"))
    assert r.status_code == 404
    assert "payroll" not in r.text
    assert get(api, "origins/" + msg["id"], "ben-test")["kind"] == "message"

    origin = get(api, "origins/" + child["id"], "ben-test")
    assert origin["cause"] == {"type": "parent_task", "id": None, "title": "a task you can no longer see"}
    assert "Ana private parent" not in api.get("/api/v2/origins/" + child["id"], headers=headers("ben-test")).text

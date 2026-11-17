"""The `tico bot` commands of clients/commands/bots.py whose words or runs are their own: each sends what the hand parser
it replaced sent (clients/hubcli.py and clients/remotecli.py before the bot specs)."""

import pytest

from clients.commands import bots
from clients.tests.helpers import cli_fixtures
from clients.tests.helpers.cli_fixtures import run_line
from clients.tico import APIError


def sent(result):
    """The tool's arguments, without the operation id every write carries."""
    return {k: v for k, v in result["tools"][0][1].items() if k != "operation_id"}


@pytest.mark.parametrize(
    "argv, args",
    [
        (["bot", "turns-at-once", "b", "unlimited"], {"bot": "b", "limit": None}),
        (["bot", "turns-at-once", "b", "3"], {"bot": "b", "limit": 3}),
        (["bot", "turns-at-once", "b"], {"bot": "b"}),
        (["bot", "browser-exit", "b", "none"], {"bot": "b", "computer": None}),
        (["bot", "app-view", "b", "off"], {"bot": "b", "on": False}),
        (["bot", "profile", "b", "--sweeps", "on"], {"bot": "b", "sweeps": True}),
        (["bot", "budget", "b", "--hour", "default", "--day", "3"], {"bot": "b", "hour": "default", "day": 3}),
        (["bot", "go-live", "b", "--no-setup"], {"bot": "b", "setup": False}),
        (["bot", "status", "set", "Reviewing", "--task", "T1"], {"focus": "Reviewing", "task_id": "T1"}),
        (["bot", "mail", "b", "--no-send"], {"slug": "b", "outbound_send": False}),
        (["bot", "owners", "b", "--add", "7", "8"], {"slug": "b", "add": ["7", "8"]}),
        (["bot", "setup-done"], {}),
        (["bot", "list", "--archived"], {"archived": True}),
        (["bots", "show", "b"], {"slug": "b"}),
    ],
)
def test_the_words_a_command_takes_are_the_tools_arguments(monkeypatch, argv, args):
    assert sent(run_line(argv, monkeypatch)) == args


def test_budget_release_releases_the_bot_from_its_hold(monkeypatch):
    result = run_line(["bot", "budget", "b", "release"], monkeypatch)
    assert ["POST", "bots/b/release", {}] in result["http"]


def test_update_sends_the_subscriptions_as_a_list_and_none_as_empty(monkeypatch):
    assert sent(run_line(["bot", "update", "b", "--subscribe-updates", "templates, tico"], monkeypatch)) == {
        "slug": "b",
        "update_subscriptions": ["templates", "tico"],
    }
    assert sent(run_line(["bot", "update", "b", "--subscribe-updates", "none"], monkeypatch)) == {
        "slug": "b",
        "update_subscriptions": [],
    }


class Api:
    """The API a run calls: answers by path, and records the writes with their idempotency keys."""

    def __init__(self, answers):
        self.answers, self.writes = answers, []

    def get(self, path, /, **_query):
        return self.answers.get(path, {})

    def post(self, path, body=None, key=None):
        self.writes.append((path, body, key))
        return {"ok": True}


def test_update_sends_no_operation_id_as_the_hand_parser_did():
    api = Api({"me": {"actor": "human:1"}, "bots": [{"slug": "b", "revision": 4}]})
    bots._update(api, {"slug": "b", "display_name": "Builder", "operation_id": "op-7"})
    assert api.writes == [("bots/b/definition", {"display_name": "Builder", "expected_revision": 4}, None)]


def test_update_of_a_bot_that_is_not_there_is_not_found():
    with pytest.raises(APIError) as refused:
        bots._update(Api({"me": {"actor": "human:1"}, "bots": []}), {"slug": "gone"})
    assert refused.value.code == "not_found"
    assert "No bot gone" in refused.value.detail


def test_branch_sends_your_computer_by_its_id(monkeypatch):
    monkeypatch.setitem(cli_fixtures.ANSWERS, "GET me", {"actor": "human:7"})
    computers = [{"id": "r1", "label": "studio", "owner_id": "7"}, {"id": "r2", "label": "studio", "owner_id": "8"}]
    monkeypatch.setitem(cli_fixtures.ANSWERS, "GET computers", {"computers": computers})
    result = run_line(["bot", "branch", "b", "--computer", "studio"], monkeypatch)
    assert result["exit"] == 0
    assert ["POST", "bots/b/copies", {"runner_id": "r1"}] in result["http"]


def test_branch_without_a_computer_is_a_planned_branch(monkeypatch):
    result = run_line(["bot", "branch", "b"], monkeypatch)
    assert ["POST", "bots/b/copies", {}] in result["http"]
    assert result["tools"] == []  # the branch is made here, as it was, not through the tool's operation id

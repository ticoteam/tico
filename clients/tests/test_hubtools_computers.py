"""The computer tools (clients/commands/computer_upkeep.py) through the table and the `tico` CLI, with a stubbed API: restart
a computer's runner, who runs which release, the installation's own update, the shared cloud machine, subscriptions."""

import json

import pytest

from clients import hubcli, hubtools, remotecli
from clients.commands import computer_upkeep as hubtools_computers
from clients.tico import APIError


class Api:
    """A person's token against stubbed routes; `routes` maps (METHOD, path) to an answer or an APIError."""

    def __init__(self, routes=None, actor="human:ana"):
        self.routes, self.calls = dict(routes or {}), []
        self.actor = actor

    def answer(self, method, path, body=None):
        self.calls.append((method, path, body))
        found = self.routes.get((method, path))
        if isinstance(found, Exception):
            raise found
        if found is None:
            raise AssertionError(f"unexpected {method} {path}")
        return found(body) if callable(found) else json.loads(json.dumps(found))

    def get(self, path, **query):
        if path == "me":
            return {"actor": self.actor, "role": "owner", "kind": "owner"}
        return self.answer("GET", path, {k: v for k, v in query.items() if v is not None} or None)

    def post(self, path, body=None, key=None):
        return self.answer("POST", path, body)

    def call(self, method, path, body=None, key=None, query=None, delegate=False):
        return self.answer(method, path, body)


def tool(name, api, **args):
    return hubtools.BY_NAME[name]["fn"](api, args)


def cli(monkeypatch, api, *argv):
    """Run `tico <argv>` through the parser and remotecli against `api`; (exit code, printed JSON)."""
    printed = []
    monkeypatch.setattr(remotecli, "Client", lambda *a, **k: api)
    monkeypatch.setenv("HUB_API_URL", "https://acme.example")
    monkeypatch.setattr("builtins.print", lambda *a, **k: printed.append(a[0]) if not k.get("file") else None)
    code = remotecli.main(hubcli.parser().parse_args(list(argv)))
    return code, json.loads(printed[-1]) if printed else None


def test_every_computer_tool_is_registered_with_a_command_and_offered_to_people_not_the_assistant():
    names = {t["name"] for t in hubtools_computers.MINE}
    from clients.cli_spec import Command
    from clients.commands.computers import COMPUTER

    assert names <= {c.tool for c in (*hubtools_computers.UPKEEP, *COMPUTER) if isinstance(c, Command)}
    for name in names:
        entry = hubtools.BY_NAME[name]
        kinds = hubtools.offered_to(entry)
        assert "owner" in kinds and "assistant" not in kinds and "bot" not in kinds, name
    assert "botops" not in hubtools.offered_to(hubtools.BY_NAME["tico_upgrade_start"])
    assert "botops" in hubtools.offered_to(hubtools.BY_NAME["tico_computer_restart"])


COMPUTERS = {
    "computers": [
        {
            "id": "r1",
            "label": "Shared cloud machine",
            "online": True,
            "release": "0.6.0-rc.3",
            "version": "0.5.4",
            "update": {"state": "current", "wanted_release": "0.6.0-rc.3", "error": ""},
        },
        {
            "id": "r2",
            "label": "Ana's Mac",
            "online": False,
            "release": "0.5.9",
            "version": "0.5.1",
            "update": {"state": "failed", "target": "0.6.0-rc.3", "error": "disk full"},
        },
    ]
}


def test_computer_restart_finds_the_computer_by_name_and_versions_says_who_runs_what(monkeypatch):
    api = Api(
        {
            ("GET", "computers"): COMPUTERS,
            ("POST", "computers/r2/restart"): {"requested": True, "running": True},
            ("GET", "system/update"): {"running": "0.6.0-rc.3", "state": "idle", "from": "", "to": ""},
        }
    )
    code, got = cli(monkeypatch, api, "computer", "restart", "ana's mac")
    assert code == 0 and got["id"] == "r2" and got["requested"] and "a turn is running now" in got["detail"]
    with pytest.raises(APIError, match="tico computer list"):
        tool("tico_computer_restart", api, computer="nowhere")

    code, got = cli(monkeypatch, api, "computer", "versions")
    assert got["summary"] == "Server 0.6.0-rc.3 (idle); computers: 1 current, 1 failed."
    assert got["computers"][1] == {
        "computer": "Ana's Mac",
        "id": "r2",
        "online": False,
        "release": "0.5.9",
        "runner_version": "0.5.1",
        "state": "failed",
        "wanted": "0.6.0-rc.3",
        "error": "disk full",
    }
    # Only the Owner reads the server's own update; an admin still sees the computers.
    api.routes[("GET", "system/update")] = APIError("forbidden", "Only the owner sees update progress", 403)
    got = tool("tico_computer_versions", api)
    assert got["server"] is None and got["summary"] == "computers: 1 current, 1 failed."


def test_upgrade_commands_reach_the_installation_routes(monkeypatch):
    api = Api(
        {
            ("GET", "system/update"): {"running": "0.6.0-rc.3", "state": "idle"},
            ("POST", "system/update/check"): {"current": "0.6.0-rc.3", "latest": "0.6.0", "available": True},
            ("POST", "system/update"): lambda body: {"started": True, "to": body["version"]},
        }
    )
    assert cli(monkeypatch, api, "upgrade", "status")[1]["running"] == "0.6.0-rc.3"
    assert cli(monkeypatch, api, "upgrade", "check")[1]["latest"] == "0.6.0"
    code, got = cli(monkeypatch, api, "upgrade", "start", " 0.6.0 ")
    assert code == 0 and got == {"started": True, "to": "0.6.0"}


CLOUD = {
    "offered": True,
    "sizes": [{"name": "default", "cpu": 4, "memory_gb": 8, "disk_gb": 51, "usd_per_hour": 0.045, "idle_stop_s": 1}],
    "caps": {"max_running": 8},
    "shared": {"size": "default", "always_on": False, "pause_minutes": 30, "runner_id": "r1", "bots": ["b-1", "b-2"]},
}


def test_cloud_show_names_bots_and_the_computer_and_set_sends_only_what_changed(monkeypatch):
    api = Api(
        {
            ("GET", "cloud-machines"): CLOUD,
            ("GET", "bots"): [{"id": "b-1", "slug": "builder"}, {"id": "b-2", "slug": "reviewer-1"}],
            ("GET", "computers"): COMPUTERS,
            ("POST", "cloud-machines/shared"): lambda body: {"shared": {**CLOUD["shared"], **body}},
        }
    )
    code, got = cli(monkeypatch, api, "cloud", "show")
    assert got["shared"]["bots"] == ["builder", "reviewer-1"] and got["shared"]["computer"] == "Shared cloud machine"
    assert got["sizes"] == [{"name": "default", "cpu": 4, "memory_gb": 8, "disk_gb": 51, "usd_per_hour": 0.045}]

    code, got = cli(monkeypatch, api, "cloud", "set", "--no-always-on", "--pause-minutes", "60")
    assert api.calls[-1] == ("POST", "cloud-machines/shared", {"always_on": False, "pause_minutes": 60})
    with pytest.raises(ValueError, match="Name something to change"):
        tool("tico_cloud_set", api)

    assert tool("tico_cloud_show", Api({("GET", "cloud-machines"): {"offered": False}}))["offered"] is False


def test_cloud_replace_needs_the_machines_exact_name():
    api = Api(
        {
            ("GET", "cloud-machines"): CLOUD,
            ("GET", "computers"): COMPUTERS,
            ("DELETE", "cloud-machines/shared"): {"removed": True},
        }
    )
    with pytest.raises(APIError, match="--confirm 'Shared cloud machine'"):
        tool("tico_cloud_replace", api, confirm="shared")
    assert not [c for c in api.calls if c[0] == "DELETE"]
    assert tool("tico_cloud_replace", api, confirm="Shared cloud machine") == {"replaced": True, "removed": True}
    empty = Api({("GET", "cloud-machines"): {**CLOUD, "shared": {"size": "default"}}})
    assert tool("tico_cloud_replace", empty, confirm="x")["replaced"] is False


def test_subscription_list_names_computers_and_never_a_value():
    api = Api(
        {
            ("GET", "model-subscriptions"): {
                "subscriptions": [
                    {
                        "id": "s1",
                        "name": "Team Max",
                        "kind": "claude_max",
                        "label": "Claude Max",
                        "runtime": "claude",
                        "status": "ok",
                        "preview": "sk-...abcd",
                        "computers": ["r1"],
                        "added_by_name": "Ana",
                        "usage": {"secret": "x"},
                    }
                ]
            },
            ("GET", "computer-subscriptions"): {"assignments": [], "capable": ["r1"]},
            ("GET", "computers"): COMPUTERS,
        }
    )
    got = tool("tico_subscription_list", api)
    assert got["subscriptions"][0]["computers"] == ["Shared cloud machine"] and "usage" not in got["subscriptions"][0]
    assert got["cannot_take_one"] == ["Ana's Mac"]


def test_subscription_list_names_each_computer_given_a_persons_plans_the_plan_in_use_and_why():
    api = Api(
        {
            ("GET", "model-subscriptions"): {"subscriptions": []},
            ("GET", "computer-subscriptions"): {
                "assignments": [
                    {"runner_id": "r1", "runtime": "claude", "subscription_id": "s1", "person": "", "set": None},
                    {
                        "runner_id": "r2",
                        "runtime": "claude",
                        "subscription_id": "p2",
                        "person": "human:ana",
                        "set": {
                            "person": "human:ana",
                            "person_name": "Ana",
                            "plans": 3,
                            "on": "p2",
                            "on_name": "Plan 2",
                            "why": {"kind": "held", "plan": "Plan 1", "until": "2026-11-13T09:00:00Z"},
                        },
                    },
                ],
                "capable": ["r1", "r2"],
            },
            ("GET", "computers"): COMPUTERS,
        }
    )
    got = tool("tico_subscription_list", api)
    assert got["people_plans"] == [
        {
            "computer": "Ana's Mac",
            "tool": "claude",
            "plans": "Ana's plans",
            "count": 3,
            "on": "Plan 2",
            "why": "Plan 1 at its limit until 2026-11-13T09:00:00Z",
        }
    ]


def test_over_mcp_a_required_key_sent_as_null_resets_where_its_schema_allows_null_and_integers_read_as_json_numbers():
    """A required key present with null is not a missing key: the turns-at-once limit and a task cap are reset with
    null over MCP, as their schemas say. An absent key, or null where the schema has no null, is still refused before
    any request. 1.0 is an integer in JSON Schema and is sent as 1, since the server's strict fields refuse a float;
    1.5 is not an integer."""
    api = Api(
        {
            ("POST", "computers/c1/turns-at-once"): lambda body: {"computer": "c1", **body},
            ("PUT", "task-caps"): lambda body: {"cap": body},
        }
    )
    protocol = hubtools.Protocol(api, api_error=APIError, kind="owner")

    def call(name, args):
        return protocol.handle(
            {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": args}}
        )["result"]

    reset = call("tico_computer_turns_at_once", {"computer": "c1", "limit": None})
    assert not reset["isError"] and api.calls[-1] == ("POST", "computers/c1/turns-at-once", {"turns_at_once": None})
    uncapped = call("tico_task_cap_set", {"sender": "bot:seo", "receiver": "person:p1", "per_day": None})
    assert not uncapped["isError"] and api.calls[-1][2]["per_day"] is None
    assert not call("tico_computer_turns_at_once", {"computer": "c1", "limit": 1.0})["isError"]
    assert api.calls[-1][2] == {"turns_at_once": 1} and type(api.calls[-1][2]["turns_at_once"]) is int
    assert not call("tico_task_cap_set", {"sender": "bot:seo", "receiver": "person:p1", "per_day": 3.0})["isError"]
    assert type(api.calls[-1][2]["per_day"]) is int

    sent = len(api.calls)
    absent = call("tico_computer_turns_at_once", {"computer": "c1"})["structuredContent"]
    assert absent["error"] == "usage" and "needs limit" in absent["detail"]
    no_computer = call("tico_computer_turns_at_once", {"computer": None, "limit": 4})["structuredContent"]
    assert no_computer["error"] == "usage" and "needs computer" in no_computer["detail"]
    half = call("tico_computer_turns_at_once", {"computer": "c1", "limit": 1.5})["structuredContent"]
    assert half["error"] == "validation" and "expected integer" in half["detail"]
    assert call("tico_computer_turns_at_once", {"computer": "c1", "limit": True})["isError"]
    assert len(api.calls) == sent

"""`hub automation ...` and the hub_automation_* tools against the real API (clients/remotecli.py, clients/hubtools.py):
the CLI and MCP run the same tool, so the same rights and the same payloads as the UI."""
import json
from urllib.parse import urlencode

import pytest

from backend.tests.test_api import api, get, headers  # noqa: F401
from backend.tests.test_member_bots import botops, finish, turn  # noqa: F401  (fixtures)
from backend.tests.test_automation_api import NOTE_STEP, as_bot
from clients import hubcli, remotecli, hubtools


class Client:
    """The hub client over the test app, as one caller."""

    def __init__(self, api, token):
        self.api, self.token = api, token

    def _call(self, method, path, body=None):
        r = self.api.request(method, "/api/v2/" + path, json=body, headers=headers(self.token))
        if r.status_code != 200:
            raise remotecli.APIError(str(r.status_code), r.text, r.status_code)
        return r.json()

    def call(self, method, path, body=None, key=None, query=None):
        query = urlencode({k: v for k, v in (query or {}).items() if v is not None})
        return self._call(method, path + ("?" + query if query else ""), body)

    def get(self, path, **params):
        return self.call("GET", path, query=params)

    def post(self, path, body, key=None):
        return self._call("POST", path, body)

    def patch(self, path, body, key=None):
        return self._call("PATCH", path, body)


def cli(api, token, *argv):
    return remotecli.automation(Client(api, token), hubcli.parser().parse_args(["automation", *argv]))


@pytest.fixture
def flow_file(tmp_path):
    path = tmp_path / "flow.json"
    path.write_text(json.dumps({"name": "Morning check", "owner": "person:ana", "steps": [NOTE_STEP],
                                "trigger": {"type": "schedule", "cron": "0 9 * * *", "tz": "UTC"}}))
    return path


def test_the_cli_creates_runs_edits_and_restores_as_the_ui_does(api, flow_file):
    made = cli(api, "ana-test", "create", "--file", str(flow_file))
    aid = made["id"]
    assert made["can_edit"] and cli(api, "ana-test", "show", aid)["version"] == 1
    assert cli(api, "ana-test", "run", aid, "--input", '{"task": {"title": "x"}}')["status"] == "ok"
    assert len(cli(api, "ana-test", "runs", aid, "--limit", "1")["runs"]) == 1
    flow_file.write_text(json.dumps({"steps": []}))
    assert cli(api, "ana-test", "update", aid, "--file", str(flow_file), "--disable")["version"] == 2
    assert cli(api, "ana-test", "restore", aid, "1")["version"] == 3
    assert [a["id"] for a in cli(api, "ana-test", "list")["automations"] if not a["builtin"]] == [aid]
    cli(api, "ana-test", "delete", aid)
    assert [a for a in cli(api, "ana-test", "list")["automations"] if not a["builtin"]] == []


def test_a_bot_without_rights_proposes_through_the_tool(api, flow_file):
    aid = cli(api, "ana-test", "create", "--file", str(flow_file))["id"]
    proposed = hubtools.BY_NAME["hub_automation_update"]["fn"](
        Client(api, as_bot(api, "finance")), {"id": aid, "steps": [], "summary": "Drop the wake"})
    assert proposed["pending"] == 2
    assert [v["n"] for v in cli(api, "ana-test", "versions", aid, "--pending")["versions"]] == [2]
    assert cli(api, "ana-test", "show", aid)["version"] == 1


def test_the_inbox_send_switch_from_the_cli(api):
    assert cli(api, "ana-test", "inbox", "ana@acme.example") == {"inbox": "ana@acme.example", "send": True}
    assert cli(api, "ana-test", "inbox", "slack:#general", "--send", "off")["send"] is False
    with pytest.raises(remotecli.APIError):
        cli(api, "ben-test", "inbox", "ana@acme.example", "--send", "off")


def test_botops_acts_with_the_askers_rights(api, botops):
    """BotOps over MCP: the owner's ask lets it save; a member's ask is refused what she may not change."""
    from backend.tests.test_mcp import call as mcp

    flow = {"name": "Ops", "owner": "bot:ops", "trigger": {"type": "event", "name": "x.y"}, "steps": []}
    attempt = turn(api, botops, person="ana-test", text="Make ops an automation")
    err, made = mcp(api, "hub_automation_create", flow, attempt["token"])
    assert not err and made["can_edit"], made
    err, listed = mcp(api, "hub_automation_list", {}, attempt["token"])
    assert not err and [a["id"] for a in listed["automations"] if not a["builtin"]] == [made["id"]]
    finish(api, botops, attempt)
    member = turn(api, botops, person="cara-test", text="Pause the ops automation")
    err, refused = mcp(api, "hub_automation_update", {"id": made["id"], "enabled": False}, member["token"])
    assert err, refused


def test_the_cli_discards_a_proposal_and_lists_the_question_sets_and_the_guide(api, flow_file):
    aid = cli(api, "ana-test", "create", "--file", str(flow_file))["id"]
    hubtools.BY_NAME["hub_automation_update"]["fn"](
        Client(api, as_bot(api, "finance")), {"id": aid, "steps": [], "summary": "Drop the wake"})
    assert [v["n"] for v in cli(api, "ana-test", "versions", aid, "--pending")["versions"]] == [2]
    with pytest.raises(remotecli.APIError):
        cli(api, "ana-test", "discard", aid, "1")            # the current version is not a proposal
    cli(api, "ana-test", "discard", aid, "2")
    assert cli(api, "ana-test", "versions", aid, "--pending")["versions"] == []
    assert cli(api, "ana-test", "show", aid)["version"] == 1
    sets = cli(api, "ana-test", "question-sets")["sets"]
    assert sets and all("@" in s["id"] for s in sets)
    guide = cli(api, "ana-test", "guide")["text"]
    assert "hub automation create --file -" in guide and "`send_reply`" in guide and "{{" not in guide


def test_botops_sets_an_email_inboxs_send_switch_for_its_owner_only(api, botops):
    from backend.tests.test_mcp import call as mcp

    ana = turn(api, botops, person="ana-test", text="Turn off replies from my inbox")
    err, out = mcp(api, "hub_inbox_send", {"inbox": "ana@acme.example", "send": False}, ana["token"])
    assert not err and out["send"] is False, out
    finish(api, botops, ana)
    cara = turn(api, botops, person="cara-test", text="Turn on Ana's replies")
    err, refused = mcp(api, "hub_inbox_send", {"inbox": "ana@acme.example", "send": True}, cara["token"])
    assert err, refused
    finish(api, botops, cara)

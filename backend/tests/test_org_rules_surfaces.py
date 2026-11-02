"""Org rules through the hub CLI and the MCP door (clients/hubcli.py, clients/remotecli.py, clients/hubtools.py): the same
tools and rights as the API. The org is the one in test_org_rules.py."""
import json
from datetime import timedelta
from pathlib import Path

import pytest

from backend import rule_due as D
from backend.tests.test_api import api, headers, post  # noqa: F401  (the api fixture)
from backend.tests.test_assistant import assistant_turn
from backend.tests.test_automation_cli import Client
from backend.tests.test_member_bots import botops, turn as botops_turn  # noqa: F401  (the botops fixture)
from backend.tests.test_org_rules import org, rule as post_rule
from clients import hubcli, hubtools, remotecli
from clients.tico import APIError

ROOT = Path(__file__).resolve().parents[2]


def cli(api, token, *argv):
    args = hubcli.parser().parse_args(["rules", *argv])
    return remotecli.via_tool(Client(api, token), remotecli._rules_args(args))


def mcp(api, name, arguments=None, token="ana-test"):
    r = api.post("/api/v2/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                      "params": {"name": name, "arguments": arguments or {}}}, headers=headers(token))
    assert r.status_code == 200, r.text
    result = r.json()["result"]
    payload = result.get("structuredContent")
    return result["isError"], payload if payload is not None else json.loads(result["content"][0]["text"])


def hide_quote(api, rule_id):
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE org_rules SET cite=? WHERE id=?",
                  (json.dumps({"file_id": "no-such-file", "quote": "SECRET-QUOTE-MARKER"}), rule_id))


def test_the_cli_adds_reads_and_lists_a_rule_the_same_way_the_mcp_does(api):
    org(api)
    added = cli(api, "ana-test", "add", "--kind", "internal", "--text", "Ops answers within a day.",
                "--who", "ops", "--with", "cara")
    assert added["status"] == "active" and added["who"]["bots"][0]["id"] == "ops"
    assert added["with"]["people"][0]["id"] == "cara"
    listed = cli(api, "ana-test", "list", "--bot", "ops")
    assert [r["id"] for r in listed["rules"]] == [added["id"]]
    _, read = mcp(api, "hub_rules_get", {"id": added["id"]})
    assert read["text"] == "Ops answers within a day." and read["version"] == 1


def test_a_quote_stays_hidden_on_the_cli_and_the_mcp_when_its_file_is_not_readable(api):
    org(api)
    source = post(api, "rule-sources", {"name": "Acme Tax Office", "kind": "government"})["source"]["id"]
    added = cli(api, "ana-test", "add", "--kind", "external", "--text", "Keep receipts for seven years.",
                "--who", "finance", "--from", source)
    hide_quote(api, added["id"])
    for listing in (cli(api, "cara-test", "list"), cli(api, "cara-test", "list", "--mine", "--bot", "finance")):
        assert "SECRET-QUOTE-MARKER" not in json.dumps(listing) and "hidden" in json.dumps(listing)
    _, read = mcp(api, "hub_rules_get", {"id": added["id"]}, token="cara-test")
    assert "SECRET-QUOTE-MARKER" not in json.dumps(read) and read["cite"] == {"hidden": True}


def test_a_bot_suggests_over_the_mcp_and_cannot_accept_it(api):
    org(api)
    isError, suggested = mcp(api, "hub_rules_add", {"kind": "internal", "text": "Ops answers within a day.",
                                                     "who": {"bots": ["ops"]}}, token="ops-test")
    assert not isError and suggested["status"] == "suggested"
    isError, _ = mcp(api, "hub_rules_accept", {"id": suggested["id"], "version": 1}, token="ops-test")
    assert isError
    _, waiting = mcp(api, "hub_rules_suggested", {}, token="ana-test")
    assert [r["id"] for r in waiting["rules"]] == [suggested["id"]]


def test_mine_on_the_mcp_is_the_bots_rules_and_settings_as_read_only_sentences(api):
    org(api)
    cli(api, "ana-test", "add", "--kind", "internal", "--text", "Ops answers within a day.", "--who", "ops")
    _, mine = mcp(api, "hub_rules_list", {"mine": True}, token="ops-test")
    assert [r["text"] for r in mine["rules"]] == ["Ops answers within a day."]
    assert mine["settings"] and all(s["text"] and s["source"] for s in mine["settings"])


def test_the_cli_manages_due_dates_and_marks_one_satisfied(api):
    org(api)
    rule = cli(api, "ana-test", "add", "--kind", "internal", "--text", "Ops answers within a day.", "--who", "ops")
    start = (D.local_today("America/Los_Angeles") - timedelta(days=1)).isoformat()
    due = cli(api, "ana-test", "due", "add", rule["id"], "--first", start, "--repeats", "once", "--owner", "ops")
    listed = cli(api, "ana-test", "due", "list")
    assert [d["id"] for d in listed["due"]] == [due["id"]] and listed["due"][0]["status"] == "overdue"
    marked = cli(api, "ana-test", "due", "satisfied", due["id"])
    assert marked["satisfied"] == start


def test_the_cli_turns_a_due_dates_routine_on_and_off(api):
    org(api)
    rule = cli(api, "ana-test", "add", "--kind", "internal", "--text", "Ops files the Acme return.", "--who", "ops")
    due = cli(api, "ana-test", "due", "add", rule["id"], "--first", "2030-03-01", "--repeats", "yearly", "--owner", "ops",
              "--task-days", "21")
    assert due["routine_id"] and due["task_days"] == 21
    assert cli(api, "ana-test", "due", "edit", due["id"], "--task-days", "7")["task_days"] == 7
    off = cli(api, "ana-test", "due", "edit", due["id"], "--no-routine")
    assert off["routine_id"] is None and off["task_days"] is None


def test_a_retired_rule_is_no_longer_in_force_and_its_due_dates_come_back(api):
    org(api)
    rule = cli(api, "ana-test", "add", "--kind", "internal", "--text", "Ops answers within a day.", "--who", "ops")
    cli(api, "ana-test", "due", "add", rule["id"], "--first", "2026-06-01", "--repeats", "yearly", "--owner", "ops")
    retired = cli(api, "ana-test", "retire", rule["id"], "--version", "1")
    assert retired["rule"]["status"] == "retired" and len(retired["due"]) == 1
    assert cli(api, "ana-test", "list")["rules"] == []


def test_the_policy_line_is_the_fixed_one():
    text = (ROOT / "policies" / "shared-rules.md").read_text()
    # `hub rules check` exists from P1b on (backend/org_rule_check.py), so the line names it.
    assert "- Rules apply to you. Read them: `hub rules list --mine`. Check a draft: `hub rules check`.\n" in text


class _Turn:
    """The Assistant's client in its run, as test_assistant's Turn calls the API: `on_behalf_of` cites its message."""

    def __init__(self, api, token):
        self.api, self.token = api, token

    def call(self, method, path, body=None, key=None, query=None, delegate=False):
        extra = {"X-Tico-On-Behalf-Of": delegate if isinstance(delegate, str) else "turn"} if delegate else {}
        r = self.api.request(method, "/api/v2/" + path, json=body, params=query,
                             headers={**headers(self.token), **extra})
        if r.status_code >= 400:
            raise APIError(r.json()["error"]["code"], r.json()["error"]["detail"], status=r.status_code)
        return r.json()

    def get(self, path, **query):
        return self.call("GET", path, query=query)

    def post(self, path, body=None, key=None):
        return self.call("POST", path, body or {}, key)


def _add_via_assistant(api, turn, args):
    return hubtools.BY_NAME["hub_rules_add"]["fn"](turn, {**args, "on_behalf_of": "turn"})


def test_the_assistant_adds_a_rule_its_owner_may_make_and_refuses_an_uncited_one(api):
    org(api)
    _, attempt = assistant_turn(api, "ana-test", "Add a rule: everyone logs time.")
    turn = _Turn(api, attempt["token"])
    assert "hub_rules_add" in {t["name"] for t in hubtools.listing(kind="assistant")}
    everyone = _add_via_assistant(api, turn, {"kind": "internal", "text": "Everyone logs time.",
                                              "who": {"everyone": True}})
    assert everyone["status"] == "active"
    group = _add_via_assistant(api, turn, {"kind": "internal", "text": "Support logs refunds.",
                                           "who": {"groups": ["support"]}})
    assert group["status"] == "active"
    with pytest.raises(APIError):
        hubtools.BY_NAME["hub_rules_add"]["fn"](turn, {"kind": "internal", "text": "Uncited add.",
                                                       "who": {"groups": ["support"]}})


def test_the_assistant_refuses_an_everyone_rule_for_a_plain_member_and_suggests_a_group_rule(api):
    org(api)
    _, attempt = assistant_turn(api, "cara-test", "Add a rule: everyone has a badge.")
    turn = _Turn(api, attempt["token"])
    with pytest.raises(APIError) as refused:
        _add_via_assistant(api, turn, {"kind": "internal", "text": "Everyone has a badge.", "who": {"everyone": True}})
    assert refused.value.status == 403
    suggested = _add_via_assistant(api, turn, {"kind": "internal", "text": "Support keeps a refund log.",
                                               "who": {"groups": ["support"]}})
    assert suggested["status"] == "suggested"


def test_botops_mine_is_its_own_rules_and_settings_not_its_requesters(api, botops):
    org(api)
    post_rule(api, {"kind": "internal", "text": "BotOps answers in its own channel.", "who": {"bots": ["botops"]}})
    post_rule(api, {"kind": "internal", "text": "Cara's own rule.", "who": {"people": ["cara"]}})
    attempt = botops_turn(api, botops, person="cara-test", text="What rules apply to me?")
    err, result = mcp(api, "hub_rules_list", {"mine": True}, token=attempt["token"])
    assert not err, result
    assert [item["text"] for item in result["rules"]] == ["BotOps answers in its own channel."]
    assert result["settings"] and all("text" in s for s in result["settings"])


def test_rules_due_takes_the_next_window_and_list_is_the_same_view(api):
    org(api)
    rule = cli(api, "ana-test", "add", "--kind", "internal", "--text", "Ops answers within a day.", "--who", "ops")
    soon = (D.local_today("America/Los_Angeles") + timedelta(days=3)).isoformat()
    due = cli(api, "ana-test", "due", "add", rule["id"], "--first", soon, "--repeats", "once", "--owner", "ops")
    upcoming = cli(api, "ana-test", "due", "--next", "30d")
    assert [d["id"] for d in upcoming["due"]] == [due["id"]]
    assert cli(api, "ana-test", "due", "list", "--next", "30d")["due_at"] == upcoming["due"]

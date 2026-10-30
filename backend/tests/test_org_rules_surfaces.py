"""Org rules through the hub CLI and the MCP door (clients/hubcli.py, clients/remotecli.py, clients/hubtools.py): the same
tools and rights as the API. The org is the one in test_org_rules.py."""
import json
from datetime import timedelta
from pathlib import Path

from backend import rule_due as D
from backend.tests.test_api import api, headers, post  # noqa: F401  (the api fixture)
from backend.tests.test_automation_cli import Client
from backend.tests.test_org_rules import org
from clients import hubcli, remotecli

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
        c.execute("UPDATE org_rules SET cite_json=? WHERE id=?",
                  (json.dumps({"file_id": "no-such-file", "quote": "SECRET-QUOTE-MARKER"}), rule_id))


def test_the_cli_adds_reads_and_lists_a_rule_the_same_way_the_mcp_does(api):
    org(api)
    added = cli(api, "ana-test", "add", "--kind", "internal", "--text", "Ops answers within a day.",
                "--who", "ops", "--with", "cara")
    assert added["status"] == "active" and added["who"]["bots"][0]["id"] == "ops"
    assert added["with"]["people"][0]["id"] == "cara"
    listed = cli(api, "ana-test", "list", "--bot", "ops")
    assert [r["id"] for r in listed["rules"]] == [added["id"]]
    _, read = mcp(api, "hub_rules_read", {"id": added["id"]})
    assert read["text"] == "Ops answers within a day." and read["version"] == 1


def test_a_quote_stays_hidden_on_the_cli_and_the_mcp_when_its_file_is_not_readable(api):
    org(api)
    source = post(api, "rule-sources", {"name": "Acme Tax Office", "kind": "government"})["source"]["id"]
    added = cli(api, "ana-test", "add", "--kind", "external", "--text", "Keep receipts for seven years.",
                "--who", "finance", "--from", source)
    hide_quote(api, added["id"])
    for listing in (cli(api, "cara-test", "list"), cli(api, "cara-test", "list", "--mine", "--bot", "finance")):
        assert "SECRET-QUOTE-MARKER" not in json.dumps(listing) and "hidden" in json.dumps(listing)
    _, read = mcp(api, "hub_rules_read", {"id": added["id"]}, token="cara-test")
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


def test_a_retired_rule_is_no_longer_in_force_and_its_due_dates_come_back(api):
    org(api)
    rule = cli(api, "ana-test", "add", "--kind", "internal", "--text", "Ops answers within a day.", "--who", "ops")
    cli(api, "ana-test", "due", "add", rule["id"], "--first", "2026-06-01", "--repeats", "yearly", "--owner", "ops")
    retired = cli(api, "ana-test", "retire", rule["id"], "--version", "1")
    assert retired["rule"]["status"] == "retired" and len(retired["due"]) == 1
    assert cli(api, "ana-test", "list")["rules"] == []


def test_the_policy_line_is_the_fixed_one():
    text = (ROOT / "policies" / "shared-rules.md").read_text()
    assert "Rules apply to you. Read them: `hub rules list --mine`. Check a draft: `hub rules check`." in text

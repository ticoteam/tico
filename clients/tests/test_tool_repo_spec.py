"""The `tico tool`, `tico repo` and `tico github` commands built from specs (clients/commands/tools.py TOOL, REPO;
clients/commands/connections.py GITHUB, REPO): each sends and prints what the hand parser it replaced did
(clients/hubcli.py and clients/remotecli.py before these specs)."""

from clients.tests.helpers import cli_fixtures
from clients.tests.helpers.cli_fixtures import run_line

_ROW = {
    "service": "posthog",
    "kind": "api",
    "writes": "no",
    "summary": "product analytics",
    "query_count": 1,
    "learning_count": 0,
    "credentials": ["POSTHOG_KEY"],
    "access": "proxy",
}
_QUERY = {"id": "signups", "title": "Signups", "description": "new accounts", "sql": "select 1", "tags": ["growth"]}
_PAGE = {
    "service": "posthog",
    "title": "PostHog",
    "kind": "api",
    "writes": "no",
    "owner": "ops",
    "summary": "product analytics",
    "access": "proxy",
    "credentials": ["POSTHOG_KEY"],
    "declared_as": "- service: posthog\n",
    "body": "Read events.\n",
    "queries": [_QUERY, {"id": "churn", "title": "Churn", "sql": "select 2"}],
    "learnings": [{"created_at": "2026-11-01T00:00:00Z", "actor": "ana", "text": "use EU host"}],
}


def _answers(monkeypatch):
    monkeypatch.setitem(cli_fixtures.ANSWERS, "GET tools", {"integrations": [_ROW]})
    monkeypatch.setitem(cli_fixtures.ANSWERS, "GET tools/posthog", _PAGE)


def test_tool_list_prints_the_teams_rows_without_running_the_tool(monkeypatch):
    _answers(monkeypatch)
    result = run_line(["tool", "list"], monkeypatch)
    assert (result["exit"], result["tools"], result["http"]) == (0, [], [["GET", "tools", None]])
    assert result["stdout"].startswith("posthog ") and "access: proxy" in result["stdout"]
    assert '"service": "posthog"' in run_line(["tool", "list", "--json"], monkeypatch)["stdout"]


def test_tool_list_of_a_bot_runs_the_tool_and_prints_json(monkeypatch):
    result = run_line(["tool", "list", "--bot", "ops"], monkeypatch)
    assert result["tools"] == [["tico_tool_list", {"bot": "ops", "operation_id": None}]]
    assert result["http"][-1] == ["GET", "bots/ops/tools", None]
    assert result["stdout"] == '{\n  "ok": true\n}\n'


def test_tool_show_prints_the_page(monkeypatch):
    _answers(monkeypatch)
    out = run_line(["tool", "show", "posthog"], monkeypatch)["stdout"]
    assert out.startswith("# PostHog (posthog, api, writes: no, owner: ops)")
    assert "  2026-11-01  ana: use EU host" in out and "## Queries (2;" in out


def test_tool_query_search_searches_the_page_or_prints_one_query(monkeypatch):
    _answers(monkeypatch)
    found = run_line(["tool", "query-search", "posthog", "accounts"], monkeypatch)
    assert found["tools"] == [] and found["http"] == [["GET", "tools/posthog", None]]
    assert found["stdout"].startswith("signups") and "churn" not in found["stdout"]
    one = run_line(["tool", "query-search", "posthog", "--id", "signups"], monkeypatch)["stdout"]
    assert one.startswith("signups: Signups") and "select 1" in one
    missing = run_line(["tool", "query-search", "posthog", "--id", "nope"], monkeypatch)
    assert missing["exit"] != 0 and "No query nope under posthog" in missing["stdout"]
    none = run_line(["tool", "query-search", "posthog", "zzz"], monkeypatch)["stdout"]
    assert none == "no matching queries\n"


def test_tool_report_sends_the_parsed_list(monkeypatch):
    result = run_line(["tool", "report", '[{"name": "gh"}]'], monkeypatch)
    assert result["tools"] == [["tico_tool_report", {"operation_id": None, "tools": [{"name": "gh"}]}]]


def test_tool_add_takes_the_hand_parsers_flags(monkeypatch):
    argv = ["tool", "add", "ops", "posthog", "--can", "read", "--scope", "project=1", "--header", "X: ${K}"]
    name, sent = run_line(argv, monkeypatch)["tools"][0]
    assert name == "tico_tool_add"
    assert sent == {
        "bot": "ops",
        "can": "read",
        "dry_run": False,
        "headers": ["X: ${K}"],
        "operation_id": None,
        "scope": ["project=1"],
        "service": "posthog",
    }
    assert run_line(["tool", "update", "t1"], monkeypatch)["exit"] != 0  # --bot is required


def test_repo_tick_and_untick_send_enabled_and_never_an_operation_id(monkeypatch):
    result = run_line(["repo", "tick", "acme/web"], monkeypatch)
    assert result["tools"] == [["tico_repo_update", {"enabled": True, "full_name": "acme/web"}]]
    assert result["http"][-1] == ["PUT", "repositories/acme/web", {"enabled": True}]
    from clients.commands import tools

    assert tools._no_key({"full_name": "a/b", "enabled": False, "operation_id": "op-1"}) == {
        "full_name": "a/b",
        "enabled": False,
    }


def test_connection_commands_send_an_operation_id_only_when_one_is_set(monkeypatch):
    from clients.commands import connections

    assert connections._key_if_set({"mode": "own", "operation_id": None}) == {"mode": "own"}
    assert connections._key_if_set({"operation_id": "op-1"}) == {"operation_id": "op-1"}
    result = run_line(["repo", "grant", "acme/web", "ops", "read", "--no-actions", "--edit"], monkeypatch)
    assert result["tools"][0] == [
        "tico_repo_grant",
        {"access": "read", "bot": "ops", "edit_workflows": True, "repository": "acme/web", "run_workflows": False},
    ]
    both = run_line(["repo", "grant", "acme/web", "ops", "--actions", "--no-actions"], monkeypatch)
    assert both["exit"] != 0 and both["http"] == [] and "not allowed with" in both["stderr"]

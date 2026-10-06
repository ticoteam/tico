"""The Tools row on a bot's page: the route's shape, that no secret can reach it, that a runner from
before the report still gets a row, and that a private bot's tools stay private (docs/creating-bots.md)."""

import json

from backend.store import H
from backend.tests.test_api import api, assign, get, headers, ready, restrict, runner  # noqa: F401  (fixtures)

RUNTIMES = {"codex": {"installed": True, "authenticated": "ready"}}
TOOLS = [
    {"service": "posthog", "identity": "PostHog project 12345 (US), personal key", "can": ["read"],
     "scope": {"project": "12345"}, "env": "POSTHOG_KEY", "note": "funnels only", "credential": "missing"},
    {"service": "slack", "identity": "Acme workspace", "can": ["read", "post"],
     "scope": {"channels": ["#ops", "#launch"]}, "env": "SLACK_TOKEN", "credential": "present"},
    {"service": "meeting-notes", "can": ["use"], "credential": "not-declared"},
]


def configure(api, bot="ops"):
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE bot_config SET config_json=?,repo=? WHERE bot=?",
                  (json.dumps({"name": bot, "runtime": "codex", "model": "gpt-6-luna", "reasoning_effort": "high"}),
                   "acme-co/emp-" + bot, bot))


def report(api, machine, bot, tools):
    row = {"ready": True, "runtime": "codex", "model": "gpt-6-luna", "repository_present": True,
           "configuration_valid": True, "problems": [], **({"tools": tools} if tools is not None else {})}
    return api.post("/api/v2/runners/heartbeat", headers=headers(machine["token"]), json={
        "version": "test", "platform": "test",
        "readiness": {"schema_version": 1, "runtimes": RUNTIMES, "bots": {bot: row}}})


def tools_of(api, bot="ops", who="ana-test"):
    return get(api, f"bots/{bot}/tools", token=who)


def test_the_row_lists_the_model_the_repository_and_each_declared_tool(api):
    configure(api)
    machine = runner(api)
    assign(api, machine, "ops")
    assert report(api, machine, "ops", TOOLS).status_code == 200
    page = tools_of(api)
    assert set(page) == {"bot", "tools", "computer", "online", "reported_at"}
    assert page["computer"] == "Test Mac" and page["online"] is True
    by_id = {t["id"]: t for t in page["tools"]}
    assert [t["id"] for t in page["tools"]] == ["model", "repo", "posthog", "slack", "meeting-notes"]
    assert all({"id", "service", "name", "logo_key", "identity", "can", "scope", "note", "status"} <= set(t)
               for t in page["tools"])
    model = by_id["model"]
    assert (model["name"], model["identity"], model["logo_key"], model["status"]) == ("Codex", "openai/gpt-6-luna", "openai", "ready")
    assert model["scope"] == {"effort": "high"}
    assert by_id["repo"]["identity"] == "acme-co/emp-ops" and by_id["repo"]["url"] == "https://github.com/acme-co/emp-ops"
    assert by_id["repo"]["status"] == "ready"
    posthog = by_id["posthog"]
    assert (posthog["name"], posthog["logo_key"], posthog["status"]) == ("PostHog", "posthog", "problem")
    assert posthog["problem"] == "Credential missing on Test Mac" and posthog["env"] == "POSTHOG_KEY"
    assert posthog["scope"] == {"project": "12345"} and posthog["note"] == "funnels only" and posthog["can"] == ["read"]
    assert (by_id["slack"]["status"], by_id["slack"]["scope"]) == ("ready", {"channels": ["#ops", "#launch"]})
    assert by_id["meeting-notes"]["logo_key"] is None and by_id["meeting-notes"]["status"] == "unknown"
    assert "problem" not in by_id["slack"]


def test_no_value_can_reach_the_row(api):
    configure(api)
    machine = runner(api)
    assign(api, machine, "ops")
    # A runner reports names and verbs; a field that could carry a value is refused, not stored.
    leaky = [{**TOOLS[1], "value": "xoxb-not-a-real-token"}]
    assert report(api, machine, "ops", leaky).status_code == 422
    assert report(api, machine, "ops", [{**TOOLS[1], "env": {"SLACK_TOKEN": "xoxb-not-a-real-token"}}]).status_code == 422
    assert report(api, machine, "ops", TOOLS).status_code == 200
    with api.app.state.store.read() as c:
        stored = c.execute("SELECT readiness_json FROM runners").fetchone()[0]
    for text in (api.get("/api/v2/bots/ops/tools", headers=headers()).text, stored,
                 api.get("/api/v2/operations", headers=headers()).text, api.get("/api/v2/bots", headers=headers()).text):
        assert "xoxb-not-a-real-token" not in text
    # The heartbeat copy other pages read does not carry the list at all.
    assert "tools" not in json.loads(api.get("/api/v2/operations", headers=headers()).text)["machines"][0]["readiness"]["bots"]["ops"]


def test_a_runner_from_before_the_report_yields_the_model_and_repository_only(api):
    configure(api)
    machine = runner(api)
    assign(api, machine, "ops")
    ready(api, machine, ["ops"])                            # the oldest heartbeat: a map of booleans
    page = tools_of(api)
    assert [t["id"] for t in page["tools"]] == ["model", "repo"]
    assert page["tools"][0]["status"] == "unknown"          # no runtime report to say
    assert report(api, machine, "ops", None).status_code == 200      # a structured report with no `tools` key
    assert [t["id"] for t in tools_of(api)["tools"]] == ["model", "repo"]
    assert tools_of(api)["tools"][0]["status"] == "ready"
    # A bot with no computer at all still answers.
    bare = tools_of(api, "coo")
    assert bare["computer"] is None and bare["online"] is False and bare["tools"][0]["id"] == "model"


def test_a_private_bots_tools_stay_with_the_people_who_can_see_it(api):
    machine = runner(api)
    assign(api, machine, "inbox")
    assert report(api, machine, "inbox", TOOLS).status_code == 200
    assert api.get("/api/v2/bots/inbox/tools", headers=headers("cara-test")).status_code == 404     # cannot even see it
    with api.app.state.store.transaction() as c:
        restrict(c, "inbox", see={"everyone": True}, read=dict(people=["ana"]), write=dict(people=["ana"]))
    assert api.get("/api/v2/bots/inbox/tools", headers=headers("cara-test")).status_code == 403     # sees it, may not read it
    assert len(tools_of(api, "inbox")["tools"]) >= 3
    assert len(tools_of(api, "inbox", "ben-test")["tools"]) >= 3                                     # an Admin has full access
    assert api.get("/api/v2/bots/nobody/tools", headers=headers("ana-test")).status_code == 404


# ----------------------------------------------------------------------------- registering
ENTRY = {"service": "PostHog", "identity": "PostHog project 340585 (US), personal key", "can": ["read"],
         "scope": {"project": "340585"}, "env": "POSTHOG_KEY", "note": "funnels only"}


def botops(api, state="active"):
    with api.app.state.store.transaction() as c:
        H.sync_registry(c, {"botops": {"name": "botops", "status": state}}, None)


def register(api, bot="ops", who="ana-test", entry=None, expected=200):
    r = api.post(f"/api/v2/bots/{bot}/tools", json=entry or ENTRY, headers=headers(who))
    assert r.status_code == expected, r.text
    return r.json()


def botops_tasks(api):
    return api.get("/api/v2/tasks", params={"owner": "botops"}, headers=headers()).json()["tasks"]


def test_a_manager_registers_a_tool_and_botops_gets_the_exact_entry(api):
    botops(api)
    register(api, who="cara-test", expected=403)                     # a member who does not manage ops
    checked = register(api, entry={**ENTRY, "can": ["qa-custom-verb"], "dry_run": True})
    assert checked["ok"] and "qa-custom-verb" in checked["yaml"] and botops_tasks(api) == []
    made = register(api, entry={**ENTRY, "title_prefix": "QA "})
    assert made["tool"]["status"] == "pending" and made["tool"]["pending"] == "add"
    assert made["tool"]["service"] == "posthog" and made["tool"]["logo_key"] == "posthog"
    assert "Credentials or its chat card" in made["credentials"] and "grant it to this bot" in made["credentials"]
    task = botops_tasks(api)[0]
    assert task["id"] == made["task_id"] and task["title"] == "QA Add PostHog access to ops"
    body = api.get("/api/v2/tasks/" + task["id"], headers=headers()).json()["task"]["body"]
    assert made["yaml"] in body and "- service: posthog" in body and "env: POSTHOG_KEY" in body
    assert "can: [read]" in body and "project: '340585'" in body
    assert "bot.yaml" in body and "never commit it" in body
    assert "hub bot check ops" in body and "publishes its own repository" in body
    assert "commit and push" not in body and "scripts/preflight.sh" not in body
    # It is visible, pending, until the computer reports the entry.
    page = tools_of(api)
    assert [t["status"] for t in page["tools"] if t["id"].startswith("pending-")] == ["pending"]
    register(api, expected=409)                                       # asking twice does not open a second task
    machine = runner(api)
    assign(api, machine, "ops")
    listed = {**ENTRY, "service": "posthog", "can": ["read"], "credential": "missing", "scope": {"project": "340585"}}
    listed.pop("note")
    assert report(api, machine, "ops", [listed]).status_code == 200
    page = tools_of(api)
    row = next(t for t in page["tools"] if t["service"] == "posthog")
    assert (row["status"], row["problem"]) == ("problem", "Credential missing on Test Mac")
    assert not any(t["id"].startswith("pending-") for t in page["tools"])
    with api.app.state.store.read() as c:
        assert c.execute("SELECT state FROM bot_tool_requests").fetchone()[0] == "done"
    listed["credential"] = "present"
    assert report(api, machine, "ops", [listed]).status_code == 200
    assert next(t for t in tools_of(api)["tools"] if t["service"] == "posthog")["status"] == "ready"


def test_a_credential_value_is_refused_and_nothing_is_kept(api):
    botops(api)
    secrets = [{"env": "sk-proj-1234567890abcdef"}, {"identity": "postgresql://reader:hunter2@db.internal/app"},
               {"note": "use Zx9Qw3Er7Ty1Ui5Op2As6Df0Gh4Jk8Lm"}]
    for extra in secrets:
        answer = register(api, entry={**ENTRY, **extra}, expected=422)
        assert answer["error"]["code"] in ("secret", "entry"), extra
        assert "never" in answer["error"]["detail"].lower() or "not accepted" in answer["error"]["detail"], answer
    for bad in ({"env": "HUB_TOKEN"}, {"scope": {"token": "x"}}):
        register(api, entry={**ENTRY, **bad}, expected=422)
    assert botops_tasks(api) == []
    with api.app.state.store.read() as c:
        assert c.execute("SELECT count(*) FROM bot_tool_requests").fetchone()[0] == 0
    assert register(api, entry={**ENTRY, "identity": "Acme workspace, marketing@acme.example", "env": "SLACK_TOKEN"})["task_id"]


MCP_JIRA = {"service": "jira", "can": ["read", "write"], "env": "JIRA_API_TOKEN", "note": "issues only",
            "mcp": {"url": "https://mcp.atlassian.com/v2/mcp", "transport": "http",
                    "headers": {"Authorization": "Bearer ${JIRA_API_TOKEN}"}}}


def test_an_mcp_tool_shows_its_host_and_the_runners_check_never_its_headers(api):
    configure(api)
    machine = runner(api)
    assign(api, machine, "ops")
    for state, status, problem in (("reachable", "ready", None), ("auth_failed", "problem", "refused the credential")):
        row = {**MCP_JIRA, "credential": "present", "mcp": {**MCP_JIRA["mcp"], "status": state}}
        assert report(api, machine, "ops", [row]).status_code == 200
        tool = {t["id"]: t for t in tools_of(api)["tools"]}["jira"]
        assert (tool["kind"], tool["status"]) == ("mcp", status)
        assert tool["mcp"] == {"host": "mcp.atlassian.com", "transport": "http", "status": state}
        assert problem is None or problem in tool["problem"]
        assert "Bearer" not in json.dumps(tool)


def test_tools_render_when_bot_has_no_config_row(api):
    with api.app.state.store.transaction() as c:
        c.execute("DELETE FROM bot_config WHERE bot='ops'")
    page = tools_of(api)
    assert page['bot'] == 'ops'
    assert isinstance(page['tools'], list)

"""The Tools row on a bot's page: the route's shape, that no secret can reach it, that a runner from
before the report still gets a row, and that a private bot's tools stay private (docs/creating-bots.md)."""

import json

from backend.store import H
from backend.tests.test_api import api, assign, get, headers, post, ready, restrict, runner  # noqa: F401  (fixtures)
from backend.tests.test_mcp import call as mcp_call

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
    secrets = [
        {"env": "phx_aBcDeFgHiJkLmNoPqRsTuVwXyZ012345"}, {"env": "sk-proj-1234567890abcdef"},
        {"identity": "token ghp_abcdefghijklmnopqrstuvwxyz0123456789 for the bot"},
        {"note": "key is sk-live-abcdefghijklmnop"}, {"scope": {"project": "xoxb-1234-5678-abcdefghijkl"}},
        {"identity": "postgresql://reader:hunter2@db.internal/app"}, {"note": "use Zx9Qw3Er7Ty1Ui5Op2As6Df0Gh4Jk8Lm"},
        {"note": "-----BEGIN PRIVATE KEY----- MIIE"}, {"env": "a-real-looking-value-here"},
    ]
    for extra in secrets:
        answer = register(api, entry={**ENTRY, **extra}, expected=422)
        assert answer["error"]["code"] in ("secret", "entry"), extra
        assert "never" in answer["error"]["detail"].lower() or "not accepted" in answer["error"]["detail"], answer
    for bad in ({"env": "PATH"}, {"env": "HUB_TOKEN"}, {"service": "Not A Service!"}, {"can": []}, {"can": ["Read It"]},
                {"scope": {"token": "x"}}, {"scope": "database=warehouse"}):
        register(api, entry={**ENTRY, **bad}, expected=422)
    assert botops_tasks(api) == []
    with api.app.state.store.read() as c:
        assert c.execute("SELECT count(*) FROM bot_tool_requests").fetchone()[0] == 0
    assert register(api, entry={**ENTRY, "identity": "Acme workspace, marketing@acme.example", "env": "SLACK_TOKEN"})["task_id"]


def test_registering_needs_botops_to_be_running(api):
    botops(api, "planned")
    assert register(api, expected=409)["error"]["code"] == "botops"
    assert register(api, "nobody", expected=404)["error"]["code"] == "not_found"


def test_removing_a_tool_is_a_botops_task_and_a_pending_request_can_be_withdrawn(api):
    botops(api)
    machine = runner(api)
    assign(api, machine, "ops")
    assert report(api, machine, "ops", TOOLS).status_code == 200
    assert api.delete("/api/v2/bots/ops/tools/slack", headers=headers("cara-test")).status_code == 403
    assert api.delete("/api/v2/bots/ops/tools/model", headers=headers()).status_code == 422
    assert api.delete("/api/v2/bots/ops/tools/nothing", headers=headers()).status_code == 404
    removed = api.delete("/api/v2/bots/ops/tools/slack", headers=headers())
    assert removed.status_code == 200 and removed.json()["removal"] is True
    task = botops_tasks(api)[0]
    assert task["title"] == "Remove Slack access from ops"
    body = api.get("/api/v2/tasks/" + task["id"], headers=headers()).json()["task"]["body"]
    assert "- service: slack" in body and "Acme workspace" in body and "SLACK_TOKEN" in body
    slack = next(t for t in tools_of(api)["tools"] if t["id"] == "slack")
    assert slack["pending"] == "remove" and slack["status"] == "ready" and slack["task_id"] == task["id"]
    assert api.delete("/api/v2/bots/ops/tools/slack", headers=headers()).status_code == 409
    assert report(api, machine, "ops", [t for t in TOOLS if t["service"] != "slack"]).status_code == 200
    assert not any(t["service"] == "slack" for t in tools_of(api)["tools"])
    with api.app.state.store.read() as c:
        assert c.execute("SELECT state FROM bot_tool_requests WHERE kind='remove'").fetchone()[0] == "done"
    # A request that has not been done yet is withdrawn by deleting its pending id.
    pending = register(api, entry={**ENTRY, "service": "stripe", "env": "STRIPE_KEY"})["tool"]["id"]
    answer = api.post(f"/api/v2/bots/ops/tools/{pending}/delete", json={}, headers=headers())
    assert answer.status_code == 200 and answer.json()["cancelled"] is True
    assert not any(t["id"] == pending for t in tools_of(api)["tools"])
    cancelled = get(api, "tasks/" + answer.json()["task_id"])["task"]
    assert cancelled["status"] == "closed" and "withdrawn" in cancelled["note"]


def test_changing_a_tool_is_one_task_and_nothing_is_removed(api):
    botops(api)
    machine = runner(api)
    assign(api, machine, "ops")
    assert report(api, machine, "ops", TOOLS).status_code == 200

    def change(body, who="ana-test", expected=200, tool="slack"):
        r = api.post(f"/api/v2/bots/ops/tools/{tool}/update", json=body, headers=headers(who))
        assert r.status_code == expected, r.text
        return r.json()

    change({"can": ["read", "post", "send"]}, who="cara-test", expected=403)        # a member who does not manage ops
    change({"can": ["read"]}, tool="model", expected=422)
    change({"can": ["read"]}, tool="nothing", expected=404)
    change({"can": ["read"]}, tool="pending-x", expected=422)
    assert change({}, expected=422)["error"]["code"] == "entry"
    assert change({"can": ["read", "post"]}, expected=409)["error"]["code"] == "unchanged"
    assert change({"can": ["Read It"]}, expected=422)
    assert change({"note": "key is sk-live-abcdefghijklmnop"}, expected=422)["error"]["code"] == "secret"
    assert botops_tasks(api) == []
    made = change({"can": ["read", "post", "send"], "scope": {"channels": "#ops", "workspace": "acme"}, "note": "send on Fridays"})
    assert made["update"] is True and made["tool"] == "slack"
    assert "can: [read, post, send]" in made["yaml"] and "workspace: acme" in made["yaml"] and "channels: " in made["yaml"]
    assert "identity: Acme workspace" in made["yaml"] and "env: SLACK_TOKEN" in made["yaml"]
    task = botops_tasks(api)[0]
    assert task["id"] == made["task_id"] and task["title"] == "Change Slack access on ops"
    body = api.get("/api/v2/tasks/" + task["id"], headers=headers()).json()["task"]["body"]
    assert made["yaml"] in body and "do not remove and re-add" in body and "lists it as changed" in body
    # Still the same tool, now marked as being changed; no removal and no second entry.
    slack = next(t for t in tools_of(api)["tools"] if t["id"] == "slack")
    assert slack["pending"] == "update" and slack["task_id"] == task["id"] and slack["status"] == "ready"
    assert [t["service"] for t in tools_of(api)["tools"]].count("slack") == 1
    change({"can": ["read"]}, expected=409)                                          # one change at a time
    assert api.delete("/api/v2/bots/ops/tools/slack", headers=headers()).status_code == 409     # not a second task on top
    with api.app.state.store.read() as c:
        assert c.execute("SELECT count(*) FROM bot_tool_requests WHERE kind='remove'").fetchone()[0] == 0
        assert c.execute("SELECT state FROM bot_tool_requests WHERE kind='update'").fetchone()[0] == "pending"
    # The computer reports the old entry: still pending. The changed entry: done.
    assert report(api, machine, "ops", TOOLS).status_code == 200
    assert next(t for t in tools_of(api)["tools"] if t["id"] == "slack")["pending"] == "update"
    changed = {**TOOLS[1], "can": ["read", "post", "send"], "scope": {"channels": ["#ops"], "workspace": "acme"}, "note": "send on Fridays"}
    assert report(api, machine, "ops", [TOOLS[0], changed, TOOLS[2]]).status_code == 200
    slack = next(t for t in tools_of(api)["tools"] if t["id"] == "slack")
    assert "pending" not in slack and slack["can"] == ["read", "post", "send"] and slack["scope"]["workspace"] == "acme"
    with api.app.state.store.read() as c:
        assert c.execute("SELECT state FROM bot_tool_requests WHERE kind='update'").fetchone()[0] == "done"


def test_a_tool_update_keeps_the_rest_and_takes_a_scope_key_and_the_note_off(api):
    botops(api)
    machine = runner(api)
    assign(api, machine, "ops")
    assert report(api, machine, "ops", TOOLS).status_code == 200
    r = api.post("/api/v2/bots/ops/tools/posthog/update", json={"scope": {"project": ""}, "note": ""}, headers=headers())
    assert r.status_code == 200, r.text
    yaml_text = r.json()["yaml"]
    assert "project: " not in yaml_text and "note" not in yaml_text and "can: [read]" in yaml_text and "env: POSTHOG_KEY" in yaml_text


def test_tool_update_is_an_mcp_tool_too(api):
    botops(api)
    machine = runner(api)
    assign(api, machine, "ops")
    assert report(api, machine, "ops", TOOLS).status_code == 200
    err, done = mcp_call(api, "hub_tool_update", {"bot": "ops", "id": "slack", "can": "read,post,send", "scope": ["workspace=acme"]})
    assert not err and done["update"] is True and "can: [read, post, send]" in done["yaml"]


def test_the_requester_filter_finds_what_one_actor_filed(api):
    botops(api)
    register(api)                                                                     # ana's request: a task for BotOps
    mine = mcp_call(api, "hub_task_list", {"requester": "me"})[1]["result"]
    assert [t["title"] for t in mine] == ["Add PostHog access to ops"] and mine[0]["requester"] == "human:ana"
    assert mcp_call(api, "hub_task_list", {"requester": "me"}, token="cara-test")[1]["result"] == []
    assert mcp_call(api, "hub_task_list", {"requester": "human:ana", "status": ["open"]})[1]["result"][0]["id"] == mine[0]["id"]
    assert api.get("/api/v2/tasks", params={"requester": "human:ana"}, headers=headers()).json()["tasks"][0]["id"] == mine[0]["id"]


def test_the_tools_are_mcp_tools_too(api):
    botops(api)
    err, listed = mcp_call(api, "hub_tool_list", {"bot": "ops"})
    assert not err and listed["tools"][0]["id"] == "model"
    err, added = mcp_call(api, "hub_tool_add", {"bot": "ops", "service": "posthog", "can": "read",
                                                  "scope": ["project=340585", "channels=#a,#b"], "env": "POSTHOG_KEY"})
    assert not err and added["tool"]["scope"] == {"project": "340585", "channels": ["#a", "#b"]}
    err, refused = mcp_call(api, "hub_tool_add", {"bot": "ops", "service": "stripe", "can": ["read"], "env": "sk_live_abcdefghijklmnop"})
    assert err and refused["error"] == "secret"
    err, gone = mcp_call(api, "hub_tool_remove", {"bot": "ops", "id": added["tool"]["id"]})
    assert not err and gone["cancelled"] is True


def test_a_google_key_the_computer_holds_is_present_and_a_missing_one_is_named(api):
    configure(api)
    machine = runner(api)
    assign(api, machine, "ops")
    gmail = {"service": "gmail", "identity": "ana@acme.example", "can": ["read"], "env": "GOOGLE_SA_KEY"}
    assert report(api, machine, "ops", [{**gmail, "credential": "present", "held": True}]).status_code == 200
    # The computer has the key, but the server has not named ops as anyone's message bot: no token, so not "ready".
    tool = {t["id"]: t for t in tools_of(api)["tools"]}["gmail"]
    assert tool["status"] == "problem" and "nobody's message bot" in tool["problem"] and '"inbox_bot": "ops"' in tool["problem"]
    issue = [i for i in get(api, "fleet/check", token="ana-test")["issues"] if i["kind"] == "missing_credential"]
    assert issue and "nobody's message bot" in issue[0]["text"] and "has no Google service-account key" not in issue[0]["text"]
    post(api, "access/people/ana", {"inbox_bot": "ops", "mailbox": "ana@acme.example"}, token="ana-test")
    tool = {t["id"]: t for t in tools_of(api)["tools"]}["gmail"]
    assert tool["status"] == "ready" and "problem" not in tool
    assert tool["detail"] == "GOOGLE_SA_KEY is present (held by the computer); a run gets a short-lived token, never the key"
    assert not [i for i in get(api, "fleet/check", token="ana-test")["issues"] if i["kind"] == "missing_credential"]
    assert report(api, machine, "ops", [{**gmail, "credential": "missing"}]).status_code == 200
    issue = [i for i in get(api, "fleet/check", token="ana-test")["issues"] if i["kind"] == "missing_credential"]
    assert issue and "has no Google service-account key" in issue[0]["text"] and "GOOGLE_SA_KEY is not set" not in issue[0]["text"]
    tool = {t["id"]: t for t in tools_of(api)["tools"]}["gmail"]
    assert tool["status"] == "problem" and tool["problem"] == "Test Mac has no Google service-account key (google-sa.json in its state directory)"


MCP_JIRA = {"service": "jira", "can": ["read", "write"], "env": "JIRA_API_TOKEN", "note": "issues only",
            "mcp": {"url": "https://mcp.atlassian.com/v2/mcp", "transport": "http",
                    "headers": {"Authorization": "Bearer ${JIRA_API_TOKEN}"}}}


def test_an_mcp_tool_shows_its_host_and_the_runners_check_never_its_headers(api):
    configure(api)
    machine = runner(api)
    assign(api, machine, "ops")
    for state, status, problem in (("reachable", "ready", None), ("unchecked", "ready", None),
                                   ("auth_failed", "problem", "refused the credential"), ("unreachable", "problem", "did not answer")):
        row = {**MCP_JIRA, "credential": "present", "mcp": {**MCP_JIRA["mcp"], "status": state}}
        assert report(api, machine, "ops", [row]).status_code == 200
        tool = {t["id"]: t for t in tools_of(api)["tools"]}["jira"]
        assert (tool["kind"], tool["status"]) == ("mcp", status)
        assert tool["mcp"] == {"host": "mcp.atlassian.com", "transport": "http", "status": state}
        assert problem is None or problem in tool["problem"]
        assert "Bearer" not in json.dumps(tool)


def test_registering_an_mcp_tool_checks_the_block_and_hands_botops_the_exact_yaml(api):
    botops(api)
    made = register(api, entry=MCP_JIRA)
    assert made["tool"]["kind"] == "mcp" and made["tool"]["mcp"]["host"] == "mcp.atlassian.com"
    assert "headers: {Authorization: 'Bearer ${JIRA_API_TOKEN}'}" in made["yaml"]
    body = api.get("/api/v2/tasks/" + botops_tasks(api)[0]["id"], headers=headers()).json()["task"]["body"]
    assert "remote MCP server" in body and "never write the value" in body
    for bad in ({"url": "http://mcp.example.com/mcp"}, {"url": "https://x.example/mcp", "transport": "ws"},
                {"url": "https://x.example/mcp", "headers": {"Authorization": "Bearer abcdefghijklmnopqrstuvwxyz"}},
                {"url": "https://x.example/mcp", "headers": {"Authorization": "${POSTHOG_KEY}"}}):
        r = api.post("/api/v2/bots/ops/tools", json={**MCP_JIRA, "service": "other", "mcp": bad}, headers=headers())
        assert r.status_code == 422, (bad, r.text)


def test_an_mcp_tool_update_replaces_only_what_is_sent_of_the_block(api):
    botops(api)
    machine = runner(api)
    assign(api, machine, "ops")
    row = {**MCP_JIRA, "credential": "present", "mcp": {**MCP_JIRA["mcp"], "status": "reachable"}}
    assert report(api, machine, "ops", [row]).status_code == 200
    r = api.post("/api/v2/bots/ops/tools/jira/update", json={"mcp": {"url": "https://mcp.atlassian.com/v3/mcp"}}, headers=headers())
    assert r.status_code == 200, r.text
    assert "url: https://mcp.atlassian.com/v3/mcp" in r.json()["yaml"] and "Bearer ${JIRA_API_TOKEN}" in r.json()["yaml"]
    # The same address again is no change; the computer reporting the new one settles the request.
    assert api.post("/api/v2/bots/ops/tools/jira/update", json={"mcp": {"url": "https://mcp.atlassian.com/v2/mcp"}},
                    headers=headers()).status_code == 409
    changed = {**row, "mcp": {**row["mcp"], "url": "https://mcp.atlassian.com/v3/mcp"}}
    assert report(api, machine, "ops", [changed]).status_code == 200
    assert "pending" not in {t["id"]: t for t in tools_of(api)["tools"]}["jira"]
    assert api.post("/api/v2/bots/ops/tools/jira/update", json={"mcp": {"url": "http://plain.example/mcp"}}, headers=headers()).status_code == 422


def test_the_mcp_add_and_update_tools_take_the_url_transport_and_headers(api):
    botops(api)
    err, added = mcp_call(api, "hub_tool_add", {"bot": "ops", "service": "linear", "can": "read", "env": "LINEAR_API_KEY",
                                                 "mcp_url": "https://mcp.linear.app/mcp", "transport": "http",
                                                 "headers": ["Authorization: Bearer ${LINEAR_API_KEY}"]})
    assert not err and added["tool"]["mcp"]["host"] == "mcp.linear.app"
    assert "Bearer ${LINEAR_API_KEY}" in added["yaml"]
    err, refused = mcp_call(api, "hub_tool_add", {"bot": "ops", "service": "wiki", "can": "read", "mcp_url": "http://wiki.example/mcp"})
    assert err and refused["error"] == "entry" and "https" in json.dumps(refused)


def test_external_tool_request_goes_to_the_profile_and_its_report_completes_it(api):
    from backend.tests.test_agents import hermes_bot, credential
    hermes_bot(api)
    token = credential(api)["token"]
    entry = {"service": "sqlite", "can": ["read"], "scope": {"database": "example"}}
    added = post(api, "bots/scout/tools", entry)
    with api.app.state.store.read() as c:
        task = H.task(c, added["task_id"])
    assert task["owner"] == "bot:scout" and "hub_tool_report" in task["body"]
    assert "preflight" not in task["body"]
    failed, out = mcp_call(api, "hub_tool_report", {"tools": [entry]}, token=token)
    assert not failed
    page = tools_of(api, "scout")
    assert any(tool["service"] == "sqlite" and tool["status"] != "pending" for tool in page["tools"])
    assert page["computer"] is None and page["reported_at"]
    with api.app.state.store.read() as c:
        assert c.execute("SELECT state FROM bot_tool_requests WHERE bot='scout'").fetchone()[0] == "done"
    post(api, "bots/scout/tools/sqlite/delete", {})
    assert not mcp_call(api, "hub_tool_report", {"tools": []}, token=token)[0]
    assert not any(tool["service"] == "sqlite" for tool in tools_of(api, "scout")["tools"])


def test_tools_show_extra_repository_capabilities_and_custom_team_connections(api):
    from backend.github_app import save_extra_repos
    configure(api)
    with api.app.state.store.transaction() as c:
        save_extra_repos(c, "ops", ["example/product"])
    page = tools_of(api)
    assert any(tool["scope"].get("repo") == "example/product" and "read" in tool["can"] for tool in page["tools"])
    machine = runner(api)
    assign(api, machine, "ops")
    assert report(api, machine, "ops", [{"service": "example-tool", "can": ["read"], "credential": "not-declared",
                                      "mcp": {"url": "https://example.com/mcp", "transport": "http", "status": "reachable"}}]).status_code == 200
    catalog = get(api, "tools")
    custom = next(item for item in catalog["integrations"] if item["service"] == "example-tool")
    assert catalog["label"] == "Available tools" and custom["bots"] == ["ops"]
    assert custom["connections"][0]["mcp"]["host"] == "example.com"
    assert custom["kind"] == "mcp" and custom["writes"] == "never"
    assert get(api, "tools/example-tool")["service"] == "example-tool"
    post(api, "tools/example-tool/learnings", {"text": "Use the read query."})
    assert get(api, "tools/example-tool")["learnings"]


def test_removed_custom_tool_keeps_learnings_readable_and_owner_can_delete(api):
    with api.app.state.store.transaction() as c:
        c.execute("INSERT INTO learnings(id,integration,actor,text,created) VALUES('stored-note','retired-tool','human:ana','Use the read query',?)", (H.now(),))
    page = get(api, "tools/retired-tool")
    assert page["read_only"] and page["learnings"][0]["id"] == "stored-note"
    assert any(row["service"] == "retired-tool" for row in get(api, "tools")["integrations"])
    post(api, "tools/retired-tool/learnings/stored-note/delete", {}, "ben-test", expected=403)
    post(api, "tools/retired-tool/learnings/stored-note/delete", {})
    get(api, "tools/retired-tool", expected=404)


def test_rejected_tool_report_is_visible_on_pending_tools(api):
    botops(api)
    configure(api)
    r = runner(api)
    assign(api, r, "ops")
    post(api, "bots/ops/tools", {"service": "example-tool", "can": ["read"]})
    doc = {"schema_version": 1, "bots": {"ops": {"ready": True, "repository_present": True,
           "warnings": ["Tool report rejected: invalid MCP declaration"]}}}
    post(api, "runners/heartbeat", {"version": "test", "platform": "test", "readiness": doc}, r["token"])
    result = get(api, "bots/ops/tools")
    assert result["report_error"] == "Tool report rejected: invalid MCP declaration"
    pending = next(t for t in result["tools"] if t["service"] == "example-tool")
    assert pending["status"] == "problem" and "rejected" in pending["problem"]


def test_a_held_credential_other_than_the_google_key_needs_no_message_bot(api):
    # Response to Demo, 2026-10-01: its vault-held Close key was shown as a mail-token problem.
    configure(api)
    machine = runner(api)
    assign(api, machine, "ops")
    close = {"service": "close-crm", "identity": "TIDY account", "can": ["read"], "env": "CLOSE_API_KEY"}
    assert report(api, machine, "ops", [{**close, "credential": "present", "held": True}]).status_code == 200
    tool = {t["id"]: t for t in tools_of(api)["tools"]}["close-crm"]
    assert tool["status"] == "ready" and "problem" not in tool
    assert not [i for i in get(api, "fleet/check", token="ana-test")["issues"] if "nobody's message bot" in i["text"]]

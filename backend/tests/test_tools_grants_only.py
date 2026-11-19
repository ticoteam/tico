"""Runs are built from grants alone (backend/bot_tools.py, credential-runtime): a tool granted in Tools reaches the bot's
run (its key, its settings, its MCP server) and shows on the bot's Tools tab with no bot.yaml entry, and asking to
register an entry that only names it files no task for BotOps, while an entry with notes still does."""

from backend.store import H
from backend.tests.helpers.api import assign, claim, get, post, ready, runner
from backend.tests.helpers.api_tools import key, level, tool
from backend.tests.helpers.credentials import setup

SECRET = "acme-close-synthetic-rw-0011223344"
TOKEN = "acme-errors-synthetic-token-5150"


def requests(api):
    with api.app.state.store.read() as c:
        return c.execute("SELECT kind, service FROM bot_tool_requests WHERE bot='ops'").fetchall()


def test_a_granted_tool_reaches_the_run_and_the_tools_tab_with_no_bot_yaml_entry(api):
    setup(api)
    crm = tool(api)["id"]  # Close CRM, on the proxy
    key(api, crm, SECRET)
    post(api, f"api-tools/{crm}/settings", {"name": "CLOSE_REGION", "value": "eu"})
    errors = tool(api, "Acme Errors", base_url="https://mcp.acme.example")["id"]
    key(api, errors, TOKEN)
    with api.app.state.store.transaction() as c:  # an MCP tool: its server is under its address
        c.execute("UPDATE tools SET mcp_url='https://mcp.acme.example/mcp' WHERE id=?", (errors,))
    level(api, crm, "bot:ops", "read")
    level(api, errors, "bot:ops", "read")
    machine = runner(api)
    assign(api, machine, "ops")
    ready(api, machine, ["ops"])  # its computer reports no bot.yaml tools at all
    post(api, "chat/ops", {"text": "Use only the synthetic fixture"})
    answer = get(api, "credential-runtime", claim(api, machine)["token"])
    items = {i["env"]: i for i in answer["credentials"]}
    assert items["CLOSE_API_KEY"]["value"].startswith("tpx1.")  # the key, through the proxy
    assert items["CLOSE_REGION"]["value"] == "eu"  # its setting
    [server] = answer["mcp_servers"]  # the MCP tool's server
    assert server["name"] == errors and server["url"].endswith(f"/api/v2/tool-proxy/{errors}/mcp")
    assert SECRET not in str(answer) and TOKEN not in str(answer)
    rows = {t["id"]: t for t in get(api, "bots/ops/tools")["tools"]}
    assert rows["granted-" + crm]["granted"]["kind"] == "api" and rows["granted-" + crm]["status"] == "ready"
    assert rows["granted-" + errors]["granted"]["kind"] == "mcp"
    # Asking to register what it already holds files nothing; an entry with a note is still BotOps' to commit.
    made = post(api, "bots/ops/tools", {"service": "close-crm", "can": ["read"], "env": "CLOSE_API_KEY"})
    assert made["granted"] is True and made["task_id"] is None and made["tool"]["id"] == "granted-" + crm
    assert requests(api) == []
    with api.app.state.store.transaction() as c:
        H.sync_registry(c, {"botops": {"name": "botops", "status": "active"}}, None)
    noted = post(api, "bots/ops/tools", {"service": "close-crm", "can": ["read"], "note": "pipeline reads only"})
    assert noted["task_id"] and [tuple(r) for r in requests(api)] == [("add", "close-crm")]

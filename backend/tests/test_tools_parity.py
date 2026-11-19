"""Every Tools write route is a `tico` command and a Tico MCP tool that calls that very route, or a person's own tap
with its reason (clients/commands/tools_admin.py ROUTES and UI_ONLY): no bot needs the Tools page."""

import argparse
import re

from clients import cli_spec, hubcli, hubtools
from clients.commands import tools_admin
from clients.remotecli import tool_name

# The Tools section's routes: API and MCP tools, keys and settings, vendors, website tools, the vault and its key cards,
# the Tools chat and a bot's bot.yaml tool entries. A route added under any of these needs a line in ROUTES or UI_ONLY.
SCOPE = re.compile(
    r"/api/v2/(api-tools|tool-|tools/|tools-chat/|mcp-tools|website-tools|credentials"
    r"|credential-(requests|set|imports)|bots/\{[^}/]+\}/tools)"
)
WRITES = {"POST", "PUT", "PATCH", "DELETE"}


def tools_write_routes(app):
    found = set()
    for route in app.routes:
        path = getattr(route, "path", "")
        if SCOPE.match(path):
            found |= {f"{method} {path}" for method in getattr(route, "methods", None) or () if method in WRITES}
    return found


def cli_tools():
    """Every MCP tool some `tico` command line runs (a spec's tools, else the tool its words name)."""
    found = set()

    def walk(parser):
        spec = parser.get_default("_spec")
        if isinstance(spec, cli_spec.Command):
            found.update((spec.tool, *spec.tools))
        elif parser.get_default("fn"):
            found.add(tool_name(parser.get_default("fn")))
        for action in parser._actions:
            if isinstance(action, argparse._SubParsersAction):
                for child in action.choices.values():
                    walk(child)

    walk(hubcli.parser())
    return found


def test_every_tools_write_route_is_a_command_and_an_mcp_tool_or_a_persons_own_tap(api):
    routes, mapped, persons = tools_write_routes(api.app), set(tools_admin.ROUTES), set(tools_admin.UI_ONLY)
    assert not mapped & persons
    assert routes - mapped - persons == set(), (
        "A Tools write route no `tico` command or MCP tool calls: add the tool (clients/commands/tools_admin.py) and "
        "its line in ROUTES, or a line in UI_ONLY saying why only a person makes it"
    )
    assert (mapped | persons) - routes == set(), "ROUTES or UI_ONLY names a route the app no longer has"
    commands = cli_tools()
    for route, name in tools_admin.ROUTES.items():
        assert name in hubtools.BY_NAME, (route, name)
        assert name in commands, f"{name} ({route}) has no `tico` command"
    # The offered list is what an agent sees: every mapped tool is offered to a person and to a bot (the Tools Owner).
    offered = {t["name"] for t in hubtools.listing(local=True, kind="bot")}
    assert set(tools_admin.ROUTES.values()) - {"tico_credential_import"} <= offered


class Recorder:
    """An `api` that records every call (method, path) and answers with what the tools read first."""

    CREDENTIALS = {
        "credentials": [
            {"id": "k1", "name": "Linear", "env": "LINEAR_API_KEY", "tool_id": "linear", "kind": "api_key"},
            {
                "id": "k2",
                "name": "Loose",
                "env": "LOOSE_KEY",
                "kind": "api_key",
                "revision": 1,
                "grants": [{"id": "g1", "subject": "bot:ops"}],
            },
        ],
        "bots": [{"id": "ops", "name": "Ops"}],
    }

    def __init__(self):
        self.calls = []

    def call(self, method, path, body=None, key=None, query=None, delegate=False):
        method, path = method.upper(), path.split("?")[0]
        self.calls.append((method, path))
        if path == "me":
            return {"kind": "owner", "role": "owner", "actor": "human:ana"}
        if path == "credentials" and method == "GET":
            return self.CREDENTIALS
        if path.startswith("api-tools/") and method == "GET":
            return {"settings": [{"id": "s1", "name": "POSTHOG_HOST"}]}
        if path.startswith("credential-imports"):
            return {"id": "i1", "state": "done", "credential_id": "k3"}
        return {"id": "x"}

    def get(self, path, /, **query):
        return self.call("GET", path, query=query)

    def post(self, path, body=None, key=None):
        return self.call("POST", path, body, key)

    def patch(self, path, body=None, key=None):
        return self.call("PATCH", path, body, key)


# For each mapped route, the arguments that make its tool take that route.
ARGS = {
    "POST /api/v2/api-tools": {"name": "Linear"},
    "PATCH /api/v2/api-tools/{tool_id}": {"id": "api:linear", "delivery": "proxy"},
    "DELETE /api/v2/api-tools/{tool_id}": {"id": "linear"},
    "POST /api/v2/api-tools/{tool_id}/keys": {"name": "Linear", "from_env": "NEW_KEY", "tool": "linear"},
    "PATCH /api/v2/api-tools/{tool_id}/keys/{key_id}": {"credential": "Linear", "level": "read"},
    "DELETE /api/v2/api-tools/{tool_id}/keys/{key_id}": {"credential": "Linear"},
    "PUT /api/v2/api-tools/{tool_id}/access/{subject}": {"id": "linear", "bot": "ops", "level": "read"},
    "POST /api/v2/api-tools/{tool_id}/check": {"id": "linear"},
    "POST /api/v2/api-tools/{tool_id}/settings": {"id": "posthog", "name": "POSTHOG_HOST", "value": "eu.posthog.com"},
    "PATCH /api/v2/api-tools/{tool_id}/settings/{setting_id}": {
        "id": "posthog",
        "setting": "POSTHOG_HOST",
        "value": "x",
    },
    "DELETE /api/v2/api-tools/{tool_id}/settings/{setting_id}": {"id": "posthog", "setting": "s1"},
    "POST /api/v2/api-tools/{tool_id}/sign-in": {"id": "linear"},
    "PUT /api/v2/api-tools/{tool_id}/template": {"id": "twilio", "template": "twilio"},
    "POST /api/v2/api-tools/{tool_id}/ungroup": {"id": "twilio"},
    "POST /api/v2/api-tools/{tool_id}/paste": {"id": "twilio", "from_env": "NEW_KEY"},
    "POST /api/v2/tool-templates/classify": {"from_env": "NEW_KEY"},
    "POST /api/v2/mcp-tools/probe": {"url": "https://mcp.linear.app/mcp"},
    "POST /api/v2/mcp-tools": {"name": "Linear", "url": "https://mcp.linear.app/mcp", "auth": "oauth"},
    "POST /api/v2/website-tools": {"sites": ["app.acme.example"]},
    "PATCH /api/v2/website-tools/{tool_id}": {"id": "website:acme", "audience": None},
    "DELETE /api/v2/website-tools/{tool_id}": {"id": "acme"},
    "POST /api/v2/website-tools/{tool_id}/accounts": {"tool": "acme", "username": "ops@acme.example"},
    "PATCH /api/v2/website-tools/{tool_id}/accounts/{account_id}": {"tool": "acme", "account": "a1", "totp_off": True},
    "DELETE /api/v2/website-tools/{tool_id}/accounts/{account_id}": {"tool": "acme", "account": "a1"},
    "PUT /api/v2/website-tools/{tool_id}/accounts/{account_id}/access/{subject}": {
        "tool": "acme",
        "account": "a1",
        "subject": "ops",
        "on": True,
    },
    "POST /api/v2/credentials": {"name": "Loose", "from_env": "NEW_KEY"},
    "POST /api/v2/credentials/{cid}": {"credential": "Loose", "from_env": "NEW_KEY"},
    "POST /api/v2/credentials/{cid}/grants": {"credential": "Loose", "to_bot": "ops"},
    "POST /api/v2/credentials/{cid}/grants/{gid}/revoke": {"credential": "Loose", "from_bot": "ops"},
    "DELETE /api/v2/credentials/{cid}": {"credential": "Loose"},
    "POST /api/v2/credential-requests": {"env": "LINEAR_API_KEY"},
    "POST /api/v2/credential-requests/{rid}/cancel": {"id": "c1"},
    "POST /api/v2/credential-set": {"name": "Linear", "env": "LINEAR_API_KEY", "from_env": "NEW_KEY", "for_bot": "ops"},
    "POST /api/v2/credential-imports": {"env": "LINEAR_API_KEY", "from_bot": "ops", "wait": 0},
    "POST /api/v2/tools-chat/proposals": {"thread_id": "t1", "rows": []},
    "POST /api/v2/bots/{bot}/tools": {"bot": "ops", "service": "linear", "can": ["read"]},
    "POST /api/v2/bots/{bot}/tools/{tool_id}/update": {"bot": "ops", "id": "t1", "note": "Read only"},
    "POST /api/v2/bots/{bot}/tools/{tool_id}/delete": {"bot": "ops", "id": "t1"},
    "POST /api/v2/tools/{service}/learnings": {"service": "linear", "text": "Paginate with first: 50"},
    "POST /api/v2/tools/{service}/learnings/{lid}/delete": {"service": "linear", "id": "l1"},
}


def pattern(route):
    method, path = route.split(" ", 1)
    path = re.sub(r"\{[^}]+:path\}", ".+", path.removeprefix("/api/v2/"))
    return method, re.compile(re.sub(r"\{[^}]+\}", "[^/]+", path))


def test_each_tool_calls_the_very_route_the_tools_page_calls(monkeypatch):
    monkeypatch.setenv("NEW_KEY", "synthetic-new-key-0000")
    assert set(ARGS) == set(tools_admin.ROUTES)
    for route, name in tools_admin.ROUTES.items():
        api = Recorder()
        hubtools.BY_NAME[name]["fn"](api, dict(ARGS[route]))
        method, path = pattern(route)
        assert any(m == method and path.fullmatch(p) for m, p in api.calls), (route, name, api.calls)

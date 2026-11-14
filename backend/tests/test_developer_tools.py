"""The developer tool list (clients/commands/_access.py DEVELOPER_TOOLS, backend/mcp.py tool_profile): a code bot's MCP
server lists only the tools a code bot uses, and no code bot loses one its instructions tell it to run. A Feature Owner
writes code too: it gets the feature list (FEATURE_TOOLS), the developer list plus the product tools its template names.

Every `tico ...` command in a code bot's template (AGENTS.md, playbooks, skills, knowledge) is parsed with
clients/hubcli.py and named as the tool it runs (`remotecli.tool_name`), beside each `tico_...` tool name written out and
each `tico` command a skill's script runs. Any such tool a bot may be offered must be in the developer list."""

import re
from pathlib import Path

from backend import bot_settings as BS
from backend import mcp as MCP
from backend.auth import Identity
from backend.tests.helpers.api import headers
from backend.tests.helpers.assistant_template_commands import argv_of, commands_in, parse_error
from clients import hubcli, hubtools
from clients.remotecli import tool_name

CATALOG = Path(__file__).resolve().parents[2] / "templates" / "catalog"
CODE_TEMPLATES = sorted(t for t in MCP.DEVELOPER_TEMPLATES if (CATALOG / t).is_dir())
FEATURE_TEMPLATES = sorted(t for t in MCP.FEATURE_TEMPLATES if (CATALOG / t).is_dir())
TOOL = re.compile(r"\btico_[a-z0-9_]+\b")
SCRIPT_CALL = re.compile(r"""\[\s*["']tico["']\s*,\s*["']([a-z-]+)["']\s*,\s*["']([a-z-]+)["']""")


def _tool_of(argv, parser):
    """The tool a `tico` command runs, or None for a command with no tool (one that runs in a shell only)."""
    if parse_error(argv, parser) is None:
        fn = getattr(hubcli.parse(parser, argv), "fn", None)
        if isinstance(fn, str) and tool_name(fn) in hubtools.BY_NAME:
            return tool_name(fn)
    # A bare mention or a placeholder the parser refuses: the longest leading words that name a tool.
    words = [w for w in argv if not w.startswith("-")]
    for n in (3, 2):
        if tool_name(" ".join(words[:n])) in hubtools.BY_NAME:
            return tool_name(" ".join(words[:n]))
    return None


def named_tools(template):
    """{tool: where it is named} for one code bot's template. bot.yaml and card.yaml are notes for people (the wizard,
    the seed), not instructions the bot runs, so they are left out."""
    parser = hubcli.parser()
    found = {}
    for path in sorted((CATALOG / template).rglob("*")):
        if not path.is_file() or path.name in ("bot.yaml", "card.yaml"):
            continue
        where = str(path.relative_to(CATALOG))
        if path.suffix == ".md":
            text = path.read_text()
            for name in TOOL.findall(text):
                found.setdefault(name, where)
            for number, line in commands_in(path):
                argv = argv_of(line)
                if argv and argv != ["\x00unbalanced quotes"] and (name := _tool_of(argv, parser)):
                    found.setdefault(name, f"{where}:{number}")
        elif path.suffix == ".py":
            for noun, verb in SCRIPT_CALL.findall(path.read_text()):
                if (name := tool_name(f"{noun} {verb}")) in hubtools.BY_NAME:
                    found.setdefault(name, where)
    return found


def test_the_code_templates_are_there():
    assert {"builder", "reviewer", "qa-owner", "release-owner"} <= set(CODE_TEMPLATES)
    assert FEATURE_TEMPLATES == ["feature-owner"]


def test_every_tool_a_code_bot_is_told_to_run_is_in_the_developer_list():
    offered = {t["name"] for t in hubtools.listing(True, "bot")}
    missing = [
        f"{template}: {name} ({where})"
        for template in CODE_TEMPLATES
        for name, where in named_tools(template).items()
        if name in offered and name not in hubtools.DEVELOPER_TOOLS
    ]
    assert not missing, "\n".join(missing)


def test_every_tool_a_feature_owner_is_told_to_run_is_in_the_feature_list():
    offered = {t["name"] for t in hubtools.listing(True, "bot")}
    named = {name: where for template in FEATURE_TEMPLATES for name, where in named_tools(template).items()}
    missing = [
        f"{name} ({where})" for name, where in named.items() if name in offered and name not in hubtools.FEATURE_TOOLS
    ]
    assert not missing, "\n".join(missing)
    # It builds, so it is told to run the code bot's core tools, and the product ones its review and pitch need.
    assert {"tico_task_show", "tico_task_done", "tico_page_publish", "tico_task_attach", "tico_goal_list"} <= set(named)
    # Every product tool on the list is one its template names: the list does not grow by itself.
    assert hubtools.FEATURE_TOOLS - hubtools.DEVELOPER_TOOLS <= set(named)


def test_the_builder_and_reviewer_name_the_core_tools():
    # The walk above finds commands: a regression in it would pass the check with nothing to check.
    builder, reviewer = named_tools("builder"), named_tools("reviewer")
    assert {"tico_task_show", "tico_task_done", "tico_task_ask", "tico_page_publish", "tico_file_read"} <= set(builder)
    assert {"tico_task_show", "tico_page_publish"} <= set(reviewer)


def test_every_developer_tool_is_a_tool_a_bot_is_offered():
    tools = {t["name"]: t for t in hubtools.TOOLS}
    assert hubtools.DEVELOPER_TOOLS <= hubtools.FEATURE_TOOLS <= set(tools)
    assert all("bot" in hubtools.offered_to(tools[n]) for n in hubtools.FEATURE_TOOLS - hubtools.DEVELOPER_TOOLS)
    # A person-only tool named in a skill (a person turns the app view on) stays a person's; everything else a bot gets.
    assert {n for n in hubtools.DEVELOPER_TOOLS if "bot" not in hubtools.offered_to(tools[n])} == {"tico_bot_app_view"}


def test_the_developer_listing_is_a_small_part_of_the_bots():
    full = hubtools.listing(True, "bot")
    dev = hubtools.listing(True, "bot", hubtools.DEVELOPER)
    assert {t["name"] for t in dev} == hubtools.DEVELOPER_TOOLS & {t["name"] for t in full}
    assert len(dev) * 8 < len(full)
    feature = hubtools.listing(True, "bot", hubtools.FEATURE)
    assert {t["name"] for t in feature} == hubtools.FEATURE_TOOLS & {t["name"] for t in full}
    assert len(feature) * 8 < len(full)
    assert hubtools.listing(True, "bot", "") == full  # no profile: the whole list
    assert hubtools.listing(True, "bot", "a-newer-profile") == full  # one this client does not know: the whole list


class FakeApi:
    def __init__(self, me):
        self.me, self.calls = me, 0

    def get(self, path, **query):
        assert path == "me"
        self.calls += 1
        return self.me


def _names(protocol):
    reply = protocol.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}})
    return {t["name"] for t in reply["result"]["tools"]}


def test_the_mcp_server_lists_the_developer_tools_for_a_code_bot_and_still_runs_others():
    api = FakeApi({"kind": "bot", "tool_profile": "developer"})
    protocol = hubtools.Protocol(api, local=True)
    assert _names(protocol) == hubtools.DEVELOPER_TOOLS - {"tico_bot_app_view"}
    assert api.calls == 1  # `GET /me` once, for the kind and the profile
    # A tool off the list still runs when the caller's kind allows it: only the listing is smaller.
    reply = protocol.handle(
        {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "tico_goal_list", "arguments": {}}}
    )
    assert "forbidden" not in str(reply)
    # Everyone else keeps the whole list; a profile only applies to a bot.
    assert len(_names(hubtools.Protocol(FakeApi({"kind": "bot"}), local=True))) > 400
    assert len(_names(hubtools.Protocol(FakeApi({"kind": "owner", "tool_profile": "developer"}), local=True))) > 400


def test_me_says_which_bots_get_the_developer_list(api):
    with api.app.state.store.transaction() as c:
        ids = [r[0] for r in c.execute("SELECT id FROM bots ORDER BY id")]
        BS.update(c, ids[0], {"template": "builder"})
        BS.update(c, ids[1], {"template": "librarian"})
        BS.update(c, ids[2], {"template": "feature-owner"})
        auth = api.app.state.auth
        assert MCP.tool_profile(c, auth, Identity(f"bot:{ids[0]}", "bot")) == "developer"
        assert MCP.tool_profile(c, auth, Identity(f"bot:{ids[1]}", "bot")) == ""
        assert MCP.tool_profile(c, auth, Identity(f"bot:{ids[2]}", "bot")) == "feature"
        assert MCP.tool_profile(c, auth, Identity("human:ana", "owner")) == ""
    me = api.get("/api/v2/me", headers=headers()).json()
    assert "tool_profile" not in me


def test_a_code_bots_own_turn_is_offered_the_developer_list_over_http(api):
    from backend.tests.helpers.api import get, setup_attempt
    from backend.tests.helpers.mcp import rpc

    token = setup_attempt(api, "ops")[2]["token"]
    full = {t["name"] for t in rpc(api, "tools/list", token=token)["result"]["tools"]}
    assert "tool_profile" not in get(api, "me", token=token) and len(full) > 400
    with api.app.state.store.transaction() as c:
        BS.update(c, "ops", {"template": "builder"})
    assert get(api, "me", token=token)["tool_profile"] == "developer"
    dev = {t["name"] for t in rpc(api, "tools/list", token=token)["result"]["tools"]}
    assert dev == hubtools.DEVELOPER_TOOLS & full

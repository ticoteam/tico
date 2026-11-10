"""No file names a Tico MCP tool `hub_*`: since 0.6.0 every tool is `tico_*`, served as `tico`, with no alias.

A bot copies the tool names it reads (its instructions, skills, the docs, the runner's prompt, the tool descriptions), and
the safety lists (backend/execution.py OUTSIDE_TOOLS, the gateway allowlist) match by name, so one `hub_task_show` left
anywhere is a call that fails or an outside action that goes unflagged. scripts/cleanup/rename_hub_tools_to_tico.py
made the change; it is rerunnable, and its check is the first test here, so a merge that brings back an old name fails
until the script is run again.
"""

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

from clients import hubtools

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "cleanup" / "rename_hub_tools_to_tico.py"


def load_script():
    spec = importlib.util.spec_from_file_location("rename_hub_tools_to_tico", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_rename_script_finds_nothing_left_to_change():
    done = subprocess.run(
        [sys.executable, str(SCRIPT), "--check"], cwd=ROOT, capture_output=True, text=True, timeout=300
    )
    assert done.returncode == 0, (
        "a file names a `hub_*` tool; run python3 scripts/cleanup/rename_hub_tools_to_tico.py\n"
        + done.stdout
        + done.stderr
    )
    assert done.stdout.startswith("would rewrite 0 ")


def test_every_tool_and_command_name_is_tico_and_so_is_the_server():
    names = {t["name"] for t in hubtools.TOOLS} | set(hubtools.SHELL_ONLY) | set(hubtools.CLI_TOOL_ALIASES)
    names |= {n for ns in hubtools.CLI_TOOL_ALIASES.values() for n in ns}
    assert len(names) > 500  # guard the guard
    assert not [n for n in names if not n.startswith("tico_")]
    assert hubtools.SERVER_INFO["name"] == "tico"


@pytest.mark.parametrize("name", ["hub_task_show", "hub_message_send", "hub_whoami"])
def test_an_old_name_is_an_unknown_tool_and_calls_nothing(name):
    class Api:
        calls = []

        def get(self, *a, **k):
            self.calls.append(a)
            return {}

        post = patch = get

    api = Api()
    reply = hubtools.Protocol(api, kind="bot").handle(
        {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": {}}}
    )
    assert reply["error"]["message"] == f"Unknown tool: {name}"
    assert api.calls == []


@pytest.mark.parametrize(
    "text, expected",
    [
        ("Run `hub_task_show` first.", "Run `tico_task_show` first."),
        ('{"tool": "mcp__hub__hub_task_create"}', '{"tool": "mcp__tico__tico_task_create"}'),
        ("mcp__tico__hub_task_list", "mcp__tico__tico_task_list"),
        ('"hub_email_" + action', '"tico_email_" + action'),
        ("the hub_finance_* tools", "the tico_finance_* tools"),
        ("through the hub_finance tools only", "through the tico_finance tools only"),
        ("every tool (`hub_*`)", "every tool (`tico_*`)"),
        ("'hub_release_now': 'publish release'", "'tico_release_now': 'publish release'"),
        # Names that only contain a tool name, and words that are not tools, stay.
        ("def hub_words(text):", "def hub_words(text):"),
        ("hub_mcp_server(&env)", "hub_mcp_server(&env)"),
        ("my_hub_task_show and HUB_TOKEN", "my_hub_task_show and HUB_TOKEN"),
        ("hub_task_show_later", "hub_task_show_later"),
        ("hub_rejected", "hub_rejected"),
    ],
)
def test_the_script_renames_tool_names_and_leaves_other_words(text, expected):
    script = load_script()
    assert script.rewrite(text, script.patterns())[0] == expected

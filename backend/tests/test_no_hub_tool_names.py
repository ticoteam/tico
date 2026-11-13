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

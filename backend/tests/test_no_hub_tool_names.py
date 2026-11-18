"""Every tool and command name in the clients/hubtools.py registry is `tico_*`, the server is named `tico`, and an old
`hub_*` name is refused as an unknown tool without calling the API. This reads the registry only, not the files that
name tools."""

import pytest

from clients import hubtools


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

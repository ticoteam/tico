"""The 0.5 tools `tico_requirements`, `tico_requirement_attach`, `tico_pipeline` and `tico_pipeline_manager` are gone in
Pipelines v3 (docs/pipelines-v3.md): over MCP each is an unknown tool and calls nothing. `tico pipeline`, `tico stage` and
`tico requirement` are the new commands (clients/commands/pipelines.py)."""

import pytest

from clients import hubcli, hubtools


class Recorder:
    """A hub client that records any call (none is made for a command that is gone)."""

    def __init__(self):
        self.calls = []

    def get(self, path, **params):
        self.calls.append(("GET", path))
        return {}

    def post(self, path, body, key=None):
        self.calls.append(("POST", path))
        return {}


@pytest.mark.parametrize(
    "name", ["tico_requirements", "tico_requirement_attach", "tico_pipeline", "tico_pipeline_manager"]
)
def test_an_old_pipeline_tool_is_an_unknown_tool_and_calls_nothing(name):
    assert name not in hubtools.BY_NAME
    client = Recorder()
    reply = hubtools.Protocol(client, kind="bot").handle(
        {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": {}}}
    )
    assert reply["error"]["message"] == f"Unknown tool: {name}"
    assert client.calls == []

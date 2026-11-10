"""`hub requirements`, `hub requirement attach`, `hub pipeline` and `hub pipeline-manager` are gone in Pipelines v3
(docs/pipelines-v3.md): a task moves with `hub task done` and `hub task move`, and a pipeline's settings are a
person's, on its page. A bot taught one of the old lines is told in one line what to use, on the CLI and over MCP."""

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
    "line, says",
    [
        (["requirements", "stage-42"], "hub task done"),
        (["requirement", "attach", "task-7", "req-3", "--source", "link"], "hub task move"),
        (["pipeline", "--type", "Ops"], "on its page"),
        (["pipeline-manager", "Product", "cpo"], "on its page"),
        (["pipeline-manager", "product-roadmap", "--clear"], "on its page"),
    ],
)
def test_an_old_pipeline_command_says_in_one_line_what_to_use(line, says):
    notice = hubcli.removed_notice(line)
    assert notice and notice.startswith(f'hub: "{line[0]}" is gone; ') and says in notice
    assert "\n" not in notice


@pytest.mark.parametrize("name", ["hub_requirements", "hub_requirement_attach", "hub_pipeline", "hub_pipeline_manager"])
def test_an_old_pipeline_tool_is_answered_with_what_to_use_and_calls_nothing(name):
    assert name not in hubtools.BY_NAME
    client = Recorder()
    reply = hubtools.Protocol(client, kind="bot").handle(
        {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": {}}}
    )
    error = reply["result"]["structuredContent"]
    assert error["error"] == "removed" and "hub_task_show" in error["detail"]
    assert client.calls == []


def test_the_task_commands_that_replace_them_still_parse():
    assert hubcli.parser().parse_args(["task", "show", "task-7"]).fn
    assert hubcli.removed_notice(["task", "show", "task-7"]) is None

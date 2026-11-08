"""`hub requirements` and `hub requirement attach` run the same tools as the MCP table (clients/hubtools.py): the CLI
line becomes the tool's call, with the same route and body."""

import pytest

from clients import hubcli, remotecli


class Recorder:
    """A hub client that records the one call a command makes."""

    def __init__(self):
        self.calls = []

    def get(self, path, **params):
        self.calls.append(("GET", path, None))
        return {}

    def post(self, path, body, key=None):
        self.calls.append(("POST", path, body))
        return {}


def run(*argv):
    args = hubcli.parser().parse_args(list(argv))
    client = Recorder()
    remotecli.via_tool(client, args)
    return client.calls


def test_hub_requirements_reads_the_rules_of_a_stage():
    assert run("requirements", "stage-42") == [("GET", "pipeline-stages/stage-42/requirements", None)]


def test_hub_requirement_attach_posts_a_link_to_the_requirements_attachments():
    calls = run("requirement", "attach", "task-7", "req-3", "--source", "link", "--url", "https://example.test/launch")
    assert calls == [
        (
            "POST",
            "tasks/task-7/requirements/req-3/attachments",
            {"source": "link", "url": "https://example.test/launch"},
        )
    ]


def test_hub_requirement_attach_sends_a_company_file_at_a_pinned_version():
    calls = run(
        "requirement", "attach", "task-7", "req-3", "--source", "file", "--file-id", "f-9", "--file-version", "2"
    )
    assert calls[0][2] == {"source": "file", "file_id": "f-9", "file_version": 2}


class Hub(Recorder):
    """A hub client that also reads the task types (a pipeline is named by its id or its name) and records a PATCH."""

    def get(self, path, **params):
        self.calls.append(("GET", path, None))
        return {"types": [{"id": "product-roadmap", "name": "Product"}, {"id": "ops", "name": "Ops"}]}

    def patch(self, path, body=None, key=None):
        self.calls.append(("PATCH", path, body))
        return {}


def run_hub(*argv):
    args = hubcli.parser().parse_args(list(argv))
    client = Hub()
    remotecli.via_tool(client, args)
    return client.calls


def test_hub_pipeline_manager_sets_the_bot_on_the_pipeline_named():
    calls = run_hub("pipeline", "manager", "Product", "cpo")
    assert calls[-1] == ("PATCH", "pipelines/product-roadmap/settings", {"manager_id": "cpo"})


def test_hub_pipeline_manager_clear_sends_an_empty_manager():
    calls = run_hub("pipeline", "manager", "product-roadmap", "--clear")
    assert calls[-1] == ("PATCH", "pipelines/product-roadmap/settings", {"manager_id": ""})


def test_hub_pipeline_manager_names_a_bot_or_clears_never_both_nor_neither():
    with pytest.raises(ValueError):
        run_hub("pipeline", "manager", "Product")
    with pytest.raises(ValueError):
        run_hub("pipeline", "manager", "Product", "cpo", "--clear")


def test_hub_pipeline_manager_refuses_a_pipeline_it_cannot_name():
    with pytest.raises(ValueError, match="No pipeline named"):
        run_hub("pipeline", "manager", "Nowhere", "cpo")


def test_hub_pipeline_still_reads_the_pipeline_report():
    args = hubcli.parser().parse_args(["pipeline", "--type", "Ops"])
    assert args.fn == "pipeline" and args.type == "Ops"
    assert hubcli.parser().parse_args(["pipeline", "manager", "Product", "--clear"]).fn == "pipeline manager"

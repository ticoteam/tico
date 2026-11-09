"""`hub requirements` and `hub requirement attach` run the same tools as the MCP table (clients/hubtools.py): the CLI
line becomes the tool's call, with the same route and body."""

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

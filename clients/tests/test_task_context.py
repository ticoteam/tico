"""Task context and notes use the same handlers for CLI and MCP, with bot credentials intact."""
from clients import hubcli, hubtools, remotecli


class Api:
    def __init__(self):
        self.calls = []

    def get(self, path, **query):
        self.calls.append((path, query))
        return {"text": "Saved decisions", "notes": "Saved decisions"} if path == "meetings/transcript" else {"pages": [], "meetings": []}


def test_task_context_and_notes_cli_mcp_parity(monkeypatch, capsys):
    client = Api()
    monkeypatch.setattr(remotecli, "Client", lambda *a, **kw: client)
    monkeypatch.setenv("HUB_API_URL", "https://tico.acme.example/api/v2")
    parser = hubcli.parser()
    remotecli.run(parser.parse_args(["task", "context", "#12"]))
    remotecli.main(parser.parse_args(["meeting", "read", "meeting-1", "--notes"]))
    assert capsys.readouterr().out.strip() == "Saved decisions"
    assert client.calls == [("tasks/#12/context", {}), ("meetings/transcript", {
        "id": "meeting-1", "offset": 0, "limit": 20000, "notes": True})]
    assert "bot" in hubtools.offered_to(hubtools.BY_NAME["hub_task_context"])
    assert "assistant" in hubtools.offered_to(hubtools.BY_NAME["hub_task_context"])


def test_page_version_fields_reach_the_shared_publisher():
    args = hubcli.parser().parse_args(["page", "publish", "report.html", "--task", "t1", "--page", "file-1", "--base-version", "3"])
    assert hubtools._page_fields(vars(args)) == {"task": "t1", "page": "file-1", "base_version": 3}

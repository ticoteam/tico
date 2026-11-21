"""An agent's new draft of a task file is that file's next version, whatever its name: the task keeps one file, and
every older version still opens."""

from backend.tests.helpers.api import get, headers, post  # noqa: F401
from backend.tests.helpers.automation_api import as_bot
from backend.tests.helpers.automation_cli import Client
from clients import hubcli, hubtools, remotecli


class Agent(Client):
    """The agent's CLI client over the test app, as a server without streamed uploads."""

    def features(self):
        return {}


def test_an_agent_replaces_a_file_in_place_and_its_older_versions_stay(api, tmp_path, monkeypatch):
    tid = post(api, "tasks", {"title": "Demand letter", "owner": "ops", "body": "Draft the letter."})["id"]
    attach = hubtools.BY_NAME["tico_task_attach"]["fn"]
    token = as_bot(api, "ops")
    agent = Client(api, token)
    first = attach(agent, {"id": tid, "name": "letter-v1.md", "text": "First draft"})
    fid = first["file_id"]
    # Archived by a person, the file comes back with the agent's next draft.
    assert api.patch(f"/api/v2/files/{fid}", json={"archived": True}, headers=headers()).status_code == 200
    # The CLI's `--replaces` is the tool's `replaces`.
    draft = tmp_path / "letter-v2.md"
    draft.write_text("Second draft")
    monkeypatch.setenv("HUB_API_URL", "http://testserver/api/v2")
    monkeypatch.setattr(remotecli, "Client", lambda *_, **__: Agent(api, token))
    second = remotecli.run(hubcli.parser().parse_args(["task", "attach", tid, str(draft), "--replaces", fid]))
    assert (second["file_id"], second["version"]) == (fid, 2)

    [listed] = get(api, f"tasks/{tid}/files?all=1")["files"]
    assert listed["id"] == fid and not listed["archived"] and listed["name"] == "letter-v2.md"
    assert sorted(v["n"] for v in listed["versions"]) == [1, 2]
    url = "/api/v2/files/" + fid
    assert api.get(url, headers=headers()).content == b"Second draft"
    assert api.get(url + "?v=1", headers=headers()).content == b"First draft"

    # Only a file on this very task is replaced, and a document continues its own file.
    other = post(api, "tasks", {"title": "Other matter", "owner": "ops", "body": "Elsewhere."})["id"]
    elsewhere = {"name": "letter.md", "text": "Third", "replaces": fid}
    r = api.post(f"/api/v2/tasks/{other}/files", json=elsewhere, headers=headers(token))
    assert r.status_code == 404, r.text
    document = {"document": "Spec", "text": "<p>Spec</p>", "replaces": fid}
    r = api.post(f"/api/v2/tasks/{tid}/files", json=document, headers=headers(token))
    assert r.status_code == 422, r.text

"""Files: visibility first (a private chat leaks nothing), versions, refusals, the runner outbox,
S3 imports and link rules. See docs/files.md."""

import os

import pytest

from backend.tests.test_api import api, assign, claim, headers, post, ready, runner  # noqa: F401  (fixtures)
from backend.tests.test_runner import live  # noqa: F401  (a real server on a port)
from clients import bot_files as BF
from clients.tico import APIError, Client
from runner import files_publish
from runner.state import State


def turn(api, bot="ops", who="ben-test", machine=None):
    """A person chats with the bot and a runner claims it: (machine, the attempt with the bot's token)."""
    machine = machine or runner(api)
    assign(api, machine, bot)
    ready(api, machine, [bot])
    post(api, "chat/" + bot, {"text": "Write the plan"}, who)
    return machine, claim(api, machine, bot)


def publish(api, attempt, name="plan.md", text="# Plan", **fields):
    return api.post("/api/v2/files/uploads", json={"name": name, "text": text, **fields},
                    headers=headers(attempt["token"]))


def listing(api, bot="ops", who="ben-test"):
    r = api.get(f"/api/v2/bots/{bot}/files", headers=headers(who))
    assert r.status_code == 200, r.text
    return r.json()


def test_a_private_chats_file_is_invisible_to_everyone_else(api):
    _, attempt = turn(api)
    made = publish(api, attempt)
    assert made.status_code == 200, made.text
    fid = made.json()["file"]["id"]
    assert listing(api)["total"] == 1
    for other in ("ana-test", "cara-test"):        # the company owner included: a personal chat is not theirs
        page = listing(api, who=other)
        assert page["total"] == 0 and page["files"] == []
        for path in ("", "/activity", "/versions", "/versions/1", "/meta"):
            assert api.get("/api/v2/files/" + fid + path, headers=headers(other)).status_code == 404, path
        assert "plan" not in api.get(f"/api/v2/bots/ops/files?limit=100", headers=headers(other)).text
    # Promote is the owner's or a bot administrator's, explicit and audited; then everyone who sees the bot sees it.
    assert api.patch("/api/v2/files/" + fid, json={"promote": True}, headers=headers("cara-test")).status_code == 404
    assert api.patch("/api/v2/files/" + fid, json={"promote": True}, headers=headers(attempt["token"])).status_code == 403
    assert api.patch("/api/v2/files/" + fid, json={"promote": True}, headers=headers("ben-test")).status_code == 200
    assert listing(api, who="cara-test")["total"] == 1
    assert api.get("/api/v2/files/" + fid, headers=headers("cara-test")).content == b"# Plan"
    log = api.get("/api/v2/files/" + fid + "/activity", headers=headers("cara-test")).json()["activity"]
    assert [a["action"] for a in log] == ["promoted", "created"]


def test_refusals_and_a_bot_writes_only_its_own_files(api):
    machine, attempt = turn(api, "ops")
    for name in (".env", "prod-credentials.csv", "id_rsa.txt", "tool.exe", "notes"):
        assert publish(api, attempt, name).status_code == 422, name
    assert publish(api, attempt, "x.md", path="../x.md").status_code == 422
    big = api.post("/api/v2/files/uploads?name=big.csv", content=b"x" * (BF.MAX_BYTES + 1),
                   headers={**headers(attempt["token"]), "Content-Type": "application/octet-stream"})
    assert big.status_code == 422, big.text
    assert listing(api)["total"] == 0
    # A bot cannot pick another bot's page: the authenticated identity decides.
    assign(api, machine, "finance")
    ready(api, machine, ["ops", "finance"])
    mine = publish(api, attempt, "mine.md", bot="finance", scope="bot")
    assert mine.status_code == 403                      # inside a chat: not bot-wide
    mine = publish(api, attempt, "mine.md", bot="finance").json()["file"]
    assert listing(api, "finance", "ana-test")["total"] == 0
    post(api, "chat/finance", {"text": "hello"}, "ana-test")
    other = claim(api, machine, "finance")
    assert api.patch("/api/v2/files/" + mine["id"], json={"title": "stolen"}, headers=headers(other["token"])).status_code == 404
    assert api.post("/api/v2/files/uploads", json={"name": "a.md", "text": "a"}, headers=headers("ana-test")).status_code == 403


def test_local_rules_refuse_traversal_links_env_and_oversize(tmp_path):
    root = tmp_path / "checkout"
    (root / "reports").mkdir(parents=True)
    (root / "reports" / "ok.md").write_text("fine")
    (root / "reports" / ".env").write_text("KEY=1")
    (tmp_path / "outside.md").write_text("secret")
    os.symlink(tmp_path / "outside.md", root / "reports" / "link.md")
    with open(root / "reports" / "huge.csv", "wb") as stream:
        stream.truncate(BF.MAX_BYTES + 1)
    assert BF.local_file(root, "reports/ok.md")[1] == "reports/ok.md"
    for bad in ("../outside.md", str(tmp_path / "outside.md"), "reports/../../outside.md", "reports/link.md",
                "reports/.env", "reports/huge.csv", "reports"):
        with pytest.raises(BF.Refused):
            BF.local_file(root, bad)


@pytest.mark.slow
def test_runner_upload_is_retried_after_a_restart_and_lands_once(api, live, tmp_path):
    machine, attempt = turn(api)
    checkout = tmp_path / "bot"
    (checkout / "reports").mkdir(parents=True)
    (checkout / "reports" / "weekly.md").write_text("# Weekly")

    class Lossy(Client):
        """Delivers the upload, then loses the reply: the runner cannot know it landed."""
        lose = True

        def request(self, *args, **kwargs):
            reply = super().request(*args, **kwargs)
            if Lossy.lose and "sync=failed" not in args[1]:
                raise APIError("unavailable", "reply lost", retryable=True)
            return reply

    class Box:
        pass
    before = Box()
    before.state, before.client = State(tmp_path / "state"), Client(live, machine["token"])
    files_publish.Client = Lossy
    try:
        files_publish.after_turn(before, {"id": attempt["id"], "bot": "ops", "config": {}}, checkout)
        with before.state.connect() as c:
            assert c.execute("SELECT phase FROM file_outbox").fetchone()[0] == "pending"
        Lossy.lose = False
        after = Box()                                   # the runner restarted: a new process, the same disk
        after.state, after.client = State(tmp_path / "state"), Client(live, machine["token"])
        assert files_publish.drain(after) == 1
        assert files_publish.drain(after) == 0
    finally:
        files_publish.Client = Client
    page = listing(api)
    assert page["total"] == 1 and page["files"][0]["version"] == 1 and page["files"][0]["synced"]
    log = api.get(f"/api/v2/files/{page['files'][0]['id']}/activity", headers=headers("ben-test")).json()["activity"]
    assert len(log) == 1


def test_a_blobs_provenance_is_read_by_index(api):
    """blob_readable runs for every version of every file a Files page lists: a scan per blob made a page
    with a few thousand files take seconds."""
    with api.app.state.store.read() as c:
        for query in ("SELECT blob_id FROM blob_media WHERE poster_blob_id=? OR thumb_blob_id=?",
                      "SELECT f.task_id,f.scope,v.attempt_id FROM bot_files f JOIN bot_file_versions v ON v.file_id=f.id "
                      "WHERE v.blob_id=? OR v.poster_blob_id=? OR v.thumb_blob_id=?",
                      "SELECT m.* FROM message_assets a JOIN messages m ON m.id=a.message_id WHERE a.blob_id=?"):
            plan = [r[3] for r in c.execute("EXPLAIN QUERY PLAN " + query, ("b",) * query.count("?"))]
            assert not any(step.startswith("SCAN") for step in plan), (query, plan)

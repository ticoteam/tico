"""Files: visibility first (a private chat leaks nothing), versions, refusals,
S3 imports and link rules. See docs/files.md."""

import os

import pytest

from backend import read_cache
from backend.store import H
from backend.tests.test_api import api, assign, claim, headers, post, ready, runner  # noqa: F401  (fixtures)
from clients import bot_files as BF


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
        assert "plan" not in api.get("/api/v2/bots/ops/files?limit=100", headers=headers(other)).text
    # Promote is the owner's or a bot administrator's, explicit and audited; then everyone who sees the bot sees it.
    assert api.patch("/api/v2/files/" + fid, json={"promote": True}, headers=headers("cara-test")).status_code == 404
    assert api.patch("/api/v2/files/" + fid, json={"promote": True}, headers=headers(attempt["token"])).status_code == 403
    assert api.patch("/api/v2/files/" + fid, json={"promote": True}, headers=headers("ben-test")).status_code == 200
    assert listing(api, who="cara-test")["total"] == 1
    assert api.get("/api/v2/files/" + fid, headers=headers("cara-test")).content == b"# Plan"
    log = api.get("/api/v2/files/" + fid + "/activity", headers=headers("cara-test")).json()["activity"]
    assert [a["action"] for a in log] == ["promoted", "created"]



def traced(api, request):
    """(the response, every SQL statement the request ran)."""
    store, statements = api.app.state.store, []
    take, give = store._take, store._give

    def tracing():
        c = take()
        c.set_trace_callback(statements.append)
        return c

    def untracing(c):
        c.set_trace_callback(None)
        give(c)
    store._take, store._give = tracing, untracing
    try:
        return request(), statements
    finally:
        store._take, store._give = take, give


def test_a_files_page_reads_a_fixed_number_of_statements_and_counts_only_what_each_person_sees(api, monkeypatch):
    """The page decides every active file to count what the caller may see. Those checks read together, so a bot with
    many files costs what a bot with a few does, and each decision (and so the page and its total) is the one made
    file by file: a private task, a private parent blob, a private message or a deleted task still hides a file."""
    store, media = api.app.state.store, api.app.state.file_metadata
    media.stop.set()                                     # previews write in the background: count only the page's reads
    media.wake.set()
    _, attempt = turn(api)
    with store.transaction() as c:
        hidden = H.task_create(c, "human:ben", "Private launch", "Secret", "bot:ops", private=True, lint=False)
        msg = H.task_comment(c, "human:ben", hidden["id"], "Hush input", wake=False)["id"]

    def add(prefix, n):
        """n bot-wide files after one that stays in Ben's chat."""
        made = []
        for i in range(n + 1):
            r = publish(api, attempt, f"{prefix}-{i}.md", f"# {prefix} {i}")
            assert r.status_code == 200, r.text
            fid = r.json()["file"]["id"]
            if i:
                assert api.patch("/api/v2/files/" + fid, json={"promote": True}, headers=headers("ben-test")).status_code == 200
            made.append(fid)
        return made

    def blob(c, fid):
        return c.execute("SELECT blob_id FROM bot_file_versions WHERE file_id=?", (fid,)).fetchone()[0]

    tasked, attached, poster, posted, framed, messaged, trashed, scoped, thumbed = add("hush", 9)[1:]
    with store.transaction() as c:
        c.execute("UPDATE bot_files SET task_id=? WHERE id=?", (hidden["id"], tasked))
        # An archived file in the private task's scope keeps its blob and its version's thumbnail private.
        c.execute("INSERT INTO bot_files(id,bot_id,scope,identity,title,kind,locator,first_activity_at,last_activity_at,"
                  "archived) VALUES('holder','ops',?,'holder','Holder','document','holder','2026','2026',1)",
                  ("task:" + hidden["id"],))
        c.execute("INSERT INTO bot_file_versions(file_id,version,blob_id,thumb_blob_id,digest,size,name,mime,actor,"
                  "created_at) VALUES('holder',1,?,?,'d',1,'holder.md','text/markdown','bot:ops','2026')",
                  (blob(c, scoped), blob(c, thumbed)))
        c.execute("INSERT INTO blob_media(blob_id,thumb_blob_id,media_state) VALUES(?,?,'ready')",
                  (blob(c, tasked), blob(c, framed)))
        c.execute("INSERT INTO task_assets(task_id,blob_id) VALUES(?,?)", (hidden["id"], blob(c, attached)))
        c.execute("INSERT INTO task_assets(task_id,blob_id) VALUES(?,?)", (hidden["id"], blob(c, poster)))
        c.execute("INSERT INTO blob_media(blob_id,poster_blob_id,media_state) VALUES(?,?,'ready')",
                  (blob(c, poster), blob(c, posted)))
        c.execute("INSERT INTO message_assets(message_id,blob_id) VALUES(?,?)", (msg, blob(c, messaged)))
        c.execute("INSERT INTO task_trash_assets(task_id,blob_id) VALUES('gone',?)", (blob(c, trashed),))
    viewers = ("ben-test", "cara-test", "ana-test", attempt["token"])

    def pages(limit=5):
        """Each viewer's first page and its statements, read once before: a run's and a message's provenance is kept
        between requests (provenance_clock), whatever the number of files."""
        out = {}
        for who in viewers:
            def page():
                return api.get(f"/api/v2/bots/ops/files?limit={limit}", headers=headers(who))
            page()
            out[who] = traced(api, page)
        return out

    add("public", 9)
    few = pages()
    add("more", 29)
    many = pages()
    for who in viewers:
        assert few[who][0].status_code == many[who][0].status_code == 200, many[who][0].text
        assert len(many[who][0].json()["files"]) == 5, who
        assert many[who][0].json()["total"] - few[who][0].json()["total"] == (30 if who in ("ben-test", attempt["token"]) else 29), who
        assert len(many[who][1]) == len(few[who][1]) < 60, (who, len(few[who][1]), len(many[who][1]))

    whole = {who: response.json() for who, (response, _) in pages(100).items()}
    assert whole["cara-test"]["total"] == len(whole["cara-test"]["files"]) == 9 + 29
    assert "hush" not in api.get("/api/v2/bots/ops/files?limit=100", headers=headers("cara-test")).text
    assert {tasked, attached, poster, posted, framed, messaged, scoped, thumbed} <= {f["id"] for f in whole["ben-test"]["files"]}
    assert trashed not in {f["id"] for page in whole.values() for f in page["files"]}
    # Batches split at every other file decide the same: no blob's links are lost or crossed between batches.
    chunks = H.chunks
    monkeypatch.setattr(H, "chunks", lambda values, size=500: chunks(values, 2))
    for who, response in whole.items():
        assert api.get("/api/v2/bots/ops/files?limit=100", headers=headers(who)).json() == response, who
    # The same pages, every file decided alone with nothing read ahead, as before the reads were batched.
    monkeypatch.setattr(read_cache, "cache", lambda c: None)
    for who, response in whole.items():
        again = api.get("/api/v2/bots/ops/files?limit=100", headers=headers(who)).json()
        assert again == response, who
        assert again["total"] == len(again["files"]), who


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


def test_task_covers_find_a_tasks_files_through_an_index(api):
    """A task list page asks for every task's cover: the files of those tasks must be found by task, not by scanning
    every bot file (bot_files_task, backend/files.py)."""
    from backend.task_review import COVERS_SQL
    with api.app.state.store.read() as c:
        plan = " ".join(str(row[3]) for row in c.execute("EXPLAIN QUERY PLAN " + COVERS_SQL.format(marks="?,?"),
                                                         ("a", "b", "a", "b")))
    assert "bot_files_task" in plan and "SCAN f" not in plan

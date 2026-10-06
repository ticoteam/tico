"""Task reviews retain the existing ask/answer protocol and legacy attachment path."""
import json

import pytest

from backend.tests.test_api import api, assign, claim, headers, post, ready, restrict, runner  # noqa: F401
from backend.store import H
from clients.hubcli import parser
from runner.service import Runner


def task(api, title="Review the draft"):
    return post(api, "tasks", {"owner": "ops", "title": title, "body": "Read the report."})["id"]


def ask(who=None, other=True):
    return {"questions": [{"id": "verdict", "header": "Review", "question": "Is this ready?",
                           "options": [{"label": "Approve"}, {"label": "Request changes"}], "other": other}], "who": who}


def attach(api, tid, name="report.md", **fields):
    return post(api, f"tasks/{tid}/files", {"name": name, "text": "# Report", **fields})


def get(api, path, who="ana-test"):
    response = api.get("/api/v2/" + path, headers=headers(who))
    assert response.status_code == 200, response.text
    return response.json()


def test_version_names_scopes_archive_and_author_edits(api):
    tid = task(api)
    first = attach(api, tid, note="Draft")
    second = attach(api, tid, note="Revised")
    assert first["file_id"] == second["file_id"] and (first["version"], second["version"]) == (1, 2)
    separate = attach(api, task(api, "Review another draft"))
    assert separate["file_id"] != first["file_id"]
    fid = first["file_id"]
    versions = get(api, f"tasks/{tid}/files")["files"][0]["versions"]
    assert [v["n"] for v in versions] == [2, 1] and versions[0]["note"] == "Revised"
    assert all(k in versions[0] for k in ("width", "height", "duration_ms", "media_state", "poster_url", "thumb_url"))
    assert api.get(versions[1]["url"], headers=headers()).content == b"# Report"
    path = f"/api/v2/files/{fid}/versions/2"
    assert api.patch(path, json={"note": "Changed", "ask": ask()}, headers=headers()).status_code == 200
    assert api.patch(path, json={"note": "No"}, headers=headers("cara-test")).status_code == 403
    assert api.patch(path, json={"note": "x" * 501}, headers=headers()).status_code == 422
    assert api.patch(f"/api/v2/files/{fid}", json={"archived": True}, headers=headers()).status_code == 200
    assert attach(api, tid)["file_id"] != fid


def test_legacy_attachment_is_v1_and_adopted_without_rewriting(api):
    tid = task(api)
    from backend.auth import Identity
    from backend.blobs import register
    digest = api.app.state.blobs.put(b"old")
    with api.app.state.store.transaction() as c:
        old = register(c, Identity("human:ana", "owner"), digest, 3, "report.md", "text/markdown")
        c.execute("INSERT INTO task_assets VALUES(?,?)", (tid, old["id"]))
    listed = get(api, f"tasks/{tid}/files")["files"][0]
    assert listed["id"] == old["id"] and listed["versions"][0]["n"] == 1
    new = attach(api, tid)
    assert new["file_id"] == old["id"] and new["version"] == 2
    assert api.get(f"/api/v2/files/{old['id']}?v=1", headers=headers()).content == b"old"
    assert api.get(f"/api/v2/files/{old['id']}?v=2", headers=headers()).content == b"# Report"


@pytest.mark.parametrize("change", [{"extra": True}])
def test_ask_validation(api, change):
    tid = task(api)
    r = api.post(f"/api/v2/tasks/{tid}/comments", json={"text": "Review", "ask": {**ask(), **change}}, headers=headers())
    assert r.status_code == 422, r.text


def test_comments_answers_rights_validation_multiple_answers_and_needs_you(api):
    tid = task(api)
    question = post(api, f"tasks/{tid}/comments", {"text": "Which draft?", "ask": ask("ben")})["comment"]
    assert question["kind"] == "ask" and question["ask"]["who"] == "ben"
    assert get(api, f"tasks/{tid}")["task"]["open_asks"] == 1
    listing = get(api, "tasks")["tasks"]
    assert next(t for t in listing if t["id"] == tid)["open_asks"] == 1
    assert tid in [t["id"] for t in get(api, "needs-you", "ben-test")["items"]]
    body = {"target": {"comment": question["id"]}, "answers": {"verdict": ["Approve"]}}
    assert api.post(f"/api/v2/tasks/{tid}/answers", json=body, headers=headers()).status_code == 403
    with api.app.state.store.transaction() as c:
        restrict(c, "ops", read={"everyone": True}, write={"people": ["ana", "ben"]})
    assert api.post(f"/api/v2/tasks/{tid}/answers", json=body, headers=headers("cara-test")).status_code == 403
    with api.app.state.store.transaction() as c:
        restrict(c, "ops", everyone=True)
    for invalid in ({"verdict": ["Bogus"]}, {"missing": ["Approve"]}, {"verdict": ["Approve", "Request changes"]}):
        assert api.post(f"/api/v2/tasks/{tid}/answers", json={**body, "answers": invalid}, headers=headers("ben-test")).status_code == 422
    answer = post(api, f"tasks/{tid}/answers", body, "ben-test")
    assert answer["comment"]["kind"] == "answer" and 'answered "Is this ready?": Approve.' in answer["comment"]["body"]
    assert answer["comment"]["refs"]["answer"] == answer["answer"]
    post(api, f"tasks/{tid}/answers", {**body, "answers": {"verdict": ["Request changes"]}, "other": "Change the intro."}, "cara-test")
    comments = get(api, f"tasks/{tid}/comments")["comments"]
    assert len(next(m for m in comments if m["id"] == question["id"])["answers"]) == 2
    assert len(get(api, f"tasks/{tid}/answers")["answers"]) == 2
    assert get(api, f"tasks/{tid}")["task"]["open_asks"] == 0


def test_file_answer_wake_structured_and_plain_runner_path(api):
    tid = task(api)
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE humans SET teams_json='[\"engineering\"]' WHERE id='ben'")
    made = attach(api, tid, ask=ask(), note="Read this version")
    fid = made["file_id"]
    body = {"target": {"file": fid, "version": 1}, "answers": {"verdict": ["Approve"]}}
    # The author is Ana; another person with comment rights can answer.
    answer = post(api, f"tasks/{tid}/answers", body, "ben-test")
    assert 'approved "report.md" v1.' in answer["comment"]["body"]
    with api.app.state.store.read() as c:
        msg = H.message(c, answer["comment"]["id"])
        assert msg["to_actor"] == "bot:ops"
        assert c.execute("SELECT 1 FROM jobs WHERE message_id=?", (msg["id"],)).fetchone()
        question = c.execute("SELECT ask_message_id FROM task_file_reviews WHERE file_id=?", (fid,)).fetchone()[0]
        assert H.answers_to(c, [question])[question]["refs"]["answer"] == answer["answer"]
    prompt = Runner.__new__(Runner).prompt({"bot": "ops", "message": msg, "history": [], "conversation": {"id": "c"}})
    assert json.loads(next(line[8:] for line in prompt.splitlines() if line.startswith("answer: "))) == answer["answer"]
    assert prompt.count(json.dumps(msg["refs"]["answer"], ensure_ascii=False)) == 1
    assert msg["body"] in prompt  # old runners still receive the same readable body
    assert get(api, f"tasks/{tid}/files")["files"][0]["versions"][0]["answers"] == [answer["answer"]]
    assert api.patch(f"/api/v2/files/{fid}/versions/1", json={"ask": ask("ben")}, headers=headers()).status_code == 422


def test_plain_ask_and_reply_still_close_through_existing_protocol(api):
    tid = task(api)
    with api.app.state.store.transaction() as c:
        question = H.task_ask(c, "bot:ops", tid, "Which format?")
    reply = post(api, f"tasks/{tid}/comments", {"text": "Markdown."})["comment"]
    with api.app.state.store.read() as c:
        assert H.answers_to(c, [question["id"]])[question["id"]]["id"] == reply["id"]
        assert H.unanswered_ask(c, H.task(c, tid)) is None
    assert get(api, f"tasks/{tid}")["task"]["open_asks"] == 0


def test_review_migration_keeps_existing_version_bytes(api):
    import sqlite3
    tid = task(api)
    fid = attach(api, tid)["file_id"]
    with api.app.state.store.read() as c:
        assert H.MIGRATIONS[20] == H.TASK_REVIEW_SCHEMA
        assert c.execute("PRAGMA user_version").fetchone()[0] >= 21
        assert c.execute("SELECT 1 FROM cloud_migrations WHERE version=54").fetchone()
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            c.execute("UPDATE bot_file_versions SET digest='changed' WHERE file_id=?", (fid,))


def test_cli_comment_uploads_use_distinct_retry_keys(tmp_path, monkeypatch):
    from clients import remotecli
    paths = [tmp_path / name for name in ("a.md", "b.md")]
    for path in paths:
        path.write_text("Draft")
    calls = []
    class Client:
        def __init__(self, *args, **kwargs):
            pass
        def get(self, path):
            return {"actor": "human:ana"}
        def post(self, path, body, key=None):
            calls.append((path, body, key))
            return {"file_id": "file-a", "version": len(calls)} if path.endswith("/files") else {"ok": True}
    monkeypatch.setattr(remotecli, "Client", Client)
    monkeypatch.setenv("HUB_API_URL", "https://example.com")
    monkeypatch.setenv("HUB_OPERATION_ID", "review-operation")
    args = parser().parse_args(["task", "comment", "t1", "Review", "--attach", str(paths[0]), "--attach", str(paths[1]),
                                "--choices", "A,B"])
    assert remotecli.run(args) == {"ok": True}
    assert len(set(c[2] for c in calls)) == 3
    assert calls[-1][1]["attachments"] == ["file-a@1", "file-a@2"]
    assert calls[-1][1]["ask"]["questions"][0]["options"] == [{"label": "A"}, {"label": "B"}]


def test_requester_bot_can_open_person_uploads_and_adopted_legacy_ids(api):
    from backend.auth import Identity
    from backend.blobs import register
    machine = runner(api)
    assign(api, machine, "coo")
    ready(api, machine, ["coo"])
    post(api, "tasks", {"owner": "coo", "title": "Review a draft", "body": "Read the report."})
    token = claim(api, machine, "coo")["token"]
    tid = post(api, "tasks", {"owner": "ops", "title": "Review the draft", "body": "Read the report."}, token)["id"]
    made = attach(api, tid)
    assert api.get(made["link"], headers=headers(token)).content == b"# Report"
    digest = api.app.state.blobs.put(b"old")
    with api.app.state.store.transaction() as c:
        old = register(c, Identity("human:ana", "owner"), digest, 3, "legacy.md", "text/markdown")
        c.execute("INSERT INTO task_assets VALUES(?,?)", (tid, old["id"]))
    adopted = attach(api, tid, name="legacy.md")
    assert adopted["file_id"] == old["id"]
    for number, data in ((1, b"old"), (2, b"# Report")):
        response = api.get(f"/api/v2/files/{old['id']}?v={number}", headers=headers(token))
        assert response.status_code == 200 and response.content == data
    # Legacy blob ownership remains readable after a series changes scope.
    with api.app.state.store.transaction() as c:
        private = H.open_conversation(c, "human:ben", ["bot:ops"], scope="personal", room_key="ops")
        c.execute("UPDATE bot_files SET scope=?,bot='inbox' WHERE id=?",
                  ("conversation:" + private["id"], old["id"]))
        from backend.store import Problem
        with pytest.raises(Problem):
            api.app.state.files.visible_file(c, Identity("human:ana", "owner"), old["id"])
    assert api.get(f"/api/v2/files/{old['id']}?v=1", headers=headers()).content == b"old"
    assert get(api, f"files/{old['id']}/meta?v=1")["name"] == "legacy.md"
    assert api.get(f"/api/v2/files/{old['id']}?v=2", headers=headers(token)).status_code == 404


@pytest.mark.parametrize("existing", [True])
def test_attach_requires_comment_rights_even_without_question(api, existing):
    tid = task(api)
    if existing:
        attach(api, tid)
    with api.app.state.store.transaction() as c:
        restrict(c, "ops", see={"people": ["cara", "ana"]}, read={"people": ["cara", "ana"]},
                 write={"people": ["ana"]})
    seen = get(api, f"tasks/{tid}", "cara-test")
    assert seen["task"]["id"] == tid and seen["can_comment"] is False
    assert get(api, f"tasks/{tid}")["can_comment"] is True
    response = api.post(f"/api/v2/tasks/{tid}/files", json={"name": "report.md", "text": "Draft"},
                        headers=headers("cara-test"))
    assert response.status_code == 403
    assert len(get(api, f"tasks/{tid}/files")["files"]) == int(existing)


@pytest.mark.parametrize("role", ["other", "requester"])
def test_answer_uses_comment_wake_rule(api, role):
    tid = task(api)
    question = post(api, f"tasks/{tid}/comments", {"text": "Review", "ask": ask("cara")})["comment"]
    token = "cara-test"
    with api.app.state.store.transaction() as c:
        if role == "mover":
            c.execute("UPDATE humans SET teams_json='[\"engineering\"]' WHERE id='cara'")
        elif role in ("owner", "requester"):
            c.execute(f"UPDATE tasks SET {role}='human:cara' WHERE id=?", (tid,))
    if role == "bot":
        machine = runner(api)
        assign(api, machine, "ops")
        ready(api, machine, ["ops"])
        token = claim(api, machine, "ops")["token"]
    answer = post(api, f"tasks/{tid}/answers", {"target": {"comment": question["id"]},
                                              "answers": {"verdict": ["Approve"]}}, token)
    assert answer["woke"] == (role != "other")
    assert bool(answer["comment"]["refs"].get("quiet")) == (role == "other")
    with api.app.state.store.read() as c:
        job = c.execute("SELECT 1 FROM jobs WHERE message_id=?", (answer["comment"]["id"],)).fetchone()
        assert bool(job) == (role in ("mover", "requester"))


@pytest.mark.parametrize("status", ["cancelled"])
def test_needs_you_excludes_inactive_tasks_with_structured_asks(api, status):
    tid = task(api)
    post(api, f"tasks/{tid}/comments", {"text": "Review", "ask": ask("ben")})
    assert tid in [t["id"] for t in get(api, "needs-you", "ben-test")["items"]]
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE tasks SET status=? WHERE id=?", (status, tid))
    assert tid not in [t["id"] for t in get(api, "needs-you", "ben-test")["items"]]


def test_readable_answer_escapes_interpolated_text_and_classifies_only_other(api):
    from backend.task_review import readable_text
    assert readable_text("www.example.com ftp://example.com") == "www&#46;example.com ftp&#58;//example.com"
    tid = task(api)
    choice = ask("ben")
    label = "[Open](https://example.com) @ops **yes** secrets/plan"
    choice["questions"][0]["question"] = "Read [this](https://example.com) @ops **now**?"
    choice["questions"][0]["options"] = [{"label": label}]
    question = post(api, f"tasks/{tid}/comments", {"text": "Review", "ask": choice})["comment"]
    body = {"target": {"comment": question["id"]}, "answers": {"verdict": [label]}}
    saved = post(api, f"tasks/{tid}/answers", body, "ben-test")
    assert readable_text(label) in saved["comment"]["body"]
    assert readable_text(choice["questions"][0]["question"]) in saved["comment"]["body"]
    assert "&#64;ops" in saved["comment"]["body"] and r"\[Open\]\(https&#58;//example.com\)" in saved["comment"]["body"]
    assert saved["answer"]["answers"] == {"verdict": [label]}
    response = api.post(f"/api/v2/tasks/{tid}/answers", json={**body, "other": "Read secrets/plan"},
                        headers=headers("ben-test"))
    assert response.status_code == 403

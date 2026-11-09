"""Files on a task (version names, scopes, archive, legacy attachments) and the readable text of a question task's
answer."""

import pytest

from backend.tests.helpers.api import assign, claim, headers, post, ready, restrict, runner  # noqa: F401
from backend import question_tasks as QT
from backend.store import H
from clients.hubcli import parser
from backend.tests.helpers.task_review import get, task


def card(api, tid, who="ben", question="Is this ready?", options=("Approve", "Request changes")):
    """A question task the bot filed for `who` under task `tid`, with one verdict question."""
    with api.app.state.store.transaction() as c:
        made = H.task_create(c, "bot:ops", "Which draft?", "Which draft?", "human:" + who, parent_id=tid, lint=False)
        QT.store(
            c,
            made["id"],
            {
                "questions": [
                    {
                        "id": "verdict",
                        "header": "Review",
                        "question": question,
                        "why": "",
                        "multi": False,
                        "other": True,
                        "options": [{"label": o, "then": "Go with " + o} for o in options],
                    }
                ]
            },
        )
    return made["id"]


def attach(api, tid, name="report.md", **fields):
    return post(api, f"tasks/{tid}/files", {"name": name, "text": "# Report", **fields})


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
    assert api.patch(path, json={"note": "Changed"}, headers=headers()).status_code == 200
    assert api.patch(path, json={"note": "No"}, headers=headers("cara-test")).status_code == 403
    assert api.patch(path, json={"note": "x" * 501}, headers=headers()).status_code == 422
    assert api.patch(f"/api/v2/files/{fid}", json={"archived": True}, headers=headers()).status_code == 200
    assert attach(api, tid)["file_id"] != fid


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
    args = parser().parse_args(
        ["task", "comment", "t1", "Review", "--attach", str(paths[0]), "--attach", str(paths[1])]
    )
    assert remotecli.run(args) == {"ok": True}
    assert len(set(c[2] for c in calls)) == 3
    assert calls[-1][1]["attachments"] == ["file-a@1", "file-a@2"]
    assert "ask" not in calls[-1][1]  # asks are gone: a comment carries no question


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
        c.execute(
            "UPDATE bot_files SET scope=?,bot_id='inbox' WHERE id=?", ("conversation:" + private["id"], old["id"])
        )
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
        restrict(c, "ops", see={"people": ["cara", "ana"]}, read={"people": ["cara", "ana"]}, write={"people": ["ana"]})
    seen = get(api, f"tasks/{tid}", "cara-test")
    assert seen["task"]["id"] == tid and seen["can_comment"] is False
    assert get(api, f"tasks/{tid}")["can_comment"] is True
    response = api.post(
        f"/api/v2/tasks/{tid}/files", json={"name": "report.md", "text": "Draft"}, headers=headers("cara-test")
    )
    assert response.status_code == 403
    assert len(get(api, f"tasks/{tid}/files")["files"]) == int(existing)


def test_done_news_skips_a_bot_that_cannot_read_the_task(api):
    parent = post(api, "tasks", {"owner": "ops", "title": "Plan the launch week", "body": "All of it."})["id"]
    child = post(
        api,
        "tasks",
        {
            "owner": "inbox",
            "title": "Draft the launch mail",
            "body": "One page.",
            "relations": [{"task": parent, "kind": "parent"}],
        },
    )["id"]
    version = get(api, f"tasks/{child}")["task"]["version"]
    post(api, f"tasks/{child}", {"version": version, "status": "done", "note": "Sent the draft."})
    with api.app.state.store.read() as c:
        told = {
            r[0]
            for r in c.execute(
                "SELECT recipient_id FROM messages WHERE body LIKE '% marked this done:%' "
                "AND json_extract(refs,'$.task')=?",
                (child,),
            )
        }
    assert "bot:inbox" in told and "bot:ops" not in told, told


@pytest.mark.parametrize("status", ["done", "closed", "declined"])
def test_needs_you_excludes_inactive_question_tasks(api, status):
    tid = task(api)
    card_id = card(api, tid)
    assert card_id in [t["id"] for t in get(api, "needs-you", "ben-test")["items"]]
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE tasks SET status=? WHERE id=?", (status, card_id))
    assert card_id not in [t["id"] for t in get(api, "needs-you", "ben-test")["items"]]


def test_readable_answer_escapes_interpolated_text_and_classifies_only_other(api):
    from backend.task_review import readable_text

    assert readable_text("www.example.com ftp://example.com") == "www&#46;example.com ftp&#58;//example.com"
    label = "[Open](https://example.com) @ops **yes** secrets/plan"
    question = "Read [this](https://example.com) @ops **now**?"
    card_id = card(api, task(api), question=question, options=(label,))
    body = {"target": {"task": card_id}, "answers": {"verdict": [label]}}
    saved = post(api, f"tasks/{card_id}/answers", body, "ben-test")
    text = saved["answer"]["text"]
    assert readable_text(label) in text and readable_text(question) in text
    assert "&#64;ops" in text and r"\[Open\]\(https&#58;//example.com\)" in text
    assert saved["answer"]["answers"] == {"verdict": [label]}

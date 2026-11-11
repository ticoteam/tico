"""Copied labels, archive provenance and digest reuse follow the page's current visibility."""

import importlib
import json

import pytest

from backend import files
from backend import task_asks
from backend.auth import Identity
from backend.store import H
from backend.tests.old_asks import with_refs
from backend.tests.helpers.api import get, headers, post  # noqa: F401
from backend.tests.helpers.file_reviews import answer as review_answer, approved, ask as review_ask
from backend.tests.helpers.page_audience import edit
from backend.tests.helpers.pages import HTML, page
from backend.tests.helpers.page_audience_review4 import public_page


def test_blob_archive_history_keeps_the_page_id_for_old_filename(api):
    tid, _, fid = public_page(api)
    uploaded = post(api, f"tasks/{tid}/files", {"name": "weekly.html", "text": HTML.decode() + "<p>Second version</p>"})
    assert uploaded["file_id"] == fid and uploaded["version"] == 2
    with api.app.state.store.read() as c:
        bid = c.execute("SELECT blob_id FROM bot_file_versions WHERE file_id=? AND version=2", (fid,)).fetchone()[0]
    assert (
        api.patch("/api/v2/files/" + fid, json={"title": "Renamed confidential draft"}, headers=headers()).status_code
        == 200
    )
    edit(api, fid, {"people": ["ben"]})
    archived = api.patch("/api/v2/files/" + bid, json={"archived": True}, headers=headers())
    assert archived.status_code == 200, archived.text
    with api.app.state.store.read() as c:
        row = c.execute(
            "SELECT * FROM history_events WHERE subject_id=? AND kind='file' AND old='weekly.html'", (tid,)
        ).fetchone()
        assert json.loads(row["data"])["page_files"] == [fid]
    for token in ("cara-test", "finance-test"):
        assert "weekly.html" not in json.dumps(get(api, "tasks/" + tid, token=token)["events"])
        assert (
            post(
                api,
                "sql",
                {"sql": f"SELECT * FROM history_events WHERE subject_id='{tid}' AND kind='file'"},
                token=token,
            )["rows"]
            == []
        )


@pytest.mark.parametrize("ordinary", [False, True])
def test_identical_recreation_can_be_opened_and_reviewed(api, ordinary):
    tid, attempt, fid = public_page(api)
    edit(api, fid, {"people": ["ben"]})
    assert api.delete("/api/v2/files/" + fid, headers=headers()).status_code == 200
    if ordinary:
        new = post(api, f"tasks/{tid}/files", {"name": "replacement.txt", "text": HTML.decode()})["file_id"]
    else:
        recreated = page(api, attempt, task=tid, title="Replacement")
        assert recreated.status_code == 200, recreated.text
        new = recreated.json()["page"]["file_id"]
        assert get(api, "pages/" + new)["page"]["file_id"] == new
    assert new != fid
    made = review_ask(api, "ops-test", [{"file": new}], on="ana", parent=tid)
    review_answer(api, made, "Approve")
    assert approved(api, "ana-test", file=new)["approved"] is True
    with api.app.state.store.read() as c:
        viewer = Identity("human:ana", "owner")
        assert task_asks.grant(c, made["requirement_id"])["subject"] == f"file:{new}@1"
        assert not files.page_ref_denials(c, api.app.state.auth, viewer, {"file": new})
        assert files.page_ref_denials(c, api.app.state.auth, viewer, fid) == {fid: "A deleted page"}


def test_migration_links_both_old_and_new_history_values(api):
    tid, _, fid = public_page(api)
    migration = importlib.import_module("backend.migrations.20261105071056_page_history_sources")
    with api.app.state.store.transaction() as c:
        ids = [
            H.history_event(c, "task", tid, "bot:ops", "file", **values)
            for values in ({"old": "weekly.html"}, {"new": "weekly.html"}, {"old": "weekly.html", "new": "Renamed"})
        ]
        migration.up(c)
        migration.up(c)
        for event_id in ids:
            assert json.loads(c.execute("SELECT data FROM history_events WHERE id=?", (event_id,)).fetchone()[0])[
                "page_files"
            ] == [fid]


@pytest.mark.parametrize("pinned", [False, True])
def test_legacy_exact_title_labels_are_task_local_and_survive_title_purge(api, pinned):
    tid, _, fid = public_page(api)
    if pinned:
        tid = post(api, "tasks", {"owner": "ops", "title": "Pinned draft", "body": "Review", "private": False})["id"]
        post(api, "pins", {"place": "task:" + tid, "item": "page:" + fid})
    # Old comments that carry a question (read as history; nothing writes one now), seeded as an old file holds them.
    questions = [
        {
            "id": "verdict",
            "header": "Review",
            "question": "Pick one",
            "options": [{"label": "Confidential draft"}, {"label": "Other"}],
            "multi": False,
        }
    ]
    comment = post(api, f"tasks/{tid}/comments", {"text": "Pick one"})["comment"]
    unrelated = post(api, "tasks", {"owner": "ops", "title": "Unrelated", "body": "Review", "private": False})["id"]
    other = post(api, f"tasks/{unrelated}/comments", {"text": "Pick one"})["comment"]
    with api.app.state.store.transaction() as c:
        with_refs(c, comment["id"], questions=questions)
        with_refs(c, other["id"], questions=questions)
    edit(api, fid, {"people": ["ben"]})
    for deleted in (False, True):
        if deleted:
            assert api.delete("/api/v2/files/" + fid, headers=headers()).status_code == 200
        assert "Confidential draft" not in json.dumps(get(api, "tasks/" + tid, token="cara-test"))
        assert "Confidential draft" in json.dumps(get(api, "messages/" + other["id"], token="cara-test"))
        with api.app.state.store.read() as c:
            shown = files.readable_page_refs(
                c, api.app.state.auth, Identity("human:cara", "human"), H.message(c, comment["id"]), task_id=tid
            )
            assert "Confidential draft" not in json.dumps(shown)


def test_identical_content_in_another_task_resolves_its_own_live_review(api):
    _, attempt, fid = public_page(api)
    with api.app.state.store.read() as c:
        digest = c.execute("SELECT source_digest FROM bot_file_versions WHERE file_id=?", (fid,)).fetchone()[0]
    assert api.delete("/api/v2/files/" + fid, headers=headers()).status_code == 200
    tid = post(api, "tasks", {"owner": "ops", "title": "Independent review", "body": "Review", "private": False})["id"]
    made = page(api, attempt, task=tid, name="independent.html", title="Independent draft")
    assert made.status_code == 200, made.text
    new = made.json()["page"]["file_id"]
    question = review_ask(api, "ops-test", [{"file": new}], on="ana", parent=tid)
    review_answer(api, question, "Approve")
    assert approved(api, "ana-test", file=new)["approved"] is True
    with api.app.state.store.read() as c:
        # A bare saved digest has no independent live identity in the deleted page's task.
        assert files.page_ref_denials(c, api.app.state.auth, Identity("human:ana", "owner"), digest) == {
            fid: "A deleted page"
        }

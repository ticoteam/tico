"""Copied labels, archive provenance and digest reuse follow the page's current visibility."""
import importlib
import json

import pytest

from backend import files
from backend.auth import Identity
from backend.store import H
from backend.tests.test_api import api, get, headers, post  # noqa: F401
from backend.tests.test_file_reviews import ask as review_ask, answer as review_answer
from backend.tests.test_files import turn
from backend.tests.test_org_rules import org
from backend.tests.test_page_audience import edit
from backend.tests.test_pages import HTML, page
from backend.tests.test_task_review import ask


def public_page(api):
    org(api)
    _, attempt = turn(api)
    tid = post(api, "tasks", {"owner": "ops", "title": "Review drafts", "body": "Review", "private": False})["id"]
    made = page(api, attempt, task=tid, title="Confidential draft")
    assert made.status_code == 200, made.text
    return tid, attempt, made.json()["page"]["file_id"]


def test_sibling_file_review_options_and_copied_answers_are_scrubbed(api):
    tid, _, fid = public_page(api)
    question = ask("ben")
    question["questions"][0]["options"][0].update(label="Confidential draft", file=fid + "@1")
    sibling = post(api, f"tasks/{tid}/files", {"name": "ordinary.txt", "text": "Public notes", "ask": question})
    post(api, f"tasks/{tid}/answers", {"target": {"file": sibling["file_id"], "version": 1},
                                      "answers": {"verdict": ["Confidential draft"]}}, token="ben-test")
    # Leave a second ordinary review open to exercise its raw question projection too.
    post(api, f"tasks/{tid}/files", {"name": "sibling.txt", "text": "Other notes", "ask": question})
    with api.app.state.store.read() as c:
        messages = [H._json(r[0], {}) for r in c.execute("SELECT refs FROM messages WHERE json_extract(refs,'$.task')=?", (tid,))]
        assert any(m.get("answer", {}).get("label_sources") ==
                   {"verdict": {"Confidential draft": {"file": fid, "version": 1}}} for m in messages)
        assert any(m.get("body_sources") == [{"file": fid, "version": 1},
                                             {"file": sibling["file_id"], "version": 1}] for m in messages)
    edit(api, fid, {"people": ["ben"]})
    for deleted in (False, True):
        if deleted:
            assert api.delete("/api/v2/files/" + fid, headers=headers()).status_code == 200
        for token in ("cara-test", "finance-test"):
            for path in ("tasks/" + tid, f"tasks/{tid}/files", f"tasks/{tid}/comments", f"tasks/{tid}/answers"):
                shown = json.dumps(get(api, path, token=token))
                assert fid not in shown and "Confidential draft" not in shown, (path, shown)


def test_real_pick_labels_and_generated_answer_bodies_retain_sources(api):
    tid, _, fid = public_page(api)
    ordinary = post(api, f"tasks/{tid}/files", {"name": "public.txt", "text": "Public choice"})["file_id"]
    made = review_ask(api, "ops-test", [{"file": fid}, {"file": ordinary}], on="ben", parent=tid, private=False, pick=True)
    option = made["question"]["refs"]["questions"][0]["options"][0]
    assert option["source"] == {"file": fid, "version": 1}
    answered = review_answer(api, made, [option["label"]], token="ben-test")
    assert answered["comment"]["refs"]["body_sources"] == [{"file": fid, "version": 1}]
    edit(api, fid, {"people": ["ben"]})
    for deleted in (False, True):
        if deleted:
            assert api.delete("/api/v2/files/" + fid, headers=headers()).status_code == 200
        for token in ("cara-test", "finance-test"):
            for path in ("tasks", "tasks/" + made["task"]["id"], "tasks/" + made["task"]["id"] + "/comments"):
                shown = json.dumps(get(api, path, token=token))
                assert fid not in shown and option["label"] not in shown, (path, shown)


def test_blob_archive_history_keeps_the_page_id_for_old_filename(api):
    tid, _, fid = public_page(api)
    uploaded = post(api, f"tasks/{tid}/files", {"name": "weekly.html", "text": HTML.decode() + "<p>Second version</p>"})
    assert uploaded["file_id"] == fid and uploaded["version"] == 2
    with api.app.state.store.read() as c:
        bid = c.execute("SELECT blob_id FROM bot_file_versions WHERE file_id=? AND version=2", (fid,)).fetchone()[0]
    assert api.patch("/api/v2/files/" + fid, json={"title": "Renamed confidential draft"}, headers=headers()).status_code == 200
    edit(api, fid, {"people": ["ben"]})
    archived = api.patch("/api/v2/files/" + bid, json={"archived": True}, headers=headers())
    assert archived.status_code == 200, archived.text
    with api.app.state.store.read() as c:
        row = c.execute("SELECT * FROM history_events WHERE subject_id=? AND kind='file' AND old='weekly.html'", (tid,)).fetchone()
        assert json.loads(row["data"])["page_files"] == [fid]
    for token in ("cara-test", "finance-test"):
        assert "weekly.html" not in json.dumps(get(api, "tasks/" + tid, token=token)["events"])
        assert post(api, "sql", {"sql": f"SELECT * FROM history_events WHERE subject_id='{tid}' AND kind='file'"}, token=token)["rows"] == []


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
    made = review_ask(api, "ops-test", [{"file": new}], on="ana", parent=tid, private=False)
    assert made["question"]["refs"]["review"]["files"][0]["file"] == new
    result = review_answer(api, made, ["Approve"])
    assert result["answer"]["reviewed"][0]["file"] == new
    with api.app.state.store.read() as c:
        viewer = Identity("human:ana", "owner")
        assert not files.page_ref_denials(c, api.app.state.auth, viewer, made["question"]["refs"])
        assert files.page_ref_denials(c, api.app.state.auth, viewer, fid) == {fid: "A deleted page"}


def test_migration_links_both_old_and_new_history_values(api):
    tid, _, fid = public_page(api)
    migration = importlib.import_module("backend.migrations.20261105071056_page_history_sources")
    with api.app.state.store.transaction() as c:
        ids = [H.history_event(c, "task", tid, "bot:ops", "file", **values)
               for values in ({"old": "weekly.html"}, {"new": "weekly.html"},
                              {"old": "weekly.html", "new": "Renamed"})]
        migration.up(c)
        migration.up(c)
        for event_id in ids:
            assert json.loads(c.execute("SELECT data FROM history_events WHERE id=?", (event_id,)).fetchone()[0])["page_files"] == [fid]


@pytest.mark.parametrize("pinned", [False, True])
def test_legacy_exact_title_labels_are_task_local_and_survive_title_purge(api, pinned):
    tid, _, fid = public_page(api)
    if pinned:
        tid = post(api, "tasks", {"owner": "ops", "title": "Pinned draft", "body": "Review", "private": False})["id"]
        post(api, "pins", {"place": "task:" + tid, "item": "page:" + fid})
    question = ask("ben")
    question["questions"][0]["options"][0]["label"] = "Confidential draft"
    comment = post(api, f"tasks/{tid}/comments", {"text": "Pick one", "ask": question})["comment"]
    unrelated = post(api, "tasks", {"owner": "ops", "title": "Unrelated", "body": "Review", "private": False})["id"]
    other = post(api, f"tasks/{unrelated}/comments", {"text": "Pick one", "ask": question})["comment"]
    edit(api, fid, {"people": ["ben"]})
    for deleted in (False, True):
        if deleted:
            assert api.delete("/api/v2/files/" + fid, headers=headers()).status_code == 200
        assert "Confidential draft" not in json.dumps(get(api, "tasks/" + tid, token="cara-test"))
        assert "Confidential draft" in json.dumps(get(api, "messages/" + other["id"], token="cara-test"))
        with api.app.state.store.read() as c:
            shown = files.readable_page_refs(c, api.app.state.auth, Identity("human:cara", "human"),
                                             H.message(c, comment["id"]), task_id=tid)
            assert "Confidential draft" not in json.dumps(shown)

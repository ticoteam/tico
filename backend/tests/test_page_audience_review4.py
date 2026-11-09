"""Copied labels, archive provenance and digest reuse follow the page's current visibility."""

import importlib
import json

import pytest

from backend import files
from backend.auth import Identity
from backend.store import H
from backend.tests.old_asks import with_refs
from backend.tests.helpers.api import get, headers, post  # noqa: F401
from backend.tests.test_api import api  # noqa: F401
from backend.tests.helpers.file_reviews import ask as review_ask, answer as review_answer
from backend.tests.helpers.page_audience import edit
from backend.tests.helpers.pages import HTML, page
from backend.tests.helpers.question_tasks import keep_asking
from backend.tests.helpers.page_audience_review4 import public_page


def test_real_pick_labels_and_generated_answer_bodies_retain_sources(api):
    tid, _, fid = public_page(api)
    ordinary = post(api, f"tasks/{tid}/files", {"name": "public.txt", "text": "Public choice"})["file_id"]
    made = review_ask(
        api, "ops-test", [{"file": fid}, {"file": ordinary}], on="ben", parent=tid, private=False, pick=True
    )
    option = made["question"]["refs"]["questions"][0]["options"][0]
    assert option["source"] == {"file": fid, "version": 1}
    answered = review_answer(api, made, [option["label"]], token="ben-test")
    assert answered["answer"]["body_sources"] == [{"file": fid, "version": 1}]
    edit(api, fid, {"people": ["ben"]})
    for deleted in (False, True):
        if deleted:
            assert api.delete("/api/v2/files/" + fid, headers=headers()).status_code == 200
        for token in ("cara-test", "finance-test"):
            for path in (
                "tasks",
                "tasks/" + made["task"]["id"],
                "tasks/" + made["task"]["id"] + "/comments",
                "tasks/" + made["task"]["id"] + "/answers",
            ):
                shown = json.dumps(get(api, path, token=token))
                assert fid not in shown and option["label"] not in shown, (path, shown)


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


@pytest.mark.parametrize("kind,outcome", [("reversible", "went_ahead"), ("send", "expired")])
def test_automatic_defaults_keep_label_and_body_provenance(api, kind, outcome):
    from backend import ask_deadlines

    keep_asking(api)
    tid, _, fid = public_page(api)
    ordinary = post(api, f"tasks/{tid}/files", {"name": "public.txt", "text": "Public choice"})["file_id"]
    made = review_ask(
        api,
        "ops-test",
        [{"file": fid}, {"file": ordinary}],
        on="ben",
        parent=tid,
        private=False,
        pick=True,
        if_no_answer={"do": "Confidential draft", "kind": kind, "by": H.shift(H.now(), days=1)},
    )
    assert made["question"]["refs"]["if_no_answer"]["source"] == {"file": fid, "version": 1}
    keep_asking(api, on=True)
    with api.app.state.store.transaction() as c:
        settled = ask_deadlines.sweep(c, H.shift(H.now(), days=6))
        assert made["question"]["id"] in settled[outcome]
        # A question task: the keeper's answer is on the task, and what its close wrote keeps the page as its source.
        closed = c.execute(
            "SELECT data FROM history_events WHERE subject_id=? AND kind='status' ORDER BY rowid DESC",
            (made["task"]["id"],),
        ).fetchone()
        assert H._json(closed["data"], {})["body_sources"] == [{"file": fid, "version": 1}]
        if outcome == "went_ahead":
            answer = H._json(H.task(c, made["task"]["id"])["answer"], {})
            assert answer["body_sources"] == [{"file": fid, "version": 1}]
            assert answer["label_sources"] == {
                "decision": {
                    "Confidential draft": {"file": fid, "version": 1},
                    "public": {"file": ordinary, "version": 1},
                }
            }
    edit(api, fid, {"people": ["ben"]})
    for deleted in (False, True):
        if deleted:
            assert api.delete("/api/v2/files/" + fid, headers=headers()).status_code == 200
        for token in ("cara-test", "finance-test"):
            shown = json.dumps(get(api, "tasks/" + made["task"]["id"], token=token))
            assert fid not in shown and "Confidential draft" not in shown, shown


def test_identical_content_in_another_task_resolves_its_own_live_review(api):
    _, attempt, fid = public_page(api)
    with api.app.state.store.read() as c:
        digest = c.execute("SELECT source_digest FROM bot_file_versions WHERE file_id=?", (fid,)).fetchone()[0]
    assert api.delete("/api/v2/files/" + fid, headers=headers()).status_code == 200
    tid = post(api, "tasks", {"owner": "ops", "title": "Independent review", "body": "Review", "private": False})["id"]
    made = page(api, attempt, task=tid, name="independent.html", title="Independent draft")
    assert made.status_code == 200, made.text
    new = made.json()["page"]["file_id"]
    question = review_ask(api, "ops-test", [{"file": new}], on="ana", parent=tid, private=False)
    assert review_answer(api, question, ["Approve"])["answer"]["reviewed"][0]["file"] == new
    with api.app.state.store.read() as c:
        # A bare saved digest has no independent live identity in the deleted page's task.
        assert files.page_ref_denials(c, api.app.state.auth, Identity("human:ana", "owner"), digest) == {
            fid: "A deleted page"
        }


def test_review_defaults_are_scrubbed_on_the_recipients_needs_you(api):
    keep_asking(api)
    tid, _, fid = public_page(api)
    ordinary = post(api, f"tasks/{tid}/files", {"name": "public.txt", "text": "Public choice"})["file_id"]
    review_ask(
        api,
        "ops-test",
        [{"file": fid}, {"file": ordinary}],
        on="cara",
        parent=tid,
        private=False,
        pick=True,
        if_no_answer={"do": "Confidential draft", "kind": "reversible", "by": H.shift(H.now(), days=1)},
    )
    assert "Confidential draft" in json.dumps(get(api, "needs-you", token="cara-test"))
    edit(api, fid, {"people": ["ben"]})
    for deleted in (False, True):
        if deleted:
            assert api.delete("/api/v2/files/" + fid, headers=headers()).status_code == 200
        for path in ("needs-you", "needs-you/summary", "tasks/" + tid):
            shown = json.dumps(get(api, path, token="cara-test"))
            assert fid not in shown and "Confidential draft" not in shown, (path, shown)

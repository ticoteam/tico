"""A stage approval names a task document (task_requirements.APPROVED): the bot writing that document while the task is
in the stage submits it, the decision model checks that version, and a person approves it with the key (visit, document
version). A stale key is refused with the version submitted now. The decision model is a recorded verdict, never a
model. Also the migration that names the documents of existing approvals and retires Pitch."""

import importlib
import json

import pytest

from backend import task_engine as E
from backend import task_requirements as R
from backend.store import H
from backend.tests.helpers import engine as X
from backend.tests.helpers.api import get, headers, post
from backend.tests.helpers.files import turn
from backend.tests.helpers.page_requirement import _say

ANA, OPS = X.ANA, "bot:ops"
SPEC = "<h1>Export spec</h1><p>Rows export as CSV.</p>"


def _flow(api, document="Spec"):
    """A pipeline whose first stage approves the task's `document` (any person), then Build; a task there, owned by
    bot:ops."""
    stages = (
        {
            "name": "Spec",
            "owner": OPS,
            "requirements": [
                {
                    "kind": "page",
                    "line": document,
                    "settings": {"document": document, "rubric": "States the goal."},
                    "who_oks": "people_only",
                }
            ],
        },
        {"name": "Build", "owner": OPS},
    )
    with api.app.state.store.transaction() as c:
        made = X.pipeline(c, "approvals", "Approvals", stages)
        row = X.new_task(c, ANA, "Export", "approvals", assignee=OPS)
    return made, row


def _write(api, tid, text, token, document="Spec"):
    return post(api, f"tasks/{tid}/files", {"document": document, "text": text}, token)


def _verdict(api, tid, verdict, failed=False):
    """The decision model's verdict on the version pinned now."""
    with api.app.state.store.transaction() as c:
        page = c.execute(
            "SELECT p.* FROM requirement_pages p JOIN visits v ON v.id = p.visit_id AND v.left_at IS NULL "
            "WHERE v.task_id=? AND p.replaced_at IS NULL",
            (tid,),
        ).fetchone()
        _say(c, dict(page), verdict, "The goal is missing." if verdict == "not_met" else "", failed=failed)


def _standing(api, tid):
    with api.app.state.store.transaction() as c:
        (standing,) = R.standings(c, E.task(c, tid))
        return standing


def _entry(api, tid):
    with api.app.state.store.transaction() as c:
        (entry,) = H.task_view(c, tid, ANA)["to_leave"]
        return entry


def _approve(api, tid, entry, version, token="ana-test"):
    approval = entry["approval"]
    return api.post(
        f"/api/v2/tasks/{tid}/asks/{entry['requirement_id']}/answer",
        json={"value": "ok", "page_version": f"{approval['file_id']}@{version}", "visit_id": approval["visit_id"]},
        headers=headers(token),
    )


def test_writing_the_document_submits_it_and_the_approval_carries_its_key(api):
    _, row = _flow(api)
    _, attempt = turn(api)
    written = _write(api, row["id"], SPEC, attempt["token"])
    assert written["submitted"]["version"] == 1
    assert _standing(api, row["id"]).on != ANA  # the decision model checks it first

    _verdict(api, row["id"], "met")
    entry = _entry(api, row["id"])
    assert entry["line"] == "Spec"
    assert entry["approval"] == {
        "document": "Spec",
        "visit_id": row["visit"]["id"],
        "file_id": written["file_id"],
        "version": 1,
        "unchecked": False,
    }
    assert _approve(api, row["id"], entry, 1).status_code == 200
    assert get(api, "tasks/" + row["id"])["task"]["stage_id"] == "approvals-build"


def test_a_stale_key_is_refused_with_the_newer_version_and_the_approval_moves_to_it(api):
    _, row = _flow(api)
    _, attempt = turn(api)
    _write(api, row["id"], SPEC, attempt["token"])
    _verdict(api, row["id"], "met")
    shown = _entry(api, row["id"])

    _write(api, row["id"], SPEC + "<p>Dates are UTC.</p>", attempt["token"])
    refused = _approve(api, row["id"], shown, 1)
    assert refused.status_code == 409, refused.text
    body = refused.json()
    assert body["approval"]["version"] == 2 and body["approval"]["document"] == "Spec"
    assert get(api, "tasks/" + row["id"])["task"]["stage_id"] == "approvals-spec"

    _verdict(api, row["id"], "met")
    entry = _entry(api, row["id"])
    assert entry["approval"]["version"] == 2
    assert _approve(api, row["id"], entry, 2).status_code == 200


def test_a_key_from_another_visit_is_refused(api):
    _, row = _flow(api)
    _, attempt = turn(api)
    _write(api, row["id"], SPEC, attempt["token"])
    _verdict(api, row["id"], "met")
    entry = _entry(api, row["id"])
    other = dict(entry, approval=dict(entry["approval"], visit_id=H.new_id()))
    assert _approve(api, row["id"], other, 1).status_code == 409


def test_an_approval_without_its_version_is_refused(api):
    _, row = _flow(api)
    _, attempt = turn(api)
    _write(api, row["id"], SPEC, attempt["token"])
    _verdict(api, row["id"], "met")
    entry = _entry(api, row["id"])
    r = api.post(
        f"/api/v2/tasks/{row['id']}/asks/{entry['requirement_id']}/answer",
        json={"value": "ok"},
        headers=headers(),
    )
    assert r.status_code >= 400 and get(api, "tasks/" + row["id"])["task"]["stage_id"] == "approvals-spec"


def test_a_first_fail_goes_back_to_the_bot_silently_and_a_second_reaches_the_person(api):
    _, row = _flow(api, "QA report")
    _, attempt = turn(api)
    _write(api, row["id"], SPEC, attempt["token"], "QA report")
    _verdict(api, row["id"], "not_met")
    first = _standing(api, row["id"])
    assert (first.state, first.on) == (R.NOT_MET, OPS)

    _write(api, row["id"], SPEC + "<p>Before and after.</p>", attempt["token"], "QA report")
    _verdict(api, row["id"], "not_met")
    second = _standing(api, row["id"])
    assert (second.state, second.on, second.line) == (R.WAITING, None, "QA report")  # any person
    assert any("Decision model" in notice for notice in second.notices)


def test_the_spec_check_only_advises_the_person(api):
    _, row = _flow(api)
    _, attempt = turn(api)
    _write(api, row["id"], SPEC, attempt["token"])
    _verdict(api, row["id"], "not_met")
    standing = _standing(api, row["id"])
    assert (standing.state, standing.on, standing.line) == (R.WAITING, None, "Spec")
    assert standing.notices == ("Decision model: The goal is missing.",)


def test_a_check_that_cannot_run_reaches_the_person_marked_not_checked(api):
    _, row = _flow(api)
    _, attempt = turn(api)
    _write(api, row["id"], SPEC, attempt["token"])
    _verdict(api, row["id"], "unsure", failed=True)
    entry = _entry(api, row["id"])
    assert entry["approval"]["unchecked"] is True
    assert _approve(api, row["id"], entry, 1).status_code == 200


def test_a_document_written_outside_its_approval_stage_is_just_a_version(api):
    _, row = _flow(api)
    _, attempt = turn(api)
    written = post(api, f"tasks/{row['id']}/files", {"document": "QA report", "text": "<h1>QA</h1>"}, attempt["token"])
    assert not written.get("submitted")
    assert _standing(api, row["id"]).state == R.NOT_MET


def test_a_bot_cannot_add_an_ok_on_a_stage_that_approves_a_document(api):
    _, row = _flow(api)
    with api.app.state.store.transaction() as c, pytest.raises(H.Refused, match="approve the Spec"):
        E.add_requirement(c, row["id"], OPS, "ok", ANA, "OK the spec", seen_version=row["version"])


# ----------------------------------------------------------------------------- the migration
MIGRATION = "backend.migrations.20261117061626_stage_approvals_documents"


def test_the_migration_names_documents_and_retires_pitch(api):
    migration = importlib.import_module(MIGRATION)
    stages = (
        {
            "name": "Pitch",
            "owner": OPS,
            "requirements": [
                {"kind": "ok", "line": "OK pitch", "who_oks": "people_only", "settings": {"body_max": 600}}
            ],
        },
        {"name": "Spec", "owner": OPS, "requirements": [{"kind": "page", "line": "Spec page"}]},
        {"name": "QA", "owner": OPS, "requirements": [{"kind": "page", "line": "QA page", "person": ANA}]},
        {
            "name": "Release",
            "owner": OPS,
            "requirements": [
                {"kind": "ok", "line": "OK release", "who_oks": "people_only"},
                {"kind": "ok", "line": "OK from the bot", "person": OPS},
            ],
        },
    )
    with api.app.state.store.transaction() as c:
        made = X.pipeline(c, "legacy", "Legacy", stages)
        row = X.new_task(c, ANA, "Old pitch", "legacy", assignee=OPS)
        migration.up(c)
        migration.up(c)  # again: nothing more changes

        def settings(stage):
            return [
                (r["kind"], r["line"], R.document_of(dict(r, settings=json.loads(r["settings"]))))
                for r in c.execute(
                    "SELECT * FROM requirements WHERE stage_id=? AND withdrawn_at IS NULL ORDER BY position", (stage,)
                ).fetchall()
            ]

        retired = c.execute("SELECT retired_at FROM stages WHERE id=?", (made["stages"]["Pitch"],)).fetchone()[0]
        assert retired
        assert settings(made["stages"]["Pitch"]) == []
        moved = E.task(c, row["id"])
        assert (moved["stage_id"], moved["visit"]["entered_how"]) == (made["stages"]["Spec"], "met")
        assert settings(made["stages"]["Spec"]) == [("page", "Spec", "Spec")]
        assert settings(made["stages"]["QA"]) == [("page", "QA report", "QA report")]
        assert settings(made["stages"]["Release"]) == [
            ("page", "Release report", "Release report"),
            ("ok", "OK from the bot", None),
        ]
        rubric = c.execute(
            "SELECT json_extract(settings, '$.rubric') FROM requirements WHERE stage_id=? AND kind='page'",
            (made["stages"]["Release"],),
        ).fetchone()[0]
        assert rubric == migration.RUBRICS["Release report"]
        assert not c.execute(
            "SELECT 1 FROM requirements WHERE json_extract(settings, '$.body_max') IS NOT NULL"
        ).fetchone()


def _attach_second_spec(api, row, name="other.html"):
    page = post(api, f"tasks/{row['id']}/files", {"name": name, "text": "<h1>Another spec</h1>"})
    entry = _entry(api, row["id"])
    post(api, f"tasks/{row['id']}/pages", {"requirement": entry["requirement_id"], "page": "page:" + page["file_id"]})
    return page


def _all_verdicts(api, tid):
    with api.app.state.store.transaction() as c:
        for page in c.execute(
            "SELECT p.* FROM requirement_pages p JOIN visits v ON v.id=p.visit_id "
            "WHERE v.task_id=? AND v.left_at IS NULL AND p.replaced_at IS NULL",
            (tid,),
        ).fetchall():
            _say(c, dict(page), "met")


def test_two_specs_are_one_approval_with_both_submitted_versions(api):
    from backend import page_rounds as rounds
    from backend import task_pages as P

    _, row = _flow(api)
    first = _write(api, row["id"], SPEC, "ana-test")
    second = _attach_second_spec(api, row)
    assert _standing(api, row["id"]).on == R.JEV
    _all_verdicts(api, row["id"])
    entry = _entry(api, row["id"])
    approval = entry["approval"]
    expected = {first["file_id"]: 1, second["file_id"]: 1}
    documents = get(api, "tasks/" + row["id"])["task"]["documents"]
    assert [d["name"] for d in documents] == ["Spec", "Spec"]
    ambiguous = api.post(f"/api/v2/tasks/{row['id']}/files", json={"document": "Spec", "text": SPEC}, headers=headers())
    assert ambiguous.status_code == 409
    assert {p["file_id"]: p["version"] for p in approval["pages"]} == expected
    assert len(entry["shows"]) == 2
    # A single page key cannot approve the unseen rest of a submission.
    assert _approve(api, row["id"], entry, 1).status_code == 409
    out = post(
        api,
        f"tasks/{row['id']}/asks/{entry['requirement_id']}/answer",
        {"value": "ok", "page_version": approval["page_version"], "visit_id": approval["visit_id"]},
    )
    assert out["task"]["stage_id"] == "approvals-build"
    with api.app.state.store.read() as c:
        answer = dict(
            c.execute("SELECT * FROM answers WHERE visit_id=? AND answer='ok'", (row["visit"]["id"],)).fetchone()
        )
        assert P.answer_pages(c, row["id"], answer) == expected
        history = rounds.rounds(c, row["id"], entry["requirement_id"])
        assert len(history) == 2 and all(p["answers"][0]["answer"] == "ok" for p in history)
        from backend import stage_effects

        body = stage_effects._handoff_body(c, E.task(c, row["id"]))
        assert all(f"Spec: page:{fid}@1" in body for fid in expected)


def test_a_shared_spec_has_separate_task_oks_and_changes_since_each(api):
    from backend import task_documents as TD
    from backend import task_pages as P

    _, a = _flow(api)
    with api.app.state.store.transaction() as c:
        b = X.new_task(c, ANA, "Another export", "approvals", assignee=OPS)
    written = _write(api, a["id"], SPEC, "ana-test")
    post(
        api,
        f"tasks/{b['id']}/pages",
        {"requirement": _entry(api, b["id"])["requirement_id"], "page": "page:" + written["file_id"]},
    )
    _all_verdicts(api, a["id"])
    _all_verdicts(api, b["id"])
    assert _approve(api, a["id"], _entry(api, a["id"]), 1).status_code == 200
    assert not _standing(api, b["id"]).met
    requirement_id = _entry(api, b["id"])["requirement_id"]
    assert _approve(api, b["id"], _entry(api, b["id"]), 1).status_code == 200
    _write(api, a["id"], SPEC + "<p>Changed</p>", "ben-test")
    for task in (a, b):
        doc = get(api, "tasks/" + task["id"])["task"]["documents"][0]
        assert (doc["page_id"], doc["approved_version"], doc["version"], doc["changed_since_ok"]) == (
            written["file_id"],
            1,
            2,
            True,
        )
    with api.app.state.store.read() as c:
        assert TD.approved_versions(c, a["id"]) == TD.approved_versions(c, b["id"]) == {written["file_id"]: 1}
        assert len(P.current_pages(c, a["visit"]["id"], requirement_id)) == 1


def test_unlinking_a_spec_removes_it_from_the_approval_set(api):
    from backend import page_links as L

    _, row = _flow(api)
    first = _write(api, row["id"], SPEC, "ana-test")
    second = _attach_second_spec(api, row)
    _all_verdicts(api, row["id"])
    with api.app.state.store.transaction() as c:
        L.unlink(c, second["file_id"], "task:" + row["id"], "Spec", ANA)
    entry = _entry(api, row["id"])
    assert entry["shows"] == [f"page:{first['file_id']}@1"]
    assert _approve(api, row["id"], entry, 1).status_code == 200


def test_every_spec_version_is_checked_before_the_set_can_be_approved(api):
    _, row = _flow(api)
    _write(api, row["id"], SPEC, "ana-test")
    _verdict(api, row["id"], "met")
    second = _attach_second_spec(api, row)
    entry = _entry(api, row["id"])
    approval = entry["approval"]
    refused = post(
        api,
        f"tasks/{row['id']}/asks/{entry['requirement_id']}/answer",
        {"value": "ok", "page_version": approval["page_version"], "visit_id": approval["visit_id"]},
        expected=409,
    )
    assert refused["error"] == "task_requirement"
    _all_verdicts(api, row["id"])
    assert _standing(api, row["id"]).state == R.WAITING
    assert any(p["file_id"] == second["file_id"] for p in approval["pages"])


def test_a_report_cannot_borrow_another_pages_decision_model_round(api):
    _, row = _flow(api, "QA report")
    _write(api, row["id"], SPEC, "ana-test", "QA report")
    _verdict(api, row["id"], "not_met")
    _write(api, row["id"], SPEC + "<p>Fixed</p>", "ana-test", "QA report")
    _verdict(api, row["id"], "met")
    second = _attach_second_spec(api, row)
    with api.app.state.store.transaction() as c:
        page = c.execute(
            "SELECT p.* FROM requirement_pages p JOIN visits v ON v.id=p.visit_id "
            "WHERE v.task_id=? AND p.file_id=? AND p.replaced_at IS NULL",
            (row["id"], second["file_id"]),
        ).fetchone()
        _say(c, dict(page), "not_met")
    standing = _standing(api, row["id"])
    assert (standing.state, standing.on) == (R.NOT_MET, OPS)

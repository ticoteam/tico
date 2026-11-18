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


def test_two_specs_are_one_approval_with_both_submitted_versions(api, monkeypatch):
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
    from backend import task_documents

    calls = []
    read = task_documents._read_approvals

    def tracked(c, ids):
        calls.append(ids)
        return read(c, ids)

    monkeypatch.setattr(task_documents, "_read_approvals", tracked)
    listed = get(api, "pages?ids=" + ",".join(expected))["pages"]
    assert calls == [[row["id"]]]
    assert {
        p["file_id"]: next(link["approval"]["version"] for link in p["links"] if link["role"] == "Spec") for p in listed
    } == expected
    calls.clear()
    documents = get(api, "tasks/" + row["id"])["task"]["documents"]
    assert calls == [[row["id"]]]
    assert {doc["page_id"]: doc["approved_version"] for doc in documents} == expected
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
    links = get(api, "pages/" + written["file_id"])["page"]["links"]
    assert {link["target"]: link.get("approval") for link in links if link["role"] == "Spec"} == {
        "task:" + a["id"]: {"version": 1},
        "task:" + b["id"]: None,
    }
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
        assert TD.approved_tasks(c, written["file_id"], "Spec") == {a["id"]: 1, b["id"]: 1}
        assert len(P.current_pages(c, a["visit"]["id"], requirement_id)) == 1
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE tasks SET private=1,visibility='private' WHERE id=?", (b["id"],))
    visible = get(api, "pages/" + written["file_id"], "cara-test")["page"]
    assert [(link["target"], link["approval"]) for link in visible["links"] if link["role"] == "Spec"] == [
        ("task:" + a["id"], {"version": 1})
    ]


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


def test_a_pages_spec_ok_does_not_approve_its_other_document_role(api):
    from backend import page_links as L
    from backend import task_documents as TD

    _, row = _flow(api)
    written = _write(api, row["id"], SPEC, "ana-test")
    _all_verdicts(api, row["id"])
    assert _approve(api, row["id"], _entry(api, row["id"]), 1).status_code == 200
    with api.app.state.store.transaction() as c:
        L.link(c, written["file_id"], "task:" + row["id"], "QA report", ANA)
    with api.app.state.store.read() as c:
        assert TD.approved_tasks(c, written["file_id"], "Spec") == {row["id"]: 1}
        assert TD.approved_tasks(c, written["file_id"], "QA report") == {}
    documents = {d["name"]: d for d in get(api, "tasks/" + row["id"])["task"]["documents"]}
    assert documents["Spec"]["approved_version"] == 1
    assert "approved_version" not in documents["QA report"]
    listed = get(api, "pages?ids=" + written["file_id"])["pages"][0]
    assert {
        link["role"]: link.get("approval") for link in listed["links"] if link["target"] == "task:" + row["id"]
    } == {
        "Spec": {"version": 1},
        "QA report": None,
    }


def test_stale_approval_requires_answer_authority_and_visibility_of_every_page(api):
    from backend.tests.helpers.api import as_member

    as_member(api, "ben@acme.example")
    _, row = _flow(api)
    first = _write(api, row["id"], SPEC, "ana-test")
    second = _attach_second_spec(api, row)
    _all_verdicts(api, row["id"])
    entry = _entry(api, row["id"])
    rid, fid = entry["requirement_id"], second["file_id"]
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE requirements SET person_id='human:ben' WHERE id=?", (rid,))
    restricted = api.patch(f"/api/v2/pages/{fid}", json={"audience": {"people": ["ana"]}}, headers=headers())
    assert restricted.status_code == 200, restricted.text
    assert api.get(f"/api/v2/pages/{fid}", headers=headers("ben-test")).status_code == 404
    assert get(api, "tasks/" + row["id"], "ben-test")["task"]["id"] == row["id"]
    path = f"/api/v2/tasks/{row['id']}/asks/{rid}/answer"
    payload = {"value": "ok", "page_version": f"{first['file_id']}@999", "visit_id": row["visit"]["id"]}
    refused = api.post(path, json=payload, headers=headers("ben-test"))
    assert refused.status_code >= 400 and "approval" not in refused.json(), refused.text
    assert all(p["file_id"] not in refused.text for p in (first, second))
    exact = api.post(
        path,
        json={"value": "ok", "page_version": entry["approval"]["page_version"], "visit_id": row["visit"]["id"]},
        headers=headers("ben-test"),
    )
    assert exact.status_code >= 400 and "approval" not in exact.json(), exact.text
    allowed = api.patch(f"/api/v2/pages/{fid}", json={"audience": None}, headers=headers())
    assert allowed.status_code == 200, allowed.text
    unauthorized = _approve(api, row["id"], entry, 999, token="cara-test")
    assert unauthorized.status_code >= 400 and "approval" not in unauthorized.json(), unauthorized.text
    assert all(p["file_id"] not in unauthorized.text for p in (first, second))
    request_headers = headers("ben-test")
    stale = api.post(path, json=payload, headers=request_headers)
    assert stale.status_code == 409, stale.text
    assert {p["file_id"] for p in stale.json()["approval"]["pages"]} == {first["file_id"], fid}
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE requirements SET person_id='human:cara' WHERE id=?", (rid,))
    replay = api.post(path, json=payload, headers=request_headers)
    assert replay.status_code >= 400 and "approval" not in replay.json(), replay.text
    assert all(p["file_id"] not in replay.text for p in (first, second))


@pytest.mark.parametrize("document", ["Proposal", "Results", "Checked in production"])
def test_a_stage_approves_its_own_document_by_its_name(api, document):
    """Opportunity's Proposal, an experiment's Results and Validate's Checked in production are documents of their
    own: written under that name, in the stage, they are submitted to its approval, never to a QA report's."""
    from backend import pipeline_settings as PS

    req = {"kind": "page", "line": document, "settings": {"document": document, "rubric": "Says it."}}
    problems: list[str] = []
    PS._check_settings(req, "Stage", problems)  # pyright: ignore[reportPrivateUsage]
    assert problems == []
    _, row = _flow(api, document)
    _, attempt = turn(api)
    written = _write(api, row["id"], SPEC, attempt["token"], document.lower())  # any spelling is the fixed name
    assert written["submitted"]["version"] == 1
    _verdict(api, row["id"], "met")
    entry = _entry(api, row["id"])
    assert (entry["line"], entry["approval"]["document"]) == (document, document)


# ----------------------------------------------------------------------------- the names migration
NAMES_MIGRATION = "backend.migrations.20261118000630_approval_document_names"


def test_the_names_migration_gives_proposal_results_and_checked_in_production_their_own_documents(api):
    migration = importlib.import_module(NAMES_MIGRATION)
    qa = {"kind": "page", "line": "QA report", "person": ANA, "settings": {"document": "QA report", "rubric": "R"}}
    stages = (
        {"name": "Proposal", "owner": OPS, "requirements": [qa]},
        {"name": "Running", "owner": OPS, "requirements": [{"kind": "page", "line": "Results page"}]},
        {"name": "Validate", "owner": OPS, "requirements": [{"kind": "page", "line": "Checked in production"}]},
        {"name": "QA", "owner": OPS, "requirements": [qa]},
        {
            "name": "Review",
            "owner": OPS,
            "requirements": [{"kind": "page", "line": "Results page", "settings": {"at_head": True}}],
        },
    )
    with api.app.state.store.transaction() as c:
        made = X.pipeline(c, "sales", "Sales", stages)
        row = X.new_task(c, ANA, "Acme lights", "sales", assignee=OPS)
    _, attempt = turn(api)
    file_id = _write(api, row["id"], SPEC, attempt["token"], "QA report")["file_id"]  # what the old name wrote
    with api.app.state.store.transaction() as c:
        migration.up(c)
        migration.up(c)  # again: nothing more changes

        def named(stage):
            return [
                (r["line"], R.document_of(dict(r, settings=json.loads(r["settings"]))))
                for r in c.execute(
                    "SELECT * FROM requirements WHERE stage_id=? AND withdrawn_at IS NULL", (made["stages"][stage],)
                ).fetchall()
            ]

        assert named("Proposal") == [("Proposal", "Proposal")]
        assert named("Running") == [("Results page", None)]  # bot-held pages stay plain pages
        assert named("Validate") == [("Checked in production", None)]
        assert named("QA") == [("QA report", "QA report")]  # a real QA report stays
        assert named("Review") == [("Results page", None)]  # a review of the newest commit is never a document
        rubric = c.execute(
            "SELECT json_extract(settings, '$.rubric') FROM requirements WHERE stage_id=?",
            (made["stages"]["Proposal"],),
        ).fetchone()[0]
        assert rubric == "R"
        docs = c.execute(
            "SELECT role, page_id FROM page_links WHERE target=? AND role NOT IN ('writer', 'related')",
            ("task:" + row["id"],),
        ).fetchall()
        assert [tuple(d) for d in docs] == [("Proposal", file_id)]


def test_the_names_migration_never_gives_a_task_two_proposals_and_leaves_finished_tasks(api):
    """A task in Proposal that already has a Proposal on another page keeps its QA report, in the page links and in
    the older task_documents rows alike; a done task in Proposal is not touched."""
    migration = importlib.import_module(NAMES_MIGRATION)
    qa = {"kind": "page", "line": "QA report", "person": ANA, "settings": {"document": "QA report", "rubric": "R"}}
    with api.app.state.store.transaction() as c:
        X.pipeline(c, "sales", "Sales", ({"name": "Proposal", "owner": OPS, "requirements": [qa]},))
        both = X.new_task(c, ANA, "Acme lights", "sales", assignee=OPS)
        done = X.new_task(c, ANA, "Bolt doors", "sales", assignee=OPS)
    _, attempt = turn(api)
    report = _write(api, both["id"], SPEC, attempt["token"], "QA report")["file_id"]
    proposal = _write(api, both["id"], SPEC + " Priced.", attempt["token"], "Proposal")["file_id"]
    assert report != proposal
    finished = _write(api, done["id"], SPEC, attempt["token"], "QA report")["file_id"]
    with api.app.state.store.transaction() as c:
        E.move(c, done["id"], E.DONE, ANA, "Sent the proposal by hand")
        migration.up(c)

        def roles(task_id):
            return sorted(
                tuple(d)
                for d in c.execute(
                    "SELECT role, page_id FROM page_links WHERE target=? AND role NOT IN ('writer', 'related')",
                    ("task:" + task_id,),
                ).fetchall()
            )

        assert roles(both["id"]) == sorted([("QA report", report), ("Proposal", proposal)])
        assert roles(done["id"]) == [("QA report", finished)]
        if c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='task_documents'").fetchone():
            names = [
                r[0] for r in c.execute("SELECT name FROM task_documents WHERE task_id=?", (both["id"],)).fetchall()
            ]
            assert names.count("Proposal") <= 1

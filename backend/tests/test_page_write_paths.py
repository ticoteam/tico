"""Publishing and document-link edits preserve page versions and reconcile task approvals."""

from backend import task_engine as E
from backend import task_pages as P
from backend import task_requirements as R
from backend.tests.helpers import engine as X
from backend.tests.helpers.api import get, headers, post
from backend.tests.helpers.company import bot
from backend.tests.helpers.files import turn
from backend.tests.helpers.page_requirement import _say


def publish(api, token="ana-test", text="<p>Report</p>", **fields):
    return api.post("/api/v2/pages", json={"name": "report.html", "text": text, **fields}, headers=headers(token))


def page(response):
    assert response.status_code == 200, response.text
    return response.json()["page"]


def task(api):
    return post(api, "tasks", {"owner": "ops", "title": "Report", "body": "Check the report"})["id"]


def link_spec(api, fid, tid):
    return post(api, f"pages/{fid}/links", {"target": "task:" + tid, "role": "Spec"})


def spec_task(c, title):
    return X.new_task(c, X.ANA, title, "page-write", assignee="bot:ops")["id"]


def approval_task(api):
    with api.app.state.store.transaction() as c:
        X.pipeline(
            c,
            "page-write",
            "Page write",
            (
                {
                    "name": "Spec",
                    "owner": "bot:ops",
                    "requirements": [
                        {"kind": "page", "line": "Spec", "settings": {"document": "Spec"}, "who_oks": "people_only"}
                    ],
                },
                {"name": "Build", "owner": "bot:ops"},
            ),
        )
        return spec_task(c, "Review the primary Spec")


def submission(c, tid):
    row = E.task(c, tid)
    (requirement,) = R.for_visit(c, row["visit"])
    return P.current_pages(c, row["visit"]["id"], requirement["id"])


def versions(api, tid):
    with api.app.state.store.read() as c:
        return {p["file_id"]: p["version"] for p in submission(c, tid)}


def test_by_id_publish_uses_normal_turn_provenance(api):
    ops = bot(api, "ops")
    _, own = turn(api)
    _, foreign = turn(api, bot="finance")
    fid = page(publish(api, ops, task=task(api)))["file_id"]

    refused = publish(api, ops, "<p>Other bot's turn</p>", page=fid, attempt=foreign["id"])
    assert refused.status_code == 403, refused.text
    revised = page(publish(api, own["token"], "<p>Own turn</p>", page=fid, attempt=foreign["id"]))
    assert revised["version"] == 2
    with api.app.state.store.read() as c:
        assert (
            c.execute("SELECT attempt_id FROM bot_file_versions WHERE file_id=? AND version=2", (fid,)).fetchone()[0]
            == own["id"]
        )


def test_new_document_role_gets_its_own_series_even_with_the_same_filename(api):
    ops, tid = bot(api, "ops"), task(api)
    spec = page(publish(api, ops, "<p>Design</p>", task=tid, **{"as": "Spec"}))
    report = page(publish(api, ops, "<p>QA result</p>", task=tid, **{"as": "QA report"}))
    assert spec["file_id"] != report["file_id"] and report["version"] == 1
    shown = get(api, f"pages/{spec['file_id']}")["page"]
    assert shown["version"] == 1
    assert {link["role"] for link in shown["links"] if link["target"] == "task:" + tid} == {"Spec"}


def test_filename_publish_checks_the_existing_pages_write_permission(api):
    fid = page(publish(api, "cara-test", scope="bot"))["file_id"]
    post(api, f"pages/{fid}/links", {"target": "human:ana", "role": "writer"})
    unlinked = api.delete(
        f"/api/v2/pages/{fid}/links", params={"target": "human:cara", "role": "writer"}, headers=headers()
    )
    assert unlinked.status_code == 200, unlinked.text
    shown = get(api, f"pages/{fid}", "cara-test")["page"]
    assert not shown["may_write"]
    refused = publish(api, "cara-test", "<p>Later version</p>", scope="bot")
    assert refused.status_code == 403, refused.text


def test_legacy_spec_checks_task_comment_permission_before_writing(api):
    finance, tid = bot(api, "finance"), task(api)
    assert get(api, f"tasks/{tid}", finance)["task"]["id"] == tid
    fid = page(publish(api, finance, scope="bot"))["file_id"]
    for role in ({"as": "Spec"}, {"spec": True}):
        refused = publish(api, finance, "<p>Proposed Spec</p>", page=fid, task=tid, **role)
        assert refused.status_code == 403, refused.text
    assert get(api, f"pages/{fid}", finance)["page"]["version"] == 1


def test_plain_attachment_keeps_the_tasks_document_in_its_own_series(api):
    tid = task(api)
    fid = page(publish(api, task=tid, **{"as": "Spec"}))["file_id"]
    uploaded = post(api, f"tasks/{tid}/files", {"name": "report.html", "text": "<p>Ordinary attachment</p>"})
    assert uploaded["file_id"] != fid and uploaded["version"] == 1
    assert get(api, f"pages/{fid}")["page"]["version"] == 1


def test_plain_attachment_can_continue_a_page_used_as_another_tasks_document(api):
    tid = task(api)
    fid = page(publish(api, task=tid, **{"as": "Spec"}))["file_id"]
    link_spec(api, fid, task(api))
    removed = api.delete(
        f"/api/v2/pages/{fid}/links", params={"target": "task:" + tid, "role": "Spec"}, headers=headers()
    )
    assert removed.status_code == 200, removed.text
    uploaded = post(api, f"tasks/{tid}/files", {"name": "report.html", "text": "<p>Ordinary attachment</p>"})
    assert uploaded["file_id"] == fid and uploaded["version"] == 2
    assert get(api, f"pages/{fid}")["page"]["version"] == 2


def test_document_link_and_archive_changes_reconcile_the_open_approval(api):
    tid = approval_task(api)
    first = page(publish(api, task=tid, **{"as": "Spec"}))["file_id"]
    with api.app.state.store.transaction() as c:
        (submitted,) = submission(c, tid)
        _say(c, submitted, "met")
    second_text = "<p>Another Spec</p>"
    second = page(publish(api, text=second_text, name="second.html"))["file_id"]
    link_spec(api, second, tid)
    assert versions(api, tid) == {first: 1, second: 1}
    with api.app.state.store.read() as c:
        (standing,) = R.standings(c, E.task(c, tid))
        assert standing.on == R.JEV

    archived = api.patch(f"/api/v2/pages/{second}", json={"archived": True}, headers=headers())
    assert page(archived)["archived"]
    assert versions(api, tid) == {first: 1}
    # Publishing unchanged HTML stays archived and leaves the remaining submission in place.
    assert page(publish(api, text=second_text, page=second))["archived"]
    assert versions(api, tid) == {first: 1}
    with api.app.state.store.read() as c:
        (standing,) = R.standings(c, E.task(c, tid))
        assert standing.state == R.WAITING and standing.on != R.JEV

    restored = api.patch(f"/api/v2/pages/{second}", json={"archived": False}, headers=headers())
    assert not page(restored)["archived"]
    assert versions(api, tid) == {first: 1, second: 1}
    unlinked = api.delete(
        f"/api/v2/pages/{second}/links", params={"target": "task:" + tid, "role": "Spec"}, headers=headers()
    )
    assert unlinked.status_code == 200, unlinked.text
    assert versions(api, tid) == {first: 1}

    assert page(publish(api, text="<p>Revised separately</p>", page=second))["version"] == 2
    link_spec(api, second, tid)
    assert versions(api, tid) == {first: 1, second: 2}


def test_legacy_document_upload_resubmits_shared_page_for_both_tasks(api):
    first = approval_task(api)
    with api.app.state.store.transaction() as c:
        second = spec_task(c, "Review the shared Spec")
    fid = page(publish(api, task=first, **{"as": "Spec"}))["file_id"]
    link_spec(api, fid, second)
    # Give both approvals an explicit submission so the test isolates updating the shared version.
    assert page(publish(api, text="<p>Shared Spec</p>", page=fid))["version"] == 2
    assert versions(api, first) == versions(api, second) == {fid: 2}

    revised = post(api, f"tasks/{first}/files", {"document": "Spec", "text": "<p>Revised Spec</p>"})
    assert revised["file_id"] == fid and revised["submitted"]["version"] == 3
    assert versions(api, first) == versions(api, second) == {fid: 3}

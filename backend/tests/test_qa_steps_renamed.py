"""A Dev board whose QA and review steps are renamed keeps its QA rules: a QA waiver at the renamed QA step, the
reviews it requires found by their step's kind, and a waiting QA check holding its ticket (backend/qa_waivers.py,
backend/check_blocked.py, backend/step_flow.titled)."""
from backend import hubdb as H
from backend.tests import test_qa_waivers as waivers
from backend.tests.test_qa_waivers import api  # noqa: F401  (api is a fixture)

RENAMED = {"Review 1": "First look", "Review 1 Fixes": "First look Fixes", "Review 2": "Second look",
           "Review 2 Fixes": "Second look Fixes", "In QA": "Acceptance", "QA Fixes": "Acceptance Fixes"}


def rename(post, type_id, steps):
    return post(type_id, {"steps": [{"id": s["id"], "name": RENAMED.get(s["name"], s["name"]), "status": s["status"]}
                                    for s in steps]})


def _rename_dev(api):
    with api.app.state.store.read() as c:
        typ = H.type_get(c, "Dev tickets")

    def post(type_id, body):
        r = waivers.call(api, "POST", "task-types/" + type_id, body=body)
        assert r.status_code == 200, r.text
        return r.json()["type"]

    rename(post, typ["id"], typ["steps"])


def _qa_ticket(api, reviews=(("approve", waivers.HEAD), ("approve", waivers.HEAD)), title="Fix the checkout total",
              qa_title="QA: PR #412"):
    """A ticket at the renamed QA step, its reviews titled for the renamed review steps."""
    with api.app.state.store.read() as c:
        typ = H.type_get(c, "Dev tickets")
    r = waivers.call(api, "POST", "tasks", body={"owner_id": "cpo", "title": title, "body": "Fix it.",
                                                 "type": typ["id"], "step": "Acceptance", "links": [waivers.PR]})
    assert r.status_code == 200, r.text
    row = r.json()["task"]
    waivers.hook(api, "pull_request", waivers.pr_event("opened", head={"sha": waivers.HEAD}))
    waivers.green(api)
    for name, (verdict, head) in zip(("First look: PR #412", "Second look: PR #412"), reviews):
        waivers.child(api, row, name, verdict=verdict, head=head)
    qa_child = waivers.child(api, row, qa_title, status="doing")
    return row, qa_child


def test_a_renamed_qa_step_is_waivable_and_its_reviews_are_required(api):
    _rename_dev(api)
    row, _ = _qa_ticket(api, reviews=(("approve", waivers.HEAD), ("changes", waivers.HEAD)))
    r = waivers.waive(api, row)
    assert r.status_code == 422 and "Second look asked for changes" in r.text, r.text
    row, _ = _qa_ticket(api, title="Fix the basket total")
    r = waivers.waive(api, row)
    assert r.status_code == 200, r.text
    assert r.json()["waiver"]["step"] == "Acceptance"
    r = waivers.waive(api, row, step="First look")
    assert r.status_code == 422, r.text


def test_a_renamed_qa_steps_waiting_check_blocks_its_ticket(api):
    _rename_dev(api)
    row, qa_child = _qa_ticket(api, qa_title="Acceptance: PR #412")       # titled for the step by its new name
    with api.app.state.store.transaction() as c:
        H.task_update(c, "bot:cmo", qa_child["id"], status="waiting", waiting_on="human:ana", note="Runner pool is down")
        held = H.task(c, row["id"])
    assert held["blocked_kind"] == "check" and held["blocked_reason"].startswith("QA blocked: Runner pool is down")


def test_a_step_named_like_a_check_without_a_check_kind_is_no_check(api):
    """The name alone makes no check: a parent at a step called Review 1 whose flow asks for none hands out ordinary
    work, and one at a renamed step whose flow is a review hands out a review."""
    from backend import auto_tasks
    with api.app.state.store.transaction() as c:
        parent = H.task_create(c, "bot:cmo", "Build it", "x", "bot:cpo")
        for ident, name, flow in (("plain", "Review 1", None), ("renamed", "Second opinion", '{"check": "review"}')):
            c.execute("INSERT INTO task_steps(id,type_id,name,position,status,flow) VALUES(?,'general',?,99,'review',?)",
                      (ident, name, flow))
        child = H.task_create(c, "bot:cpo", "Look it over", "x", "bot:cmo", parent_id=parent["id"])
        found = {}
        for ident in ("plain", "renamed"):
            c.execute("UPDATE tasks SET step_id=? WHERE id=?", (ident, parent["id"]))
            found[ident] = auto_tasks.review(c, H.task(c, child["id"]))
    assert found == {"plain": False, "renamed": True}

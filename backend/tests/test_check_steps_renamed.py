"""A Dev board whose check steps are renamed keeps every check rule: review, QA, waivers and pools find their steps by
the step's flow (`check`: review, qa or release_qa), never by the names Review 1, Review 2, In QA or Post Release QA
(backend/step_flow.titled)."""
import json

import pytest

from backend import check_pools, step_flow
from backend import hubdb as H
from backend.tests import test_check_pools as pools
from backend.tests.test_github import api, get, headers  # noqa: F401  (api is a fixture)

RENAMED = {"Review 1": "First look", "Review 1 Fixes": "First look Fixes", "Review 2": "Second look",
           "Review 2 Fixes": "Second look Fixes", "In QA": "Acceptance", "QA Fixes": "Acceptance Fixes"}


def rename(post, type_id, steps):
    """Rename a board's steps the way Settings > Tasks saves them: the whole type, each step by id."""
    return post(type_id, {"steps": [{"id": s["id"], "name": RENAMED.get(s["name"], s["name"]), "status": s["status"]}
                                    for s in steps]})


def test_the_dev_board_seeds_each_check_step_with_its_kind():
    assert {n: f.get("check") for n, f in step_flow.DEV.items() if f.get("check")} == {
        "Review 1": "review", "Review 2": "review", "In QA": "qa", "Post Release QA": "release_qa"}


def test_renamed_review_steps_file_pooled_checks_and_keep_review_2_off_review_1s_bot(api):
    pools.team(api)
    task = pools.ticket(api, pools.RECEIPT)
    kind = get(api, "task-types/" + task["type_id"])["type"]

    def post(type_id, body):
        r = api.post("/api/v2/task-types/" + type_id, headers=headers(), json=body)
        assert r.status_code == 200, r.text
        return r.json()["type"]

    names = [s["name"] for s in rename(post, task["type_id"], kind["steps"])["steps"]]
    assert "First look" in names and "Review 1" not in names
    pools.set_pool(api, task, "First look", ["rev-a", "rev-b"])
    pools.set_pool(api, task, "Second look", ["rev-a", "rev-b"])
    pools.busy(api, "rev-b")
    with api.app.state.store.read() as c:
        assert check_pools.open_checks(c, "rev-b") == 1

    r = pools.move(api, task, "First look")
    assert r.status_code == 200, r.text
    [first] = pools.checks(api, task)
    assert first["title"] == "First look: PR #412" and first["owner"] == "bot:rev-a"
    with api.app.state.store.read() as c:
        assert check_pools.open_checks(c, "rev-a") == 1, "a renamed step's check counts toward the limit"
        assert step_flow.check_kind(step_flow.child_check(c, first)) == "review"
    with api.app.state.store.transaction() as c:
        # One open request per check step and head, whatever the step is called.
        with pytest.raises(H.Refused, match="already open"):
            H.task_create(c, "bot:cpo", "First look: PR #412", "Again.", "bot:rev-b", parent_id=task["id"])
        H.task_update(c, "bot:rev-a", first["id"], status="done", note=f"VERDICT: approve PR #412 head {pools.HEAD}")

    task = get(api, "tasks/" + task["id"])["task"]
    assert pools.move(api, task, "Second look").status_code == 200
    [second] = pools.checks(api, task)
    assert second["title"] == "Second look: PR #412"
    assert second["owner"] == "bot:rev-b", "rev-a has fewer open checks but did the first review"


def test_a_renamed_review_step_still_passes_the_review_gate(api):
    pools.team(api)
    task = pools.ticket(api, None)      # no test receipt at the head
    kind = get(api, "task-types/" + task["type_id"])["type"]

    def post(type_id, body):
        r = api.post("/api/v2/task-types/" + type_id, headers=headers(), json=body)
        assert r.status_code == 200, r.text
        return r.json()["type"]

    rename(post, task["type_id"], kind["steps"])
    r = pools.move(api, task, "First look")
    assert r.status_code == 422 and "receipt" in r.text.lower(), r.text
    with api.app.state.store.transaction() as c:
        # A hand-filed request titled for the renamed review step meets the same gate.
        with pytest.raises(H.Refused) as refused:
            H.task_create(c, "bot:cpo", "Second look: PR #412", "Check it.", "bot:rev-a", parent_id=task["id"])
    assert refused.value.rule == "review_gate" and "receipt" in refused.value.detail.lower(), refused.value.detail


def test_a_board_flowed_before_check_kinds_gets_them_once_even_when_renamed(api):
    """An existing Dev board's steps had a flow but no `check`: the upgrade gives the seeded names their kind and a
    renamed step the kind its flow implies (gated: review; passing to the merge: QA), once."""
    task = pools.ticket(api, None)
    with api.app.state.store.transaction() as c:
        for s in step_flow.steps(c, task["type_id"]):
            flow = {k: v for k, v in step_flow.of(s).items() if k != "check"}
            name = "Second look" if s["name"] == "Review 2" else s["name"]
            c.execute("UPDATE task_steps SET flow_json=?, name=? WHERE id=?",
                      (json.dumps(flow) if flow else None, name, s["id"]))
        c.execute("DELETE FROM events WHERE action=? AND target=?", (step_flow.CHECKS_SEEDED, task["type_id"]))
        step_flow.seed_checks(c)
        kinds = {s["name"]: step_flow.check_kind(s) for s in step_flow.steps(c, task["type_id"]) if step_flow.check_kind(s)}
        assert kinds == {"Review 1": "review", "Second look": "review"}
        first = next(s for s in step_flow.steps(c, task["type_id"]) if s["name"] == "Review 1")
        c.execute("UPDATE task_steps SET flow_json=? WHERE id=?", (json.dumps({"gate": True, "check": "qa"}), first["id"]))
        step_flow.seed_checks(c)
        assert step_flow.check_kind(step_flow.step(c, first["id"])) == "qa", "a step's kind, once set, is a person's"

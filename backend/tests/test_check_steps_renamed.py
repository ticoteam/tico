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
    assert first["title"] == "First look: PR #412" and first["owner_id"] == "bot:rev-a"
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
    assert second["owner_id"] == "bot:rev-b", "rev-a has fewer open checks but did the first review"


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
            c.execute("UPDATE task_steps SET flow=?, name=? WHERE id=?",
                      (json.dumps(flow) if flow else None, name, s["id"]))
        c.execute("DELETE FROM events WHERE action=? AND target=?", (step_flow.CHECKS_SEEDED, task["type_id"]))
        step_flow.seed_checks(c)
        kinds = {s["name"]: step_flow.check_kind(s) for s in step_flow.steps(c, task["type_id"]) if step_flow.check_kind(s)}
        assert kinds == {"Review 1": "review", "Second look": "review"}
        first = next(s for s in step_flow.steps(c, task["type_id"]) if s["name"] == "Review 1")
        c.execute("UPDATE task_steps SET flow=? WHERE id=?", (json.dumps({"gate": True, "check": "qa"}), first["id"]))
        step_flow.seed_checks(c)
        assert step_flow.check_kind(step_flow.step(c, first["id"])) == "qa", "a step's kind, once set, is a person's"


def _renamed(api, task, names):
    kind = get(api, "task-types/" + task["type_id"])["type"]
    r = api.post("/api/v2/task-types/" + task["type_id"], headers=headers(), json={"steps": [
        {"id": s["id"], "name": names.get(s["name"], s["name"]), "status": s["status"]} for s in kind["steps"]]})
    assert r.status_code == 200, r.text


def test_a_step_renamed_to_a_plain_word_makes_only_its_own_requests_checks(api):
    """A review step renamed `Review`: "Review the docs" is ordinary work (both file), while `Review: PR #412`
    is that step's request and meets the review gate."""
    pools.team(api)
    task = pools.ticket(api, None)
    _renamed(api, task, {"Review 1": "Review"})
    with api.app.state.store.transaction() as c:
        for title in ("Review the copy", "Review the docs"):
            H.task_create(c, "bot:cpo", title, "Look it over.", "bot:cmo", parent_id=task["id"])
        with pytest.raises(H.Refused) as refused:
            H.task_create(c, "bot:cpo", "Review: PR #412", "Check it.", "bot:cmo", parent_id=task["id"])
    assert refused.value.rule == "review_gate", refused.value.detail


def test_the_generic_review_titles_name_the_renamed_steps_in_board_order(api):
    task = pools.ticket(api, None)
    _renamed(api, task, {"Review 1": "First look", "Review 2": "Second look"})
    with api.app.state.store.read() as c:
        assert step_flow.titled(c, task["type_id"], "Review 1: PR #412")["name"] == "First look"
        assert step_flow.titled(c, task["type_id"], "Review 2: PR #412")["name"] == "Second look"
        assert step_flow.titled(c, task["type_id"], "First look the other way") is None


def test_a_receipt_in_a_keeper_notice_naming_a_renamed_review_step_counts(api):
    """The review gate names the board's review steps, so a keeper notice about the renamed step is read for a
    receipt as one about Review 1 was."""
    from backend import test_receipts
    sha = "d" * 40
    notice = {"id": "m1", "sender_id": "keeper", "created_at": "2026-01-01", "body": f"First look asked.\nTests: head {sha}: 3 passed, 0 failed"}
    task = {"id": "t", "owner_id": "bot:cpo", "note": "", "messages": [notice], "children": []}
    assert test_receipts.at_head({**task, "review_steps": ["First look"]}, sha)[0] == "passed"
    assert test_receipts.at_head(task, sha)[0] != "passed", "a keeper line that names no review step is not evidence"


def test_the_task_manager_finds_a_second_open_request_for_a_renamed_step(api):
    from backend import task_manager
    task = pools.ticket(api, pools.RECEIPT)
    _renamed(api, task, {"Review 1": "First look"})
    with api.app.state.store.transaction() as c:
        made = [H.task_create(c, "bot:cpo", f"Look {i}", "Check it.", "bot:cmo", parent_id=task["id"]) for i in (1, 2)]
        for row in made:
            c.execute("UPDATE tasks SET title='First look: PR #412' WHERE id=?", (row["id"],))
        c.execute("UPDATE tasks SET private=0 WHERE id IN (?,?,?)", (task["id"], *(m["id"] for m in made)))
        found = [i for i in task_manager._reviews(c) if i["kind"] == "review_duplicate"]
    assert [i["task"] for i in found] == [made[1]["id"]], found


def test_renaming_a_step_keeps_the_boards_rules_on_it(api):
    """The Dev board's "wake QA on In QA" row follows In QA to its new name."""
    task = pools.ticket(api, None)
    with api.app.state.store.transaction() as c:
        c.execute("INSERT INTO rules(id,scope_kind,scope_id,event,condition,then_outcome,params,enabled,position,"
                  "note,created_by,created_at,updated_at,revision) VALUES('r-qa','type',?,'step_entered',?,'wake','{}',1,1,"
                  "'QA',?,?,?,1)", (task["type_id"], json.dumps({"step": ["Review 2", "Done"], "role": "qa"}),
                                     H.KEEPER, H.now(), H.now()))
    _renamed(api, task, {"Review 2": "Second look"})
    with api.app.state.store.read() as c:
        row = c.execute("SELECT condition, revision FROM rules WHERE id='r-qa'").fetchone()
    assert json.loads(row[0]) == {"step": ["Second look", "Done"], "role": "qa"} and row[1] == 2


def test_the_upgrade_finds_a_renamed_release_check_by_its_pool_after_the_merge_step(api):
    task = pools.ticket(api, None)
    with api.app.state.store.transaction() as c:
        for ident, name, position, flow, pool in (("pr", "Shipped", 10, {"on_merge": True}, None),
                                                  ("after", "Live check", 11, {"can_hold": False}, '["qa"]'),
                                                  ("before", "Pairing", 3, {"can_hold": False}, '["qa"]')):
            c.execute("INSERT INTO task_steps(id,type_id,name,position,status,flow,check_pool) VALUES(?,?,?,?,?,?,?)",
                      (ident, task["type_id"], name, position, "review", json.dumps(flow), pool))
        c.execute("DELETE FROM events WHERE action=? AND target=?", (step_flow.CHECKS_SEEDED, task["type_id"]))
        step_flow.seed_checks(c)
        assert step_flow.check_kind(step_flow.step(c, "after")) == "release_qa"
        assert step_flow.check_kind(step_flow.step(c, "before")) is None, "a pool before the merge step is no release check"


def test_a_numbered_review_title_names_only_a_review_step_the_board_has(api):
    """"Review 3 failing tests" under a Dev ticket (two review steps) is ordinary work: it files with no receipt and
    is no check. A board with no review steps still reads the Dev board's own Review 1 and Review 2 titles."""
    pools.team(api)
    task = pools.ticket(api, None)      # no test receipt: a review request would meet the gate
    with api.app.state.store.transaction() as c:
        for title in ("Review 3 failing tests", "Review 10 pages"):
            made = H.task_create(c, "bot:cpo", title, "Look them over.", "bot:cmo", parent_id=task["id"])
            assert step_flow.child_check(c, made) is None, title
        assert step_flow.titled(c, None, "Review 3: PR #412") is None
        assert step_flow.titled(c, None, "Review 2: PR #412")["generic"] is True
        assert step_flow.check_kind(step_flow.titled(c, None, "QA: PR #412")) == "qa"


def test_a_flow_saved_without_check_keeps_the_steps_kind(api):
    """A steps file written before steps had kinds sends a check step's flow without `check`: the step keeps its
    kind, so its request stays a check rather than an open child that holds the ticket (a QA request held that way
    can't be waived). `"check": null` clears the kind."""
    task = pools.ticket(api, None)
    review_2 = {"timeout_hours": 1, "gate": True, "skip_tag": "review-1-only"}

    def save(flow):
        kind = get(api, "task-types/" + task["type_id"])["type"]
        r = api.post("/api/v2/task-types/" + task["type_id"], headers=headers(), json={"steps": [
            {"id": s["id"], "name": s["name"], "status": s["status"],
             **({"flow": flow} if s["name"] == "Review 2" else {})} for s in kind["steps"]]})
        assert r.status_code == 200, r.text
        with api.app.state.store.read() as c:
            return step_flow.check_kind(step_flow.titled(c, task["type_id"], "Review 2: PR #412"))

    assert save(review_2) == "review"
    assert save({**review_2, "timeout_hours": 2}) == "review", "a second save without it keeps it too"
    assert save({**review_2, "check": None}) is None

"""A review or QA subtask's verdict and the head it judged are fields the parent's owner is woken with."""
import pytest

from backend.hubdb import Refused
from backend.store import H
from backend.tests.test_api import api, post  # noqa: F401
from backend.tests.test_subtask_results import queued_in
from backend.tests.test_task_lanes import task_bot

HEAD = "a1b2c3d" * 5 + "a1b2c"


def pair(api, review_title="Review 1: import"):
    task_bot(api, lanes=True, others=("finance",))
    with api.app.state.store.transaction() as c:
        parent = H.task_create(c, "human:ana", "Implement the import", "Build it.", "bot:ops", lint=False)
        review = H.task_create(c, "bot:ops", review_title, "Review it.", "bot:finance", parent_id=parent["id"], lint=False)
        c.execute("DELETE FROM jobs")
    return parent, review


def test_done_with_a_verdict_and_head_wakes_the_parent_owner_with_both(api):
    parent, review = pair(api)
    with api.app.state.store.transaction() as c:
        H.task_update(c, "bot:finance", review["id"], status="done", note="Looks right.",
                      verdict="approve", verdict_head=HEAD)
        row = H.task(c, review["id"])
        woke = [m for m in queued_in(c, "ops") if m["refs"].get("subtask") == review["id"]]
    assert (row["verdict"], row["verdict_head"]) == ("approve", HEAD)
    assert len(woke) == 1 and not woke[0]["refs"].get("quiet")
    assert (woke[0]["refs"]["verdict"], woke[0]["refs"]["verdict_head"]) == ("approve", HEAD)
    # The parent's Subtasks list reads the tree: the verdict rides on its node.
    with api.app.state.store.read() as c:
        node = H.task_tree(c, parent["id"])[0]
    assert (node["verdict"], node["verdict_head"]) == ("approve", HEAD)


def test_a_prose_verdict_line_fills_the_fields_and_a_newer_one_replaces_them(api):
    parent, review = pair(api)
    with api.app.state.store.transaction() as c:
        H.task_update(c, "bot:finance", review["id"], status="doing", note=f"Notes.\nVERDICT: changes PR #4 head {HEAD}")
        row = H.task(c, review["id"])
        assert (row["verdict"], row["verdict_head"]) == ("changes", HEAD)
        later = "b" * 40
        H.task_update(c, "bot:finance", review["id"], status="done", note=f"VERDICT: approve PR #4 head {later}")
        row = H.task(c, review["id"])
        assert (row["verdict"], row["verdict_head"]) == ("approve", later)
        woke = [m for m in queued_in(c, "ops") if m["refs"].get("verdict") == "approve"]
    assert woke and woke[0]["refs"]["verdict_head"] == later


def test_a_flag_outranks_the_prose_and_a_verdict_alone_leaves_the_head_unknown(api):
    parent, review = pair(api)
    with api.app.state.store.transaction() as c:
        H.task_update(c, "bot:finance", review["id"], status="done", note=f"VERDICT: changes PR #4 head {HEAD}",
                      verdict="pass")
        row = H.task(c, review["id"])
    assert (row["verdict"], row["verdict_head"]) == ("pass", None)


def test_a_verdict_is_refused_from_a_stranger_on_a_bad_value_and_on_a_task_with_no_parent(api):
    parent, review = pair(api)
    with api.app.state.store.transaction() as c:
        with pytest.raises(Refused, match="verdict is approve"):
            H.task_update(c, "bot:finance", review["id"], verdict="maybe")
        with pytest.raises(Refused, match="7 to 40 hex"):
            H.task_update(c, "bot:finance", review["id"], verdict="approve", verdict_head="not-a-sha")
        with pytest.raises(Refused, match="owner of the review"):
            H.task_update(c, "bot:ops", review["id"], verdict="approve")
        with pytest.raises(Refused, match="not"):
            H.task_update(c, "bot:ops", parent["id"], verdict="approve")
        assert H.task(c, review["id"])["verdict"] is None


def test_the_api_takes_the_fields_on_done_and_returns_them_on_the_task(api):
    task_bot(api, lanes=True)
    with api.app.state.store.transaction() as c:
        parent = H.task_create(c, "human:ana", "Implement the import", "Build it.", "bot:ops", lint=False)
        check = H.task_create(c, "bot:ops", "QA: import", "Check it.", "human:ana", parent_id=parent["id"], lint=False)
        version = H.task(c, check["id"])["version"]
    done = post(api, f"tasks/{check['id']}", {"status": "done", "note": "Passed.", "version": version,
                                              "verdict": "pass", "verdict_head": HEAD.upper()[:12]})
    assert (done["verdict"], done["verdict_head"]) == ("pass", HEAD[:12])
    post(api, f"tasks/{check['id']}", {"verdict": "maybe", "version": done["version"]}, expected=422)


def test_a_structured_verdict_after_a_prose_one_wakes_the_parent_owner(api):
    parent, review = pair(api)
    later = "b" * 40
    with api.app.state.store.transaction() as c:
        H.task_comment(c, "bot:finance", review["id"], f"VERDICT: changes PR #4 head {HEAD}")
        H.task_update(c, "bot:finance", review["id"], status="done", note="Wrapping up.", verdict="approve",
                      verdict_head=later)
        woke = [m["refs"] for m in queued_in(c, "ops") if m["refs"].get("subtask") == review["id"]]
    approve = [r for r in woke if r.get("verdict") == "approve"]
    assert approve and approve[-1]["verdict_head"] == later and not approve[-1].get("quiet"), \
        "a new verdict on a new head is a result, not bookkeeping"

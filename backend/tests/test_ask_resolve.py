"""A question answered elsewhere, withdrawn, or made moot by the task finishing leaves the person's Needs you."""

import pytest

from backend.store import H
from backend.tests.test_tasks_board import api, headers, post  # noqa: F401

ANA, OPS = "human:ana", "bot:ops"



@pytest.fixture(autouse=True)
def private_tasks_have_an_unrelated_manager(api):
    from backend.tests.test_api import unrelated_task_managers
    with api.app.state.store.transaction() as c:
        unrelated_task_managers(c)


def question(c, actor, task_id, text="Which month should the report use?", who=ANA, refs=None):
    ask = {"questions": [{"id": "month", "header": "Month", "question": text, "multi": False, "other": True,
                          "options": [{"label": "July", "description": ""}, {"label": "August", "description": ""}]}],
           "who": who}
    return H.task_comment(c, actor, task_id, text, wake=False, ask=ask, extra_refs=refs)


def listed(c, task_id, who=ANA):
    return task_id in {t["id"] for t in H.needs_you(c, who)["tasks"]}


def test_the_keepers_stuck_question_leaves_needs_you_when_the_task_is_done(api):
    with api.app.state.store.transaction() as c:
        work = H.task_create(c, ANA, "Draft the board report", "For Friday.", OPS)
        stuck = question(c, H.KEEPER, work["id"], "No progress for 30 min after a status check.", refs={"stuck": True})
        pick = question(c, OPS, work["id"], "Which chart goes first?")
        H.task_update(c, OPS, work["id"], status="done", note="Drafted.")
        assert H.message(c, stuck["id"])["superseded_at"] and H.message(c, pick["id"])["superseded_at"]
        assert not listed(c, work["id"])        # every question about the work ended with it, picks too
        accept = question(c, OPS, work["id"], "Does the draft read right?")
        assert not H.message(c, accept["id"])["superseded_at"]
        assert listed(c, work["id"])            # a done task's own question for acceptance, asked once done, counts
        # A done task holding a stuck question from before the task-end hook existed is not listed either.
        old = H.task_create(c, ANA, "Check the cash forecast", "Monthly.", OPS)
        question(c, H.KEEPER, old["id"], "No progress for 30 min after a status check.", refs={"stuck": True})
        c.execute("UPDATE tasks SET status='done' WHERE id=?", (old["id"],))
        assert not listed(c, old["id"])


def test_an_answer_given_elsewhere_is_quoted_by_the_asker_or_owner_bot_and_the_asker_may_withdraw(api):
    with api.app.state.store.transaction() as c:
        work = H.task_create(c, ANA, "Draft the board report", "For Friday.", OPS)
        asked = question(c, OPS, work["id"])
        assert listed(c, work["id"]) and H.status_counts(c, OPS)[1] == 1
        with pytest.raises(H.Refused):
            H.task_ask_resolve(c, "bot:cpo", work["id"], asked["id"], "August")     # neither asker nor owner
        with pytest.raises(H.Refused):
            H.task_ask_resolve(c, ANA, work["id"], asked["id"])                     # only the asker withdraws
        jobs = c.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
        out = H.task_ask_resolve(c, OPS, work["id"], asked["id"], "Use August, it closed yesterday.")
        reply = out["answer"]
        assert reply["kind"] == "answer" and reply["in_reply_to"] == asked["id"] and reply["refs"]["quoted"]
        assert reply["answer"]["by"] == ANA and reply["answer"]["quoted_by"] == OPS and reply["answer"]["other"] == "Use August, it closed yesterday."
        assert c.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == jobs         # it wakes nobody
        assert not listed(c, work["id"]) and H.status_counts(c, OPS)[1] == 0
        assert [a["other"] for a in H.message(c, asked["id"])["answers"]] == ["Use August, it closed yesterday."]
        with pytest.raises(H.Refused):
            H.task_ask_resolve(c, OPS, work["id"], asked["id"], "Again")            # already answered

        again = question(c, OPS, work["id"], "Should it include the forecast?")
        assert listed(c, work["id"])
        withdrawn = H.task_ask_resolve(c, OPS, work["id"], again["id"])
        assert withdrawn["answer"] is None and withdrawn["ask"]["superseded_at"]
        assert not listed(c, work["id"])
        assert again["id"] in {m["id"] for m in H.task_comments(c, work["id"])}     # kept for the record


def test_the_resolve_route_refuses_a_person_who_did_not_ask(api):
    task = post(api, "tasks", {"owner": "ops", "title": "Draft the board report", "body": "For Friday."})
    with api.app.state.store.transaction() as c:
        asked = question(c, OPS, task["id"])
    path = f"/api/v2/tasks/{task['id']}/comments/{asked['id']}/resolve"
    refused = api.post(path, json={}, headers=headers())
    assert refused.status_code == 403, refused.text
    assert api.post(path, json={"quote": ""}, headers=headers()).status_code == 422     # never a silent withdraw
    with api.app.state.store.read() as c:
        assert listed(c, task["id"])

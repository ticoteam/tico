"""A private task stuck because the next step's person cannot see it (task #392): its Needs you card says so in plain
words and names who to let in (`turn.share`), a person lets exactly that one in and moves it on in one press (a move
with `let_see`), and a bot is told to ask a person, never how to share it itself."""

import json

import pytest

from backend import task_engine as E
from backend import waiting_on as W
from backend.store import H
from backend.tests.helpers import engine as X
from backend.tests.helpers.api import get, headers, post

ANA, BEN = "human:ana", "human:ben"
BUILDER, MANAGER, REVIEWER = "bot:builder", "bot:manager", "bot:reviewer"


@pytest.fixture
def c(company):
    with company.transaction() as conn:
        for slug in ("builder", "manager", "reviewer", "other"):
            X.bot(conn, slug)
        stages = (
            {"name": "Build", "owner": "assignee", "requirements": [{"kind": "owner_done"}]},
            {"name": "Review 1", "owner": REVIEWER, "requirements": [{"kind": "owner_done"}]},
        )
        X.pipeline(conn, "eng", "Engineering", stages, manager=MANAGER)
        yield conn


def _held(c):
    """Ana's private task, built and done in Build: Review 1's owner cannot read it, so it waits there."""
    row = H.task_create(c, ANA, "Fix the card", "x", BUILDER, pipeline="eng", private=True, lint=False)
    H.task_done(c, BUILDER, row["id"], "Built.", H.task(c, row["id"])["version"])
    assert E.task(c, row["id"])["stage"]["name"] == "Build"
    return row["id"]


def _to_person(c, task_id, person=ANA):
    """The ladder brings the held task to `person` (never_stuck._person)."""
    c.execute("UPDATE visits SET level='person', person_id=? WHERE task_id=? AND left_at IS NULL", (person, task_id))


def _version(c, task_id):
    return H.task(c, task_id)["version"]


def test_a_stuck_card_says_who_cannot_see_the_task_in_plain_words_and_names_them_to_let_in(c):
    task_id = _held(c)
    _to_person(c, task_id)
    (item,) = [n for n in W.needs(c, ANA) if n.get("task_id") == task_id]
    assert item["line"] == "Stuck in Build: reviewer can't see this task", item
    turn = H.task_view(c, task_id, ANA)["turn"]
    assert turn["share"] == REVIEWER and turn["line"] == item["line"], turn
    said = json.dumps(H.task_view(c, task_id, ANA))
    assert "Who can see" not in said and "owns Review" not in said, said


def test_letting_the_named_person_see_it_adds_only_them_and_moves_it_on_with_a_history_line(c):
    task_id = _held(c)
    _to_person(c, task_id)
    H.task_let_see(c, ANA, task_id, REVIEWER, _version(c, task_id))
    after = E.task(c, task_id)
    assert (after["stage"]["name"], after["owner_id"]) == ("Review 1", REVIEWER)
    assert H.task_readers(H.task(c, task_id)) == [REVIEWER]
    assert all(n.get("task_id") != task_id for n in W.needs(c, ANA))
    events = c.execute(
        "SELECT actor_id, old, new FROM history_events WHERE subject_id=? AND kind='reader'", (task_id,)
    ).fetchall()
    assert [tuple(e) for e in events] == [(ANA, None, REVIEWER)]


def test_a_card_drawn_for_someone_else_adds_nobody_and_says_who_it_is_now(c):
    task_id = _held(c)
    _to_person(c, task_id)
    with pytest.raises(H.Refused, match="task_changed") as refused:
        H.task_let_see(c, ANA, task_id, "bot:other", _version(c, task_id))
    assert "reviewer" in refused.value.detail and "Nobody was added" in refused.value.detail
    assert H.task_readers(H.task(c, task_id)) == []
    assert E.task(c, task_id)["stage"]["name"] == "Build"


def test_a_bot_never_lets_anyone_see_a_task_and_its_refused_move_says_to_ask_a_person(c):
    task_id = _held(c)
    with pytest.raises(H.Refused, match="task_not_shared") as refused:
        H.task_move(c, BUILDER, task_id, "next", None, _version(c, task_id))
    detail = refused.value.detail
    assert detail == "reviewer does the next step but can't see this private task. Ask Ana to let them see it.", detail
    assert refused.value.extra["fix"] == "Ask Ana to let them see it"
    assert "Who can see" not in json.dumps(refused.value.extra) and "reader" not in json.dumps(refused.value.extra)
    with pytest.raises(H.Refused):
        H.task_let_see(c, BUILDER, task_id, REVIEWER, _version(c, task_id))
    assert H.task_readers(H.task(c, task_id)) == []


def test_a_person_moving_it_by_hand_is_told_the_same_cause_in_plain_words(c):
    task_id = _held(c)
    with pytest.raises(H.Refused, match="task_not_shared") as refused:
        H.task_move(c, ANA, task_id, "next", None, _version(c, task_id))
    assert refused.value.detail.startswith("reviewer does the next step but can't see this private task."), refused


def test_the_move_route_lets_the_named_person_see_it_and_moves_it_on(api):
    """Through POST /tasks/{id}/move with `let_see`, as the Needs you card sends it."""
    stage = {"owner": "assignee", "requirements": [{"kind": "owner_done"}]}
    record = {
        "name": "Eng",
        "stages": [
            {"id": "build", "name": "Build", **stage},
            {"id": "review", "name": "Review 1", "owner": "human:cara", "requirements": [{"kind": "owner_done"}]},
        ],
    }
    post(api, "pipelines/eng", record)
    made = post(
        api, "tasks", {"title": "Fix the card", "body": "x", "owner": "ana", "pipeline": "eng", "private": True}
    )
    task = get(api, "tasks/" + made["id"])["task"]
    post(api, f"tasks/{made['id']}/done", {"version": task["version"]})
    task = get(api, "tasks/" + made["id"])["task"]
    r = api.post(f"/api/v2/tasks/{made['id']}/move", json={"to": "next", "version": task["version"]}, headers=headers())
    assert r.status_code == 409 and "Cara" in r.text and "Who can see" in r.text, r.text  # the task page's own label
    assert task["turn"]["share"] == "human:cara", task["turn"]
    wrong = {"to": "next", "let_see": "ben", "version": task["version"]}
    r = api.post(f"/api/v2/tasks/{made['id']}/move", json=wrong, headers=headers())
    assert r.status_code == 409 and "task_changed" in r.text, r.text
    moved = post(api, f"tasks/{made['id']}/move", {"to": "next", "let_see": "human:cara", "version": task["version"]})
    assert moved["stage"]["id"] == "review"
    assert get(api, "tasks/" + made["id"], token="cara-test")["task"]["id"] == made["id"]

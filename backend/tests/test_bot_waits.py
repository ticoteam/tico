"""A task waits on a bot's work through a blocker (`hub task wait <task> --on task:<its task>`), which holds the work
while it is open and wakes the owner once with the outcome when it resolves. `--status waiting --on bot:<slug>` is
refused (spec D9): a task waits on a person, and a bot asks a bot in a DM. Nothing about the task changes."""

import json

import pytest

from backend import hubdb as H
from backend import task_relations as TR
from backend.tests.helpers.api import get, headers, post, restrict, setup_attempt  # noqa: F401  (fixture)


@pytest.fixture
def c(api):
    """The test hub's store, with cpo as the builder, ops as the bot doing the prerequisite and finance paused."""
    with api.app.state.store.transaction() as conn:
        conn.execute("UPDATE bots SET state='active' WHERE slug IN ('cpo','ops')")
        conn.execute("UPDATE bots SET state='paused' WHERE slug='finance'")
        yield conn


def parked(c, prerequisite_owner="bot:ops", tag=""):
    """cpo's task and the prerequisite another bot is working on, linked blocked_by (`tag` keeps titles apart)."""
    task = H.task_create(c, "human:ana", "Ship the receipt gate" + tag, "", "bot:cpo", lint=False)
    pre = H.task_create(c, "human:ana", "Review the receipt gate fix" + tag, "", prerequisite_owner, lint=False)
    TR.relate(c, "bot:cpo", task["id"], pre["id"], "blocked_by")
    return task, pre


def jobs(c, slug):
    return [
        dict(r)
        for r in c.execute(
            "SELECT m.body, m.refs FROM jobs j JOIN messages m ON m.id=j.message_id WHERE j.bot_id=?", (slug,)
        )
    ]


def test_a_blocker_owned_by_another_bot_holds_the_task_and_names_that_bot(c):
    task, _ = parked(c)
    c.execute("DELETE FROM jobs")
    H.task_update(c, "bot:cpo", task["id"], status="waiting", note="Needs the review of the receipt gate fix first.")
    after = H.task(c, task["id"])
    assert after["status"] == "waiting" and after["note"] == "Needs the review of the receipt gate fix first."
    assert "waiting_on" not in after  # nothing stored: who it waits on is derived
    assert H.wait_detail(c, after) == {"why": "an open blocker", "on": "bot:ops"}
    assert jobs(c, "cpo") == []  # held while it is open: nothing starts the builder on it


def test_the_prerequisite_resolving_wakes_the_owner_once_with_the_outcome(c):
    for outcome, words in [("done", "is finished"), ("declined", "was declined")]:
        task, pre = parked(c, tag=f" ({outcome})")
        H.task_update(c, "bot:cpo", task["id"], status="waiting", note="Needs the review first.")
        c.execute("DELETE FROM jobs")
        H.task_update(c, "bot:ops", pre["id"], status=outcome, note="Reviewed.")
        woke = jobs(c, "cpo")
        assert len(woke) == 1 and woke[0]["body"].startswith("Unblocked: Ship the receipt gate")
        assert words in woke[0]["body"]
        assert json.loads(woke[0]["refs"])["blocker_status"] == outcome
        assert H.wait_detail(c, H.task(c, task["id"])) is None  # eligible again: nothing holds it


def test_waiting_on_a_bot_is_refused_with_a_dm_and_changes_nothing(c):
    for target in ("bot:ops", "bot:finance", "ops"):
        task, _ = parked(c, tag=f" [{target}]")
        before = dict(H.task(c, task["id"]))
        with pytest.raises(H.Refused) as refused:
            H.task_update(c, "bot:cpo", task["id"], status="waiting", waiting_on=target, note="Waiting on it.")
        assert refused.value.rule == "waiting_on", refused.value
        said = str(refused.value)
        assert "waits on a person, not a bot" in said and "hub message send" in said and "--needs-action" in said
        assert f"hub task wait {task['id']} --on task:" in said
        after = dict(H.task(c, task["id"]))
        assert {k: after[k] for k in ("status", "note", "version")} == {
            k: before[k] for k in ("status", "note", "version")
        }
        assert TR.child_ids(c, task["id"]) == []


def test_api_bot_wait_is_refused_and_preserves_the_task(api):
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE bots SET state='active' WHERE slug IN ('cpo','ops')")
    _, _, attempt = setup_attempt(api, "cpo", operator="ben")
    token = attempt["token"]
    with api.app.state.store.transaction() as c:
        task = H.task_create(c, "human:ana", "Ship after prerequisite", "", "bot:cpo", lint=False, private=False)
        pre = H.task_create(c, "human:ana", "Complete prerequisite", "", "bot:ops", lint=False, private=False)
    post(api, "tasks/" + task["id"] + "/relations", {"task": pre["id"], "kind": "blocked_by"})
    with api.app.state.store.read() as c:
        before = dict(H.task(c, task["id"]))
    response = api.post(
        "/api/v2/tasks/" + task["id"],
        headers=headers(token),
        json={
            "version": before["version"],
            "status": "waiting",
            "note": "Needs prerequisite first.",
            "waiting_on": "bot:ops",
        },
    )
    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "waiting_on", response.text
    assert "hub message send ops" in response.text and pre["id"] not in response.text
    with api.app.state.store.read() as c:
        after = dict(H.task(c, task["id"]))
    assert {k: after[k] for k in ("status", "note", "version")} == {k: before[k] for k in ("status", "note", "version")}

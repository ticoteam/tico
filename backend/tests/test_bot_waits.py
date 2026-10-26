"""A task can wait on a bot doing prerequisite work (`--status waiting --on bot:<slug>`): the bot must be active and own
an open task this one is blocked by, which holds the work while it is open and wakes the owner with the outcome when it
resolves. Anything else is refused with how to link it, and nothing about the task changes."""
import json

import pytest

from backend import hubdb as H
from backend import task_relations as TR
from backend.tests.test_api import api  # noqa: F401  (fixture)


@pytest.fixture
def c(api):
    """The test hub's store, with cpo as the builder, ops as the bot doing the prerequisite and finance paused."""
    with api.app.state.store.transaction() as conn:
        conn.execute("UPDATE bots SET state='active' WHERE slug IN ('cpo','ops')")
        conn.execute("UPDATE bots SET state='paused' WHERE slug='finance'")
        yield conn


def parked(c, prerequisite_owner="bot:ops"):
    """cpo's task and the prerequisite another bot is working on, linked blocked_by."""
    task = H.task_create(c, "human:ana", "Ship the receipt gate", "", "bot:cpo", lint=False)
    pre = H.task_create(c, "human:ana", "Review the receipt gate fix", "", prerequisite_owner, lint=False)
    TR.relate(c, "bot:cpo", task["id"], pre["id"], "blocked_by")
    return task, pre


def jobs(c, slug):
    return [dict(r) for r in c.execute(
        "SELECT m.body, m.refs_json FROM jobs j JOIN messages m ON m.id=j.message_id WHERE j.bot=?", (slug,))]


def test_an_active_bot_owning_the_blocker_is_accepted_and_holds_the_task_without_storing_a_person(c):
    task, _ = parked(c)
    c.execute("DELETE FROM jobs")
    H.task_update(c, "bot:cpo", task["id"], status="waiting", waiting_on="bot:ops",
                  note="Needs the review of the receipt gate fix first.")
    after = H.task(c, task["id"])
    assert after["status"] == "waiting" and after["note"] == "Needs the review of the receipt gate fix first."
    assert after["waiting_on"] is None                    # people's Needs you and needs_human are untouched
    assert H.wait_detail(c, after) == {"why": "an open blocker", "on": "bot:ops"}
    # Held while it is open: the stranded sweep leaves it waiting, and nothing starts the builder on it.
    H.sweep_stranded(c, at=H.shift(H.now(), hours=6))
    assert H.task(c, task["id"])["status"] == "waiting"
    assert jobs(c, "cpo") == []


@pytest.mark.parametrize("outcome, words", [("done", "is finished"), ("declined", "was declined")])
def test_the_prerequisite_resolving_wakes_the_owner_once_with_the_outcome(c, outcome, words):
    task, pre = parked(c)
    H.task_update(c, "bot:cpo", task["id"], status="waiting", waiting_on="bot:ops",
                  note="Needs the review first.")
    c.execute("DELETE FROM jobs")
    H.task_update(c, "bot:ops", pre["id"], status=outcome, note="Reviewed.")
    woke = jobs(c, "cpo")
    assert len(woke) == 1 and woke[0]["body"].startswith("Unblocked: Ship the receipt gate")
    assert words in woke[0]["body"]
    assert json.loads(woke[0]["refs_json"])["blocker_status"] == outcome
    assert H.wait_detail(c, H.task(c, task["id"])) is None   # eligible again: nothing holds it


@pytest.mark.parametrize("target, says", [
    ("bot:finance", "finance is not an active bot on the team"),
    ("bot:nobody", "bot:nobody is not a person or an active bot on the team"),
    ("ops!", "ops! is not a person or an active bot on the team"),
    ("bot:cpo", "Nothing cpo owns blocks this task"),      # a bot with no open prerequisite here
])
def test_an_invalid_bot_target_is_refused_with_the_valid_target_and_changes_nothing(c, target, says):
    task, _ = parked(c)
    before = dict(H.task(c, task["id"]))
    with pytest.raises(H.Refused) as refused:
        H.task_update(c, "bot:cpo", task["id"], status="waiting", waiting_on=target, note="Waiting on it.")
    assert refused.value.rule == "waiting_on" and says in str(refused.value)
    assert "hub task wait" in str(refused.value)            # says how to name a valid target
    after = dict(H.task(c, task["id"]))
    assert {k: after[k] for k in ("status", "note", "waiting_on", "version")} == \
           {k: before[k] for k in ("status", "note", "waiting_on", "version")}


def test_a_bot_whose_prerequisite_already_finished_is_refused(c):
    task, pre = parked(c)
    H.task_update(c, "bot:ops", pre["id"], status="done", note="Reviewed.")
    with pytest.raises(H.Refused) as refused:
        H.task_update(c, "bot:cpo", task["id"], status="waiting", waiting_on="bot:ops", note="Waiting.")
    assert "Nothing ops owns blocks this task" in str(refused.value)
    assert H.task(c, task["id"])["status"] == "open"


def test_a_person_wait_still_works_and_a_bot_wait_replaces_it(c):
    task, _ = parked(c)
    H.task_update(c, "bot:cpo", task["id"], status="waiting", waiting_on="bot:ops", note="Needs review.")
    assert H.task(c, task["id"])["waiting_on"] is None
    other = H.task_create(c, "human:ana", "Write the launch post", "", "bot:cpo", lint=False)
    H.task_update(c, "bot:cpo", other["id"], status="waiting", waiting_on="ana",
                  note="Approve the launch post? I'll publish it after.")
    assert H.wait_detail(c, H.task(c, other["id"]))["on"] == "human:ana"

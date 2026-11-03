"""Quiet work stays visible and resumes (Dev 257).

Oct 19: Dev 242 was filed for the Builder's next run. A run carried it at 04:28, said in its reply it would do it
"after Dev 241 review", and finished. The task was then carried and nothing else: no job of its own, no stored time
to come back, and the board could not tell it from one still waiting. A person pressed Run now seven hours later. Now
a run that leaves a carried task without working it hands it back to the next run, a run that will not do it now
stores when to pick it up, and the task says which of delivered, eligible or queued it is.
"""

import pytest

from backend import github as G
from backend import health_monitor
from backend import hubdb as H
from backend import quiet_work as Q
from backend.tests.test_api import api, assign, get, post, ready, runner  # noqa: F401


def setup(api):
    r = runner(api)
    assign(api, r, "ops")
    ready(api, r, ["ops"])
    return r


def later(api, title="Watch the new refund requests"):
    return post(api, "tasks", {"owner": "ops", "title": title, "body": "Check the counts on your next run.",
                               "next_run": True})


def wake(api, r, text="How are the numbers today?"):
    """Something else starts a run of ops; the run is claimed and started. Returns the attempt."""
    post(api, "chat/ops", {"text": text})
    attempt = post(api, "jobs/claim", {"next_run": True}, token=r["token"])["attempt"]
    post(api, f"attempts/{attempt['id']}/started", {"thread_id": "t-" + attempt["id"]}, token=r["token"])
    return attempt


def finish(api, r, attempt, outcome="completed"):
    post(api, f"attempts/{attempt['id']}/complete", {"outcome": outcome, "text": "ok", "last_seq": 0},
         token=r["token"])


def shown(api, task):
    got = get(api, "tasks/" + task["id"])["task"]
    # `quiet` is the field's older name, still sent for one release.
    assert got.get("quiet") == got.get("deferred")
    return got


def own_jobs(api, task):
    with api.app.state.store.read() as c:
        return c.execute("SELECT count(*) FROM jobs j JOIN messages m ON m.id=j.message_id "
                         "JOIN conversations cv ON cv.id=m.conversation_id "
                         f"WHERE j.state='queued' AND {H.MESSAGE_TASK_SQL}=?", (task["id"],)).fetchone()[0]


def test_a_run_that_leaves_it_hands_it_to_the_next_run(api):
    r = setup(api)
    task = later(api)
    assert shown(api, task)["deferred"]["state"] == "eligible"
    listed = next(t for t in get(api, "tasks?owner=ops")["tasks"] if t["id"] == task["id"])
    assert listed["deferred"]["state"] == "eligible" and listed["quiet"] == listed["deferred"], "the board's rows too"
    first = wake(api, r)
    assert [t["id"] for t in first["next_run"]] == [task["id"]]
    assert "hub task later " + task["id"] in first["next_run"][0]["if_not_now"], "the run is told how to say not now"
    during = shown(api, task)["deferred"]
    assert during["state"] == "delivered" and during["run"] == first["id"] and during["running"] is True
    assert during["at"], "and when it was handed over"
    finish(api, r, first)
    after = shown(api, task)
    assert after["next_run_waiting"] is True, "not worked and nothing stored: it is not silently taken"
    assert after["deferred"]["state"] == "eligible" and after["deferred"]["returned_from"] == first["id"]
    second = wake(api, r, "Anything else?")
    assert [t["id"] for t in second["next_run"]] == [task["id"]], "the next run carries it again"


def test_a_run_that_worked_it_does_not_carry_it_again(api):
    r = setup(api)
    task = later(api)
    first = wake(api, r)
    with api.app.state.store.transaction() as c:
        H.task_comment(c, "bot:ops", task["id"], "Counts look normal; checking again Friday.", wake=False)
    finish(api, r, first)
    after = shown(api, task)
    assert after["next_run_waiting"] is False
    assert after["deferred"]["state"] == "delivered" and after["deferred"]["worked"] is True
    assert wake(api, r, "Anything else?")["next_run"] == [], "worked: not re-carried"


def test_a_step_move_or_an_explicit_handled_counts_as_worked(api):
    r = setup(api)
    task = later(api)
    first = wake(api, r)
    with api.app.state.store.transaction() as c:
        Q.defer(c, "bot:ops", task["id"], handled=True)
    finish(api, r, first)
    assert shown(api, task)["deferred"]["worked"] is True
    assert wake(api, r, "Anything else?")["next_run"] == []


def test_a_pull_request_refreshed_is_not_work_and_one_pushed_to_is(api):
    """Review 1: GitHub refreshing a linked pull request's checks is not the bot working the task; a push is."""
    r = setup(api)
    task = later(api)
    url = "https://github.com/ticoteam/tico/pull/412"
    with api.app.state.store.transaction() as c:
        H.task_link(c, "human:ana", task["id"], url)
    first = wake(api, r)
    with api.app.state.store.transaction() as c:
        G.pull_request(c, {"action": "refresh", "pull_request": {"html_url": url, "state": "open",
                                                                 "head": {"sha": "a1"}, "mergeable": True}})
        assert c.execute("SELECT updated_at FROM task_links WHERE task_id=?", (task["id"],)).fetchone()[0]
    finish(api, r, first)
    assert shown(api, task)["deferred"]["state"] == "eligible", "metadata only: handed back"
    second = wake(api, r, "Anything else?")
    assert [t["id"] for t in second["next_run"]] == [task["id"]]
    with api.app.state.store.transaction() as c:
        G.pull_request(c, {"action": "synchronize", "sender": {"login": "ops-bot"},
                           "pull_request": {"html_url": url, "state": "open", "head": {"sha": "b2"}}})
    finish(api, r, second)
    after = shown(api, task)
    assert after["deferred"]["state"] == "delivered" and after["deferred"]["worked"] is True, "a push is work"
    assert wake(api, r, "And now?")["next_run"] == []


def test_handled_drops_a_condition_stored_earlier(api):
    """Review 1: a run that deferred and then said it is done with it is not brought back by the old condition."""
    r = setup(api)
    blocker = post(api, "tasks", {"owner": "ops", "title": "Dev 241 review", "body": "Review it."})
    with api.app.state.store.transaction() as c:
        c.execute("DELETE FROM jobs")
    task = later(api, "Dev 242")
    first = wake(api, r)
    with api.app.state.store.transaction() as c:
        Q.defer(c, "bot:ops", task["id"], after=blocker["id"])
        Q.defer(c, "bot:ops", task["id"], handled=True)
    finish(api, r, first)
    held = shown(api, task)
    assert "resume" not in held["deferred"] and held["deferred"]["worked"] is True
    with api.app.state.store.transaction() as c:
        H.task_update(c, "bot:ops", blocker["id"], status="done", note="Reviewed.")
    assert own_jobs(api, task) == 0, "the former condition does not fire"
    assert shown(api, task)["next_run"] is True


def test_deferred_until_a_task_is_done_runs_once_when_it_is(api):
    r = setup(api)
    blocker = post(api, "tasks", {"owner": "ops", "title": "Dev 241 review", "body": "Review it."})
    with api.app.state.store.transaction() as c:
        c.execute("DELETE FROM jobs")       # ops's own run for the blocker is not what this tests
    task = later(api, "Dev 242")
    first = wake(api, r)
    with api.app.state.store.transaction() as c:
        Q.defer(c, "bot:ops", task["id"], after=blocker["id"])
    finish(api, r, first)
    held = shown(api, task)
    assert held["next_run_waiting"] is False, "the condition holds it"
    assert held["deferred"]["state"] == "delivered" and held["deferred"]["run"] == first["id"]
    assert held["deferred"]["resume"]["kind"] == "task" and held["deferred"]["resume"]["task"] == blocker["id"]
    assert held["deferred"]["words"] == "Picked up again after Dev 241 review is done."
    assert wake(api, r, "Anything else?")["next_run"] == [], "not carried while it waits"
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE jobs SET state='completed' WHERE state IN ('queued','leased','running')")
        c.execute("UPDATE attempts SET state='completed' WHERE state IN ('leased','running')")
        stalled = [row["id"] for row in H.stalled_tasks(c, at=H.shift(H.now(), hours=3))]
    assert task["id"] not in stalled, "the stall check leaves a deferred task to its condition"
    assert own_jobs(api, task) == 0
    with api.app.state.store.transaction() as c:
        H.task_update(c, "bot:ops", blocker["id"], status="done", note="Reviewed.")
    assert own_jobs(api, task) == 1, "one run of its own"
    resumed = shown(api, task)
    assert resumed["next_run"] is False and resumed["deferred"]["state"] == "queued"
    with api.app.state.store.transaction() as c:
        H.task_close(c, "human:ana", blocker["id"])
    assert own_jobs(api, task) == 1, "and only one"
    attempt = post(api, "jobs/claim", {"next_run": True}, token=r["token"])["attempt"]
    assert attempt["task"]["id"] == task["id"], "the run is the task"
    assert attempt["message"]["body"].startswith("Resume: Dev 242\nDev 241 review is done.")


def test_deferred_to_a_time_runs_once_then(api):
    r = setup(api)
    task = later(api)
    first = wake(api, r)
    at = H.shift(H.now(), hours=2)
    with api.app.state.store.transaction() as c:
        Q.defer(c, "bot:ops", task["id"], at=at)
    finish(api, r, first)
    assert shown(api, task)["deferred"]["resume"]["until"] == at
    with api.app.state.store.transaction() as c:
        assert Q.due(c, H.now()) == [], "not before its time"
        assert Q.due(c, H.shift(at, seconds=1)) == [task["id"]]
        assert Q.due(c, H.shift(at, seconds=60)) == [], "once"
    assert own_jobs(api, task) == 1


def test_next_run_puts_it_back_at_once(api):
    r = setup(api)
    task = later(api)
    first = wake(api, r)
    with api.app.state.store.transaction() as c:
        Q.defer(c, "bot:ops", task["id"], next_run=True)
    assert shown(api, task)["deferred"]["state"] == "eligible"
    finish(api, r, first)
    assert [t["id"] for t in wake(api, r, "Anything else?")["next_run"]] == [task["id"]]


def test_a_person_defers_through_the_api_and_wrong_asks_are_refused(api):
    setup(api)
    blocker = post(api, "tasks", {"owner": "ops", "title": "Dev 241 review", "body": "Review it."})
    task = later(api)
    got = post(api, f"tasks/{task['id']}/defer", {"after": blocker["id"]})
    assert got["deferred"]["resume"]["task"] == blocker["id"] and got["next_run_waiting"] is False
    post(api, f"tasks/{task['id']}/defer", {"after": blocker["id"], "next_run": True}, expected=422)
    post(api, f"tasks/{task['id']}/defer", {"at": "2020-01-01T00:00:00Z"}, expected=422)
    post(api, f"tasks/{blocker['id']}/defer", {"next_run": True}, expected=422)   # not a quiet task
    with api.app.state.store.transaction() as c:
        with pytest.raises(H.Refused):
            Q.defer(c, "bot:cpo", task["id"], next_run=True)     # another bot's task
        H.task_update(c, "bot:ops", blocker["id"], status="done", note="Reviewed.")
        other = H.task_create(c, "human:ana", "Later", "Later.", "bot:ops", next_run=True)
        with pytest.raises(H.Refused):
            Q.defer(c, "bot:ops", other["id"], after=blocker["id"])   # already finished: work it now


def test_run_now_drops_a_stored_condition(api):
    setup(api)
    blocker = post(api, "tasks", {"owner": "ops", "title": "Dev 241 review", "body": "Review it."})
    task = later(api)
    post(api, f"tasks/{task['id']}/defer", {"after": blocker["id"]})
    got = post(api, f"tasks/{task['id']}/run-now", {})["task"]
    assert "resume" not in got and got["deferred"]["state"] == "queued"


def test_health_names_a_task_handed_over_two_hours_ago_and_not_worked(api):
    r = setup(api)
    task = later(api)
    first = wake(api, r)
    finish(api, r, first)
    with api.app.state.store.transaction() as c:
        assert health_monitor._quiet_work_checks(c) == [], "not yet two hours"
        old = H.shift(H.now(), hours=-3)
        c.execute("UPDATE events SET created_at=? WHERE action=?", (old, Q.CARRIED))
        found = health_monitor._quiet_work_checks(c)
        assert [f["key"] for f in found] == [f"quiet_unworked:ops:{task['id']}"]
        assert "ops" in found[0]["title"] and task["id"][:8] in found[0]["detail"]
        assert "hub task later " + task["id"] in found[0]["fix"]
        Q.defer(c, "bot:ops", task["id"], at=H.shift(H.now(), hours=1))
        assert health_monitor._quiet_work_checks(c) == [], "a stored condition is not a finding"


def test_a_carrier_from_before_settling_counts_too(api):
    """A run that finished before this change left its task carried for good: Health names it."""
    r = setup(api)
    task = later(api)
    first = wake(api, r)
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE attempts SET state='completed', finished_at=? WHERE id=?", (H.now(), first["id"]))
        c.execute("UPDATE events SET created_at=? WHERE action=?", (H.shift(H.now(), hours=-3), Q.CARRIED))
        view = Q.view(c, H.task(c, task["id"]))
        assert view["state"] == "delivered" and view["worked"] is False
        assert [f["key"] for f in health_monitor._quiet_work_checks(c)] == [f"quiet_unworked:ops:{task['id']}"]


def test_health_leaves_a_task_the_run_worked_or_handled(api):
    """Review 1: past two hours, a run still going that commented, and a run that said handled, are no finding."""
    r = setup(api)
    commented = later(api, "Watch the refunds")
    handled = later(api, "Watch the signups")
    first = wake(api, r)
    old = H.shift(H.now(), hours=-3)
    with api.app.state.store.transaction() as c:
        H.task_comment(c, "bot:ops", commented["id"], "Counts look normal.", wake=False)
        Q.defer(c, "bot:ops", handled["id"], handled=True)
        c.execute("UPDATE events SET created_at=? WHERE action=?", (old, Q.CARRIED))
        assert health_monitor._quiet_work_checks(c) == [], "the run is still going and worked both"
        c.execute("UPDATE attempts SET state='completed', finished_at=? WHERE id=?", (H.now(), first["id"]))
        assert health_monitor._quiet_work_checks(c) == [], "a run that ended before settling, likewise"
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE attempts SET state='running', finished_at=NULL WHERE id=?", (first["id"],))
    finish(api, r, first)
    with api.app.state.store.transaction() as c:
        assert health_monitor._quiet_work_checks(c) == [], "and once settled"
        assert [Q.view(c, H.task(c, t["id"]))["worked"] for t in (commented, handled)] == [True, True]

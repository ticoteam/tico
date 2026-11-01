"""A subtask's result reaches its Waiting parent at once: the parent's other open subtasks, the very thing it waits
for, never hold that result back (execution.job_refusal, auto_tasks.waiting with `result`). Every other notice, and
every other wait (a snooze, a person, an approval, a worktree, a blocker outside the task), still holds it."""
import pytest

from backend import execution, task_relations as TR
from backend.store import H
from backend.tests.test_api import api, claim, post  # noqa: F401  (api is a fixture)
from backend.tests.test_task_lanes import task_bot

VERDICT = "VERDICT: approve PR #12 head "


@pytest.fixture(autouse=True)
def forget_claims():
    execution.CLAIMED_BUSY.clear()
    yield
    execution.CLAIMED_BUSY.clear()


def waiting_parent(api, results=3):
    """ops's task, Waiting since its turn ended, with `results` subtasks finance finished and cpo's still open:
    the live case behind Dev 593 (three verdicts queued behind one other owner's open subtask)."""
    machine = task_bot(api, lanes=True, others=("finance", "cpo"))
    post(api, "runners/heartbeat", {"version": "test", "platform": "test", "capacity": 8,
                                    "readiness": {bot: True for bot in ("ops", "finance", "cpo")},
                                    "capabilities": ["task_lanes_v2", "session_roots_v1"]}, token=machine["token"])
    with api.app.state.store.transaction() as c:
        parent = H.task_create(c, "human:ana", "Settle the freeze findings", "Collect the reviews.", "bot:ops",
                               lint=False)
    turn = claim(api, machine, "ops")
    assert turn["task"]["id"] == parent["id"]
    post(api, f"attempts/{turn['id']}/started", {"thread_id": "parent-session"}, machine["token"])
    with api.app.state.store.transaction() as c:
        done = [H.task_create(c, "bot:ops", f"Review {n}", "Review it.", "bot:finance", parent_id=parent["id"],
                              lint=False)["id"] for n in range(results)]
        still = H.task_create(c, "bot:ops", "Fix the blocker", "Fix it.", "bot:cpo", parent_id=parent["id"],
                              lint=False)["id"]
        H.task_update(c, "bot:ops", parent["id"], status="waiting", note="Waiting on the reviews and the fix.")
    post(api, f"attempts/{turn['id']}/complete", {"outcome": "completed", "last_seq": 0, "text": "Filed reviews."},
         machine["token"])
    with api.app.state.store.transaction() as c:
        row = H.task(c, parent["id"])
        assert row["status"] == "waiting" and row["blocked_since"]
        assert H.waiting_for(c, row) == "an open child task"
        for n, child in enumerate(done):
            H.task_update(c, "bot:finance", child, status="done", note=VERDICT + str(n) * 40)
    return machine, parent["id"], done, still


def queued(api, task_id, bot="ops"):
    with api.app.state.store.read() as c:
        return [dict(r) for r in c.execute(
            "SELECT j.*, m.refs FROM jobs j JOIN messages m ON m.id=j.message_id JOIN conversations cv "
            f"ON cv.id=m.conversation_id WHERE j.bot=? AND j.state='queued' AND {H.MESSAGE_TASK_SQL}=? "
            "ORDER BY j.rowid", (bot, task_id))]


def refusal(api, machine, job):
    """job_refusal's verdict on `job` as the claim would ask it, with nothing busy."""
    with api.app.state.store.read() as c:
        runner = c.execute("SELECT * FROM runners WHERE id=?", (machine["runner_id"],)).fetchone()
        ready = execution.readiness_document(runner["readiness"])
        return execution.job_refusal(c, job, {}, ready, set(), execution.lanes_on(runner),
                                     execution.roots_on(runner))


def results(api, task_id):
    return [j for j in queued(api, task_id) if H._json(j["refs"], {}).get("subtask")]


def test_every_queued_result_is_claimed_in_the_parents_session_while_another_subtask_is_open(api):
    machine, parent, done, still = waiting_parent(api)
    assert len(results(api, parent)) == 3
    turn = claim(api, machine, "ops")
    assert turn and turn["task"]["id"] == parent, "the first verdict is handed out, not refused task_wait"
    assert turn["message"]["refs"]["subtask"] == done[0]
    assert turn["task"]["session_task_id"] == parent and turn["lane"] == "task:" + parent
    post(api, f"attempts/{turn['id']}/started", {"thread_id": "parent-session"}, machine["token"])
    inputs = post(api, f"attempts/{turn['id']}/inputs", {}, machine["token"])["messages"]
    assert [m["refs"]["subtask"] for m in inputs] == done[1:], "the other two join the same turn"
    for m in inputs:
        post(api, f"attempts/{turn['id']}/inputs/{m['id']}/ack", {}, machine["token"])
    assert not results(api, parent)
    with api.app.state.store.read() as c:
        row = H.task(c, parent)
        assert row["status"] == "waiting" and row["blocked_since"], "the result does not move the task"
        assert H.task(c, still)["status"] == "open"


def test_any_other_notice_on_the_waiting_parent_is_still_held(api):
    machine, parent, done, still = waiting_parent(api, results=0)
    with api.app.state.store.transaction() as c:
        H._wake(c, H.task(c, parent), "bot:ops", "Stalled: Settle the freeze findings", {"wake": "stalled"},
                trigger="other")
    stalled, = queued(api, parent)
    assert refusal(api, machine, stalled) == "task_wait"
    assert claim(api, machine, "ops") is None


@pytest.mark.parametrize("wait", ["snooze", "waiting_on", "approval", "worktree", "blocker", "question", "manual"])
def test_a_result_still_waits_for_the_parents_other_waits(api, wait):
    machine, parent, done, still = waiting_parent(api, results=1)
    with api.app.state.store.transaction() as c:
        if wait == "snooze":
            c.execute("UPDATE tasks SET snoozed_until=? WHERE id=?", (H.shift(H.now(), hours=2), parent))
        elif wait == "waiting_on":
            c.execute("UPDATE tasks SET waiting_on='human:ana' WHERE id=?", (parent,))
        elif wait == "approval":
            c.execute("INSERT INTO approvals(id,kind,task_id,requested_by,created_at) VALUES(?,'send',?,'bot:ops',?)",
                      (H.new_id(), parent, H.now()))
        elif wait == "worktree":
            from backend.worktree_waits import ensure
            ensure(c)
            c.execute("INSERT INTO worktree_waits(id,task_id,owner,repo,generation,state,created_at,updated_at) "
                      "VALUES(?,?,'bot:ops','acme/app',0,'waiting',?,?)", (H.new_id(), parent, H.now(), H.now()))
        elif wait == "question":    # read past the skipped subtasks: wait_detail's own checks still hold it
            c.execute("INSERT INTO messages(id,conversation_id,from_actor,to_actor,kind,body,refs,created_at) "
                      "VALUES(?,?,'bot:ops','human:ana','ask','Which release?',?,?)",
                      (H.new_id(), H.task(c, parent)["conversation_id"], '{"task": "%s"}' % parent, H.now()))
        elif wait == "manual":
            c.execute("UPDATE tasks SET blocked_kind='manual',blocked_reason='Held until the freeze lifts' "
                      "WHERE id=?", (parent,))
        else:   # a task outside this one that it waits on, not one of its subtasks
            other = H.task_create(c, "human:ana", "Ship the release", "x", "bot:cpo", lint=False)["id"]
            TR.relate(c, "human:ana", other, parent, "blocks")
    result, = results(api, parent)
    assert refusal(api, machine, result) == "task_wait"
    assert claim(api, machine, "ops") is None


def test_a_subtask_that_also_blocks_its_parent_does_not_hold_another_subtasks_result(api):
    machine, parent, done, still = waiting_parent(api, results=1)
    with api.app.state.store.transaction() as c:
        TR.relate(c, "bot:ops", still, parent, "blocks")
    result, = results(api, parent)
    assert refusal(api, machine, result) is None

"""Token saving 3/9 (Dev 141): a stall or status check about a task that stands exactly as at its owner's last
checkpoint starts no run (backend/wake_rules.py same_state). Anything that differs, or anyone asking, still does."""

import json

from backend import hubdb as H
from backend import wake_rules as W
from backend.tests.test_api import api, assign, get, post, ready, runner  # noqa: F401
from backend.tests.test_wake_rules import skips


def jobs(c, bot):
    """Runs waiting to start (the checkpointing turn's own job is done)."""
    return c.execute("SELECT count(*) FROM jobs WHERE bot_id=? AND id NOT IN (SELECT job_id FROM attempts)",
                     (bot,)).fetchone()[0]


def setup(api):
    r = runner(api)
    assign(api, r, "ops")
    assign(api, r, "cpo")
    ready(api, r, ["ops", "cpo"])
    return r


def checkpointed(api, r, title="Build 3/9"):
    """A task of cpo's whose last completed turn left a checkpoint; returns the task id."""
    task = post(api, "tasks", {"owner": "cpo", "title": title, "body": "Build it."})
    complete_task(api, r, task["id"])
    return task["id"]


def complete_task(api, r, tid, during=None):
    attempt = post(api, "jobs/claim", {"next_run": True, "bot": "cpo"}, token=r["token"])["attempt"]
    assert attempt["task"]["id"] == tid
    post(api, f"attempts/{attempt['id']}/started", {"thread_id": "t1"}, token=r["token"])
    if during:
        during()
    post(api, f"attempts/{attempt['id']}/complete", {"outcome": "completed", "text": "Waiting on review.",
                                                     "last_seq": 0}, token=r["token"])
    return attempt


def check(c, tid, wake="status_check"):
    """The stall check the scheduler writes for a task that has not moved."""
    c.execute("DELETE FROM jobs WHERE id NOT IN (SELECT job_id FROM attempts)")
    task = H.task(c, tid)
    msg = H._wake(c, task, task["owner_id"], "Status check: anything to report?", {"wake": wake}, trigger="stall_check")
    return msg


def test_a_check_on_a_task_that_stands_as_at_the_checkpoint_is_skipped_and_logged(api):
    r = setup(api)
    tid = checkpointed(api, r)
    with api.app.state.store.transaction() as c:
        assert H.task(c, tid)["checkpoint_state"]
        msg = check(c, tid)
        assert jobs(c, "cpo") == 0
        assert msg["refs"]["quiet"] is True and msg["refs"][W.MARK] == "same_state"
        [logged] = skips(c)
        assert {k: logged["detail"][k] for k in ("bot", "task", "rule", "trigger")} == {
            "bot": "cpo", "task": tid, "rule": "same_state", "trigger": "stall_check"}
        # Nothing to carry to the next run: the check only asked whether anything moved.
        assert c.execute("SELECT count(*) FROM notes WHERE message_id=?", (msg["id"],)).fetchone()[0] == 0
        # And the skip itself changes nothing: the next check is skipped as well.
        check(c, tid, "stalled")
        assert jobs(c, "cpo") == 0 and len(skips(c)) == 2


def test_the_task_view_does_not_carry_the_stored_state(api):
    r = setup(api)
    tid = checkpointed(api, r)
    assert "checkpoint_state" not in get(api, "tasks/" + tid)["task"]


def test_no_checkpoint_means_the_check_runs(api):
    setup(api)
    with api.app.state.store.transaction() as c:
        task = H.task_create(c, "bot:ops", "Fresh", "Nothing yet", "bot:cpo")
        assert not H.task(c, task["id"])["checkpoint_state"]
        check(c, task["id"])
        assert jobs(c, "cpo") == 1 and skips(c) == []


def test_a_checkpoint_without_a_stored_state_means_the_check_runs(api):
    r = setup(api)
    tid = checkpointed(api, r)
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE tasks SET checkpoint_state=NULL WHERE id=?", (tid,))
        check(c, tid)
        assert jobs(c, "cpo") == 1


def changes(c, tid):
    """One change per entry, each made the way it happens, none by cpo itself."""
    other = H.task_create(c, "bot:ops", "Docs " + tid[:8], "Write them", "bot:ops")

    def comment():
        H.task_comment(c, "bot:ops", tid, "Heads up: the spec moved.")

    def status():
        c.execute("UPDATE tasks SET status='waiting' WHERE id=?", (tid,))

    def step():
        c.execute("UPDATE tasks SET step_id=(SELECT id FROM task_steps LIMIT 1) WHERE id=?", (tid,))

    def relation():
        c.execute("INSERT INTO task_relations(from_task,to_task,kind,created_at) VALUES(?,?,'related',?)",
                  (*sorted((other["id"], tid)), H.now()))

    def relation_state():
        relation()
        c.execute("DELETE FROM jobs WHERE id NOT IN (SELECT job_id FROM attempts)")
        c.execute("UPDATE tasks SET checkpoint_state=? WHERE id=?", (W.state_of(c, H.task(c, tid)), tid))
        c.execute("UPDATE tasks SET status='done' WHERE id=?", (other["id"],))

    def pr_head():
        c.execute("INSERT INTO task_links(id,task_id,kind,url,title,state,added_by,created_at,pr_sha,checks) "
                  "VALUES(?,?,'pr','https://github.com/o/r/pull/9','PR','open','bot:cpo',?,'abc','pending')",
                  ("lk" + tid[:8], tid, H.now()))

    def pr_checks():
        pr_head()
        c.execute("DELETE FROM jobs WHERE id NOT IN (SELECT job_id FROM attempts)")
        c.execute("UPDATE tasks SET checkpoint_state=? WHERE id=?", (W.state_of(c, H.task(c, tid)), tid))
        c.execute("UPDATE task_links SET checks='passing' WHERE task_id=?", (tid,))

    return {"comment": comment, "status": status, "step": step, "relation": relation,
            "relation state": relation_state, "pr head": pr_head, "pr checks": pr_checks}


def test_any_one_field_changed_means_the_check_runs(api):
    r = setup(api)
    for name in ("comment", "status", "step", "relation", "relation state", "pr head", "pr checks"):
        tid = checkpointed(api, r, "Task " + name)
        with api.app.state.store.transaction() as c:
            changes(c, tid)[name]()
            check(c, tid)
            assert jobs(c, "cpo") == 1, name
            assert not [s for s in skips(c) if s["detail"]["task"] == tid], name


def test_a_persons_comment_with_otherwise_the_same_state_runs(api):
    r = setup(api)
    tid = checkpointed(api, r)
    post(api, f"tasks/{tid}/comments", {"text": "Any update?"})          # the person who made the task
    with api.app.state.store.transaction() as c:
        check(c, tid)
        assert jobs(c, "cpo") == 1


def test_an_ask_a_person_made_is_never_held(api):
    r = setup(api)
    tid = checkpointed(api, r)
    with api.app.state.store.transaction() as c:
        c.execute("DELETE FROM jobs WHERE id NOT IN (SELECT job_id FROM attempts)")
        task = H.task(c, tid)
        msg = H._wake(c, task, "bot:cpo", "Status check", {"wake": "status_check", "needs_action": True},
                      trigger="stall_check")
        assert W.MARK not in msg["refs"] and jobs(c, "cpo") == 1


def test_wakes_that_are_not_plain_checks_still_run(api):
    r = setup(api)
    tid = checkpointed(api, r)
    for wake in ("stuck_retry", "stuck_cause", "restate_ask"):
        with api.app.state.store.transaction() as c:
            check(c, tid, wake)
            assert jobs(c, "cpo") == 1, wake


def test_someone_changing_the_task_during_the_turn_leaves_no_state_to_compare(api):
    r = setup(api)
    task = post(api, "tasks", {"owner": "cpo", "title": "Busy", "body": "Build it."})
    attempt = post(api, "jobs/claim", {"next_run": True, "bot": "cpo"}, token=r["token"])["attempt"]
    post(api, f"attempts/{attempt['id']}/started", {"thread_id": "t1"}, token=r["token"])
    post(api, f"tasks/{task['id']}/comments", {"text": "While you work: use blue."})   # a person, mid-turn
    post(api, f"attempts/{attempt['id']}/complete", {"outcome": "completed", "text": "Done.", "last_seq": 0},
         token=r["token"])
    with api.app.state.store.transaction() as c:
        assert H.task(c, task["id"])["checkpoint"] == "Done."
        assert not H.task(c, task["id"])["checkpoint_state"]
        check(c, task["id"])
        assert jobs(c, "cpo") == 1


def test_another_bots_task_is_not_held_for_this_bots_checkpoint(api):
    r = setup(api)
    tid = checkpointed(api, r)
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE tasks SET owner_id='bot:ops' WHERE id=?", (tid,))
        c.execute("DELETE FROM jobs WHERE id NOT IN (SELECT job_id FROM attempts)")
        task = H.task(c, tid)
        H._wake(c, task, "bot:ops", "Status check", {"wake": "status_check"}, trigger="stall_check")
        assert jobs(c, "ops") == 1


def test_synchronizing_an_existing_pr_head_with_unchanged_checks_runs_the_next_check(api):
    from backend import github as G
    r = setup(api)
    task = post(api, "tasks", {"owner": "cpo", "title": "Review code", "body": "Build it."})
    tid = task["id"]
    url = "https://github.com/acme/widget/pull/9"
    with api.app.state.store.transaction() as c:
        H.task_link(c, "human:ana", tid, url, kind="pr")
        G.pull_request(c, {"action": "opened", "pull_request": {
            "html_url": url, "head": {"sha": "a" * 40}, "mergeable": None}})
    complete_task(api, r, tid)
    with api.app.state.store.transaction() as c:
        before = dict(c.execute("SELECT * FROM task_links WHERE task_id=?", (tid,)).fetchone())
        assert before["checks"] == "pending" and before["mergeable"] == "unknown"
        assert before["pr_sha"] is None
        G.pull_request(c, {"action": "synchronize", "pull_request": {
            "html_url": url, "head": {"sha": "b" * 40}, "mergeable": None}})
        after = dict(c.execute("SELECT * FROM task_links WHERE task_id=?", (tid,)).fetchone())
        assert json.loads(after["detail"])["head_sha"] == "b" * 40
        assert [after[k] for k in ("pr_sha", "checks", "mergeable")] == [
            before[k] for k in ("pr_sha", "checks", "mergeable")]
        assert jobs(c, "cpo") == 0       # synchronize itself is quiet
        assert W.state_of(c, H.task(c, tid)) != H.task(c, tid)["checkpoint_state"]
        check(c, tid)
        assert jobs(c, "cpo") == 1


def test_editing_an_existing_comment_through_the_api_runs_the_next_check(api):
    r = setup(api)
    task = post(api, "tasks", {"owner": "cpo", "title": "Use the correction", "body": "Build it."})
    tid = task["id"]
    comment = post(api, f"tasks/{tid}/comments", {"text": "Use blue."})["comment"]
    complete_task(api, r, tid)
    with api.app.state.store.read() as c:
        before = H.task(c, tid)
        assert W.state_of(c, before) == before["checkpoint_state"]
    edited = post(api, f"tasks/{tid}/comments/{comment['id']}", {"text": "Correction: use green."})
    assert edited["woke"] is False and edited["comment"]["id"] == comment["id"]
    with api.app.state.store.transaction() as c:
        assert H.task(c, tid)["version"] == before["version"]
        assert W.state_of(c, H.task(c, tid)) != before["checkpoint_state"]
        check(c, tid)
        assert jobs(c, "cpo") == 1


def test_an_existing_related_task_changing_mid_turn_is_not_checkpointed_unseen(api):
    r = setup(api)
    other = post(api, "tasks", {"owner": "ops", "title": "Related work", "body": "Do it."})
    task = post(api, "tasks", {"owner": "cpo", "title": "Parent work", "body": "Build it."})
    tid = task["id"]
    with api.app.state.store.transaction() as c:
        H.TR.relate(c, "human:ana", tid, other["id"], "related")
    attempt = complete_task(api, r, tid, during=lambda: post(api, f"tasks/{other['id']}", {
        "status": "doing", "version": other["version"]}))
    with api.app.state.store.transaction() as c:
        assert H.task(c, other["id"])["status"] == "doing"
        assert H.task(c, tid)["version"] == attempt["task"]["version"]
        assert H.task(c, tid)["checkpoint"] == "Waiting on review."
        assert H.task(c, tid)["checkpoint_state"] is None
        check(c, tid)
        assert jobs(c, "cpo") == 1


def test_scheduler_skips_never_become_retries_or_diagnostics(api, monkeypatch):
    from backend.execution import LANES
    r = setup(api)
    post(api, "runners/heartbeat", {"version": "test", "platform": "test", "capabilities": [LANES],
                                  "readiness": {"cpo": True, "ops": True}}, token=r["token"])
    tid = checkpointed(api, r)
    with api.app.state.store.transaction() as c:
        # Both the idle stall and the recent-session spot-check paths see the same settled task.
        assert H.task(c, tid)["checkpoint_state"]
        start = H.now()
        for spot_only in (False, True):
            if spot_only:
                c.execute("UPDATE tasks SET next_run=0 WHERE id=?", (tid,))
                monkeypatch.setattr(H, "stalled_tasks", lambda *a, **k: [])
            next_run = H.task(c, tid)["next_run"]
            for minutes in (31, 62, 93, 124):
                at = H.shift(start, seconds=minutes * 60)
                monkeypatch.setattr(H, "now", lambda: at)
                candidates = H.spot_check_tasks(c, at) if spot_only else H.stalled_tasks(c, at)
                assert tid in [t["id"] for t in candidates], (spot_only, minutes)
                result = H.wake_stalled(c, at)
                assert all(not ids for ids in result.values()), result
                assert jobs(c, "cpo") == 0
                assert H.task(c, tid)["next_run"] == next_run
        assert [s["detail"]["rule"] for s in skips(c)] == ["same_state"] * 8
        assert c.execute("SELECT count(*) FROM events WHERE target=? AND action IN "
                         "('task.stall_wake','task.stuck_retry','task.stuck','task.stall_escalated')",
                         (tid,)).fetchone()[0] == 0
        assert c.execute("SELECT count(*) FROM tasks WHERE title LIKE 'Find why %'").fetchone()[0] == 0


def test_a_second_active_turn_keeps_status_retry_and_diagnostic_recovery(api, monkeypatch):
    from backend.execution import LANES
    r = setup(api)
    post(api, "runners/heartbeat", {"version": "test", "platform": "test", "capabilities": [LANES],
                                  "readiness": {"cpo": True, "ops": True}}, token=r["token"])
    task = post(api, "tasks", {"owner": "cpo", "title": "Resume unchanged work", "body": "Build it."})
    tid = task["id"]
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE tasks SET next_run=0 WHERE id=?", (tid,))
    complete_task(api, r, tid)
    post(api, f"tasks/{tid}/run-now", {})
    attempt = post(api, "jobs/claim", {"bot": "cpo"}, token=r["token"])["attempt"]
    assert attempt["lane"] == "task:" + tid
    with api.app.state.store.transaction() as c:
        row = H.task(c, tid)
        assert row["checkpoint_state"] == W.state_of(c, row)
        # Even a leased turn must not borrow the preceding completed turn's idle checkpoint.
        assert W.MARK not in check(c, tid)["refs"]
        c.execute("DELETE FROM jobs WHERE id NOT IN (SELECT job_id FROM attempts)")
    post(api, f"attempts/{attempt['id']}/started", {"thread_id": "t1"}, token=r["token"])
    with api.app.state.store.transaction() as c:
        c.execute("INSERT OR IGNORE INTO bots(id,slug,display_name,state) VALUES('botops','botops','BotOps','active')")
        row = H.task(c, tid)
        assert row["status"] == "doing" and not row["next_run"]
        assert row["checkpoint_state"] == W.state_of(c, row)
        start = H.now()
        # A real second turn, resuming the same thread, has made no task progress. While it is live, no check or
        # retry steers it (H.stall_skip); the checks run again only once the turn ends.
        for minutes in (31, 61, 91):
            at = H.shift(start, seconds=minutes * 60)
            monkeypatch.setattr(H, "now", lambda: at)
            assert tid in [t["id"] for t in H.spot_check_tasks(c, at)]
            assert H.stall_skip(c, H.task(c, tid), at) == "live_session"
            assert not any(H.wake_stalled(c, at).values())
        assert c.execute("SELECT count(*) FROM events WHERE target=? AND action IN "
                         "('task.stall_wake','task.stuck_retry','task.stuck','task.stall_escalated')",
                         (tid,)).fetchone()[0] == 0
        assert c.execute("SELECT count(*) FROM tasks WHERE title LIKE 'Find why %'").fetchone()[0] == 0


def test_a_related_task_changed_by_another_lane_of_the_same_bot_is_not_seen(api):
    from backend.execution import LANES
    r = setup(api)
    post(api, "runners/heartbeat", {"version": "test", "platform": "test", "capacity": 8,
                                  "capabilities": [LANES], "readiness": {"cpo": True, "ops": True}},
         token=r["token"])
    first = post(api, "tasks", {"owner": "cpo", "title": "Lane A", "body": "Build it."})
    second = post(api, "tasks", {"owner": "cpo", "title": "Lane B", "body": "Build it too."})
    with api.app.state.store.transaction() as c:
        H.TR.relate(c, "human:ana", first["id"], second["id"], "related")
    a, b = [post(api, "jobs/claim", {"next_run": True, "bot": "cpo"}, token=r["token"])["attempt"]
            for _ in range(2)]
    assert [a["lane"], b["lane"]] == ["task:" + first["id"], "task:" + second["id"]]
    for attempt in (a, b):
        post(api, f"attempts/{attempt['id']}/started", {"thread_id": attempt["lane"]}, token=r["token"])
    with api.app.state.store.read() as c:
        before = H.task(c, first["id"])
        comments_before = c.execute("SELECT count(*) FROM messages WHERE conversation_id=? AND sender_id<>?",
                                    (first["conversation_id"], H.KEEPER)).fetchone()[0]
    # B's leased identity changes an already-related General task while A runs.
    post(api, f"tasks/{second['id']}", {"status": "waiting", "version": b["task"]["version"]}, token=b["token"])
    # B can finish before A; checking only currently running siblings would miss the write.
    post(api, f"attempts/{b['id']}/complete", {"outcome": "completed", "text": "B moved.", "last_seq": 0},
         token=r["token"])
    with api.app.state.store.read() as c:
        assert c.execute("SELECT count(*) FROM messages WHERE conversation_id=? AND sender_id<>?",
                         (first["conversation_id"], H.KEEPER)).fetchone()[0] == comments_before
    post(api, f"attempts/{a['id']}/complete", {"outcome": "completed", "text": "A checkpoint.", "last_seq": 0},
         token=r["token"])
    with api.app.state.store.transaction() as c:
        assert H.task(c, second["id"])["status"] == "waiting"
        row = H.task(c, first["id"])
        assert row["version"] == before["version"]
        assert row["checkpoint"] == "A checkpoint."
        assert row["checkpoint_state"] is None
        check(c, first["id"])
        assert jobs(c, "cpo") == 1

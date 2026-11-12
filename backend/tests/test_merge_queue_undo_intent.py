"""An old visit's undo never takes out a PR that an open visit still wants queued (SM-02, docs/pipelines-v3.md
Merge). The undo is moot while an open visit (the task's next one, or another task's on the same PR) wants the PR
queued at the same head; one that reached GitHub anyway owes that visit's queue again. A PR GitHub removed for
failed checks still waits for an explicit queue-again, and a merged PR stays merged.

Each schedule below drives the real task moves and the Heartbeat's source-action step (`MQ.run`) against the in-memory
GitHub; a GitHub call held open on a thread stands for a slow GitHub answer."""

import threading

from backend import task_engine as E
from backend.item_sources import github as MQ
from backend.tests.helpers import engine as X
from backend.tests.helpers.fake_github import FakeGitHub
from backend.tests.helpers.github_facts_merge_queue import REPO, RM, in_merge, later, new, seen, stage


def held(gh, call):
    """Hold the next `call` to GitHub open until `release` is set: it is out when `out` is set. Later calls go
    through at once."""
    real = getattr(gh, call)
    out, release = threading.Event(), threading.Event()

    def slow(*args):
        setattr(gh, call, real)
        out.set()
        assert release.wait(15)
        return real(*args)

    setattr(gh, call, slow)
    return out, release


def in_background(store, gh):
    thread = threading.Thread(target=MQ.run, args=(store, gh))
    thread.start()
    return thread


def finish(thread, release):
    release.set()
    thread.join(15)
    assert not thread.is_alive()


def current(c, task_id):
    """The open visit's queue actions and the task's actions still owed."""
    visit = E.task(c, task_id)["visit"]["id"]
    acts = [(e["kind"], e["state"]) for e in MQ.effects(c, visit)]
    owed = [
        r[0]
        for r in c.execute(
            "SELECT e.kind FROM effects e JOIN visits v ON v.id = e.visit_id WHERE v.task_id=? AND e.state='owed'",
            (task_id,),
        )
    ]
    return acts, owed


def queue_calls(gh):
    return [call[0] for call in gh.calls if call[0] in ("enqueue", "dequeue")]


def undo_states(c, task_id):
    return [
        r[0]
        for r in c.execute(
            "SELECT e.state FROM effects e JOIN visits v ON v.id = e.visit_id WHERE v.task_id=? AND e.kind='undo' "
            "ORDER BY e.created_at, e.rowid",
            (task_id,),
        )
    ]


def back_and_forward(c, task_id):
    E.move(c, task_id, "Build", X.ANA, "Review again")
    E.move(c, task_id, "next", X.ANA)


def close_and_reopen(c, task_id):
    E.move(c, task_id, E.CLOSED, X.ANA, "Not now", E.task(c, task_id)["version"])
    E.move(c, task_id, E.REOPEN, X.ANA, "Try again", E.task(c, task_id)["version"])
    E.move(c, task_id, "next", X.ANA)


def test_back_and_forward_while_the_queue_call_is_out_leaves_the_pr_queued(company):
    """Merge -> Build -> Merge while the first visit's enqueue is out: it succeeds after its visit left, which owes an
    undo; the new visit wants the same PR at the same head, so that undo is moot and GitHub is never asked to dequeue."""
    gh = FakeGitHub()
    with company.transaction() as c:
        task_id = in_merge(c, gh, 1)
    out, release = held(gh, "enqueue")
    thread = in_background(company, gh)
    assert out.wait(10)
    with company.transaction() as c:
        back_and_forward(c, task_id)
    finish(thread, release)
    MQ.run(company, gh)
    with company.read() as c:
        assert stage(c, task_id) == "Merge"
        assert current(c, task_id) == ([("act", "done")], [])
        assert undo_states(c, task_id) == ["moot"]
        assert MQ.pr(c, REPO, 1)["queue_state"] == "queued"
    assert gh.prs[(REPO, 1)]["queue_state"] == "queued"
    assert "dequeue" not in queue_calls(gh)


def test_close_and_reopen_while_the_queue_call_is_out_leaves_the_pr_queued(company):
    gh = FakeGitHub()
    with company.transaction() as c:
        task_id = in_merge(c, gh, 1)
    out, release = held(gh, "enqueue")
    thread = in_background(company, gh)
    assert out.wait(10)
    with company.transaction() as c:
        close_and_reopen(c, task_id)
        assert stage(c, task_id) == "Merge"
    finish(thread, release)
    MQ.run(company, gh)
    with company.read() as c:
        assert current(c, task_id) == ([("act", "done")], [])
        assert undo_states(c, task_id) == ["moot"]
    assert gh.prs[(REPO, 1)]["queue_state"] == "queued"
    assert "dequeue" not in queue_calls(gh)


def test_one_task_leaving_merge_keeps_the_pr_another_task_on_merge_queued(company):
    """Two tasks link one PR and both reach Merge; one is sent back. Its undo is moot: the other task still wants the
    PR queued at that head, and its `tico/merge-ok` stays success."""
    gh = FakeGitHub()
    with company.transaction() as c:
        first = in_merge(c, gh, 1)
        second = new(c, 1)
        seen(c, gh, 1)
        assert stage(c, second) == "Merge"
    MQ.run(company, gh)
    with company.transaction() as c:
        E.move(c, first, "Build", X.ANA, "Review again")
    MQ.run(company, gh)
    head = gh.prs[(REPO, 1)]["head_sha"]
    with company.read() as c:
        assert undo_states(c, first) == ["moot"]
        assert current(c, second) == ([("act", "done")], [])
    assert gh.prs[(REPO, 1)]["queue_state"] == "queued"
    assert "dequeue" not in queue_calls(gh)
    assert gh.merge_ok[(REPO, head)][-1][0] is True


def test_an_undo_already_out_when_the_new_visit_queues_owes_the_queue_again(company):
    """The old visit's dequeue is out; the task comes back to Merge and another pass queues the PR before that dequeue
    lands. The dequeue took out what the new visit wants, so its queue is owed again and the next pass restores it."""
    gh = FakeGitHub()
    with company.transaction() as c:
        task_id = in_merge(c, gh, 1)
    MQ.run(company, gh)
    with company.transaction() as c:
        E.move(c, task_id, "Build", X.ANA, "Review again")
    out, release = held(gh, "dequeue")
    thread = in_background(company, gh)
    assert out.wait(10)
    with company.transaction() as c:
        E.move(c, task_id, "next", X.ANA)
    MQ.run(company, gh)  # the new visit's queue goes out while the old dequeue is still out
    assert gh.prs[(REPO, 1)]["queue_state"] == "queued"
    finish(thread, release)
    assert gh.prs[(REPO, 1)]["queue_state"] == "none"  # the old dequeue landed
    with company.read() as c:
        assert current(c, task_id) == ([("act", "owed")], ["act"])
    MQ.run(company, gh)
    with company.read() as c:
        assert current(c, task_id) == ([("act", "done")], [])
        assert MQ.pr(c, REPO, 1)["queue_state"] == "queued"
    assert gh.prs[(REPO, 1)]["queue_state"] == "queued"
    # the second pass dequeues too (the new queue was only owed then), and the held dequeue lands after it queued
    assert queue_calls(gh) == ["enqueue", "dequeue", "enqueue", "dequeue", "enqueue"]


def test_an_old_undo_at_another_head_runs_and_the_new_head_is_queued_again(company):
    """A new commit while the task is back in Build: the old visit's undo is pinned to the old head, so it is carried
    out (failure on the old head), and when its dequeue lands after the new head was queued, the new head is owed its
    queue again. Success ends on the new head."""
    gh = FakeGitHub()
    with company.transaction() as c:
        task_id = in_merge(c, gh, 1)
    MQ.run(company, gh)
    old_head = gh.prs[(REPO, 1)]["head_sha"]
    with company.transaction() as c:
        E.move(c, task_id, "Build", X.ANA, "Review again")
        new_head = gh.push(REPO, 1)
        gh.set_checks(REPO, new_head, "success")
        seen(c, gh, 1)
    out, release = held(gh, "dequeue")
    thread = in_background(company, gh)
    assert out.wait(10)
    with company.transaction() as c:
        if stage(c, task_id) != "Merge":
            E.move(c, task_id, "next", X.ANA)
        assert stage(c, task_id) == "Merge"
    MQ.run(company, gh)
    finish(thread, release)
    MQ.run(company, gh)
    with company.read() as c:
        assert current(c, task_id) == ([("act", "done")], [])
        assert undo_states(c, task_id) == ["done"]
        assert [e["pinned"] for e in MQ.effects(c, E.task(c, task_id)["visit"]["id"])] == [new_head]
    assert gh.prs[(REPO, 1)]["queue_state"] == "queued"
    assert gh.merge_ok[(REPO, old_head)][-1][0] is False
    assert gh.merge_ok[(REPO, new_head)][-1][0] is True


def test_a_pr_github_removed_for_failed_checks_waits_for_an_explicit_queue_again(company):
    """GitHub took the PR out for failed checks. One of two tasks on it goes back: its undo is moot, the other's queue is
    not owed again by itself, and only `tico task queue` puts it back."""
    gh = FakeGitHub()
    with company.transaction() as c:
        first = in_merge(c, gh, 1)
        second = new(c, 1)
        seen(c, gh, 1)
    MQ.run(company, gh)
    gh.drop(REPO, 1, "The merge group's checks failed", check="e2e", url="https://ci.example/run/9")
    MQ.refresh(company, gh, later(10))
    with company.transaction() as c:
        E.move(c, first, "Build", X.ANA, "Review again")
    MQ.run(company, gh)
    with company.read() as c:
        assert undo_states(c, first) == ["moot"]
        assert current(c, second) == ([("act", "done")], [])
    assert gh.prs[(REPO, 1)]["queue_state"] == "removed"
    assert queue_calls(gh) == ["enqueue", "enqueue"]
    with company.transaction() as c:
        MQ.requeue(c, second, RM, seen_version=E.task(c, second)["version"])
    MQ.run(company, gh)
    assert gh.prs[(REPO, 1)]["queue_state"] == "queued"


def test_a_pr_github_merged_stays_merged(company):
    """The enqueue is out across back and forward, and GitHub merges the PR before the next pass: the task moves on,
    the old undo's dequeue finds it merged, and nothing queues it again."""
    gh = FakeGitHub()
    with company.transaction() as c:
        task_id = in_merge(c, gh, 1)
    out, release = held(gh, "enqueue")
    thread = in_background(company, gh)
    assert out.wait(10)
    with company.transaction() as c:
        back_and_forward(c, task_id)
    finish(thread, release)
    gh.merge(REPO, 1)
    with company.transaction() as c:
        seen(c, gh, 1)
    MQ.run(company, gh)
    with company.read() as c:
        assert stage(c, task_id) == "Release"
        assert MQ.pr(c, REPO, 1)["state"] == "merged"
        assert [r[0] for r in c.execute("SELECT kind FROM effects WHERE state='owed'")] == []
    assert gh.prs[(REPO, 1)]["state"] == "merged"
    assert queue_calls(gh) == ["enqueue", "dequeue"]


def test_an_undo_is_not_skipped_for_a_queue_that_is_only_owed(company):
    """Back and forward: the old visit's undo is not moot against the new visit's queue while that queue is only owed.
    The new queue's call fails once, and the task goes back before its retry: that owed queue is mooted unrun (no undo
    of its own), so the old undo must already have taken the PR out."""
    gh = FakeGitHub()
    with company.transaction() as c:
        task_id = in_merge(c, gh, 1)
    MQ.run(company, gh)
    with company.transaction() as c:
        back_and_forward(c, task_id)
    gh.fail_next("ready")  # the new visit's first call fails: tried again later
    MQ.run(company, gh)
    with company.transaction() as c:
        E.move(c, task_id, "Build", X.ANA, "Review again")
    MQ.run(company, gh)
    with company.read() as c:
        assert stage(c, task_id) == "Build"
        assert [r[0] for r in c.execute("SELECT kind FROM effects WHERE state='owed'")] == []
    assert gh.prs[(REPO, 1)]["queue_state"] == "none"

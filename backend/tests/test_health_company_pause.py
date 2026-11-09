"""Health while the company is paused (P793 ticket 3): Check health opens no task for any finding, counts the findings it
holds, and sends ONE digest task on the first run after Resume; a bot its owner paused is paused, not stuck.

Synthetic fixture team only (backend/tests/test_environment.py via test_maintenance_health's built install).
"""

from backend import hubdb as H
from backend import invariants
from backend.maintenance_steps import check_health
from backend.tests.test_maintenance_health import (  # noqa: F401  (fixtures)
    botops_tasks,
    built,
    code_findings,
    environment,
    item,
    readers,
    run,
)
from backend.tests.test_onboarding import signed_in


def _company(api, action):
    api.post(f"/api/v2/company/{action}", json={}, headers=signed_in()).raise_for_status()


def _held(api):
    with api.app.state.store.read() as c:
        return check_health.held_count(c)


def _digests(api):
    return [t for t in botops_tasks(api) if "still open after the pause" in t["title"]]


def _two_urgent(code_findings):
    code_findings[:] = [
        item("push_stuck", key="push_stuck:one", title="Pushes are stuck one", subject="one"),
        item("push_stuck", key="push_stuck:two", title="Pushes are stuck two", subject="two"),
    ]


def test_a_paused_company_opens_no_task_for_a_finding_and_counts_what_it_holds(environment, code_findings):
    api, _ = built(environment)
    _two_urgent(code_findings)
    _company(api, "pause")
    result = run(api)
    assert botops_tasks(api) == []  # urgent findings would file on the first run; the pause holds them
    assert result["woke"] == 0
    assert _held(api) == 2
    assert len(result["details"]["held"]) == 2
    run(api)  # the next hourly run: still nothing opened, still counted once
    assert botops_tasks(api) == [] and _held(api) == 2


def test_resume_sends_one_digest_for_the_open_findings_not_one_per_finding(environment, code_findings):
    api, _ = built(environment)
    _two_urgent(code_findings)
    _company(api, "pause")
    run(api)
    _company(api, "resume")
    result = run(api)
    (digest,) = _digests(api)  # one task, for two findings
    assert "2 health findings" in digest["title"]
    assert result["details"]["digest"] == {"task": digest["id"], "findings": 2, "problems": 2}
    assert result["woke"] == 1
    run(api)  # the next run adds nothing: the digest covers both, no second task appears
    assert botops_tasks(api) == [digest] and _held(api) == 0


def test_a_digest_botops_closes_is_not_reopened_per_finding_until_the_problem_clears(environment, code_findings):
    api, _ = built(environment)
    _two_urgent(code_findings)
    _company(api, "pause")
    run(api)
    _company(api, "resume")
    run(api)
    (digest,) = _digests(api)
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE tasks SET status='done' WHERE id=?", (digest["id"],))
    run(api)  # the findings are still there: no per-finding task opens
    assert len(botops_tasks(api)) == 1
    code_findings[:] = []
    run(api)  # they clear: the digest's problems are cleared
    code_findings[:] = [item("push_stuck", key="push_stuck:one", title="Pushes are stuck one", subject="one")]
    run(api)  # one comes back: it opens its own task as usual
    back = [t for t in botops_tasks(api) if t["id"] != digest["id"]]
    assert len(back) == 1 and "Pushes are stuck one" in back[0]["title"]


def test_a_bot_its_owner_paused_is_not_a_stuck_task(environment):
    api, _ = built(environment)
    old = H.shift(H.now(), hours=-3)
    with api.app.state.store.transaction() as c:
        task = H.task_create(c, H.KEEPER, "Waiting on its owner", "", "bot:botops", lint=False)
        c.execute("UPDATE tasks SET status='doing', updated_at=? WHERE id=?", (old, task["id"]))
        c.execute("UPDATE jobs SET state='completed' WHERE bot_id='botops'")  # no turn owed: the task is idle
    ctx = invariants.Ctx(now=H.now(), at=None)
    with api.app.state.store.transaction() as c:
        assert invariants.task_stuck(c, ctx) == [task["id"]]  # active and idle for the hour: stuck
        c.execute("UPDATE bots SET state='paused' WHERE id='botops'")
        assert invariants.task_stuck(c, ctx) == []  # paused by its owner: not stuck
        c.execute("UPDATE bots SET state='active' WHERE id='botops'")

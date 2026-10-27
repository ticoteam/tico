"""Notice turns retain task privacy, operator scope and the task's independent waits."""
import pytest

from backend import auto_tasks, hubdb as H, rules as R, task_privacy
from backend.tests.test_api import api, claim, get, post, runner  # noqa: F401
from backend.tests.test_parked_urgent_news import machine, parked, still_blocked
from backend.tests.test_urgent_news import sent_job


def scope(api, task):
    return post(api, 'bots/cpo/control', {'action': 'scope', 'task_id': task,
                                         'expected_revision': get(api, 'bots/cpo')['revision']})


def test_notice_delivery_keeps_operator_and_task_claim_scope(api):
    r = machine(api)
    stranger = runner(api, operator='ben')
    with api.app.state.store.transaction() as c:
        task, blocker = parked(c, incident=True)
        msg, job = sent_job(c, task, 'bot:cpo', 'The incident needs attention', {'news': 'step_entered'})
        assert job and msg['refs'][R.PARKED_NOTICE]
    assert claim(api, stranger, 'cpo') is None
    scope(api, blocker['id'])
    assert claim(api, r, 'cpo') is None
    scope(api, task['id'])
    attempt = claim(api, r, 'cpo')
    assert attempt and attempt['message']['id'] == msg['id'] and attempt['task'] is None
    with api.app.state.store.read() as c:
        still_blocked(c, task, blocker)


@pytest.mark.parametrize('wait', ['approval', 'worktree', 'put_back'])
def test_notice_turn_never_clears_an_independent_wait(api, wait):
    r = machine(api)
    with api.app.state.store.transaction() as c:
        task, blocker = parked(c, incident=True)
        if wait == 'approval':
            c.execute("INSERT INTO approvals(id,kind,task_id,requested_by,created) "
                      "VALUES('approval','send',?,'bot:cpo',?)", (task['id'], H.now()))
        elif wait == 'worktree':
            from backend.worktree_waits import ensure
            ensure(c)
            c.execute("INSERT INTO worktree_waits(id,task_id,owner,repo,generation,state,created,updated) "
                      "VALUES('worktree',?,'bot:cpo','acme/app',0,'waiting',?,?)", (task['id'], H.now(), H.now()))
        else:
            H._task_event(c, task['id'], 'bot:cpo', 'queue_return', 'doing', 'open', 'Return to queue')
            assert auto_tasks.put_back_held(c, H.task(c, task['id']))
        before = H.task(c, task['id'])
        msg, job = sent_job(c, before, 'bot:cpo', 'The incident needs attention', {'news': 'step_entered'})
        assert job and job['held'] == 0 and msg['refs'][R.PARKED_NOTICE]
    attempt = claim(api, r, 'cpo')
    if wait == 'put_back':
        assert attempt is None
    else:
        assert attempt and attempt['task'] is None
        post(api, f"attempts/{attempt['id']}/started", {'thread_id': 'notice-session'}, token=r['token'])
        post(api, f"attempts/{attempt['id']}/complete", {'outcome': 'completed', 'last_seq': 0,
                                                      'text': 'Read the notice.'}, token=r['token'])
    with api.app.state.store.read() as c:
        still_blocked(c, task, blocker)
        after = H.task(c, task['id'])
        assert all(after[k] == before[k] for k in ('status', 'step_id', 'waiting_on', 'snoozed_until', 'next_run'))
        if wait == 'approval':
            assert c.execute("SELECT decision FROM approvals WHERE id='approval'").fetchone()[0] is None
        elif wait == 'worktree':
            assert c.execute("SELECT state FROM worktree_waits WHERE id='worktree'").fetchone()[0] == 'waiting'
        else:
            assert auto_tasks.put_back_held(c, after)


def test_private_notice_retains_output_fences_without_a_task_attachment(api):
    from backend.tests.test_api import unrelated_task_managers
    r = machine(api)
    with api.app.state.store.transaction() as c:
        unrelated_task_managers(c)
        task, blocker = parked(c, incident=True)
        c.execute("UPDATE tasks SET private=1 WHERE id=?", (task['id'],))
        msg, job = sent_job(c, H.task(c, task['id']), 'bot:cpo', 'Private incident news',
                            {'news': 'step_entered'})
        assert job and msg['refs'][R.PARKED_NOTICE]
    attempt = claim(api, r, 'cpo')
    assert attempt and attempt['task'] is None
    with api.app.state.store.read() as c:
        assert task['id'] in task_privacy.attempt_tasks(c, attempt['id'])
    post(api, 'updates', {'body': 'Private derived output'}, attempt['token'], expected=403)
    get(api, 'messages/' + msg['id'], 'cara-test', expected=404)
    with api.app.state.store.read() as c:
        still_blocked(c, task, blocker)

"""Reopening Waiting work releases stale quiet-work flags without an early-run bypass."""
import pytest

from backend import auto_tasks, execution, health, quiet_work
from backend.store import H
from backend.tests.test_api import api, get, post  # noqa: F401
from backend.tests.test_task_lanes import task_bot


def reopened(api, sweep=True):
    task = post(api, 'tasks', {'owner_id': 'ops', 'title': 'Check the installed runtime',
                              'body': 'Check the installed runtime.', 'next_run': True})
    with api.app.state.store.transaction() as c:
        H.task_update(c, 'bot:ops', task['id'], status='waiting', note='Waiting for installation evidence')
        if sweep:
            assert H.sweep_stranded(c, at=H.shift(H.now(), hours=3)) == [(task['id'], 'open')]
        else:
            H.task_update(c, 'human:ana', task['id'], status='open')
    return task


def reason(api, machine):
    with api.app.state.store.read() as c:
        return execution.bot_refusal(c, api.app.state.store.settings, machine['runner_id'], 'ops')


@pytest.mark.parametrize('sweep', [False, True])
def test_reopened_quiet_task_reuses_its_queued_job_once(api, sweep):
    machine = task_bot(api, lanes=True)
    task = reopened(api, sweep)
    with api.app.state.store.read() as c:
        [job] = c.execute("SELECT id FROM jobs WHERE bot_id='ops' AND state='queued'").fetchall()
        assert H.task(c, task['id'])['next_run'] == 1  # also covers queues stranded before this fix
    assert reason(api, machine) is None
    attempt = post(api, 'jobs/claim', {'next_run': True}, machine['token'])['attempt']
    assert attempt and attempt['job_id'] == job['id'] and attempt['task']['id'] == task['id']
    assert attempt['task']['status'] == 'doing' and not attempt['task']['next_run']
    assert attempt['next_run'] == []
    assert post(api, 'jobs/claim', {}, machine['token'])['attempt'] is None
    with api.app.state.store.read() as c:
        assert c.execute("SELECT count(*) FROM jobs WHERE bot_id='ops'").fetchone()[0] == 1
        assert H.task(c, task['id'])['carried_by'] is None


@pytest.mark.parametrize('gate', ['snooze', 'blocker', 'off', 'cap', 'resume', 'defer'])
def test_reopened_job_keeps_real_gates_and_health_reports_them(api, gate):
    machine = task_bot(api, lanes=True)
    task = reopened(api)
    with api.app.state.store.transaction() as c:
        if gate == 'snooze':
            c.execute('UPDATE tasks SET snoozed_until=? WHERE id=?',
                      (H.shift(H.now(), days=1), task['id']))
        elif gate == 'blocker':
            blocker = H.task_create(c, 'human:ana', 'Supply installation evidence', 'Supply the evidence.',
                                    'human:ben', lint=False)
            H.TR.add_blocker(c, 'human:ana', task['id'], blocker['id'], mover=True)
        elif gate == 'resume':
            quiet_work.defer(c, 'bot:ops', task['id'], at=H.shift(H.now(), days=1))
        elif gate == 'defer':
            quiet_work.defer(c, 'bot:ops', task['id'], next_run=True)
    if gate in ('off', 'cap'):
        post(api, 'bots/ops/keep-tasks-going', {'keep_tasks_going': 0 if gate == 'off' else 1,
                                              'expected_revision': get(api, 'bots/ops')['revision']})
        if gate == 'cap':
            with api.app.state.store.transaction() as c:
                other = H.task_create(c, 'human:ana', 'Run the existing check', '', 'bot:ops', lint=False,
                                      next_run=True)
                H.task_update(c, 'bot:ops', other['id'], status='doing')
                c.execute('UPDATE tasks SET next_run=0 WHERE id=?', (other['id'],))
    expected = {'off': 'auto_start_off', 'cap': 'task_cap', 'resume': 'task_next_run',
                'defer': 'task_next_run'}.get(gate, 'task_wait')
    assert reason(api, machine) == expected
    assert post(api, 'jobs/claim', {}, machine['token'])['attempt'] is None
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE jobs SET created_at=? WHERE bot_id='ops' AND state='queued'",
                  (H.shift(H.now(), hours=-40),))
        rows = [r for r in health._waiting(c, {machine['runner_id']}, api.app.state.store.settings)[2]
                if r['bot_id'] == 'ops']
        assert [r['reason'] for r in rows] == [expected]
        assert expected in health.HELD


def test_a_completed_carrier_does_not_strand_reopened_work(api):
    machine = task_bot(api, lanes=True)
    task = post(api, 'tasks', {'owner_id': 'ops', 'title': 'Check runtime history',
                              'body': 'Check the installed history.', 'next_run': True})
    post(api, 'chat/ops', {'text': 'Check the queued work.'})
    carrier = post(api, 'jobs/claim', {'next_run': True}, machine['token'])['attempt']
    assert [t['id'] for t in carrier['next_run']] == [task['id']]
    post(api, f"attempts/{carrier['id']}/started", {'thread_id': 'carrier'}, machine['token'])
    with api.app.state.store.transaction() as c:
        H.task_update(c, 'bot:ops', task['id'], status='waiting', note='Waiting for runtime evidence')
        H.task_update(c, 'human:ana', task['id'], status='open')
    assert reason(api, machine) == 'task_next_run'  # do not race the carrier still working it
    post(api, f"attempts/{carrier['id']}/complete", {'outcome': 'completed', 'last_seq': 0}, machine['token'])
    assert reason(api, machine) is None
    attempt = post(api, 'jobs/claim', {}, machine['token'])['attempt']
    assert attempt and attempt['task']['id'] == task['id']


@pytest.mark.parametrize('worked', [False, True])
def test_carrier_after_reopen_settles_before_queued_notice_starts(api, worked):
    machine = task_bot(api, lanes=True)
    task = post(api, 'tasks', {'owner_id': 'ops', 'title': 'Check the installation receipt',
                              'body': 'Check the installation receipt.', 'next_run': True})
    # An older chat job claims first and carries the newly reopened work.
    post(api, 'chat/ops', {'text': 'Check the pending receipts.'})
    with api.app.state.store.transaction() as c:
        H.task_update(c, 'bot:ops', task['id'], status='waiting', note='Waiting for receipt')
        H.task_update(c, 'human:ana', task['id'], status='open')
    carrier = post(api, 'jobs/claim', {'next_run': True}, machine['token'])['attempt']
    assert [t['id'] for t in carrier['next_run']] == [task['id']]
    post(api, f"attempts/{carrier['id']}/started", {'thread_id': 'receipt-carrier'}, machine['token'])
    assert reason(api, machine) == 'task_next_run'
    if worked:
        with api.app.state.store.transaction() as c:
            H.task_update(c, 'bot:ops', task['id'], note='Installation receipt checked')
    post(api, f"attempts/{carrier['id']}/complete", {'outcome': 'completed', 'last_seq': 0}, machine['token'])
    if worked:
        assert reason(api, machine) == 'task_next_run'
        assert post(api, 'jobs/claim', {}, machine['token'])['attempt'] is None
        with api.app.state.store.read() as c:
            assert H.task(c, task['id'])['carried_by'] == carrier['id']
        with api.app.state.store.transaction() as c:
            H.task_update(c, 'bot:ops', task['id'], status='waiting', note='Waiting for another receipt')
            H.task_update(c, 'human:ana', task['id'], status='open')
        assert reason(api, machine) is None
        attempt = post(api, 'jobs/claim', {}, machine['token'])['attempt']
        assert attempt and attempt['task']['id'] == task['id']
    else:
        assert reason(api, machine) is None
        attempt = post(api, 'jobs/claim', {}, machine['token'])['attempt']
        assert attempt and attempt['task']['id'] == task['id']


def test_quiet_work_without_a_reopening_still_waits(api):
    machine = task_bot(api, lanes=True)
    task = post(api, 'tasks', {'owner_id': 'ops', 'title': 'Check on the next run',
                              'body': 'Check the counts next time.', 'next_run': True})
    with api.app.state.store.read() as c:
        assert auto_tasks.refusal(c, H.task(c, task['id']), {}, woken=True) == 'task_next_run'
    assert post(api, 'jobs/claim', {}, machine['token'])['attempt'] is None


def test_owner_cannot_start_held_work_early(api):
    machine = task_bot(api, lanes=True)
    task = reopened(api)
    with api.app.state.store.transaction() as c:
        quiet_work.defer(c, 'bot:ops', task['id'], at=H.shift(H.now(), days=1))
    post(api, 'chat/ops', {'text': 'Check the queue without starting held work.'})
    owner = post(api, 'jobs/claim', {}, machine['token'])['attempt']
    result = post(api, f"tasks/{task['id']}/run-now", {}, owner['token'], expected=403)
    assert "a party's manager or BotOps" in result['error']['detail']
    assert reason(api, machine) == 'task_next_run'
    assert post(api, 'jobs/claim', {}, machine['token'])['attempt'] is None

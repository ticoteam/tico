"""A task notice can release stale carriage without bypassing a real wait."""
import json

import pytest

from backend import auto_tasks, execution, health, quiet_work
from backend.store import H
from backend.tests.test_api import api, get, post  # noqa: F401
from backend.tests.test_task_lanes import task_bot


def task_notice(api, task, refs, *, quiet=False, body="Check the runtime notice."):
    with api.app.state.store.transaction() as c:
        return H._wake(c, H.task(c, task['id']), 'bot:ops', body,
                       {**refs, **({'quiet': True} if quiet else {})},
                       trigger=refs.get('trigger', 'schedule'))


def quiet_task(api, title='Check the weekly runtime report'):
    return post(api, 'tasks', {'owner': 'ops', 'title': title,
                               'body': 'Check the weekly runtime report.', 'next_run': True})


def carrier(api, machine, task):
    post(api, 'chat/ops', {'text': 'Read the pending runtime report.'})
    run = post(api, 'jobs/claim', {'next_run': True}, machine['token'])['attempt']
    assert [t['id'] for t in run['next_run']] == [task['id']]
    post(api, f"attempts/{run['id']}/started", {'thread_id': 'runtime-carrier'}, machine['token'])
    return run


def finish_carrier(api, machine, task, run):
    with api.app.state.store.transaction() as c:
        H.task_update(c, 'bot:ops', task['id'], note='Read the earlier runtime report')
    post(api, f"attempts/{run['id']}/complete", {'outcome': 'completed', 'last_seq': 0}, machine['token'])
    with api.app.state.store.transaction() as c:
        # The later notice is new work, after this completed delivery.
        c.execute('UPDATE attempts SET created_at=?,finished_at=? WHERE id=?',
                  (H.shift(H.now(), days=-8), H.shift(H.now(), days=-7), run['id']))
        assert H.task(c, task['id'])['carried_by'] == run['id']


@pytest.mark.parametrize('refs', [{'wake': 'due'}, {'wake': 'snooze'}, {'wake': 'resume'},
                                  {'trigger': 'pr_checks', 'wake': 'pr_checks_done'}])
@pytest.mark.parametrize('delivered', [False, True])
def test_actionable_notice_releases_stale_or_absent_carrier_once(api, refs, delivered):
    machine = task_bot(api, lanes=True)
    task = quiet_task(api)
    if delivered:
        run = carrier(api, machine, task)
        finish_carrier(api, machine, task, run)
    notice = task_notice(api, task, refs)
    task_notice(api, task, {'wake': 'due'}, body='Also check the latest runtime notice.')
    with api.app.state.store.read() as c:
        assert execution.bot_refusal(c, api.app.state.store.settings, machine['runner_id'], 'ops') is None
    run = post(api, 'jobs/claim', {'next_run': True}, machine['token'])['attempt']
    assert run and run['task']['id'] == task['id'] and run['message']['id'] == notice['id']
    assert run['task']['status'] == 'doing' and not run['task']['next_run']
    assert not run['next_run']
    assert 'now part of this' in run['message']['body']
    assert post(api, 'jobs/claim', {}, machine['token'])['attempt'] is None
    post(api, f"attempts/{run['id']}/started", {'thread_id': 'notice-run'}, machine['token'])
    post(api, f"attempts/{run['id']}/complete", {'outcome': 'completed', 'last_seq': 0}, machine['token'])
    assert post(api, 'jobs/claim', {}, machine['token'])['attempt'] is None


@pytest.mark.parametrize('held', [False, True])
@pytest.mark.parametrize('reopen', [False, True])
def test_live_or_held_carrier_preserves_deferral(api, held, reopen):
    machine = task_bot(api, lanes=True)
    task = quiet_task(api)
    run = carrier(api, machine, task)
    if reopen:
        with api.app.state.store.transaction() as c:
            H.task_update(c, 'bot:ops', task['id'], status='waiting', note='Waiting for report')
            H.task_update(c, 'human:ana', task['id'], status='open')
    if held:
        with api.app.state.store.transaction() as c:
            c.execute("UPDATE attempts SET state='failed' WHERE id=?", (run['id'],))
            c.execute("UPDATE jobs SET state='uncertain' WHERE id=?", (run['job_id'],))
    message = task_notice(api, task, {'wake': 'due'})
    with api.app.state.store.read() as c:
        assert auto_tasks.refusal(c, H.task(c, task['id']), {}, woken=True, message=message) == 'task_next_run'
    assert post(api, 'jobs/claim', {}, machine['token'])['attempt'] is None
    with api.app.state.store.read() as c:
        row = H.task(c, task['id'])
        assert row['next_run'] and row['carried_by'] == run['id']


@pytest.mark.parametrize('gate', ['resume', 'snooze', 'blocker', 'person', 'approval', 'off', 'cap'])
def test_actionable_notice_preserves_real_gates(api, gate):
    machine = task_bot(api, lanes=True)
    task = quiet_task(api)
    task_notice(api, task, {'wake': 'due'})
    with api.app.state.store.transaction() as c:
        if gate == 'resume':
            quiet_work.defer(c, 'bot:ops', task['id'], at=H.shift(H.now(), days=1))
        elif gate == 'snooze':
            c.execute('UPDATE tasks SET snoozed_until=? WHERE id=?', (H.shift(H.now(), days=1), task['id']))
        elif gate == 'person':
            c.execute("UPDATE tasks SET waiting_on='human:ben' WHERE id=?", (task['id'],))
        elif gate == 'blocker':
            other = H.task_create(c, 'human:ana', 'Supply a report', '', 'human:ben', lint=False)
            H.TR.add_blocker(c, 'human:ana', task['id'], other['id'], mover=True)
        elif gate == 'approval':
            H.approval_request(c, 'bot:ops', 'send', {'to': 'ben@acme.example', 'cc': [], 'subject': 'Report',
                                                   'body_sha256': 'a' * 64, 'mailbox': 'ops@acme.example'},
                               task_id=task['id'])
        elif gate in ('off', 'cap'):
            config = json.loads(c.execute("SELECT config FROM bot_config WHERE bot='ops'").fetchone()[0])
            c.execute("UPDATE bot_config SET config=? WHERE bot='ops'",
                      (json.dumps({**config, 'keep_tasks_going': 0 if gate == 'off' else 1}),))
            if gate == 'cap':
                other = H.task_create(c, 'human:ana', 'Check existing report', '', 'bot:ops', lint=False, next_run=True)
                H.task_update(c, 'bot:ops', other['id'], status='doing')
                c.execute('UPDATE tasks SET next_run=0 WHERE id=?', (other['id'],))
    expected = {'resume': 'task_next_run', 'off': 'auto_start_off', 'cap': 'task_cap'}.get(gate, 'task_wait')
    with api.app.state.store.read() as c:
        assert execution.bot_refusal(c, api.app.state.store.settings, machine['runner_id'], 'ops') == expected
    assert post(api, 'jobs/claim', {}, machine['token'])['attempt'] is None
    with api.app.state.store.read() as c:
        assert H.task(c, task['id'])['next_run']


@pytest.mark.parametrize('refs,quiet', [({'wake': 'due'}, True), ({'wake': 'update'}, False),
                                         ({'trigger': 'pr_checks', 'wake': 'pr_review'}, False),
                                         ({'trigger': 'pr_checks'}, False)])
def test_quiet_notice_does_not_release_deferral(api, refs, quiet):
    machine = task_bot(api, lanes=True)
    task = quiet_task(api)
    message = task_notice(api, task, refs, quiet=quiet)
    with api.app.state.store.read() as c:
        assert auto_tasks.refusal(c, H.task(c, task['id']), {}, woken=True, message=message) == 'task_next_run'
    assert post(api, 'jobs/claim', {}, machine['token'])['attempt'] is None


@pytest.mark.parametrize('ready', [False, True])
def test_health_groups_only_jobs_with_the_same_actual_reason(api, ready):
    machine = task_bot(api, lanes=True)
    tasks = [quiet_task(api, title=f'Check weekly report {i}') for i in range(3)]
    for task in tasks:
        task_notice(api, task, {'wake': 'due'})
    with api.app.state.store.transaction() as c:
        if not ready:
            c.execute('UPDATE tasks SET resume=? WHERE id=?',
                      (json.dumps({'kind': 'time', 'until': H.shift(H.now(), days=1)}), tasks[0]['id']))
        for task in tasks[1:]:
            c.execute("UPDATE tasks SET next_run=0,waiting_on='human:ben' WHERE id=?", (task['id'],))
        c.execute("UPDATE jobs SET created_at=? WHERE bot_id='ops' AND state='queued'", (H.shift(H.now(), hours=-3),))
        rows = [r for r in health._waiting(c, {machine['runner_id']}, api.app.state.store.settings)[2] if r['bot_id'] == 'ops']
        assert len(rows) == 1
        expected = {'slow' if ready else 'task_next_run': 1, 'task_wait': 2}
        assert {item['reason']: item['queued'] for item in rows[0]['queue_reasons']} == expected
    row = next(r for r in get(api, 'health')['stuck'] if r['bot_id'] == 'ops')
    assert {item['reason']: item['queued'] for item in row['queue_reasons']} == expected
    assert '2 jobs: waiting' in row['why']
    assert ('1 job: ready to start' if ready else '1 job: deferred') in row['why']

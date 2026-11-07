"""A parked task holds its own job, not the bot's independent ready queue (Dev 687)."""
import pytest

from backend import auto_tasks, execution
from backend.store import H
from backend.tests.test_api import api, claim, post  # noqa: F401
from backend.tests.test_task_lanes import new_task, task_bot


@pytest.mark.parametrize('wait', ['snooze', 'prerequisite', 'person', 'check', 'queue_return'])
@pytest.mark.parametrize('person_action', [False, True])
def test_wait_does_not_park_independent_jobs(api, wait, person_action):
    # Serial runners too: finishing the person's turn must free the bot for B/C.
    machine = task_bot(api, lanes=False)
    a = new_task(api, 'Collect the waiting report')
    with api.app.state.store.transaction() as c:
        if wait == 'snooze':
            c.execute('UPDATE tasks SET snoozed_until=? WHERE id=?', (H.shift(H.now(), days=1), a['id']))
        elif wait == 'person':
            c.execute("UPDATE tasks SET waiting_on='human:ben' WHERE id=?", (a['id'],))
        elif wait == 'prerequisite':
            prerequisite = H.task_create(c, 'human:ana', 'Supply report input', '', 'human:ben', lint=False)
            H.TR.add_blocker(c, 'human:ana', a['id'], prerequisite['id'], mover=True)
        elif wait == 'check':
            H.task_create(c, 'bot:ops', 'Check the report', '', 'bot:finance', parent_id=a['id'], lint=False)
        else:
            # A real owner queue return, kept on the original queued assignment.
            H.task_update(c, 'bot:ops', a['id'], status='doing')
            H.task_update(c, 'bot:ops', a['id'], status='open', note='Return this report to the queue until the next hour.')
        before = H.task(c, a['id'])
        assert auto_tasks.waiting(c, before, starting=True)
    b, c_task = new_task(api, 'Prepare independent report'), new_task(api, 'Prepare independent summary')
    if person_action:
        post(api, 'messages', {'to': 'bot:ops', 'text': 'Please continue the ready work.',
                               'needs_action': True})
        run = claim(api, machine, 'ops')
        assert run and run['message']['from_actor'] == 'human:ana'
        finish(api, machine, run)
    runs = []
    for _ in range(2):
        with api.app.state.store.read() as c:
            assert execution.bot_refusal(c, api.app.state.store.settings, machine['runner_id'], 'ops') is None
        run = claim(api, machine, 'ops')
        assert run and run['task']['id'] in (b['id'], c_task['id'])
        runs.append(run)
        # Preserve ordinary serial concurrency while the eligible task runs.
        assert claim(api, machine, 'ops') is None
        finish(api, machine, run)
    assert {r['task']['id'] for r in runs} == {b['id'], c_task['id']}
    assert len({r['job_id'] for r in runs}) == 2
    assert claim(api, machine, 'ops') is None
    with api.app.state.store.read() as c:
        after = H.task(c, a['id'])
        assert auto_tasks.waiting(c, after, starting=True)
        assert (after['status'], after['snoozed_until'], after['waiting_on']) == (
            before['status'], before['snoozed_until'], before['waiting_on'])
        assert not c.execute('SELECT 1 FROM turns WHERE task_id=?', (a['id'],)).fetchone()
        assert execution.bot_refusal(c, api.app.state.store.settings, machine['runner_id'], 'ops') == 'task_wait'


def finish(api, machine, run):
    post(api, f"attempts/{run['id']}/started", {'thread_id': 'queue-test-' + run['id']}, machine['token'])
    post(api, f"attempts/{run['id']}/complete", {'outcome': 'completed', 'last_seq': 0}, machine['token'])


@pytest.mark.parametrize('lanes', [False, True])
@pytest.mark.parametrize('lapsed', [False, True])
def test_wait_keeps_active_turn_fences_but_lapsed_turn_is_task_scoped(api, lanes, lapsed):
    from backend.tests.test_api import expire

    machine = task_bot(api, lanes=lanes)
    a = new_task(api, 'Collect report input')
    active = claim(api, machine, 'ops')
    assert active and active['task']['id'] == a['id']
    post(api, f"attempts/{active['id']}/started", {'thread_id': 'waiting-report'}, machine['token'])
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE tasks SET waiting_on='human:ben' WHERE id=?", (a['id'],))
    b = new_task(api, 'Prepare separate report')
    if lapsed:
        expire(api, active['id'])
    run = claim(api, machine, 'ops')
    if not lanes and not lapsed:
        assert run is None  # a genuinely active serial turn still occupies the bot
    else:
        assert run and run['task']['id'] == b['id']
        finish(api, machine, run)
    with api.app.state.store.read() as c:
        assert H.task(c, a['id'])['waiting_on'] == 'human:ben'
        assert c.execute('SELECT count(*) FROM turns WHERE task_id=?', (a['id'],)).fetchone()[0] == 1
        if lapsed:
            assert c.execute('SELECT state FROM jobs WHERE id=?', (active['job_id'],)).fetchone()[0] == 'uncertain'

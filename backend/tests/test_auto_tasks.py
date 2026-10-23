"""Assigned work starts without a notice, within task caps and the existing execution fences."""
from backend.store import H
from backend.tests.test_api import api, get, post  # noqa: F401
from backend.tests.test_task_lanes import task_bot


def deck(api, n=5):
    typ = post(api, 'task-types', {'name': 'Development', 'steps': [
        {'name': 'On Deck', 'status': 'open'}, {'name': 'In Progress', 'status': 'doing'},
        {'name': 'Review 1', 'status': 'review'}, {'name': 'Done', 'status': 'done'}]})['type']
    tasks = [post(api, 'tasks', {'owner': 'ops', 'title': f'Implement item {i}', 'body': 'Build the requested feature.', 'type': typ['id'],
                                'step': typ['steps'][0]['id']}) for i in range(n)]
    # These old assignments have no pending notice: they previously needed a person's nudge.
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE jobs SET state='cancelled'")
        for i, task in enumerate(tasks):
            c.execute('UPDATE tasks SET rank=? WHERE id=?', (n-i, task['id']))
    return tasks


def take(api, machine):
    return post(api, 'jobs/claim', {}, token=machine['token'])['attempt']


def finish(api, machine, attempt, task):
    post(api, f"attempts/{attempt['id']}/started", {'thread_id': attempt['lane']}, machine['token'])
    post(api, 'tasks/' + task['id'], {'step': 'Done', 'version': get(api, 'tasks/' + task['id'])['task']['version']})
    post(api, f"attempts/{attempt['id']}/complete", {'outcome': 'completed', 'last_seq': 0, 'text': 'Finished.'}, machine['token'])


def set_cap(api, n):
    revision = get(api, 'bots/ops')['revision']
    return post(api, 'bots/ops/keep-tasks-going', {'keep_tasks_going': n, 'expected_revision': revision})


def test_three_start_and_finishing_one_starts_next_by_rank(api):
    machine = task_bot(api, lanes=True, capacity=8)
    tasks = deck(api)
    set_cap(api, 3)
    attempts = [take(api, machine) for _ in range(3)]
    assert [a['task']['id'] for a in attempts] == [t['id'] for t in reversed(tasks[2:])]
    assert all(get(api, 'tasks/' + t['id'])['task']['step']['name'] == 'In Progress' for t in tasks[2:])
    assert take(api, machine) is None
    assert get(api, 'bots/ops')['task_flow'] == {'going': 3, 'waiting': 2}
    finish(api, machine, attempts[0], tasks[4])
    assert take(api, machine)['task']['id'] == tasks[1]['id']


def test_default_starts_snoozed_and_blocked_skip_then_reuse_existing_wakes(api):
    from backend import task_snooze
    machine = task_bot(api, lanes=True)
    tasks = deck(api, 3)
    post(api, 'tasks/' + tasks[2]['id'] + '/snooze', {'until': H.shift(H.now(), days=1)})
    post(api, 'tasks/' + tasks[1]['id'] + '/relations', {'kind': 'blocked_by', 'task': tasks[0]['id']})
    first = take(api, machine)
    assert first['task']['id'] == tasks[0]['id']
    assert take(api, machine) is None
    finish(api, machine, first, tasks[0])
    assert take(api, machine)['task']['id'] == tasks[1]['id']
    with api.app.state.store.transaction() as c:
        c.execute('UPDATE tasks SET snoozed_until=? WHERE id=?', (H.shift(H.now(), seconds=-1), tasks[2]['id']))
        task_snooze.wake_ended(c, H.now())
    assert take(api, machine)['task']['id'] == tasks[2]['id']


def test_usage_hold_and_off_start_none_and_waiting_work_frees_cap(api):
    machine = task_bot(api, lanes=True)
    tasks = deck(api, 2)
    set_cap(api, 0)
    assert take(api, machine) is None
    set_cap(api, 1)
    with api.app.state.store.transaction() as c:
        H.status_set(c, H.KEEPER, 'ops', state='limited')
    assert take(api, machine) is None
    assert get(api, 'tasks/' + tasks[1]['id'])['task']['status'] == 'open'
    with api.app.state.store.transaction() as c:
        H.status_set(c, H.KEEPER, 'ops', state='idle')
    first = take(api, machine)
    post(api, f"attempts/{first['id']}/started", {'thread_id': first['lane']}, machine['token'])
    # Work may keep its factory step while waiting for a person or external check.
    with api.app.state.store.transaction() as c:
        c.execute('UPDATE tasks SET waiting_on=? WHERE id=?', ('human:ana', tasks[1]['id']))
    post(api, f"attempts/{first['id']}/complete", {'outcome': 'completed', 'last_seq': 0, 'text': 'Waiting.'}, machine['token'])
    assert take(api, machine)['task']['id'] == tasks[0]['id']


def test_setting_revision_permissions_and_private_counters(api):
    from backend.tests.test_api import as_member
    as_member(api, 'ben@acme.example')
    revision = get(api, 'bots/ops')['revision']
    post(api, 'bots/ops/keep-tasks-going', {'keep_tasks_going': 3, 'expected_revision': revision}, token='cara-test', expected=403)
    set_cap(api, 3)
    post(api, 'bots/ops/keep-tasks-going', {'keep_tasks_going': 1, 'expected_revision': revision}, expected=409)
    post(api, 'bots/ops/keep-tasks-going', {'keep_tasks_going': -1, 'expected_revision': revision}, expected=422)
    change = next(row for row in get(api, 'settings/history')['changes'] if row['field'] == 'keep_tasks_going')
    post(api, 'settings/history/' + change['id'] + '/undo', {'expected_revision': get(api, 'bots/ops')['revision']})
    assert get(api, 'bots/ops')['keep_tasks_going'] is None
    private = post(api, 'tasks', {'owner': 'ops', 'title': 'Private work', 'body': 'Private fixture.', 'private': True})
    with api.app.state.store.transaction() as c:
        H._set_status(c, H.KEEPER, H.task(c, private['id']), 'doing')
    assert get(api, 'bots/ops')['task_flow']['going'] == 1
    assert get(api, 'bots/ops', token='ben-test')['task_flow']['going'] == 0


def test_review_children_are_independent_and_uncapped_but_computer_capacity_still_applies(api):
    machine = task_bot(api, lanes=True, capacity=10)
    typ = post(api, 'task-types', {'name': 'Review flow', 'steps': [{'name': 'Review 1', 'status': 'review'}]})['type']
    parent = post(api, 'tasks', {'owner': 'finance', 'title': 'Implementation awaiting review', 'body': 'Review it.',
                                 'type': typ['id'], 'step': 'Review 1'})
    children = [post(api, 'tasks', {'owner': 'ops', 'title': f'Review change {i}', 'body': 'Review the change.',
                 'relations': [{'kind': 'parent', 'task': parent['id']}]}) for i in range(11)]
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE jobs SET state='cancelled'")
    set_cap(api, 1)
    attempts = [take(api, machine) for _ in range(10)]
    assert all(attempts), 'the optional task cap never holds review children'
    assert len({a['lane'] for a in attempts}) == 10
    assert {a['task']['id'] for a in attempts} <= {t['id'] for t in children}
    assert take(api, machine) is None, 'reviews still share finite computer capacity'


def test_another_owners_task_notice_never_starts_their_task_or_uses_our_cap(api):
    machine = task_bot(api, lanes=True)
    set_cap(api, 0)
    with api.app.state.store.transaction() as c:
        other = H.task_create(c, 'human:ana', 'Another owners work', 'Their assignment.',
                              'bot:finance', lint=False)
        H._wake(c, other, 'bot:ops', 'Please advise about this assignment.', {'needs_action': True})
    attempt = take(api, machine)
    assert attempt and attempt['bot'] == 'ops'
    assert get(api, 'tasks/' + other['id'])['task']['status'] == 'open'

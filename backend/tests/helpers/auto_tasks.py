"""Helpers that other tests share, moved out of test_auto_tasks.py so they import no test file."""

from backend.tests.helpers.api import get, post


def deck(api, n=5):
    typ = post(
        api,
        'task-types',
        {
            'name': 'Development',
            'steps': [
                {'name': 'On Deck', 'status': 'open'},
                {'name': 'In Progress', 'status': 'doing'},
                {'name': 'Review 1', 'status': 'review'},
                {'name': 'Done', 'status': 'done'},
            ],
        },
    )['type']
    tasks = [
        post(
            api,
            'tasks',
            {
                'owner': 'ops',
                'title': f'Implement item {i}',
                'body': 'Build the requested feature.',
                'type': typ['id'],
                'step': typ['steps'][0]['id'],
            },
        )
        for i in range(n)
    ]
    # These old assignments have no pending notice: they previously needed a person's nudge.
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE jobs SET state='canceled'")
        for i, task in enumerate(tasks):
            c.execute('UPDATE tasks SET rank=? WHERE id=?', (n - i, task['id']))
    return tasks


def take(api, machine):
    return post(api, 'jobs/claim', {}, token=machine['token'])['attempt']


def finish(api, machine, attempt, task):
    post(api, f"attempts/{attempt['id']}/started", {'thread_id': attempt['lane']}, machine['token'])
    post(
        api,
        'tasks/' + task['id'],
        {'step': 'Done', 'version': get(api, 'tasks/' + task['id'])['task']['version']},
        attempt['token'],
    )
    post(
        api,
        f"attempts/{attempt['id']}/complete",
        {'outcome': 'completed', 'last_seq': 0, 'text': 'Finished.'},
        machine['token'],
    )


def set_cap(api, n):
    revision = get(api, 'bots/ops')['revision']
    return post(api, 'bots/ops/keep-tasks-going', {'keep_tasks_going': n, 'expected_revision': revision})

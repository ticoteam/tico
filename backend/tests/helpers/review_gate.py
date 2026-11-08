"""Helpers that other tests share, moved out of test_review_gate.py so they import no test file."""

from backend import hubdb as H
from backend.tests.helpers.github import api, get, headers, post, hook, pr_event, PR


HEAD, OLD, BASE = 'a' * 40, 'b' * 40, 'c' * 40


def ticket(api, receipt=None, **fields):
    with api.app.state.store.transaction() as c:
        typ = H.type_create(
            c,
            H.KEEPER,
            'Dev tickets',
            [
                {'name': 'In Progress', 'status': 'doing'},
                {'name': 'Review 1', 'status': 'review'},
                {'name': 'Review 1 Fixes', 'status': 'doing'},
                {'name': 'Review 2', 'status': 'review'},
                {'name': 'Done', 'status': 'done'},
            ],
        )
    task = post(
        api,
        'tasks',
        {
            'owner': 'cpo',
            'title': 'Improve messages',
            'body': 'Build messages.',
            'type': typ['id'],
            'step': 'In Progress',
            'links': [PR],
            **fields,
        },
    )
    hook(api, 'pull_request', pr_event('opened', head={'sha': HEAD}))
    if receipt:
        with api.app.state.store.transaction() as c:
            c.execute('UPDATE tasks SET note=? WHERE id=?', (receipt, task['id']))
    return get(api, 'tasks/' + task['id'])['task']


def move(api, task, step='Review 1'):
    return api.post(
        '/api/v2/tasks/' + task['id'],
        headers=headers(),
        json={'version': get(api, 'tasks/' + task['id'])['task']['version'], 'step': step},
    )


def another_ticket(api, n):
    """One more ticket of the same type for a table of rows in one test, the only task holding the PR link."""
    with api.app.state.store.read() as c:
        typ = H.type_get(c, 'Dev tickets')
    task = post(
        api,
        'tasks',
        {
            'owner': 'cpo',
            'title': f'Improve messages {n}',
            'body': 'Build messages.',
            'type': typ['id'],
            'step': 'In Progress',
            'links': [PR],
        },
    )
    hook(api, 'pull_request', pr_event('opened', head={'sha': HEAD}))
    with api.app.state.store.transaction() as c:
        c.execute("DELETE FROM task_links WHERE task_id<>?", (task['id'],))
    return get(api, 'tasks/' + task['id'])['task']

"""Helpers that other tests share, moved out of test_chat_goals.py so they import no test file."""

from backend.tests.helpers.api import assign, post, runner
from backend import bot_settings as BS


def setup(api, supported=True, runtime='codex'):
    machine = runner(api)
    assign(api, machine, 'ops')
    with api.app.state.store.transaction() as c:
        BS.write(c, 'ops', {'runtime': runtime})
    post(
        api,
        'runners/heartbeat',
        {
            'version': 'test',
            'platform': 'test',
            'readiness': {
                'schema_version': 1,
                'bots': {
                    'ops': {
                        'ready': True,
                        'runtime': runtime,
                        'goals': supported,
                        'commands': [
                            {'name': 'compact', 'help': 'Compact this conversation', 'kind': 'harness'},
                            {
                                'name': 'docs:publish',
                                'args': '[target]',
                                'help': 'Publish the Acme docs',
                                'kind': 'harness',
                            },
                        ],
                    }
                },
            },
        },
        machine['token'],
    )
    conv = post(api, 'conversations', {'participants': ['bot:ops']})
    return machine, conv['id']


def action(api, cid, name='set', objective=None, **kwargs):
    return post(
        api,
        f'conversations/{cid}/goal',
        {'action': name, **({'objective': objective} if objective is not None else {})},
        **kwargs,
    )


def goal_event(api, machine, attempt, status, note='', started=True):
    """What the runner reports when the harness says the attempt's goal changed (runner-rs/src/goals.rs)."""
    goal = attempt['chat_goal']
    if started:
        post(api, f"attempts/{attempt['id']}/started", {'thread_id': 'fake-thread'}, machine['token'])
    payload = {'goal_id': goal['id'], 'revision': goal['updated_at'], 'status': status, 'note': note}
    post(
        api,
        f"attempts/{attempt['id']}/events",
        {'events': [{'seq': 1, 'kind': 'goal', 'payload': payload}]},
        machine['token'],
    )


def task_goal_world(api):
    machine = runner(api)
    assign(api, machine, 'ops')
    with api.app.state.store.transaction() as c:
        BS.write(c, 'ops', {'runtime': 'codex', 'task_goal': 'on'})
    post(
        api,
        'runners/heartbeat',
        {
            'version': 'test',
            'platform': 'test',
            'capabilities': ['task_lanes_v2', 'session_roots_v1', 'task_goals_v1'],
            'readiness': {'schema_version': 1, 'bots': {'ops': {'ready': True, 'runtime': 'codex', 'goals': True}}},
        },
        machine['token'],
    )
    return machine, post(api, 'tasks', {'owner': 'ops', 'title': 'Publish the Acme summary', 'body': 'For the board.'})

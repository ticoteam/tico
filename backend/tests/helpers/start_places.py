"""Helpers that other tests share, moved out of test_start_places.py so they import no test file."""

from backend.tests.helpers.api import post
from backend.tests.helpers.attempt_contention import fleet
from backend.tests.helpers.runner_load import point


def waiters(api, n=37, running=3):
    computer = fleet(api, attempts=n + running, trees=0)
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE runner_states SET capacity=? WHERE runner_id=?", (n + running, computer['runner_id']))
        for i in range(n):
            c.execute("UPDATE attempts SET state='leased' WHERE id=?", (f'attempt-{i}',))
            c.execute("UPDATE jobs SET state='leased' WHERE attempt_id=?", (f'attempt-{i}',))
            from backend import start_places

            start_places.claim(c, f'attempt-{i}')
    for i in range(n):
        post(api, f'attempts/attempt-{i}/start-place', {'waiting': 'shared login'}, computer['token'])
        post(
            api,
            f'attempts/attempt-{i}/events',
            {
                'events': [
                    {
                        'seq': 1,
                        'kind': 'status',
                        'payload': {'waiting': 'Waiting for the shared ChatGPT login', 'login': 'ChatGPT'},
                    }
                ]
            },
            computer['token'],
        )
    return computer


def overload(api, computer, cpu=95, mem=0.5):
    """Six one-minute samples over 90%: performance.load_status calls the computer overloaded."""
    import time

    now = int(time.time()) // 60 * 60
    post(
        api,
        'runners/performance',
        {'samples': [point(now - 300 + i * 60, cpu=cpu, mem=mem) for i in range(6)]},
        computer['token'],
    )


def memory(api, computer, mem, at=None):
    """One synthetic sample at this minute (or at `at`), memory at `mem` of the computer's total."""
    import time

    at = at or int(time.time()) // 60 * 60
    post(api, 'runners/performance', {'samples': [point(at, cpu=20, mem=mem)]}, computer['token'])

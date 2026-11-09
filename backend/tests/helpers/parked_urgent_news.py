"""Helpers that other tests share, moved out of test_parked_urgent_news.py so they import no test file."""

from backend import hubdb as H
from backend.tests.helpers.profiles import put
from backend.tests.helpers.task_lanes import task_bot


def parked(c, profile='lead', party='owned', incident=False):
    put(c, 'cpo', profile)
    owner = 'bot:cpo' if party == 'owned' else 'bot:ops'
    requester = 'bot:cpo' if party == 'requested' else 'bot:ops'
    blocker = H.task_create(c, 'bot:ops', 'Prepare the machine', 'Synthetic prerequisite', 'bot:ops')
    task = H.task_create(
        c, requester, 'Restore the service', 'Synthetic held work', owner, labels=['incident'] if incident else []
    )
    H.TR.add_blocker(c, 'human:ana', task['id'], blocker['id'])
    c.execute('DELETE FROM jobs')
    return H.task(c, task['id']), blocker


def still_blocked(c, task, blocker):
    assert H.live_blocker(c, task['id']) == blocker['id']
    assert H.task(c, blocker['id'])['status'] == 'open'


def machine(api):
    return task_bot(api, lanes=True, others=('cpo',))

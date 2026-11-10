"""The task and Meetings pages share one existing project meeting in the demo."""

from .. import hubdb as H
from ..demo_seed import version


SHARED = 'fw24'  # the firmware rollout (demo_content.TASKS): the task the meeting and its page share


def seed(b):
    live = b.call('POST', 'live-meetings', {'title': 'Launch planning', 'visibility': 'private'})['id']
    b.call('POST', f'live-meetings/{live}/access', {'add': ['ben']})
    b.call('POST', f'live-meetings/{live}/access', {'add': ['cara']}, token='demo-ben')
    b.call('POST', f'live-meetings/{live}/bots', {'bots': ['ops-manager']}, token='demo-ben')
    with b.store.read() as c:
        meeting = c.execute(
            "SELECT id FROM meetings WHERE review_state='live' ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
    if meeting and b.tasks:
        task_id = b.tasks[SHARED]
        b.call('POST', f"meetings/{meeting['id']}/tasks", {'task_ids': [task_id]})
        b.call(
            'POST',
            f"meetings/{meeting['id']}/items",
            {'section': 'task', 'text': 'Review the agreed follow-up', 'proposed_task_id': task_id},
        )
        # The normal task page can show its editor from the same history a real edit records.
        task = b.call('GET', f'tasks/{task_id}')['task']
        b.call(
            'POST',
            f'tasks/{task_id}',
            {'version': task['version'], 'due': b.ago(days=1).isoformat().replace('+00:00', 'Z')},
        )
        # The shared task's owner asks Ana on the task, so it is in her Needs you and its Tasks badge.
        b.at(hours=1)
        b.write(
            lambda c: H.task_ask(
                c,
                H.task(c, task_id)['owner_id'],
                task_id,
                'human:ana',
                "Can we use the meeting's agreed follow-up for the launch? If you agree, I'll add it to the plan.",
                kind='ok',
                seen_version=version(c, task_id),
            )
        )
    # Settings > People shows each person's Tico Recorder (backend/recorder_installs.py); Ana and Cara use one.
    stamp = lambda at: at.strftime('%Y-%m-%dT%H:%M:%S.%f') + 'Z'
    b.write(
        lambda c: c.executemany(
            "INSERT OR REPLACE INTO recorder_installs(person_id,install_id,version,last_seen_at,created_at,updated_at) "
            "VALUES(?,?,?,?,?,?)",
            [
                (person, install, '0.2.0', stamp(b.ago(hours=hours)), stamp(b.ago(days=20)), stamp(b.ago(hours=hours)))
                for person, install, hours in (('ana', 'a' * 32, 2), ('cara', 'c' * 32, 70))
            ],
        )
    )

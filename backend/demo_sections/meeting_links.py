"""The task and Meetings pages share one existing project meeting in the demo."""

from .. import hubdb as H
from ..demo_seed import version


SHARED = 'fw24'  # the firmware rollout (demo_content.TASKS): the task the meeting and its page share


def seed(b):
    live = b.call('POST', 'live-meetings', {'title': 'Launch planning', 'visibility': 'private'})['id']
    b.call('POST', f'live-meetings/{live}/access', {'add': ['ben']})
    b.call('POST', f'live-meetings/{live}/access', {'add': ['cara']}, token='demo-ben')
    b.call('POST', f'live-meetings/{live}/bots', {'bots': ['operations-manager']}, token='demo-ben')
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
        # Eli, who handed the shared task to its bot, asks Ana on it, so it is in her Needs you and its Tasks badge. A
        # person asks: the bots' messages already seeded today use up Everyone's 3 a day to Ana (Rules › Internal), and
        # a bot's ask counts against that line like its messages do (reach.check), so the bot's ask would be refused.
        b.at(hours=1)
        b.write(
            lambda c: H.task_ask(
                c,
                H.task(c, task_id)['requester_id'],
                task_id,
                'human:ana',
                "Can we use the meeting's agreed follow-up for the launch? If you agree, Firmware adds it to the plan.",
                kind='ok',
                seen_version=version(c, task_id),
            )
        )

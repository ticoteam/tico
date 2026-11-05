"""The task and Meetings pages share one existing project meeting in the demo."""

from .. import hubdb as H


def seed(b):
    with b.store.read() as c:
        meeting = c.execute("SELECT id FROM meetings WHERE review_state='live' ORDER BY created_at DESC LIMIT 1").fetchone()
    if meeting and b.tasks:
        task_id = next(iter(b.tasks.values()))
        b.call('POST', f"meetings/{meeting['id']}/tasks", {'task_ids': [task_id]})
        b.call('POST', f"meetings/{meeting['id']}/items", {'section': 'task',
               'text': 'Review the agreed follow-up', 'proposed_task_id': task_id})
        # The normal task page can show its editor from the same history a real edit records.
        task = b.call('GET', f'tasks/{task_id}')['task']
        b.call('POST', f'tasks/{task_id}', {'version': task['version'], 'due': b.ago(days=1).isoformat().replace('+00:00', 'Z')})
        # An answerable follow-up puts the shared task in the personal card queue and its Tasks badge.
        b.at(hours=1)
        b.write(lambda c: H.say(c, H.task(c, task_id)['owner_id'], 'human:ana',
            "Can we use the meeting's agreed follow-up for the launch? If you agree, I'll add it to the plan.",
            kind='ask', conversation_id=H.task(c, task_id)['conversation_id'], refs={'task': task_id}))

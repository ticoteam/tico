"""The task and Meetings pages share one existing project meeting in the demo."""


def seed(b):
    with b.store.read() as c:
        meeting = c.execute("SELECT id FROM meetings WHERE review_state='live' ORDER BY created_at DESC LIMIT 1").fetchone()
    if meeting and b.tasks:
        target = next(iter(b.tasks.values()))
        b.call('POST', f"meetings/{meeting['id']}/tasks", {'task_ids': [target]})
        b.call('POST', f"meetings/{meeting['id']}/items", {'section': 'task',
               'text': 'Review the agreed follow-up', 'proposed_task_id': target})

"""Live, evidence-based outcomes for notes handed to tasks."""

import re

from . import hubdb as H


def outcome(conn, record, actor='', owner=False):
    sent = record.get('sent') or {}
    delivery = record.get('delivery') or {}
    task_id = delivery.get('task_id') or sent.get('task')
    base = {'bot': sent.get('slug') or delivery.get('destination'), 'task_id': task_id}
    if not task_id:
        status = delivery.get('status')
        return dict(
            base,
            status=status or ('sent' if sent else 'not_sent'),
            summary=(
                'Delivery failed. Retry sending this note.'
                if status == 'error'
                else 'Processing and delivery are pending.'
                if status == 'pending'
                else 'Sent; no linked task outcome is available.'
                if sent
                else 'Not sent to a bot yet.'
            ),
        )
    task = conn.execute('SELECT * FROM tasks WHERE id=?', (task_id,)).fetchone()
    if not task or not (owner or actor and actor in (task['owner_id'], task['requester_id'])):
        return dict(base, status='sent', summary='Sent; the task outcome is unavailable to this account.')
    task = dict(task)
    children = [
        dict(t)
        for t in conn.execute(
            "SELECT id,title,status,owner_id,requester_id FROM tasks WHERE id IN (SELECT to_task_id FROM links "
            "WHERE from_task_id=?) ORDER BY created_at",
            (task_id,),
        )
        if owner or actor and actor in (t['owner_id'], t['requester_id'])
    ]
    # Quote the actual latest task note, never synthesize completion from a delivery receipt.
    note = task.get('note') or ''
    # An open task waiting on someone else says on whom (waiting_on), in place of its note.
    waits = H.task_waits(conn, [task_id]).get(task_id) or [] if task['status'] == 'open' else []
    if waits and waits[0].ask:
        note = waits[0].line
    else:
        note = note.split('\n\n', 1)[0]
    note = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', note)
    note = re.sub(r'\s+', ' ', note).strip()
    if len(note) > 300:
        note = note[:297].rsplit(' ', 1)[0] + '…'
    fallback = {
        'open': 'Delivered; awaiting review.',
        'done': 'Review completed; no outcome summary was recorded.',
        'closed': 'Review closed; no outcome summary was recorded.',
    }
    complete = sum(t['status'] in ('done', 'closed') for t in children)
    return dict(
        base,
        bot=task['owner_id'].removeprefix('bot:'),
        status=task['status'],
        summary=note or fallback.get(task['status'], 'No outcome reported yet.'),
        updated_at=task['updated_at'],
        followups=len(children),
        completed_followups=complete,
        open_followups=len(children) - complete,
        tasks=[{'id': t['id'], 'title': t['title'], 'status': t['status']} for t in children],
    )

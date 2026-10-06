"""Live, evidence-based outcomes for notes handed to Hub tasks."""
import re


def outcome(conn, record, actor='', owner=False):
    sent = record.get('sent') or {}
    delivery = record.get('delivery') or {}
    task_id = delivery.get('task_id') or sent.get('task')
    base = {'bot':sent.get('slug') or delivery.get('destination'), 'task_id':task_id}
    if not task_id:
        status = delivery.get('status')
        return dict(base, status=status or ('sent' if sent else 'not_sent'), summary=(
            'Delivery failed. Retry sending this note.' if status == 'error' else
            'Processing and delivery are pending.' if status == 'pending' else
            'Sent; no linked task outcome is available.' if sent else 'Not sent to a bot yet.'))
    task = conn.execute('SELECT * FROM tasks WHERE id=?', (task_id,)).fetchone()
    if not task or not (owner or actor and actor in (task['owner'],task['requester'])):
        return dict(base, status='sent', summary='Sent; the task outcome is unavailable to this account.')
    task = dict(task)
    children = [dict(t) for t in conn.execute("SELECT id,title,status,owner,requester FROM tasks WHERE id IN (SELECT from_task FROM task_relations "
                "WHERE kind='parent' AND to_task=?) ORDER BY created", (task_id,))
                if owner or actor and actor in (t['owner'], t['requester'])]
    # Quote the actual latest task note, never synthesize completion from a delivery receipt.
    note = task.get('note') or ''
    if task['status'] == 'waiting':
        waiting = next((line.strip() for line in note.splitlines() if re.match(r'\s*(waiting|blocked|awaiting|needs? a decision)\b',line,re.I)), '')
        note = waiting or note
    else:
        note = note.split('\n\n',1)[0]
    note = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', note)
    note = re.sub(r'\s+', ' ', note).strip()
    if len(note) > 300:
        note = note[:297].rsplit(' ',1)[0] + '…'
    fallback = {'open':'Delivered; awaiting review.', 'doing':'Review is in progress.',
                'waiting':'Waiting for a decision or follow-up.', 'done':'Review completed; no outcome summary was recorded.',
                'closed':'Review closed; no outcome summary was recorded.'}
    complete = sum(t['status'] in ('done','closed') for t in children)
    return dict(base, bot=task['owner'].removeprefix('bot:'), status=task['status'],
                summary=note or fallback.get(task['status'],'No outcome reported yet.'),
                updated=task['updated'], followups=len(children), completed_followups=complete,
                open_followups=len(children)-complete)

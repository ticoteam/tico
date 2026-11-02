"""A person's board cleanup is one next-run note per recipient, never one model turn per ticket.

Detailed notes remain on each task, with their original audience. The batch contains only a count, so it cannot
carry one private task's words into another task's session. Awaited child results use their existing return path.
"""
import json

from . import hubdb as H

PREFIX = 'completion-note:'
MAX_BATCH = 1000


def record(c, target, task_id):
    key = PREFIX + target
    saved = c.execute('SELECT value FROM registry_metadata WHERE key=?', (key,)).fetchone()
    old = json.loads(saved[0]) if saved else {}
    note = H.note(c, old.get('note_id')) if old.get('note_id') else None
    pending = note and not note.get('carried_by') and not note.get('cancelled_at') and not note.get('settled_at')
    ids = old.get('tasks', []) if pending else []
    count = int(old.get('count', len(ids))) if pending else 0
    if task_id in ids:
        return
    ids = (ids + [task_id])[-MAX_BATCH:]
    count += 1
    body = (f"A person finished or closed {count} task{'s' if count != 1 else ''}. "
            "Their notes remain on each task; read them when continuing your work.")
    if pending:
        c.execute('UPDATE notes SET body=? WHERE id=?', (body, note['id']))
        note_id = note['id']
    else:
        note_id = H.new_id()
        c.execute('INSERT INTO notes(id,from_actor,to_actor,body,created_at) VALUES(?,?,?,?,?)',
                  (note_id, H.KEEPER, target, body, H.now()))
        H.event(c, H.KEEPER, 'note.create', note_id, {'to': target, 'completion_batch': True})
    c.execute('INSERT INTO registry_metadata VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
              (key, json.dumps({'note_id': note_id, 'tasks': ids, 'count': count})))

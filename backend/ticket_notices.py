"""Started, blocked and done notices for Dev tickets.

When an engineering ticket first starts, gets a blocker that is still open, or reaches done, the keeper writes one line
from a fixed template: the ticket, its step, the blocker and its pull request. It goes to every roadmap item the ticket
delivers (its parent or a task it blocks, on the Product roadmap), else to the ticket's requester on the ticket. It
is written `quiet`, so it queues no run and is not mirrored to Slack: nobody wakes who would not have before. It
appears in the task's comments like any other."""

STARTED = 'Started: {ticket}. Step: {step}. PR: {pr}.'
BLOCKED = 'Blocked: {ticket}. Step: {step}. Waits on {blocker}. PR: {pr}.'
DONE = 'Done: {ticket}. Step: {step}. PR: {pr}.'
TEMPLATES = {'started': STARTED, 'blocked': BLOCKED, 'done': DONE}
STARTED_STATUSES = ('doing', 'review', 'ready')


def _H():
    from . import hubdb
    return hubdb


def _engineering(conn, row):
    from .roadmap_flow import ENGINEERING
    return bool(row and conn.execute('SELECT 1 FROM task_types WHERE id=? AND role=?',
                                     (row.get('type_id'), ENGINEERING)).fetchone())


def _name(row):
    return (f"#{row['number']} " if row.get('number') is not None else '') + str(row.get('title') or '').strip()


def _step(conn, row):
    found = conn.execute('SELECT name FROM task_steps WHERE id=?', (row.get('step_id'),)).fetchone()
    return found[0] if found else row.get('status') or 'none'


def _pr(conn, row):
    found = conn.execute("SELECT url FROM task_links WHERE task_id=? AND kind='pr' ORDER BY created_at DESC,rowid DESC "
                         'LIMIT 1', (row['id'],)).fetchone()
    return found[0] if found else 'none'


def _names_blocker(conn, kind, blocker, reader):
    return kind == 'blocked' and bool(blocker) and (reader is None or _H().task_private_readable(conn, reader, blocker))


def text(conn, kind, row, blocker=None, reader=None):
    """The notice for `row`. A blocker the reader may not read is named only as a private task."""
    fields = {'ticket': _name(row), 'step': _step(conn, row), 'pr': _pr(conn, row)}
    if kind == 'blocked':
        fields['blocker'] = (f'{_name(blocker)} ({_step(conn, blocker)})' if _names_blocker(conn, kind, blocker, reader)
                             else 'a private task')
    return TEMPLATES[kind].format(**fields)


def _places(conn, row):
    """(task, person or bot) pairs a notice about `row` goes to: each roadmap item it delivers, addressed to the
    item's bot (else its person); else the ticket itself, to its requester. A private ticket stays on itself."""
    from . import task_relations as TR
    from .roadmap_flow import PRODUCT, role_type
    H = _H()
    product = role_type(conn, PRODUCT)
    out = []
    if product and not H.task_private(conn, row):
        for item_id in dict.fromkeys([TR.parent_of(conn, row['id']), *TR.blocked_ids(conn, row['id'])]):
            item = H.task(conn, item_id) if item_id else None
            if not item or item.get('type_id') != product:
                continue
            parties = [a for a in dict.fromkeys([item['owner'], item['requester']]) if a and a != H.KEEPER]
            to = next((a for a in parties if H.is_bot(a)), None) or (parties[0] if parties else None)
            if to:
                out.append((item, to))
    if not out and row.get('requester') and row['requester'] != H.KEEPER:
        out.append((row, row['requester']))
    return out


def post(conn, kind, row, actor=None, blocker=None):
    """Write the notice everywhere it goes. The requester who made the change themselves is not told about it."""
    from . import task_relations as TR
    H = _H()
    sent = []
    for where, to in _places(conn, row):
        if where['id'] == row['id'] and to == actor:
            continue
        conv = H.conversation(conn, where.get('conversation_id'))
        if not conv:
            continue
        refs = {'task': where['id'], 'comment': True, 'quiet': True, 'task_notice': kind, 'ticket': row['id']}
        if kind == 'blocked' and where['id'] == row['id'] and blocker and \
                TR.requester_hears_block(conn, row, blocker, actor):
            refs = {**refs, 'news': 'task_blocked'}      # the requester's wake for the block (task_relations)
            del refs['quiet']
        if _names_blocker(conn, kind, blocker, to):
            # Its name and step are in the text, so the comment is read only by who may read the blocker too, then
            # and later (task_privacy.message_tasks): not a guest of the ticket, nor anyone after the blocker narrows.
            refs['blocker'] = blocker['id']
        msg = H._write_message(conn, H.KEEPER, to, text(conn, kind, row, blocker, reader=to), conv, 'say', refs,
                               None, None)
        sent.append(msg['id'])
    if sent:
        H.event(conn, H.KEEPER, 'task.notice', row['id'], {'kind': kind, 'messages': sent})
    return sent


def moved(conn, actor, task_id, old, new):
    """A ticket's status changed from `old` to `new`: started the first time it is worked on, done each time it is."""
    H = _H()
    row = H.task(conn, task_id)
    if old == new or not _engineering(conn, row):
        return []
    if new == 'done':
        return post(conn, 'done', row, actor)
    if new in STARTED_STATUSES and not conn.execute(
            "SELECT 1 FROM events WHERE actor=? AND action='task.notice' AND target=? "
            "AND json_extract(detail,'$.kind')='started' LIMIT 1", (H.KEEPER, task_id)).fetchone():
        return post(conn, 'started', row, actor)
    return []


def blocked(conn, actor, row, blocker_id):
    """A ticket now waits on `blocker_id`: told while that is still open."""
    H = _H()
    blocker = H.task(conn, blocker_id)
    row = H.task(conn, row['id'])
    if not blocker or blocker['status'] not in H.ACTIVE_STATUSES or not _engineering(conn, row):
        return []
    return post(conn, 'blocked', row, actor, blocker)

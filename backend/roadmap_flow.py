"""Default product and engineering pipelines over task steps and parent relations."""

import re

PRODUCT = 'product-roadmap'
ENGINEERING = 'engineering'


def managed_type(c, manager):
    row = c.execute('SELECT id FROM task_types WHERE manager=? ORDER BY created,id LIMIT 1', (manager,)).fetchone()
    return row[0] if row else None


def ensure(c):
    from . import hubdb as H
    for ident, name, manager, steps in (
        (PRODUCT, 'Product roadmap', 'product-manager', [
            ('Suggested', 'open'), ('Exploring', 'doing'), ('Spec', 'review'),
            ('Approved', 'review'), ('Building', 'doing'), ('Shipped', 'done'), ('Declined', 'declined')]),
        (ENGINEERING, 'Engineering', 'engineering-lead', [
            ('Todo', 'open'), ('Doing', 'doing'), ('Waiting', 'waiting'),
            ('In review', 'review'), ('Ready to ship', 'ready'), ('Done', 'closed'), ('Declined', 'declined')]),
    ):
        if ident == ENGINEERING:
            dev = next((r for r in c.execute('SELECT id,name FROM task_types ORDER BY id')
                        if re.fullmatch(r'dev(?:[ -]?tickets?)?', r['name'], re.I)
                        or re.fullmatch(r'dev(?:[ -]?tickets?)?', r['id'], re.I)), None)
            if dev:
                c.execute('UPDATE task_types SET manager=? WHERE id=?', (manager, dev['id']))
                continue
        existing = c.execute('SELECT id FROM task_types WHERE id=? OR name=? COLLATE NOCASE', (ident, name)).fetchone()
        if existing:
            c.execute('UPDATE task_types SET manager=? WHERE id=?', (manager, existing[0]))
            continue
        c.execute('INSERT INTO task_types(id,name,bots,manager,steps_only,created,updated) VALUES(?,?,?,?,?,?,?)',
                  (ident, name, 'work', manager, int(ident == PRODUCT), H.now(), H.now()))
        for position, (step, status) in enumerate(steps):
            c.execute('INSERT INTO task_steps(id,type_id,name,position,status) VALUES(?,?,?,?,?)',
                      (ident + '-' + str(position), ident, step, position, status))


def sync(c, task_id):
    from . import hubdb as H
    row = H.task(c, task_id)
    if not row or row.get('type_id') != managed_type(c, 'product-manager'):
        return
    typ = H.type_get(c, row['type_id'])
    current = next((s['name'] for s in typ['steps'] if s['id'] == row['step_id']), None)
    if current not in ('Approved', 'Building', 'Shipped'):
        return
    total, unfinished, shipped = c.execute(
        "SELECT count(*),coalesce(sum(t.status NOT IN ('done','closed','declined')),0),"
        "coalesce(sum(t.status IN ('done','closed')),0) "
        "FROM task_relations r JOIN tasks t ON t.id=r.from_task "
        "WHERE r.to_task=? AND r.kind='parent' AND t.type_id IN (SELECT id FROM task_types WHERE manager='engineering-lead')", (task_id,)).fetchone()
    if not total:
        return
    wanted = 'Building' if unfinished else 'Shipped' if shipped else 'Approved'
    step = next((s for s in typ['steps'] if s['name'] == wanted), None)
    if not step or current == wanted:
        return
    H._set_status(c, H.KEEPER, row, step=step['id'])
    version = ',version=version+1' if 'version' in {r[1] for r in c.execute('PRAGMA table_info(tasks)')} else ''
    c.execute('UPDATE tasks SET done_at=?' + version + ' WHERE id=?',
              (H.now() if wanted == 'Shipped' else None, task_id))
    H._recount(c, row['owner'])
    H.event(c, H.KEEPER, 'task.update', task_id, {'step': step['id']})


def changed(c, task_id):
    from . import task_relations as TR
    sync(c, task_id)
    parent = TR.parent_of(c, task_id)
    if parent:
        sync(c, parent)

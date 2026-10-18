"""Default product and engineering pipelines over task steps and parent relations.

The two types are found by their `role`, never by who manages them: the bot that managed a type can be archived
and its work handed to another, and the type must still be found."""

import re

PRODUCT = 'product-roadmap'
ENGINEERING = 'engineering'
# The bots that managed each type before types had a role; only the one-time backfill reads them.
LEGACY_MANAGERS = {PRODUCT: 'product-manager', ENGINEERING: 'engineering-lead'}
DEV_NAME = r'dev(?:[ -]?tickets?)?'


def role_type(c, role):
    """The id of the first type with this role, or None. Only one type is the Product roadmap; Engineering and a
    Dev tickets type made later may both carry the engineering role."""
    row = c.execute('SELECT id FROM task_types WHERE role=? ORDER BY created,id LIMIT 1', (role,)).fetchone()
    return row[0] if row else None


def _dev(row):
    return bool(re.fullmatch(DEV_NAME, row[1] or '', re.I) or re.fullmatch(DEV_NAME, row[0] or '', re.I))


def migrate(c):
    """Give the Product roadmap and Dev tickets types their role, once. The roadmap is the type its legacy bot
    manages, else the type with the role's own id; engineering goes to every type its legacy bot manages (each
    counted for the roadmap before) and to the Engineering type. Nothing else on any type or task changes.

    The column, its backfill and the index commit together: startup runs in autocommit, and a start stopped after
    the column alone would see it next time and never backfill a renamed type that claim() can't recognise."""
    from . import hubdb as H
    c.execute('SAVEPOINT roadmap_roles')
    try:
        columns = {r[1] for r in c.execute('PRAGMA table_info(task_types)')}
        if 'role' not in columns:
            H.add_column(c, 'task_types', 'role', 'TEXT')

            def ids(sql, *args):
                return [r[0] for r in c.execute('SELECT id FROM task_types WHERE ' + sql + ' ORDER BY created,id', args)]
            managed = (lambda role: ids('manager=?', LEGACY_MANAGERS[role])) if 'manager' in columns else (lambda role: [])
            for role, chosen in ((PRODUCT, (managed(PRODUCT) or ids('id=?', PRODUCT))[:1]),
                                 (ENGINEERING, managed(ENGINEERING) + ids('id=?', ENGINEERING))):
                for ident in chosen:
                    c.execute('UPDATE task_types SET role=? WHERE id=? AND role IS NULL', (role, ident))
        c.execute("CREATE UNIQUE INDEX IF NOT EXISTS task_types_product_role ON task_types(role) "
                  "WHERE role='product-roadmap'")
        claim(c)
    except BaseException:  # an interrupted start too: releasing a half-done backfill would commit it
        c.execute('ROLLBACK TO roadmap_roles')
        raise
    finally:
        c.execute('RELEASE roadmap_roles')


def claim(c):
    """At every start and whenever a type is created or renamed: a type named Dev tickets gets the engineering role,
    and the Product roadmap type its role while no type has it. A role or manager a type has never changes."""
    for row in c.execute('SELECT id,name FROM task_types WHERE role IS NULL').fetchall():
        if _dev(row):
            c.execute('UPDATE task_types SET role=? WHERE id=?', (ENGINEERING, row[0]))
    if not role_type(c, PRODUCT):
        row = c.execute('SELECT id FROM task_types WHERE role IS NULL AND (id=? OR name=? COLLATE NOCASE) '
                        'ORDER BY id=? DESC LIMIT 1', (PRODUCT, 'Product roadmap', PRODUCT)).fetchone()
        if row:
            c.execute('UPDATE task_types SET role=? WHERE id=?', (PRODUCT, row[0]))


def ensure(c):
    """Create the two role types when they are missing. An existing type keeps the manager it has; one with
    none gets the default."""
    from . import hubdb as H
    for ident, name, manager, steps in (
        (PRODUCT, 'Product roadmap', LEGACY_MANAGERS[PRODUCT], [
            ('Suggested', 'open'), ('Exploring', 'doing'), ('Spec', 'review'),
            ('Approved', 'review'), ('Building', 'doing'), ('Shipped', 'done'), ('Declined', 'declined')]),
        (ENGINEERING, 'Engineering', LEGACY_MANAGERS[ENGINEERING], [
            ('Todo', 'open'), ('Doing', 'doing'), ('Waiting', 'waiting'),
            ('In review', 'review'), ('Ready to ship', 'ready'), ('Done', 'closed'), ('Declined', 'declined')]),
    ):
        existing = role_type(c, ident)
        if not existing and ident == ENGINEERING:
            existing = next((r[0] for r in c.execute('SELECT id,name FROM task_types WHERE role IS NULL ORDER BY id')
                             if _dev(r)), None)
        if not existing:
            row = c.execute('SELECT id FROM task_types WHERE (id=? OR name=? COLLATE NOCASE) AND role IS NULL',
                            (ident, name)).fetchone()
            existing = row[0] if row else None
        if existing:
            c.execute('UPDATE task_types SET role=?,manager=coalesce(manager,?) WHERE id=?', (ident, manager, existing))
            continue
        c.execute('INSERT INTO task_types(id,name,bots,manager,role,steps_only,created,updated) VALUES(?,?,?,?,?,?,?,?)',
                  (ident, name, 'work', manager, ident, int(ident == PRODUCT), H.now(), H.now()))
        for position, (step, status) in enumerate(steps):
            c.execute('INSERT INTO task_steps(id,type_id,name,position,status) VALUES(?,?,?,?,?)',
                      (ident + '-' + str(position), ident, step, position, status))


def sync(c, task_id):
    from . import hubdb as H
    row = H.task(c, task_id)
    if not row or row.get('type_id') != role_type(c, PRODUCT):
        return
    typ = H.type_get(c, row['type_id'])
    current = next((s['name'] for s in typ['steps'] if s['id'] == row['step_id']), None)
    if current not in ('Approved', 'Building', 'Shipped'):
        return
    # A child shipped when it reached a done status. Closed counts too where the type has no done step (the default
    # Engineering type's Done is closed), or when it was closed straight from done: accepting finished work closes
    # it. A Dev ticket closed from any other status (a duplicate, superseded, or declined first) did not ship.
    total, unfinished, shipped = c.execute(
        "SELECT count(*),coalesce(sum(t.status NOT IN ('done','closed','declined')),0),"
        "coalesce(sum(t.status='done' OR (t.status='closed' AND (NOT EXISTS "
        "(SELECT 1 FROM task_steps s WHERE s.type_id=t.type_id AND s.status='done') OR "
        # The unary + keeps SQLite on the per-task index: without ANALYZE it picks task_events(field, ts) and reads
        # every status change on the server.
        "(SELECT e.old FROM task_events e WHERE e.task_id=t.id AND +e.field='status' "
        "ORDER BY e.ts DESC,e.rowid DESC LIMIT 1)='done'))),0) "
        "FROM task_relations r JOIN tasks t ON t.id=r.from_task "
        "WHERE r.to_task=? AND r.kind='parent' AND t.type_id IN (SELECT id FROM task_types WHERE role=?)",
        (task_id, ENGINEERING)).fetchone()
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

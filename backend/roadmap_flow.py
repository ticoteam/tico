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
    from .read_cache import one
    row = one(c, 'SELECT id FROM task_types WHERE role=? ORDER BY created_at,id LIMIT 1', (role,))
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
                return [r[0] for r in c.execute('SELECT id FROM task_types WHERE ' + sql + ' ORDER BY created_at,id', args)]
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
    # A Dev tickets type gets its hand-off wake rules once (rules.seed_dev); they are rows a person can edit.
    from .rules import seed_dev
    seed_dev(c)
    # ...and its check steps their pools of reviewers and QA (backend/check_pools.py), once.
    from .check_pools import seed as seed_pools
    seed_pools(c)


def ensure(c):
    """Create the two role types when they are missing. An existing type keeps the manager it has; one with
    none gets the default."""
    from . import hubdb as H
    for ident, name, manager, steps in (
        (PRODUCT, 'Product roadmap', LEGACY_MANAGERS[PRODUCT], [
            ('Suggested', 'open'), ('Exploring', 'doing'), ('Spec', 'review'),
            ('Approved', 'review'), ('Building', 'doing'), ('Shipped', 'done'), ('Declined', 'declined')]),
        (ENGINEERING, 'Engineering', LEGACY_MANAGERS[ENGINEERING], [
            ('Todo', 'open'), ('Doing', 'doing'),
            ('In review', 'review'), ('Ready to ship', 'ready'), ('Done', 'closed')]),
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
        c.execute('INSERT INTO task_types(id,name,bots,manager,role,steps_only,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)',
                  (ident, name, 'work', manager, ident, int(ident == PRODUCT), H.now(), H.now()))
        for position, (step, status) in enumerate(steps):
            c.execute('INSERT INTO task_steps(id,type_id,name,position,status) VALUES(?,?,?,?,?)',
                      (ident + '-' + str(position), ident, step, position, status))


# The roadmap steps a linked Dev ticket moves an item between, in order. Ready to ship is optional: a type without it
# goes from Building straight to Shipped.
FLOW = ('Approved', 'Building', 'Ready to ship', 'Shipped')
READY_STEP = 'Ready to ship'
READY_FROM = 'pending release'     # a Dev tickets step: it and every step after it are "ready to ship"
READY_MARKER = 'roadmap_ready_to_ship_step'


def tickets(c, task_id):
    """The engineering tickets that deliver a roadmap item: its children and the tickets it is blocked by, each
    once, as (id, status, type_id, step position, closed straight from done, type has a done step)."""
    return c.execute(
        "SELECT t.id,t.status,t.type_id,s.position,"
        # The unary + keeps SQLite on the per-task index: without ANALYZE it picks task_events(field, ts) and reads
        # every status change on the server.
        "t.status='closed' AND (SELECT e.old FROM task_events e WHERE e.task_id=t.id AND +e.field='status' "
        "ORDER BY e.created_at DESC,e.rowid DESC LIMIT 1)='done',"
        "EXISTS (SELECT 1 FROM task_steps d WHERE d.type_id=t.type_id AND d.status='done') "
        "FROM tasks t LEFT JOIN task_steps s ON s.id=t.step_id "
        "WHERE t.id IN (SELECT r.from_task FROM task_relations r WHERE r.to_task=? AND r.kind IN ('parent','blocks')) "
        "AND t.type_id IN (SELECT id FROM task_types WHERE role=?)", (task_id, ENGINEERING)).fetchall()


def stage(c, ticket, marks):
    """Where one ticket is for its roadmap item: 'shipped', 'ready', 'started', 'waiting' or None (does not count).

    A ticket shipped when it reached a done status. Closed counts too where the type has no done step (the default
    Engineering type's Done is closed), or when it was closed straight from done: accepting finished work closes it.
    One closed from any other status (a duplicate, superseded, or declined first) and a declined one count for nothing.
    Started is any step after the type's first; ready is its Pending Release step or later (or a ready status).
    Steps, not statuses: Review 1 and Pending Release are both review."""
    _, status, type_id, position, closed_done, has_done = ticket
    if status == 'done' or status == 'closed' and (closed_done or not has_done):
        return 'shipped'
    if status in ('closed', 'declined'):
        return None
    if type_id not in marks:
        steps = c.execute('SELECT position,name,status FROM task_steps WHERE type_id=? ORDER BY position',
                          (type_id,)).fetchall()
        first = steps[0][0] if steps else None
        ready = next((s[0] for s in steps if s[2] == 'ready' or (s[1] or '').strip().lower() == READY_FROM), None)
        marks[type_id] = (first, ready)
    first, ready = marks[type_id]
    if status == 'ready' or position is not None and ready is not None and position >= ready:
        return 'ready'
    if position is not None and first is not None and position > first or position is None and status in ('doing', 'review'):
        return 'started'
    return 'waiting'


def wanted(stages):
    """The roadmap step for these tickets' stages, or None when no ticket is linked. Linking alone moves nothing: the
    item is Building once a ticket is In Progress or later, Ready to ship once every ticket that counts is at Pending
    Release or later, Shipped once every one is done. It follows them back the same way on every change."""
    if not stages:
        return None
    counted = [s for s in stages if s]
    if counted and all(s == 'shipped' for s in counted):
        return 'Shipped'
    if counted and all(s in ('ready', 'shipped') for s in counted):
        return READY_STEP
    return 'Building' if any(s != 'waiting' for s in counted) else 'Approved'


def sync(c, task_id):
    from . import hubdb as H
    row = H.task(c, task_id)
    if not row or row.get('type_id') != role_type(c, PRODUCT):
        return
    typ = H.type_get(c, row['type_id'])
    current = next((s['name'] for s in typ['steps'] if s['id'] == row['step_id']), None)
    if current not in FLOW:
        return
    marks = {}
    goal = wanted([stage(c, t, marks) for t in tickets(c, task_id)])
    names = {s['name'] for s in typ['steps']}
    if goal == READY_STEP and goal not in names:
        goal = 'Building'
    step = next((s for s in typ['steps'] if s['name'] == goal), None)
    if not step or current == goal:
        return
    H._set_status(c, H.KEEPER, row, step=step['id'])
    version = ',version=version+1' if 'version' in {r[1] for r in c.execute('PRAGMA table_info(tasks)')} else ''
    c.execute('UPDATE tasks SET done_at=?' + version + ' WHERE id=?',
              (H.now() if goal == 'Shipped' else None, task_id))
    H._recount(c, row['owner_id'])
    H.event(c, H.KEEPER, 'task.update', task_id, {'step': step['id']})


def delivers(c, item, ticket):
    """Whether `ticket` is an engineering ticket that `item`, a roadmap item, waits on: its link stays when the
    ticket is done, so the item still counts it (Shipped is every linked ticket done) and follows it if it reopens."""
    product = role_type(c, PRODUCT)
    return bool(product and (item or {}).get('type_id') == product and c.execute(
        'SELECT 1 FROM task_types WHERE id=? AND role=?', ((ticket or {}).get('type_id'), ENGINEERING)).fetchone())


def ready_step(c):
    """Give the Product roadmap a Ready to ship step right after Building, once: status doing, and Building's public
    name ('In progress'), so nothing changes publicly until Shipped. Later steps move down one. A team that deletes or
    renames the step does not get it back: the marker is set once a roadmap type has been looked at. Runs after
    roadmaps.migrate, which makes the public_name column and the registry_metadata table."""
    from . import hubdb as H
    if c.execute('SELECT 1 FROM registry_metadata WHERE key=?', (READY_MARKER,)).fetchone():
        return
    type_id = role_type(c, PRODUCT)
    if not type_id:
        return
    steps = c.execute('SELECT id,name,position FROM task_steps WHERE type_id=? ORDER BY position', (type_id,)).fetchall()
    building = next((s for s in steps if s[1] == 'Building'), None)
    c.execute('SAVEPOINT roadmap_ready_step')
    try:
        if building and not any(s[1] == READY_STEP for s in steps):
            ident, n = type_id + '-ready', 1
            while c.execute('SELECT 1 FROM task_steps WHERE id=?', (ident,)).fetchone():
                n += 1
                ident = f'{type_id}-ready-{n}'
            # Highest first: a type may keep positions unique.
            for later in sorted((s for s in steps if s[2] > building[2]), key=lambda s: -s[2]):
                c.execute('UPDATE task_steps SET position=position+1 WHERE id=?', (later[0],))
            public = c.execute('SELECT public_name FROM task_steps WHERE id=?', (building[0],)).fetchone()[0]
            c.execute('INSERT INTO task_steps(id,type_id,name,position,status,public_name) VALUES(?,?,?,?,?,?)',
                      (ident, type_id, READY_STEP, building[2] + 1, 'doing', public or 'In progress'))
            c.execute('UPDATE task_types SET updated_at=? WHERE id=?', (H.now(), type_id))
        c.execute('INSERT INTO registry_metadata(key,value) VALUES(?,?)', (READY_MARKER, '{"done": true}'))
    except BaseException:
        c.execute('ROLLBACK TO roadmap_ready_step')
        raise
    finally:
        c.execute('RELEASE roadmap_ready_step')


def changed(c, task_id, itself=True):
    """A task changed: sync it, its parent and every task it blocks (a roadmap item waits on its Dev tickets).
    `itself=False` leaves the task's own step alone: someone just moved it there by hand."""
    from . import task_relations as TR
    if itself:
        sync(c, task_id)
    for item in dict.fromkeys([TR.parent_of(c, task_id), *TR.blocked_ids(c, task_id)]):
        if item:
            sync(c, item)

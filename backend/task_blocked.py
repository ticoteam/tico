"""One persisted hold and active-build clock for task boards, filters and stall checks.

Waiting remains the wire status. Engineering types give it a real Blocked column;
the previous step is retained so clearing a hold never skips a review gate.
"""
import threading
import time
from datetime import datetime


def ensure(c):
    from . import hubdb as H
    for name, kind in (
        ('blocked_since', 'TEXT'), ('blocked_reason', 'TEXT'), ('blocked_kind', 'TEXT'),
        ('blocked_from_step', 'TEXT'), ('blocked_from_status', 'TEXT'), ('blocked_gate_json', 'TEXT'),
        ('building_started_at', 'TEXT'), ('building_seconds', 'REAL NOT NULL DEFAULT 0'),
    ):
        H.add_column(c, 'tasks', name, kind)
    c.execute('CREATE INDEX IF NOT EXISTS tasks_blocked_since ON tasks(blocked_since) WHERE blocked_since IS NOT NULL')


def ensure_steps(c):
    """Place the engineering hold before the first working step, retaining every step id."""
    from . import hubdb as H
    for typ in c.execute("SELECT id FROM task_types WHERE role='engineering'").fetchall():
        steps = [dict(s) for s in c.execute('SELECT * FROM task_steps WHERE type_id=? ORDER BY position,id', (typ[0],))]
        blocked = next((s for s in steps if s['name'] == 'Blocked' and s['status'] == 'waiting'), None)
        rest = [s for s in steps if s != blocked]
        at = next((i for i, s in enumerate(rest) if s['status'] != 'open'), len(rest))
        if not blocked:
            blocked = {'id': H.new_id(), 'position': None}
            # Role claiming also runs before the public-name migration backfills its defaults.
            public = 'public_name' in {r[1] for r in c.execute('PRAGMA table_info(task_steps)')}
            c.execute("INSERT INTO task_steps(id,type_id,name,position,status" + (",public_name" if public else "") +
                      ") VALUES(?,?,'Blocked',?,'waiting'" + (",'Blocked'" if public else "") + ")",
                      (blocked['id'], typ[0], at))
        for position, step in enumerate(rest[:at] + [blocked] + rest[at:]):
            if step['position'] != position:
                c.execute('UPDATE task_steps SET position=? WHERE id=?', (position, step['id']))
        # Upgrade active builds without restarting their age at server startup. This runs inside a deferred
        # transaction that has already read, so a write statement, even one matching nothing, fails at once with
        # "database is locked" if another connection committed meanwhile: write only when a build needs it.
        unstamped = ("FROM tasks WHERE type_id=? AND status='doing' AND building_started_at IS NULL "
                     "AND building_seconds=0 AND created LIKE '____-__-__T%'")
        if c.execute("SELECT 1 " + unstamped + " LIMIT 1", (typ[0],)).fetchone():
            c.execute("UPDATE tasks SET building_started_at=coalesce((SELECT max(ts) FROM task_events e WHERE "
                      "e.task_id=tasks.id AND e.field='step' AND e.new=tasks.step_id),created) "
                      "WHERE id IN (SELECT id " + unstamped + ")", (typ[0],))


def backfill(c):
    """Run after retiring legacy held steps, so the saved resume step still exists."""
    from . import hubdb as H
    for row in H._rows(c.execute("SELECT t.* FROM tasks t JOIN task_types ty ON ty.id=t.type_id "
                                "WHERE ty.role='engineering' AND t.status='waiting' AND t.blocked_since IS NULL")):
        step = H._one(c, 'SELECT status FROM task_steps WHERE id=?', (row.get('step_id'),))
        H._set_status(c, H.KEEPER, row, 'waiting', note=row.get('note') or 'Waiting on a hold')
        previous = step['status'] if step and step['status'] != 'waiting' else 'open'
        c.execute('UPDATE tasks SET blocked_from_status=? WHERE id=?', (previous, row['id']))


def seconds(start, at):
    if not start:
        return 0
    return max(0, (datetime.fromisoformat(at.replace('Z', '+00:00')) -
                   datetime.fromisoformat(start.replace('Z', '+00:00'))).total_seconds())


def clocks(row, at):
    """The Building clock excludes every hold; blocked age starts at the first hold."""
    return {'building_seconds': (row.get('building_seconds') or 0) + seconds(row.get('building_started_at'), at),
            'blocked_seconds': seconds(row.get('blocked_since'), at)}


def transition(c, row, effective, step_id, note, at, manual=False):
    """Called in the same transaction as every status/step move."""
    from . import hubdb as H
    if 'blocked_since' not in row:
        return  # old, deliberately minimal hubdb fixtures
    if (H.type_get(c, row.get('type_id')) or {}).get('role') == 'product-roadmap':
        return  # roadmap steps derive from linked delivery tickets, not task holds
    building = effective == 'doing'
    elapsed = clocks(row, at)['building_seconds']
    c.execute('UPDATE tasks SET building_seconds=?,building_started_at=? WHERE id=?',
              (elapsed, at if building else None, row['id']))
    if effective == 'waiting':
        if not row.get('blocked_since'):
            reason = str(note or '').strip().splitlines()[0] if str(note or '').strip() else 'Waiting on a hold'
            reason = reason[:1000]
            kind = 'manual' if manual else 'dependency' if H.waiting_for(c, row) else 'stranded'
            c.execute('UPDATE tasks SET blocked_since=?,blocked_reason=?,blocked_kind=?,blocked_from_step=?,blocked_from_status=? WHERE id=?',
                      (at, reason, kind, row.get('step_id'), row.get('status') or 'open', row['id']))
            H._task_event(c, row['id'], H.KEEPER, 'blocked_since', None, at, reason)
        elif manual and str(note or '').strip():
            c.execute("UPDATE tasks SET blocked_reason=?,blocked_kind='manual' WHERE id=?", (str(note).strip().splitlines()[0][:1000], row['id']))
    elif row.get('blocked_since'):
        c.execute('UPDATE tasks SET blocked_since=NULL,blocked_reason=NULL,blocked_kind=NULL,blocked_from_step=NULL,blocked_from_status=NULL,blocked_gate_json=NULL WHERE id=?', (row['id'],))
        H._task_event(c, row['id'], H.KEEPER, 'blocked_since', row['blocked_since'], None, 'Hold cleared')


def bump(c, row):
    # hubdb-only callers predate the server's optimistic version column.
    if 'version' in row:
        c.execute('UPDATE tasks SET version=version+1 WHERE id=?', (row['id'],))


def wait(c, row, include_manual=True):
    from . import hubdb as H
    if not include_manual and row.get('blocked_kind') == 'manual':
        row = {**row, 'blocked_kind': None}
    detail = H.wait_detail(c, row)
    # An armed PR completion callback is a wake subscription, not a hold on building.
    return detail if detail and not detail['why'].startswith('GitHub on ') else None


def hold(c, task_id, reason, kind='dependency'):
    from . import hubdb as H, review_gate
    reason = str(reason)[:1000]
    row = H.task(c, task_id)
    # A refused move into Engineering still holds a task on its original board.
    # Ordinary dependency holds stay scoped to Engineering's recovery rules.
    if (not row or row['status'] not in (*H.ACTIVE_STATUSES, 'review')
            or kind != 'gate' and not review_gate.engineering(c, row)):
        return
    from . import step_flow
    if step_flow._ready(c) and not step_flow.can_hold(c, row.get('step_id')):
        return        # a step no ticket is held from (its flow's can_hold): a merged change is reverted or fixed forward
    if row.get('blocked_since'):
        # Retain the first hold time and resume step when another gate also holds it.
        if (kind == 'gate' and row.get('blocked_kind') != 'manual'
                or kind == 'check' and row.get('blocked_kind') in ('dependency', 'stranded', 'check')
                and (row.get('blocked_kind'), row.get('blocked_reason')) != (kind, reason)):
            c.execute('UPDATE tasks SET blocked_kind=?,blocked_reason=?,updated=? WHERE id=?', (kind, reason, H.now(), task_id))
            bump(c, row)
        return
    H._set_status(c, H.KEEPER, row, 'waiting', note=reason)
    c.execute('UPDATE tasks SET blocked_kind=?,blocked_reason=? WHERE id=?', (kind, reason, task_id))
    bump(c, row)


def reconciliation(c, row, snapshots=None):
    """Read the needed action before taking the writer, then recheck under it. `snapshots` is a fresh GitHub read of
    the linked PRs (review_gate.snapshot): only from one do all-green hosted checks at the current head clear a gate
    hold without a receipt (fresh())."""
    from . import hubdb as H, review_gate
    if (not row or not row.get('blocked_since') or row.get('blocked_kind') in ('manual', 'stranded')
            or not review_gate.engineering(c, row)):
        return None
    waiting = wait(c, row)
    if row.get('blocked_kind') == 'gate':
        from . import move_retry
        # A refused move Tico still retries (backend/move_retry.py) is made when the gate clears; resuming the earlier
        # step here would undo it and drop the retry.
        if move_retry.remembered(c, row):
            return None
        gate = H._json(row.get('blocked_gate_json'), {}) or {}
        # A fresh refusal can describe a newer head/check result than the cached link.
        # Only a subsequent GitHub observation may clear that uncertainty.
        for pr in review_gate.links(c, row):
            if gate.get('pr') != pr['url']:
                continue
            if snapshots is not None:
                detail = snapshots.get(pr['id']) or {}
                seen = detail.get('at') or ''
            else:
                detail = H._json(pr.get('detail_json'), {}) or {}
                seen = pr.get('updated') or ''
            if ((gate.get('kind') == 'ci' or detail.get('head_sha') != gate.get('head'))
                    and seen <= gate.get('at', '')):
                return None
        if review_gate.problem(c, row, row['owner'], snapshots=snapshots, require_pr=True):
            return None
        if waiting:
            return 'dependency'
    if waiting or row.get('blocked_kind') == 'worktree' and H.unlinked_pushes(c, row['id']):
        return None
    return 'resume'


def reconcile(c, task_id, snapshots=None):
    from . import hubdb as H
    row = H.task(c, task_id)
    action = reconciliation(c, row, snapshots)
    if action == 'dependency':
        c.execute("UPDATE tasks SET blocked_kind='dependency',blocked_reason='Waiting on a dependency',updated=? WHERE id=?", (H.now(), task_id))
        bump(c, row)
    if action != 'resume':
        return
    previous = H._one(c, 'SELECT * FROM task_steps WHERE id=? AND type_id=?', (row.get('blocked_from_step'), row.get('type_id')))
    # Restoring the pre-hold step is not retrying the refused destination.
    H._set_status(c, H.KEEPER, row, row.get('blocked_from_status') or 'open',
                  step=previous['id'] if previous else None, note='Hold cleared')
    bump(c, row)
    # Back at a check step: a verdict given there during the hold moves the ticket now.
    from . import verdict_moves
    verdict_moves.resumed(c, task_id)


FRESH_EVERY_S = 60                  # how often fresh() looks
FRESH_PER_PASS = 20
_fresh_lock = threading.Lock()
_fresh = {"at": 0.0, "seen": {}, "tried": {}}


def fresh(service, store=None, force=False):
    """Gate holds the cached read keeps, read once afresh from GitHub per change in what the gate reads: the webhook
    cache counts skipped checks as passing, so all-green hosted checks at the current head clear a hold only from a
    fresh read (review_gate.problem). A hold with a move Tico still retries is left to that retry, which reads GitHub
    itself (backend/move_retry.py). The holds tried longest ago go first, so reads that keep failing on some
    holds never keep the rest from their turn. Returns the ids of tasks whose hold cleared."""
    from . import hubdb as H, move_retry, review_gate
    store = store or service.store
    with _fresh_lock:
        if not force and time.monotonic() < _fresh["at"]:
            return []
        _fresh["at"] = time.monotonic() + FRESH_EVERY_S
    due, held = [], set()
    with store.read() as c:
        for row in H._rows(c.execute("SELECT * FROM tasks WHERE blocked_since IS NOT NULL AND blocked_kind='gate'")):
            held.add(row['id'])
            prs = review_gate.links(c, row)
            if not prs or reconciliation(c, row) is not None or move_retry.remembered(c, row):
                continue
            seen = move_retry.fingerprint(c, row)
            if _fresh["seen"].get(row['id']) != seen:
                due.append((row, prs, seen))
    # Forget holds that ended, so the record stays as small as the holds that are live.
    for record in (_fresh["seen"], _fresh["tried"]):
        for task_id in set(record) - held:
            record.pop(task_id, None)
    due.sort(key=lambda item: _fresh["tried"].get(item[0]['id'], 0.0))
    cleared = []
    for row, prs, seen in due[:FRESH_PER_PASS]:
        _fresh["tried"][row['id']] = time.monotonic()
        snapshots = review_gate.snapshot(service, prs)
        if not snapshots:
            _fresh["seen"][row['id']] = seen
            continue                    # no GitHub App configured: the cached read is all there is
        with store.transaction() as c:
            now_row = H.task(c, row['id'])
            # Anything the gate reads that changed while GitHub was read (an edit, or a webhook's newer check result
            # at the same head, which leaves the task version alone) supersedes this read; the next pass reads again.
            if (not now_row or now_row.get('version') != row.get('version')
                    or move_retry.fingerprint(c, now_row) != seen):
                continue
            # A failed read is tried again next pass; a complete one waits for the next change in what the gate reads.
            if not any(s.get('error') for s in snapshots.values()):
                _fresh["seen"][row['id']] = seen
            reconcile(c, row['id'], snapshots)
            if not H.task(c, row['id']).get('blocked_since'):
                cleared.append(row['id'])
    return cleared


def alert_target(c, row, at):
    from . import hubdb as H
    if not row.get('blocked_since') or seconds(row['blocked_since'], at) < 4 * 3600:
        return None
    from . import review_gate
    # The same scope as hold() and reconciliation(): an ordinary waiting task on another board is no engineering
    # hold, while a refused move into Engineering (a gate hold) still is.
    if row.get('blocked_kind') != 'gate' and not review_gate.engineering(c, row):
        return None
    manager = H.type_manager(c, row)
    target = H.bot_actor(manager) if manager else row['requester']
    if target == row['owner'] or not H.task_private_readable(c, target, row):
        return None
    if H.is_bot(target) and (H.bot(c, H.actor_id(target)) or {}).get('state') != 'active':
        return None
    if c.execute("SELECT 1 FROM events WHERE action='task.blocked_age' AND target=? AND json_extract(detail_json,'$.since')=?", (row['id'], row['blocked_since'])).fetchone():
        return None
    return target


def candidates(c, at):
    """Discover only actual hold changes or due alerts in a read snapshot."""
    from . import hubdb as H
    rows = H._rows(c.execute("SELECT t.* FROM tasks t JOIN task_types ty ON ty.id=t.type_id "
                            "WHERE ty.role='engineering' AND t.status IN ('open','doing','review','ready') "
                            "UNION SELECT * FROM tasks WHERE blocked_since IS NOT NULL"))
    return [row['id'] for row in rows if (
        (not row.get('blocked_since') and wait(c, row))
        or reconciliation(c, row) or alert_target(c, row, at))]


def sweep(c, at, task_ids=None):
    """Share the existing stall sweep; one private-safe age alert per blocked episode."""
    from . import hubdb as H
    scope, args = '', ()
    if task_ids is not None:
        args = tuple(task_ids)
        scope = ' AND t.id IN (' + ','.join('?' for _ in args) + ')' if args else ' AND 0'
    # Reuse the existing wait predicates for asks, approvals and legacy holds too.
    # Their creation paths need no separate state/query implementation.
    for row in H._rows(c.execute("SELECT t.* FROM tasks t JOIN task_types ty ON ty.id=t.type_id "
                                "WHERE ty.role='engineering' AND t.status IN ('open','doing','review','ready')" + scope, args)):
        if detail := wait(c, row):
            if detail.get('check'):
                from . import check_blocked
                check_blocked.apply(c, row['id'])
            else:
                hold(c, row['id'], 'Waiting on ' + detail['why'])
    for row in H._rows(c.execute('SELECT t.* FROM tasks t WHERE blocked_since IS NOT NULL' + scope, args)):
        reconcile(c, row['id'])
        row = H.task(c, row['id'])
        target = alert_target(c, row, at)
        if not target:
            continue
        key = row['blocked_since']
        H._wake(c, row, target, f"Blocked over 4 hours: {row['title']}\n{row['blocked_reason']}\nSince {key}",
                refs={'news': 'task_blocked_age'}, quiet_bots=False)
        H.event(c, H.KEEPER, 'task.blocked_age', row['id'], {'since': key})

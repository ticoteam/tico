"""One persisted hold and active-build clock for task boards, filters and stall checks.

A hold is a mark on the ticket, not a place on the board: the ticket keeps the step it is in, and its wire status is
waiting while it is held, so wakes, claims and the build clock see it held. The hold's own columns (blocked_since,
blocked_from_step, blocked_from_status) say when it began and where clearing it returns the ticket, so clearing a hold
never skips a review gate.
"""
import threading
import time
from datetime import datetime


def ensure(c):
    from . import hubdb as H
    for name, kind in (
        ('blocked_since', 'TEXT'), ('blocked_reason', 'TEXT'), ('blocked_kind', 'TEXT'),
        ('blocked_from_step', 'TEXT'), ('blocked_from_status', 'TEXT'), ('blocked_gate', 'TEXT'),
        ('building_started_at', 'TEXT'), ('building_seconds', 'REAL NOT NULL DEFAULT 0'),
        # When its last tracked hold cleared, for a task still waiting: the stranded sweep's clock (task_holds.py).
        ('holds_cleared_at', 'TEXT'),
    ):
        H.add_column(c, 'tasks', name, kind)
    c.execute('CREATE INDEX IF NOT EXISTS tasks_blocked_since ON tasks(blocked_since) WHERE blocked_since IS NOT NULL')


def stamp_builds(c):
    """Stamp the start of every active build that predates the building clock's own column. Runs at every start."""
    for typ in c.execute("SELECT id FROM task_types WHERE role='engineering'").fetchall():
        # Upgrade active builds without restarting their age at server startup. This runs inside a deferred
        # transaction that has already read, so a write statement, even one matching nothing, fails at once with
        # "database is locked" if another connection committed meanwhile: write only when a build needs it.
        unstamped = ("FROM tasks WHERE type_id=? AND status='doing' AND building_started_at IS NULL "
                     "AND building_seconds=0 AND created_at LIKE '____-__-__T%'")
        if c.execute("SELECT 1 " + unstamped + " LIMIT 1", (typ[0],)).fetchone():
            c.execute("UPDATE tasks SET building_started_at=coalesce((SELECT max(ts) FROM task_events e WHERE "
                      "e.task_id=tasks.id AND e.field='step' AND e.new=tasks.step_id),created_at) "
                      "WHERE id IN (SELECT id " + unstamped + ")", (typ[0],))


def backfill(c):
    """Run after retiring legacy held steps, so the saved resume step still exists. A ticket in a team's own waiting
    step stays there on any board, held as if moved there by hand (hubdb.declared_hold), so no sweep reopens it. This
    includes a ticket parked in the Blocked step an earlier version made. The Product roadmap's steps follow its
    delivery tickets, not holds."""
    from . import hubdb as H
    from .roadmap_flow import PRODUCT
    for row in H._rows(c.execute(
            "SELECT t.* FROM tasks t JOIN task_types ty ON ty.id=t.type_id LEFT JOIN task_steps s ON s.id=t.step_id "
            "WHERE t.status='waiting' AND t.blocked_since IS NULL AND coalesce(ty.role,'')<>? "
            "AND (ty.role='engineering' OR s.status='waiting')", (PRODUCT,))):
        step = H._one(c, 'SELECT s.name,s.status,ty.role FROM task_steps s JOIN task_types ty ON ty.id=s.type_id '
                         'WHERE s.id=?', (row.get('step_id'),))
        H._set_status(c, H.KEEPER, row, 'waiting', note=row.get('note') or 'Waiting on a hold')
        previous = step['status'] if step and step['status'] != 'waiting' else 'open'
        c.execute('UPDATE tasks SET blocked_from_status=? WHERE id=?', (previous, row['id']))
        if step and step['status'] == 'waiting':
            c.execute("UPDATE tasks SET blocked_kind='manual',blocked_reason=? WHERE id=?",
                      (f"In {step['name']}"[:1000], row['id']))


def forget_retired_resumes(c):
    """A held engineering ticket resumes at On Deck, not at the step it would go back to, when that step is only where
    retiring its waiting step put it: a step read from its history, often weeks old, that nobody chose. Its last move
    tells: retiring wrote it, and a hold moves nothing. A ticket held in Blocked by an older version has that hold's
    move after it, so its step and the move before it tell. Writes only when a ticket needs it, as stamp_builds does."""
    from . import hubdb as H
    for task_id, saved, step in c.execute(
            "SELECT t.id,t.blocked_from_step,t.step_id FROM tasks t JOIN task_types ty ON ty.id=t.type_id "
            "WHERE ty.role='engineering' AND t.blocked_since IS NOT NULL "
            "AND t.blocked_from_step IS NOT NULL").fetchall():
        moves = c.execute("SELECT new,note FROM task_events WHERE task_id=? AND field='step' "
                          "ORDER BY ts DESC,rowid DESC LIMIT 2", (task_id,)).fetchall()
        retired_here = bool(moves) and moves[0][0] == saved and moves[0][1] == H.RETIRED_WAITING
        held_in_blocked = (len(moves) == 2 and moves[0][0] == step and moves[1][0] == saved
                           and moves[1][1] == H.RETIRED_WAITING)
        if retired_here or held_in_blocked:
            c.execute("UPDATE tasks SET blocked_from_step=NULL,blocked_from_status='open' WHERE id=?", (task_id,))


def leave_retired_blocked(c):
    """Holds an earlier version parked in its own Blocked step go back to a working step: the one they left when it
    still exists, else the step its last move into Blocked came from (the history), else On Deck (after
    forget_retired_resumes, so a stale step is already gone). The hold itself stays, reason and clock unchanged; a
    person's manual hold stays put. The Blocked step is left empty for the team to delete.
    Writes only when a ticket is in that step, as stamp_builds does."""
    from . import hubdb as H
    rows = c.execute(
        "SELECT t.id,t.type_id,t.blocked_from_step,t.step_id FROM tasks t "
        "JOIN task_types ty ON ty.id=t.type_id JOIN task_steps s ON s.id=t.step_id "
        "WHERE ty.role='engineering' AND t.status='waiting' AND t.blocked_since IS NOT NULL "
        "AND coalesce(t.blocked_kind,'')<>'manual' AND s.name='Blocked' AND s.status='waiting'").fetchall()
    for task_id, type_id, saved, step_id in rows:
        steps = {s[0]: (s[1], s[2]) for s in c.execute(
            "SELECT id,name,status FROM task_steps WHERE type_id=?", (type_id,)).fetchall()}
        came_from = c.execute("SELECT old FROM task_events WHERE task_id=? AND field='step' AND new=? "
                              "ORDER BY ts DESC,rowid DESC LIMIT 1", (task_id, step_id)).fetchone()
        target = next((sid for sid in (saved, came_from and came_from[0])
                       if sid in steps and steps[sid][1] in H.ACTIVE_STATUSES and steps[sid][1] != 'waiting'), None)
        if not target:
            target = next((sid for sid, (name, status) in steps.items()
                           if name == 'On Deck' and status == 'open'), None)
        if not target:
            continue
        c.execute("UPDATE tasks SET step_id=?,step_rank=? WHERE id=?",
                  (target, H._step_end(c, target, task_id), task_id))
        H._task_event(c, task_id, H.KEEPER, 'step', step_id, target, 'Its hold left the retired Blocked step')


def seconds(start, at):
    if not start:
        return 0
    return max(0, (datetime.fromisoformat(at.replace('Z', '+00:00')) -
                   datetime.fromisoformat(start.replace('Z', '+00:00'))).total_seconds())


def clocks(row, at):
    """The Building clock excludes every hold; blocked age starts at the first hold."""
    return {'building_seconds': (row.get('building_seconds') or 0) + seconds(row.get('building_started_at'), at),
            'blocked_seconds': seconds(row.get('blocked_since'), at)}


def transition(c, row, effective, step_id, note, at, manual=False, stepped=False):
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
            if manual:
                reason = str(note or '').strip().splitlines()[0] if str(note or '').strip() else 'Waiting on a hold'
                kind = 'manual'
            else:
                # Named from what holds it, never from the note: a note can't say it waits on someone it doesn't.
                from . import task_holds
                reason, kind = task_holds.reason(c, row)
            reason = reason[:1000]
            c.execute('UPDATE tasks SET blocked_since=?,blocked_reason=?,blocked_kind=?,blocked_from_step=?,blocked_from_status=? WHERE id=?',
                      (at, reason, kind, row.get('step_id'), row.get('status') or 'open', row['id']))
            H._task_event(c, row['id'], H.KEEPER, 'blocked_since', None, at, reason)
        elif manual and (str(note or '').strip() or row.get('blocked_kind') != 'manual'):
            # A person's move into a waiting step declares the hold even over one Tico found: the old reason, written
            # for the hold it replaces, never stays on a ticket parked by hand.
            text = str(note).strip().splitlines()[0][:1000] if str(note or '').strip() else 'Waiting on a hold'
            c.execute("UPDATE tasks SET blocked_reason=?,blocked_kind='manual' WHERE id=?", (text, row['id']))
        if stepped and row.get('blocked_since') and step_id and step_id != row.get('step_id'):
            # A move to another working step while held (a person's, or a retry's that stays held): the hold clears
            # onto the step it was moved to, not the one it left (hubdb._task_state's resume).
            landed = c.execute('SELECT status FROM task_steps WHERE id=?', (step_id,)).fetchone()
            if landed and landed[0] != 'waiting':
                c.execute('UPDATE tasks SET blocked_from_step=?,blocked_from_status=? WHERE id=?',
                          (step_id, landed[0], row['id']))
    elif row.get('blocked_since'):
        c.execute('UPDATE tasks SET blocked_since=NULL,blocked_reason=NULL,blocked_kind=NULL,blocked_from_step=NULL,blocked_from_status=NULL,blocked_gate=NULL'
                  + (',holds_cleared_at=NULL' if 'holds_cleared_at' in row else '') + ' WHERE id=?', (row['id'],))
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
            c.execute('UPDATE tasks SET blocked_kind=?,blocked_reason=?,updated_at=? WHERE id=?', (kind, reason, H.now(), task_id))
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
    if not row or not row.get('blocked_since') or row.get('blocked_kind') in ('manual', 'stranded'):
        return None
    # A board move can retain a worktree hold; its pushes must still be linked before it resumes.
    if row.get('blocked_kind') != 'worktree' and not review_gate.engineering(c, row):
        return None
    waiting = wait(c, row)
    if row.get('blocked_kind') == 'gate':
        from . import move_retry
        # A refused move Tico still retries (backend/move_retry.py) is made when the gate clears; resuming the earlier
        # step here would undo it and drop the retry.
        if move_retry.remembered(c, row):
            return None
        gate = H._json(row.get('blocked_gate'), {}) or {}
        # A fresh refusal can describe a newer head/check result than the cached link.
        # Only a subsequent GitHub observation may clear that uncertainty.
        for pr in review_gate.links(c, row):
            if gate.get('pr') != pr['url']:
                continue
            if snapshots is not None:
                detail = snapshots.get(pr['id']) or {}
                seen = detail.get('at') or ''
            else:
                detail = H._json(pr.get('detail'), {}) or {}
                seen = pr.get('updated_at') or ''
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
        from . import task_holds
        c.execute("UPDATE tasks SET blocked_kind='dependency',blocked_reason=?,updated_at=? WHERE id=?",
                  (task_holds.words(c, task_holds.holds(c, row))[:1000], H.now(), task_id))
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
    from . import review_gate
    if row.get('blocked_kind') == 'worktree' and not review_gate.engineering(c, row):
        # General tasks have no Engineering step rule to wake their owner when the hold clears.
        resumed = H.task(c, task_id)
        H._wake(c, resumed, resumed['owner'], f"Worktree hold cleared: {resumed['title']}",
                refs={'news': 'assignment'})


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
    # Age alerts stay scoped to Engineering and refused moves into it; a retained worktree hold on another
    # board still clears through reconciliation, without an Engineering age alert.
    if row.get('blocked_kind') != 'gate' and not review_gate.engineering(c, row):
        return None
    manager = H.type_manager(c, row)
    target = H.bot_actor(manager) if manager else row['requester']
    if target == row['owner'] or not H.task_private_readable(c, target, row):
        return None
    if H.is_bot(target) and (H.bot(c, H.actor_id(target)) or {}).get('state') != 'active':
        return None
    if c.execute("SELECT 1 FROM events WHERE action='task.blocked_age' AND target=? AND json_extract(detail,'$.since')=?", (row['id'], row['blocked_since'])).fetchone():
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
                from . import task_holds
                found = task_holds.holds(c, row)
                hold(c, row['id'], task_holds.words(c, found) if found else 'Waiting on ' + detail['why'])
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

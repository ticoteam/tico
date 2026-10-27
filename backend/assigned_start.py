"""One recovery wake for a lost assignment; overdue starts remain visible until attempted.

The audit ledger is keyed by the assignment event, not by mutable task.updated. It survives
restarts and does not turn a Lead's profile into an idle task-pulling profile.
"""
import logging
import sqlite3

from . import auto_tasks, hubdb as H, task_privacy as privacy

EVENT = 'task.assignment-rewake'
WAKE_HOURS = 1
ESCALATE_HOURS = 2
SCAN_LIMIT = 200
CURSOR_KEY = "assignment-recovery-cursor"


def candidates(c, at=None, task_ids=None, recorded=None, *, after="", scanned=None, visible="1", manager_actor=None, attempts=None, on_error=None):
    if task_ids is not None and not task_ids:
        return
    at = at or H.now()
    cutoff = H.shift(at, hours=-WAKE_HOURS)
    params = [cutoff]
    scope = f" AND t.id IN ({','.join('?' for _ in task_ids)})" if task_ids else ''
    params.extend(task_ids or ())
    # Select the current assignment's ledger entry before reading task bodies or provenance.
    assignment_key = "coalesce((SELECT id FROM task_events WHERE task_id=t.id AND field='owner' ORDER BY ts DESC,rowid DESC LIMIT 1),t.owner||':'||t.created)"
    ledger = ("SELECT 1 FROM events e WHERE e.target=t.id AND e.action='task.assignment-rewake' "
              f"AND json_extract(e.detail_json,'$.assignment')={assignment_key}")
    if recorded is not None:
        scope += " AND " + ("EXISTS" if recorded else "NOT EXISTS") + "(" + ledger
        if recorded:
            scope += " AND json_extract(e.detail_json,'$.rewoken_at')<=?"
            params.append(H.shift(at, hours=-ESCALATE_HOURS))
        scope += ")"
    if after:
        scope += " AND t.id>?"
        params.append(after)
    if manager_actor:
        scope += " AND b.slug IN (SELECT bot FROM bot_config WHERE reports_to IN (?,?))"
        params.extend((manager_actor, H.actor_id(manager_actor)))
    # One page bounds Python wait/provenance work, including pages with no eligible wake.
    # The scheduler persists the last inspected id, so held/started rows cannot starve later work.
    attempts = {} if attempts is None else attempts
    visibility = f" AND t.id IN (SELECT id FROM tasks WHERE ({visible}))" if visible != "1" else ""
    rows = H._rows(c.execute("SELECT t.* FROM tasks t JOIN bots b ON t.owner='bot:'||b.slug "
                                 "WHERE t.status='open' AND b.state IN ('active','paused','quarantined') AND t.created<=? "
                                 + scope + visibility + " ORDER BY t.id LIMIT ?",
                                 (*params, SCAN_LIMIT)))
    earliest = min((row['created'] for row in rows), default=cutoff)
    for row in rows:
        if scanned is not None:
            scanned[:] = [row['id']]
        try:
            assignment = c.execute("SELECT id,ts FROM task_events WHERE task_id=? AND field='owner' "
                                   "ORDER BY ts DESC,rowid DESC LIMIT 1", (row['id'],)).fetchone()
            key, since = (assignment['id'], assignment['ts']) if assignment else (row['owner'] + ':' + row['created'], row['created'])
            if since > cutoff:
                continue
            saved = next((H._json(r['detail_json'], {}) for r in c.execute(
                "SELECT detail_json FROM events WHERE target=? AND action=? ORDER BY ts DESC",
                (row['id'], EVENT)) if H._json(r['detail_json'], {}).get('assignment') == key), None)
            # Most reads need only one side of the ledger. Skip the other before wait/provenance checks.
            if recorded is False and saved:
                continue
            if recorded is True and (not saved or saved['rewoken_at'] > H.shift(at, hours=-ESCALATE_HOURS)):
                continue
            if auto_tasks.waiting(c, row, starting=True):
                continue
            # Include folded inputs and next-run carried work, not just the job's original task.
            bot = H.actor_id(row['owner'])
            floor, touched = attempts.get(bot, ("9999", {}))
            if earliest < floor:
                for attempt in c.execute("SELECT id,created FROM attempts WHERE bot=? AND created>=? AND created<? ORDER BY created DESC",
                                         (bot, earliest, floor)).fetchall():
                    for tid in privacy.attempt_tasks(c, attempt['id']):
                        touched.setdefault(tid, attempt['created'])
                attempts[bot] = earliest, touched
            if touched.get(row['id'], '') >= since:
                continue
            yield row, key, since, saved
        except Exception as exc:
            if on_error is None:
                raise
            on_error(row, exc)


def due(c, at, limit=50, cursor=None, failures=None):
    """Read outside the scheduler write lock; each selected row is rechecked in its own transaction."""
    out = []
    after = cursor[0] if cursor else ""
    if cursor is not None:
        cursor[:] = [""]

    def failed(row, exc):
        logging.getLogger(__name__).exception('Assignment recovery selection failed for one task')
        if failures is not None:
            failures.append({'assigned_start': row['id'], 'error': type(exc).__name__})

    for row, _, _, saved in candidates(c, at, recorded=False, after=after, scanned=cursor, on_error=failed):
        if not saved and H.bot(c, H.actor_id(row['owner']))['state'] == 'active':
            out.append(row['id'])
            if len(out) >= limit:
                break
    return out


def sweep(c, at=None, task_ids=None):
    at = at or H.now()
    woke = []
    for row, key, since, saved in candidates(c, at, task_ids):
        if saved:
            continue
        if H.bot(c, H.actor_id(row['owner']))['state'] != 'active':
            continue
        # A delivery refusal is also one try, recorded durably; never repeat it every tick.
        c.execute('SAVEPOINT assignment_rewake')
        refusal = None
        try:
            if not c.execute('SELECT 1 FROM conversations WHERE id=?', (row['conversation_id'],)).fetchone():
                raise H.Refused('missing_conversation', 'The assignment has no task conversation.')
            message = H._wake(c, row, row['owner'], 'Assigned, not started: ' + row['title'],
                              {'assignment_rewake': True, 'needs_action': True}, trigger='other')
        except H.Refused as exc:
            c.execute('ROLLBACK TO assignment_rewake')
            message, refusal = None, exc.rule
        except Exception as exc:
            # Storage failures must reach the scheduler's fatal-error isolation. A bad task's
            # delivery gets one recorded try so it cannot starve the next bounded batch.
            code = getattr(exc, 'sqlite_errorcode', 0) & 0xff
            if not c.in_transaction or code in (sqlite3.SQLITE_FULL, sqlite3.SQLITE_IOERR,
                                                sqlite3.SQLITE_CORRUPT, sqlite3.SQLITE_NOTADB,
                                                sqlite3.SQLITE_READONLY):
                raise
            c.execute('ROLLBACK TO assignment_rewake')
            logging.getLogger(__name__).exception('Assignment recovery delivery failed')
            message, refusal = None, 'recovery_error'
        finally:
            if c.in_transaction:
                c.execute('RELEASE assignment_rewake')
        H.event(c, H.KEEPER, EVENT, row['id'], {'assignment': key, 'assigned_at': since,
                                            'rewoken_at': at, 'refusal': refusal or (None if message else 'no_queued_wake')})
        if message:
            woke.append(row['id'])
    return woke


def overdue(c, at=None, *, visible="1", manager_actor=None, limit=200):
    """Page past ineligible ledger rows; cap alert output, not the pre-filter candidate window."""
    at = at or H.now()
    after, count, attempts = "", 0, {}
    while count < limit:
        scanned = [""]
        for row, _, since, saved in candidates(c, at, recorded=True, visible=visible,
                                               manager_actor=manager_actor, after=after,
                                               scanned=scanned, attempts=attempts):
            yield {**row, 'assigned_at': since, 'rewoken_at': saved['rewoken_at'], 'rewake_refusal': saved.get('refusal')}
            count += 1
            if count >= limit:
                return
        if not scanned[0]:
            return
        after = scanned[0]


def manager(c, row):
    config = c.execute('SELECT reports_to FROM bot_config WHERE bot=?', (H.actor_id(row['owner']),)).fetchone()
    target = config['reports_to'] if config else ''
    return target if H.actor_kind(target) else H.bot_actor(target) if target else None


def reason(c, row, settings):
    """The queued job's own fences, even beside another claimable lane; never another task's refusal."""
    from . import execution as E
    from .shared_bots import follow
    from .store import readiness_document
    bot = H.actor_id(row['owner'])
    owner = H.bot(c, bot)
    if owner and owner['state'] != 'active':
        return owner['state']
    config = c.execute(f"SELECT bc.config_json,{E.DRAINING_SQL.replace('a.bot', 'bc.bot')} draining "
                       "FROM bot_config bc WHERE bc.bot=?", (bot,)).fetchone()
    runner = c.execute('SELECT r.* FROM assignments a JOIN runners r ON r.id=a.runner_id '
                       'WHERE a.bot=? AND r.revoked_at IS NULL', (bot,)).fetchone()
    if not runner:
        return 'no_computer'
    if not config:
        return 'config'
    if not runner['last_seen'] or runner['last_seen'] < H.shift(H.now(), minutes=-10):
        return 'computer_offline'
    ready = readiness_document(runner['readiness_json'])
    held_bots, held_lanes = E.claimed_busy(runner['id'])
    why, declared = E.claim_refusal(c, settings, runner, bot, config['config_json'], config['draining'], ready, held_bots)
    if why:
        return why
    followed = follow(c, bot, declared)
    busy = set(E.live_lanes(c)) | held_lanes
    jobs = c.execute(f"SELECT j.*,{E.unfenced_sql()} free,{E.scoped_sql()} in_scope "
                     "FROM jobs j JOIN messages m ON m.id=j.message_id JOIN conversations cv ON cv.id=m.conversation_id "
                     f"WHERE j.bot=? AND j.state='queued' AND {H.MESSAGE_TASK_SQL}=? ORDER BY j.created,j.id",
                     (bot, row['id'])).fetchall()
    if jobs:
        job = jobs[0]
        return ('scoped' if not job['in_scope'] else 'uncertain' if not job['free'] else
                E.job_refusal(c, job, followed, ready, busy, E.lanes_on(runner), E.roots_on(runner)) or
                ('cooling_off' if E.login_refusal(c, settings, runner['id'], bot, followed, job['id']) else 'not_started'))
    return auto_tasks.refusal(c, row, followed, woken=True) or row.get('rewake_refusal') or 'no_queued_wake'


def description(c, row, settings, why=None):
    why = why or reason(c, row, settings)
    return f"Assigned, not started: {row['title']}. Refusal reason: {why}."

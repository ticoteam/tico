"""One recovery wake for a lost assignment; overdue starts remain visible until attempted.

The audit ledger is keyed by the assignment event, not by mutable task.updated. It survives
restarts and does not turn a Lead's profile into an idle task-pulling profile.
"""
import logging
import os
import sqlite3

from . import auto_tasks, hubdb as H, task_privacy as privacy

EVENT = 'task.assignment-rewake'
WAKE_HOURS = 1
ESCALATE_HOURS = 2
SCAN_LIMIT = 200
CURSOR_KEY = "assignment-recovery-cursor"
# (database file, task id, assignment key) -> True once the assignment's bot has run the task since it was assigned.
# Attempts are never deleted and a new assignment has a new key, so the answer never turns false. Keyed by the
# database file, as health's token cache is, so separate stores (and a restored file) never share entries.
ATTEMPTED = {}
ATTEMPTED_LIMIT = 20000


def page_ledger(c, rows):
    """Each row's current assignment (its ledger key and time) and the ledger entries of its task, read for the whole
    page in two statements. Per row these were two lookups each, which `candidates` now reads from here."""
    owners, ledger = {}, {}
    for part in H.chunks(row['id'] for row in rows):
        marks = ','.join('?' * len(part))
        for task_id, event_id, ts in c.execute(
                f"SELECT task_id,id,ts FROM task_events WHERE field='owner' AND task_id IN ({marks}) "
                "ORDER BY task_id,ts DESC,rowid DESC", part):
            owners.setdefault(task_id, (event_id, ts))
        for target, detail in c.execute(
                f"SELECT target,detail FROM events WHERE action=? AND target IN ({marks}) ORDER BY target,ts DESC",
                (EVENT, *part)):
            ledger.setdefault(target, []).append(H._json(detail, {}))
    return owners, ledger


def _memo_scope(c):
    """The database file this connection reads, or None (no memo) for an unnamed or missing file."""
    path = next((r[2] for r in c.execute("PRAGMA database_list") if r[1] == "main"), "")
    try:
        stat = os.stat(path) if path else None
    except OSError:
        return None
    return (path, stat.st_dev, stat.st_ino) if stat else None


def candidates(c, at=None, task_ids=None, recorded=None, *, after="", scanned=None, visible="1", manager_actor=None,
               attempts=None, on_error=None, page=None):
    if task_ids is not None and not task_ids:
        return
    at = at or H.now()
    cutoff = H.shift(at, hours=-WAKE_HOURS)
    params = [cutoff]
    scope = f" AND t.id IN ({','.join('?' for _ in task_ids)})" if task_ids else ''
    params.extend(task_ids or ())
    # Select the current assignment's ledger entry before reading task bodies or provenance.
    assignment_key = "coalesce((SELECT id FROM task_events WHERE task_id=t.id AND field='owner' ORDER BY ts DESC,rowid DESC LIMIT 1),t.owner||':'||t.created_at)"
    ledger = ("SELECT 1 FROM events e WHERE e.target=t.id AND e.action='task.assignment-rewake' "
              f"AND json_extract(e.detail,'$.assignment')={assignment_key}")
    if recorded is not None:
        scope += " AND " + ("EXISTS" if recorded else "NOT EXISTS") + "(" + ledger
        if recorded:
            scope += " AND json_extract(e.detail,'$.rewoken_at')<=?"
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
    # Checked per candidate task (a key lookup), so the reader's rule keeps its own column names. As
    # `t.id IN (SELECT id FROM tasks WHERE ...)` SQLite listed every task the rule allows first: 55 ms a call on TIDY.
    visibility = f" AND EXISTS(SELECT 1 FROM tasks v WHERE v.id=t.id AND ({visible}))" if visible != "1" else ""
    rows = H._rows(c.execute("SELECT t.* FROM tasks t JOIN bots b ON t.owner='bot:'||b.slug "
                                 "WHERE t.status='open' AND b.state IN ('active','paused','quarantined') AND t.created_at<=? "
                                 + scope + visibility + " ORDER BY t.id LIMIT ?",
                                 (*params, SCAN_LIMIT)))
    if page is not None:
        page[:] = [len(rows)]
    owners, ledger = page_ledger(c, rows)
    # Only a committed view may be remembered: a write transaction can still roll back what it reads.
    writing = getattr(c, 'write_transaction', False) or c.total_changes != getattr(c, 'provenance_changes', c.total_changes)
    memo_scope = _memo_scope(c) if rows else None
    # Tasks the page's wait checks can't rule out; the rest of the page skips waiting() (auto_tasks.maybe_waiting).
    suspect = auto_tasks.maybe_waiting(c, rows)
    for row in rows:
        if scanned is not None:
            scanned[:] = [row['id']]
        try:
            owned = owners.get(row['id'])
            key, since = (owned[0], owned[1]) if owned else (row['owner'] + ':' + row['created_at'], row['created_at'])
            if since > cutoff:
                continue
            saved = next((entry for entry in ledger.get(row['id'], ()) if entry.get('assignment') == key), None)
            # Most reads need only one side of the ledger. Skip the other before wait/provenance checks.
            if recorded is False and saved:
                continue
            if recorded is True and (not saved or saved['rewoken_at'] > H.shift(at, hours=-ESCALATE_HOURS)):
                continue
            if row['id'] in suspect and auto_tasks.waiting(c, row, starting=True):
                continue
            # Include folded inputs and next-run carried work, not just the job's original task.
            memo = (memo_scope, row['id'], key) if memo_scope else None
            if memo in ATTEMPTED:
                continue
            bot = H.actor_id(row['owner'])
            floor, touched = attempts.get(bot, ("9999", {}))
            # Only runs since this assignment can count, so the bot's attempts are read from `since`, not the page's oldest row.
            if since < floor:
                found = c.execute("SELECT id,created_at FROM attempts WHERE bot=? AND created_at>=? AND created_at<? ORDER BY created_at DESC",
                                  (bot, since, floor)).fetchall()
                # Every run's task set in a fixed number of statements; attempt_tasks then reads each from that snapshot.
                privacy.prime_attempts(c, [attempt['id'] for attempt in found])
                for attempt in found:
                    for tid in privacy.attempt_tasks(c, attempt['id']):
                        touched.setdefault(tid, attempt['created_at'])
                attempts[bot] = since, touched
            if touched.get(row['id'], '') >= since:
                if memo is not None and not writing:
                    if len(ATTEMPTED) >= ATTEMPTED_LIMIT:
                        ATTEMPTED.clear()
                    ATTEMPTED[memo] = True
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
        scanned, page = [""], []
        for row, _, since, saved in candidates(c, at, recorded=True, visible=visible,
                                               manager_actor=manager_actor, after=after,
                                               scanned=scanned, attempts=attempts, page=page):
            yield {**row, 'assigned_at': since, 'rewoken_at': saved['rewoken_at'], 'rewake_refusal': saved.get('refusal')}
            count += 1
            if count >= limit:
                return
        # A short page is the last: the page after it, from its last id, would be empty and is not run.
        if not scanned[0] or page[0] < SCAN_LIMIT:
            return
        after = scanned[0]


def manager(c, row):
    config = c.execute('SELECT reports_to FROM bot_config WHERE bot=?', (H.actor_id(row['owner']),)).fetchone()
    target = config['reports_to'] if config else ''
    return target if H.actor_kind(target) else H.bot_actor(target) if target else None


def _bot_state(c, bot, settings):
    """What every task of this bot shares: why none of its tasks can start (or None), and what a task's own check needs.
    Read once per bot per `shared` map in `reason`, not once per overdue task of the bot."""
    from . import execution as E
    from .shared_bots import follow
    from .store import readiness_document
    owner = H.bot(c, bot)
    if owner and owner['state'] != 'active':
        return owner['state'], None
    config = c.execute(f"SELECT bc.config,{E.DRAINING_SQL.replace('a.bot', 'bc.bot')} draining "
                       "FROM bot_config bc WHERE bc.bot=?", (bot,)).fetchone()
    runner = c.execute('SELECT r.* FROM assignments a JOIN runners r ON r.id=a.runner_id '
                       'WHERE a.bot=? AND r.revoked_at IS NULL', (bot,)).fetchone()
    if not runner:
        return 'no_computer', None
    if not config:
        return 'config', None
    if not runner['last_seen_at'] or runner['last_seen_at'] < H.shift(H.now(), minutes=-10):
        return 'computer_offline', None
    ready = readiness_document(runner['readiness'])
    held_bots, held_lanes = E.claimed_busy(runner['id'])
    why, declared = E.claim_refusal(c, settings, runner, bot, config['config'], config['draining'], ready, held_bots)
    if why:
        return why, None
    return None, {'runner': runner, 'ready': ready, 'followed': follow(c, bot, declared),
                  'busy': set(E.live_lanes(c)) | held_lanes}


def reason(c, row, settings, shared=None, suspect=None):
    """The queued job's own fences, even beside another claimable lane; never another task's refusal.
    `shared`: a dict the caller keeps across its tasks, so each bot's shared checks are read once for all of them.
    `suspect`: auto_tasks.maybe_waiting over those tasks."""
    from . import execution as E
    bot = H.actor_id(row['owner'])
    if shared is None:
        why, state = _bot_state(c, bot, settings)
    else:
        if bot not in shared:
            shared[bot] = _bot_state(c, bot, settings)
        why, state = shared[bot]
    if why:
        return why
    runner, ready, followed, busy = state['runner'], state['ready'], state['followed'], state['busy']
    jobs = c.execute(f"SELECT j.*,{E.unfenced_sql()} free,{E.scoped_sql()} in_scope "
                     "FROM jobs j JOIN messages m ON m.id=j.message_id JOIN conversations cv ON cv.id=m.conversation_id "
                     f"WHERE j.bot=? AND j.state='queued' AND {H.MESSAGE_TASK_SQL}=? ORDER BY j.created_at,j.id",
                     (bot, row['id'])).fetchall()
    if jobs:
        job = jobs[0]
        return ('scoped' if not job['in_scope'] else 'uncertain' if not job['free'] else
                E.job_refusal(c, job, followed, ready, busy, E.lanes_on(runner), E.roots_on(runner), suspect=suspect,
                              runner=runner) or
                ('cooling_off' if E.login_refusal(c, settings, runner['id'], bot, followed, job['id']) else 'not_started'))
    return (auto_tasks.refusal(c, row, followed, woken=True, suspect=suspect) or row.get('rewake_refusal')
            or 'no_queued_wake')


def description(c, row, settings, why=None):
    why = why or reason(c, row, settings)
    return f"Assigned, not started: {row['title']}. Refusal reason: {why}."

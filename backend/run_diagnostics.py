"""Bounded attempt diagnostics. Metadata follows run permissions; stderr follows raw export permissions."""
import json
import re

from .rows import AttemptRow
from .store import H

STALL_MINUTES = 5
# The server's own cap (cap below): a silent run ends this long past its bot's max_run_minutes, on the server's clock.
# The runner enforces the limit itself, but a frozen computer cannot, so the server must not wait for it.
MAX_RUN_MINUTES, MAX_RUN_GRACE_MINUTES = 60, 5
CAP_EVENT = 'attempt.max-run-cap'
TAIL_BYTES = 64 * 1024
TAIL_LINES = 200
# Defense in depth after the runner's literal/encoded granted-secret redactor.
SECRET = re.compile(r"(?i)(?:bearer|basic)\s+\S+|\b(?:sk|pk|xai|gsk|ghp|github_pat)[-_][\w-]{6,}|\b[A-Za-z0-9_+=-]{32,}\b|(?i:password|token|api[_ -]?key|secret)\s*[:=]\s*\S+")


def scrub(text):
    return SECRET.sub('[redacted]', str(text or ''))


def tail(text):
    value = '\n'.join(scrub(text).splitlines()[-TAIL_LINES:])
    return value.encode()[-TAIL_BYTES:].decode('utf-8', errors='ignore')


def ensure(c, aid):
    c.execute('INSERT OR IGNORE INTO attempt_diagnostics(attempt_id) SELECT id FROM attempts WHERE id=?', (aid,))


def touch(c, aid, kind=None, payload=None):
    ensure(c, aid)
    if kind:
        payload = payload or {}
        # No arguments, message text or tool output in list metadata.
        label = scrub(payload.get('tool') or kind)[:120]
        c.execute('UPDATE attempt_diagnostics SET last_event_at=?,last_event=?,stalled_at=NULL WHERE attempt_id=?',
                  (H.now(), label, aid))
    else:
        c.execute('UPDATE attempt_diagnostics SET heartbeat_at=?,stalled_at=NULL WHERE attempt_id=?', (H.now(), aid))


def keep_stderr(c, row: AttemptRow, text):
    ensure(c, row['id'])
    if (row['finished'] and row['finished'] < H.shift(H.now(), days=-7) or
            c.execute('SELECT 1 FROM attempt_capture_drops WHERE attempt_id=?', (row['id'],)).fetchone()):
        return {'kept': False}
    value = tail(text)
    c.execute('UPDATE attempt_diagnostics SET stderr_tail=?,stderr_at=? WHERE attempt_id=?',
              (value, H.now(), row['id']))
    if row['state'] in ('leased', 'running'):
        touch(c, row['id'], 'stderr')
    return {'kept': True, 'bytes': len(value.encode())}


def reason(c, row: AttemptRow, outcome, summary=''):
    if row['stop_requested'] and outcome != 'completed':
        from .quarantine_stops import stopped_by
        # A quarantine's Stop reads as the quarantine, never as a lost computer or the keeper (Dev 573).
        return stopped_by(c, row) or 'Cancelled by ' + row['stop_requested']
    if outcome in ('completed', 'ok'):
        return 'Finished'
    if row['state'] == 'expired':
        capped = c.execute("SELECT detail_json FROM events WHERE target=? AND action=? LIMIT 1",
                           (row['id'], CAP_EVENT)).fetchone()
        if capped:
            return scrub(json.loads(capped[0]).get('reason') or 'Ended by the server')[:500]
        return 'Computer lost or heartbeat stopped (attempt lease expired)'
    result = json.loads(row['result_json'] or '{}')
    if result.get('text') == 'Runner restarted during execution':
        return 'Runner restarted during execution'
    # The newest terminal cause wins over a primary model that failed before a fallback succeeded.
    events = c.execute("SELECT kind,payload_json FROM attempt_events WHERE attempt_id=? "
                        "AND kind IN ('error','diagnostic') ORDER BY seq DESC LIMIT 50", (row['id'],)).fetchall()
    for ev in events:
        p = json.loads(ev['payload_json'])
        if p.get('end_reason'):
            return scrub(p['end_reason'])[:500]
    for ev in events:
        p = json.loads(ev['payload_json'])
        if p.get('runner_error'):
            continue
        if p.get('process_exit') and isinstance(p.get('exit_code'), int):
            code = p['exit_code']
            if p.get('oom_killed') is True:
                return 'OOM-killed (reported by the runner)'
            return f'Process exited (signal {-code})' if code < 0 else f'Process exited (exit code {code})'
        if ev['kind'] == 'error' and p.get('error'):
            err = p['error']
            if isinstance(err, dict):
                err = json.dumps(err, ensure_ascii=False)
            code = p.get('code') or ''
            return scrub('Model error' + (' (' + str(code) + ')' if code else '') + ': ' + str(err))[:500]
    for ev in events:
        p = json.loads(ev['payload_json'])
        if p.get('runner_error'):
            return scrub(str(p.get('error_type') or 'Runner error') + ': ' + str(p['runner_error']))[:500]
    return scrub(result.get('error') or result.get('reason') or
                 ('Process interrupted (no exit status reported)' if outcome == 'interrupted' else
                  'Process failed (no exit status reported)'))[:500]


def finish(c, aid, outcome, summary=''):
    row = c.execute('SELECT * FROM attempts WHERE id=?', (aid,)).fetchone()
    if not row:
        return
    ensure(c, aid)
    c.execute('UPDATE attempt_diagnostics SET end_reason=?,ended_at=?,stalled_at=NULL WHERE attempt_id=?',
              (reason(c, row, outcome, summary), row['finished'] or H.now(), aid))


def restored(c, aid):
    ensure(c, aid)
    c.execute('UPDATE attempt_diagnostics SET end_reason=NULL,ended_at=NULL,stalled_at=NULL,heartbeat_at=? '
              'WHERE attempt_id=?', (H.now(), aid))


def metadata(c, aid):
    from .read_cache import one
    row = one(c, 'SELECT end_reason,ended_at,last_event,last_event_at,heartbeat_at,stalled_at '
              'FROM attempt_diagnostics WHERE attempt_id=?', (aid,))
    return {'end_reason': row['end_reason'] if row else None, 'ended_at': row['ended_at'] if row else None,
            'last_event': row['last_event'] if row else None, 'last_event_at': row['last_event_at'] if row else None,
            'last_heartbeat_at': row['heartbeat_at'] if row else None,
            'stalled': bool(row and row['stalled_at']), 'stalled_at': row['stalled_at'] if row else None,
            'stall_minutes': STALL_MINUTES}


def sweep(c, at=None):
    at = at or H.now()
    cutoff = H.shift(at, seconds=-STALL_MINUTES * 60)
    # Include leased attempts, which might never have reached started. Never change their lifecycle or lease.
    c.execute("INSERT OR IGNORE INTO attempt_diagnostics(attempt_id) SELECT id FROM attempts "
              "WHERE state IN ('leased','running')")
    c.execute("UPDATE attempt_diagnostics SET stalled_at=coalesce(stalled_at,?) WHERE attempt_id IN "
              "(SELECT a.id FROM attempts a JOIN attempt_diagnostics d ON d.attempt_id=a.id "
              "WHERE a.state IN ('leased','running') AND max(coalesce(d.heartbeat_at,a.created),"
              "coalesce(d.last_event_at,a.created))<=?)", (at, cutoff))
    cap(c, at)
    c.execute('UPDATE attempt_diagnostics SET stderr_tail=NULL,stderr_at=NULL WHERE stderr_at<?',
              (H.shift(at, days=-7),))


def max_run_minutes(c, bot):
    """The bot's own limit, read as the runner reads it (runner-rs execute.rs): a number or a numeric text."""
    row = c.execute('SELECT config_json FROM bot_config WHERE bot=?', (bot,)).fetchone()
    try:
        return float(H._json(row[0], {}).get('max_run_minutes') or MAX_RUN_MINUTES) if row else MAX_RUN_MINUTES
    except (TypeError, ValueError):
        return MAX_RUN_MINUTES


def cap(c, at):
    """End a run the computer has gone silent on, once it is past its limit plus the grace.

    Silent means no heartbeat or event for STALL_MINUTES; past means started more than max_run_minutes plus
    MAX_RUN_GRACE_MINUTES ago. The lease lapses now and execution.expire ends the run on its existing path. That path
    never extends a capped run (execution.expire reads CAP_EVENT), and the reason names the cap. A run whose computer
    still heartbeats is left to the runner's own limit."""
    cutoff = H.shift(at, seconds=-STALL_MINUTES * 60)
    now = H.now()
    for row in c.execute("SELECT a.id,a.bot,a.started,max(a.started,coalesce(d.heartbeat_at,''),"
                         "coalesce(d.last_event_at,'')) AS seen FROM attempts a "
                         "LEFT JOIN attempt_diagnostics d ON d.attempt_id=a.id "
                         "WHERE a.state IN ('leased','running') AND a.started IS NOT NULL "
                         "AND NOT EXISTS(SELECT 1 FROM events e WHERE e.target=a.id AND e.action=?)",
                         (CAP_EVENT,)).fetchall():
        minutes = max_run_minutes(c, row['bot'])
        if H.shift(row['started'], seconds=(minutes + MAX_RUN_GRACE_MINUTES) * 60) > at or row['seen'] > cutoff:
            continue
        reason = (f'Ended by the server: no heartbeat for {STALL_MINUTES} minutes, more than '
                  f'{MAX_RUN_GRACE_MINUTES} minutes past max_run_minutes ({minutes:g} minutes)')
        H.event(c, H.KEEPER, CAP_EVENT, row['id'], {'bot': row['bot'], 'max_run_minutes': minutes,
                                                   'silent_since': row['seen'], 'reason': reason})
        c.execute('UPDATE attempts SET lease_until=? WHERE id=?', (now, row['id']))


def stalled_runs(c, who, auth):
    from .stream_capture import require_run
    from .store import Problem
    rows = []
    for r in c.execute("SELECT a.id,a.bot FROM attempts a JOIN attempt_diagnostics d ON d.attempt_id=a.id "
                       "WHERE a.state IN ('leased','running') AND d.stalled_at IS NOT NULL ORDER BY d.stalled_at LIMIT 200"):
        try:
            require_run(c, who, auth, r['id'])
        except Problem:
            continue
        rows.append({'run': r['id'], 'bot': r['bot'], **metadata(c, r['id'])})
    return rows

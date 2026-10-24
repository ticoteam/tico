"""Short computer reservations, separate from a worker's renewable delivery lease."""

from .store import H

START_SECONDS = 180
SCHEMA = """
CREATE TABLE IF NOT EXISTS start_places(
 attempt_id TEXT PRIMARY KEY REFERENCES attempts(id) ON DELETE CASCADE,
 start_until TEXT, wait_reason TEXT NOT NULL DEFAULT '', waiting_since TEXT
);
"""


def restore_waits(c):
    """An upgrade preserves waits reported by older runners, including harnesses now opting out of login queues."""
    for row in c.execute("SELECT a.id,a.created,(SELECT e.payload_json FROM attempt_events e WHERE e.attempt_id=a.id "
                         "AND e.kind='status' AND json_extract(e.payload_json,'$.waiting') IS NOT NULL "
                         "ORDER BY e.seq DESC LIMIT 1) payload FROM attempts a WHERE a.state='leased' "
                         "AND NOT EXISTS(SELECT 1 FROM start_places p WHERE p.attempt_id=a.id)").fetchall():
        payload = H._json(row['payload'], {})
        reason = ('shared login' if payload.get('login') else 'pre-start requirements') if isinstance(payload, dict) and payload.get('waiting') else ''
        c.execute("INSERT INTO start_places(attempt_id,start_until,wait_reason,waiting_since) VALUES(?,?,?,?)",
                  (row['id'], None if reason else H.shift(row['created'], seconds=START_SECONDS),
                   reason, row['created'] if reason else None))


def ready_waiting(c, runner_id):
    return bool(c.execute("SELECT 1 FROM attempts a JOIN start_places p ON p.attempt_id=a.id "
                          "WHERE a.runner_id=? AND a.state='leased' AND a.lease_until>? AND a.stop_requested IS NULL "
                          "AND p.wait_reason='computer busy' LIMIT 1", (runner_id, H.now())).fetchone())


def held(c, runner_id):
    """Running work and unexpired starts; an idle worker owns no computer capacity."""
    now = H.now()
    return {r[0]: r[1] for r in c.execute(
        "SELECT a.bot,count(*) FROM attempts a LEFT JOIN start_places p ON p.attempt_id=a.id "
        "WHERE a.runner_id=? AND (a.state='running' OR (a.state='leased' AND a.lease_until>? "
        "AND (p.start_until>? OR (p.attempt_id IS NULL AND a.created>?)))) GROUP BY a.bot",
        (runner_id, now, now, H.shift(now, seconds=-START_SECONDS)))}


def claim(c, aid):
    c.execute("INSERT INTO start_places(attempt_id,start_until) VALUES(?,?)",
              (aid, H.shift(H.now(), seconds=START_SECONDS)))


def waiting(c, aid, reason):
    """Only a pre-start wait releases a place; a model already executing keeps its place."""
    if not c.execute("SELECT 1 FROM attempts a LEFT JOIN start_places p ON p.attempt_id=a.id "
                     "WHERE a.id=? AND a.state='leased' AND (p.start_until IS NULL OR p.start_until>?)", (aid, H.now())).fetchone():
        return
    c.execute("INSERT INTO start_places(attempt_id,wait_reason,waiting_since) VALUES(?,?,?) "
              "ON CONFLICT(attempt_id) DO UPDATE SET start_until=NULL,wait_reason=excluded.wait_reason,"
              "waiting_since=CASE WHEN start_places.wait_reason=excluded.wait_reason THEN "
              "coalesce(start_places.waiting_since,excluded.waiting_since) ELSE excluded.waiting_since END", (aid, reason, H.now()))


def overdue(c):
    return [r[0] for r in c.execute(
        "SELECT a.id FROM attempts a LEFT JOIN start_places p ON p.attempt_id=a.id WHERE a.state='leased' "
        "AND (p.start_until<=? OR (p.attempt_id IS NULL AND a.created<=?))",
        (H.now(), H.shift(H.now(), seconds=-START_SECONDS)))]


def reserve(c, attempt):
    """Called inside the API writer transaction; two contenders cannot win the last place."""
    from .performance import load_status
    aid, rid = attempt['id'], attempt['runner_id']
    if attempt['stop_requested']:
        return {"reserved": False, "stop": True}
    if load_status(c, rid)['overloaded']:
        waiting(c, aid, 'computer busy')
        return {"reserved": False, "waiting": "computer busy"}
    place = c.execute("SELECT * FROM start_places WHERE attempt_id=?", (aid,)).fetchone()
    if place and place['start_until']:
        return {"reserved": place['start_until'] > H.now(), "start_until": place['start_until']}
    capacity = c.execute("SELECT capacity FROM runners WHERE id=?", (rid,)).fetchone()[0] or 1
    # Waiters ready to run take their turns by original claim order. Login/lane waiters are not ready yet.
    ahead = c.execute("SELECT 1 FROM start_places p JOIN attempts a ON a.id=p.attempt_id "
                      "WHERE a.runner_id=? AND a.state='leased' AND a.lease_until>? AND a.stop_requested IS NULL "
                      "AND p.wait_reason='computer busy' AND (a.created,a.id)<(?,?) LIMIT 1",
                      (rid, H.now(), attempt['created'], aid)).fetchone()
    if sum(held(c, rid).values()) >= capacity or ahead:
        waiting(c, aid, 'computer busy')
        return {"reserved": False, "waiting": "computer busy"}
    until = H.shift(H.now(), seconds=START_SECONDS)
    c.execute("INSERT INTO start_places(attempt_id,start_until) VALUES(?,?) ON CONFLICT(attempt_id) "
              "DO UPDATE SET start_until=excluded.start_until,wait_reason='',waiting_since=NULL", (aid, until))
    return {"reserved": True, "start_until": until}


def summary(c, runner_id, settings):
    """Four numeric counts and bounded, neutral reasons; no task, bot or login identity leaks."""
    from . import performance
    out = dict(running=0, starting=0, waiting=0, queued=0, start_seconds=START_SECONDS)
    groups = {}

    def add(reason, count, since):
        out['waiting'] += count
        old = groups.setdefault(reason, {'reason': reason, 'count': 0, 'oldest': since})
        old['count'] += count
        old['oldest'] = min(old['oldest'], since)

    now = H.now()
    for row in c.execute("SELECT a.state,a.created,a.lease_until,p.start_until,p.wait_reason,p.waiting_since "
                         "FROM attempts a LEFT JOIN start_places p ON p.attempt_id=a.id "
                         "WHERE a.runner_id=? AND a.state IN ('leased','running')", (runner_id,)):
        if row['state'] == 'running':
            out['running'] += 1
        elif row['wait_reason']:
            add(row['wait_reason'], 1, row['waiting_since'] or row['created'])
        elif row['lease_until'] > now and (row['start_until'] or H.shift(row['created'], seconds=START_SECONDS)) > now:
            out['starting'] += 1
        else:
            out['queued'] += 1
    # Quota and readiness waits need not be leased to a worker to be visible.
    from .execution import claim_refusal
    from .store import readiness_document
    runner = c.execute("SELECT * FROM runners WHERE id=?", (runner_id,)).fetchone()
    ready = readiness_document(runner['readiness_json']) if runner else {}
    overloaded = performance.load_status(c, runner_id)['overloaded']
    reasons = {'cooling_off': 'quota', 'limit': 'spending limit', 'not_ready': 'computer readiness',
               'readiness_unknown': 'computer readiness', 'sign_in_rejected': 'sign-in',
               'draining': 'bot paused'}
    for row in c.execute("SELECT j.bot,count(*) n,min(j.created) oldest,bc.config_json,b.state bot_state "
                         "FROM jobs j JOIN assignments a ON a.bot=j.bot JOIN bots b ON b.slug=j.bot "
                         "JOIN bot_config bc ON bc.bot=j.bot WHERE a.runner_id=? AND j.state='queued' GROUP BY j.bot",
                         (runner_id,)):
        why = 'bot paused' if row['bot_state'] != 'active' else ''
        if not why and runner:
            refused, _ = claim_refusal(c, settings, runner, row['bot'], row['config_json'], False, ready)
            why = reasons.get(refused, 'pre-start requirements' if refused else '')
        if not why and overloaded:
            why = 'computer busy'
        if why:
            add(why, row['n'], row['oldest'])
        else:
            out['queued'] += row['n']
    out['waits'] = sorted(groups.values(), key=lambda g: (g['oldest'], g['reason']))
    return out


def describe(counts):
    parts = [f"{counts['running']} running", f"{counts['starting']} starting"]
    now = H.parse_ts(H.now())
    for wait in counts['waits']:
        minutes = max(0, int((now - H.parse_ts(wait['oldest'])).total_seconds() / 60))
        age = f"{minutes // 60} h" if minutes >= 60 else f"{minutes} min"
        reason = "waiting: computer busy" if wait['reason'] == 'computer busy' else "waiting for " + wait['reason']
        parts.append(f"{wait['count']} {reason} (oldest {age})")
    if not counts['waiting']:
        parts.append('0 waiting')
    parts.append(f"{counts['queued']} queued")
    return ' · '.join(parts)

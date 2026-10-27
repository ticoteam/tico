"""Short computer reservations, separate from a worker's renewable delivery lease."""

from types import MappingProxyType

from .store import H

START_SECONDS = 180
SCHEMA = """
CREATE TABLE IF NOT EXISTS start_places(
 attempt_id TEXT PRIMARY KEY REFERENCES attempts(id) ON DELETE CASCADE,
 start_until TEXT, managed INTEGER NOT NULL DEFAULT 0, wait_reason TEXT NOT NULL DEFAULT '', waiting_since TEXT
);
"""


def restore_waits(c):
    """An upgrade preserves the same legacy waits the live event reader recognises."""
    from .login_waits import wait
    H.add_column(c, "start_places", "managed", "INTEGER NOT NULL DEFAULT 0")
    for row in c.execute("SELECT a.id,a.created FROM attempts a WHERE a.state='leased' "
                         "AND NOT EXISTS(SELECT 1 FROM start_places p WHERE p.attempt_id=a.id)").fetchall():
        payload = wait(c, row['id'])
        reason = ('shared login' if payload.get('login') else 'pre-start requirements') if payload else ''
        c.execute("INSERT INTO start_places(attempt_id,start_until,wait_reason,waiting_since) VALUES(?,?,?,?)",
                  (row['id'], None, reason, row['created'] if reason else None))


def ready_waiting(c, runner_id):
    """Whether this computer already holds a start waiting on capacity/stop. Cached for the read snapshot:
    claim waiter order and held_starts ask once per computer."""
    from . import read_cache
    from .org_turns import REASON
    values = read_cache.cache(c)
    key = ("start_places.ready_waiting", runner_id)
    if values is not None and key in values:
        return values[key]
    out = bool(c.execute("SELECT 1 FROM attempts a JOIN start_places p ON p.attempt_id=a.id "
                         "WHERE a.runner_id=? AND a.state='leased' AND a.lease_until>? AND a.stop_requested IS NULL "
                         "AND p.wait_reason IN ('computer busy',?) LIMIT 1",
                         (runner_id, H.now(), REASON)).fetchone())
    if values is not None:
        values[key] = out
    return out


def held(c, runner_id):
    """Running work and unexpired starts; legacy workers retain their original lease capacity.

    Cached for the read snapshot (a read transaction or a claim's write transaction until its first write): claim
    waiter order and Health capacity fences ask once per computer. Read-only: callers share one map.
    """
    from . import read_cache
    values = read_cache.cache(c)
    key = ("start_places.held", runner_id)
    if values is not None and key in values:
        return values[key]
    now = H.now()
    out = MappingProxyType({r[0]: r[1] for r in c.execute(
        "SELECT a.bot,count(*) FROM attempts a LEFT JOIN start_places p ON p.attempt_id=a.id "
        "WHERE a.runner_id=? AND a.state IN ('leased','running') AND (a.state='running' OR (a.state='leased' AND a.lease_until>? "
        "AND (p.start_until>? OR p.attempt_id IS NULL OR p.managed=0))) GROUP BY a.bot",
        (runner_id, now, now))})
    if values is not None:
        values[key] = out
    return out


def held_totals(c):
    """{runner_id: turns held} for every computer in one pass. Health's capacity fence used to call
    `held` once per runner while already walking the fleet."""
    now = H.now()
    return {r[0]: r[1] for r in c.execute(
        "SELECT a.runner_id,count(*) FROM attempts a LEFT JOIN start_places p ON p.attempt_id=a.id "
        "WHERE a.state IN ('leased','running') AND (a.state='running' OR (a.state='leased' AND a.lease_until>? "
        "AND (p.start_until>? OR p.attempt_id IS NULL OR p.managed=0))) GROUP BY a.runner_id",
        (now, now))}


def claim(c, aid):
    c.execute("INSERT INTO start_places(attempt_id,start_until,managed) VALUES(?,?,1)",
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


def managed(c, aid):
    return bool(c.execute("SELECT 1 FROM start_places WHERE attempt_id=? AND managed=1", (aid,)).fetchone())


def is_waiting(c, aid):
    return bool(c.execute("SELECT 1 FROM start_places WHERE attempt_id=? AND wait_reason<>''", (aid,)).fetchone())


def overdue(c):
    # The redundant IN lets the read walk attempts_live_lease, not every attempt ever run.
    return [r[0] for r in c.execute(
        "SELECT a.id FROM attempts a JOIN start_places p ON p.attempt_id=a.id WHERE a.state='leased' "
        "AND a.state IN ('leased','running') AND p.start_until<=?", (H.now(),))]


def from_person(c, job_id):
    """Whether a job answers a person's own message to the bot: a chat, a message in its task, an answer to its ask.
    Such a turn starts on an overloaded computer and ahead of the turns waiting there: the load is the bots' own work,
    and holding it left a person's message waiting behind a hundred bot turns. A keeper notice (a routine, a sweep,
    a rule's wake), or a bot's message, is not one, even when a person's act caused it. Two primary-key reads."""
    return bool(c.execute("SELECT 1 FROM jobs j JOIN messages m ON m.id=j.message_id "
                          "WHERE j.id=? AND m.from_actor LIKE 'human:%'", (job_id,)).fetchone())


def person_queued(c, runner_id):
    """Whether a person's message waits in this computer's queue: only then does a held claim look for a job."""
    return bool(c.execute("SELECT 1 FROM assignments x JOIN jobs j ON j.bot=x.bot AND j.state='queued' AND j.held=0 "
                          "JOIN messages m ON m.id=j.message_id WHERE x.runner_id=? AND m.from_actor LIKE 'human:%' "
                          "LIMIT 1", (runner_id,)).fetchone())


def reserve(c, attempt, settings=None):
    """Called inside the API writer transaction; two contenders cannot win the last place."""
    from .performance import load_status
    aid, rid = attempt['id'], attempt['runner_id']
    if attempt['stop_requested']:
        return {"reserved": False, "stop": True}
    person = from_person(c, attempt['job_id'])
    if not person and load_status(c, rid)['overloaded']:
        waiting(c, aid, 'computer busy')
        return {"reserved": False, "waiting": "computer busy"}
    place = c.execute("SELECT * FROM start_places WHERE attempt_id=?", (aid,)).fetchone()
    if place and place['start_until']:
        return {"reserved": place['start_until'] > H.now(), "start_until": place['start_until']}
    from . import org_turns
    # Admission is claim's job: a turn already counted in the company's in-use total (waiting, or an unmanaged
    # live lease) takes its place without raising the count, so waiters cannot freeze a stopped company.
    counted = (place and place['wait_reason']) or (attempt['lease_until'] > H.now() and not (place and place['managed']))
    if settings and not counted and org_turns.observe(c, settings, detailed=False)['at_stop']:
        waiting(c, aid, org_turns.REASON)
        return {'reserved': False, 'waiting': org_turns.REASON}
    capacity = c.execute("SELECT capacity FROM runners WHERE id=?", (rid,)).fetchone()[0] or 1
    # Waiters ready to run take their turns by original claim order. Login/lane waiters are not ready yet.
    ahead = c.execute("SELECT 1 FROM start_places p JOIN attempts a ON a.id=p.attempt_id "
                      "WHERE a.runner_id=? AND a.state='leased' AND a.lease_until>? AND a.stop_requested IS NULL "
                      "AND p.wait_reason IN ('computer busy',?) AND (a.created,a.id)<(?,?) LIMIT 1",
                      (rid, H.now(), org_turns.REASON, attempt['created'], aid)).fetchone()
    if sum(held(c, rid).values()) >= capacity or (ahead and not person):
        waiting(c, aid, 'computer busy')
        return {"reserved": False, "waiting": "computer busy"}
    until = H.shift(H.now(), seconds=START_SECONDS)
    c.execute("INSERT INTO start_places(attempt_id,start_until,managed) VALUES(?,?,1) ON CONFLICT(attempt_id) "
              "DO UPDATE SET start_until=excluded.start_until,managed=1,wait_reason='',waiting_since=NULL", (aid, until))
    return {"reserved": True, "start_until": until}


def summary(c, runner_id, settings, default=None, *, classify=True):
    """Four numeric counts and bounded, neutral reasons; no task, bot or login identity leaks. Pass one company
    Default across every computer in a listing (Health, Operations, /computers): without it each summary rebuilds
    pools/groups/spend while classifying queued bots.

    `classify=False` skips only claim_refusal for each queued bot (quota, readiness, sign-in, spend): Health below
    its warning level, where its stuck and queue checks already name why a bot is not starting. Paused bots and a
    busy computer still read as waits."""
    from .read_cache import value
    return value(c, ("start_summary", runner_id, bool(classify)),
                 lambda: _summaries(c, (runner_id,), settings, default, classify)[runner_id])


def summaries(c, runner_ids, settings, default=None):
    """`summary` for many computers in two scans. Each computer's counts are the ones `summary` would build
    on its own, including the same claim refusal for each queued bot."""
    from .read_cache import value
    ids = tuple(sorted({rid for rid in runner_ids if rid}))
    return value(c, ("start_summaries", ids), lambda: _summaries(c, ids, settings, default))


def _summaries(c, runner_ids, settings, default=None, classify=True):
    from . import performance
    out = {rid: dict(running=0, starting=0, waiting=0, queued=0, start_seconds=START_SECONDS) for rid in runner_ids}
    groups = {rid: {} for rid in runner_ids}
    if not runner_ids:
        return out

    def add(rid, reason, count, since):
        out[rid]['waiting'] += count
        old = groups[rid].setdefault(reason, {'reason': reason, 'count': 0, 'oldest': since})
        old['count'] += count
        old['oldest'] = min(old['oldest'], since)

    now = H.now()
    marks = ",".join("?" * len(runner_ids))
    for row in c.execute("SELECT a.runner_id,a.state,a.created,a.lease_until,p.start_until,p.wait_reason,"
                         "p.waiting_since,p.managed FROM attempts a LEFT JOIN start_places p ON p.attempt_id=a.id "
                         f"WHERE a.runner_id IN ({marks}) AND a.state IN ('leased','running')", runner_ids):
        rid = row['runner_id']
        if row['state'] == 'running':
            out[rid]['running'] += 1
        elif row['wait_reason']:
            add(rid, row['wait_reason'], 1, row['waiting_since'] or row['created'])
        elif row['lease_until'] > now and (not row['managed'] or (row['start_until'] or '') > now):
            out[rid]['starting'] += 1
        else:
            out[rid]['queued'] += 1
    from .execution import claim_refusal
    from .store import readiness_document
    runners = {row['id']: row for row in c.execute(f"SELECT * FROM runners WHERE id IN ({marks})", runner_ids)}
    overloaded = {rid: performance.load_status(c, rid)['overloaded'] for rid in runner_ids}
    reasons = {'cooling_off': 'quota', 'limit': 'spending limit', 'not_ready': 'computer readiness',
               'readiness_unknown': 'computer readiness', 'sign_in_rejected': 'sign-in',
               'draining': 'bot paused', 'run_hold': 'stopped runs', 'backoff': 'a failed run', 'budget': 'run budget'}
    for row in c.execute("SELECT a.runner_id,j.bot,count(*) n,min(j.created) oldest,bc.config_json,b.state bot_state "
                         "FROM jobs j JOIN assignments a ON a.bot=j.bot JOIN bots b ON b.slug=j.bot "
                         "JOIN bot_config bc ON bc.bot=j.bot WHERE a.runner_id IN (" + marks + ") AND j.state='queued' "
                         "GROUP BY a.runner_id,j.bot", runner_ids):
        rid = row['runner_id']
        runner = runners.get(rid)
        ready = readiness_document(runner['readiness_json']) if runner else {}
        why = 'bot paused' if row['bot_state'] != 'active' else ''
        if not why and runner and classify:
            refused, _ = claim_refusal(c, settings, runner, row['bot'], row['config_json'], False, ready,
                                       default=default)
            why = reasons.get(refused, 'pre-start requirements' if refused else '')
        if not why and overloaded[rid]:
            why = 'computer busy'
        if why:
            add(rid, why, row['n'], row['oldest'])
        else:
            out[rid]['queued'] += row['n']
    for rid in out:
        out[rid]['waits'] = sorted(groups[rid].values(), key=lambda g: (g['oldest'], g['reason']))
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

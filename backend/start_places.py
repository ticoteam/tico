"""Short computer reservations, separate from a worker's renewable delivery lease."""

from types import MappingProxyType
from typing import Any

from .store import H
from . import bot_settings as BS

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
    for row in c.execute("SELECT a.id,a.created_at FROM attempts a WHERE a.state='leased' "
                         "AND NOT EXISTS(SELECT 1 FROM start_places p WHERE p.attempt_id=a.id)").fetchall():
        payload = wait(c, row['id'])
        reason = ('shared login' if payload.get('login') else 'pre-start requirements') if payload else ''
        c.execute("INSERT INTO start_places(attempt_id,start_until,wait_reason,waiting_since) VALUES(?,?,?,?)",
                  (row['id'], None, reason, row['created_at'] if reason else None))


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
                         "WHERE a.runner_id=? AND a.state='leased' AND a.lease_until>? AND a.stop_requested_at IS NULL "
                         "AND (p.wait_reason LIKE 'computer busy%' OR p.wait_reason=?) LIMIT 1",
                         (runner_id, H.now(), REASON)).fetchone())
    if values is not None:
        values[key] = out
    return out


MEMORY_HOLD = 'computer busy: memory'
NOT_REPORTING = 'computer not reporting'


def hold_rows(c, runner_ids):
    """The runner rows computer_hold reads, for many computers in one read: {runner id: row}."""
    ids = sorted({rid for rid in runner_ids if rid})
    out = {}
    for start in range(0, len(ids), 500):
        part = ids[start:start + 500]
        for row in c.execute(f"SELECT * FROM runners_with_state WHERE id IN ({','.join('?' * len(part))})", part):
            out[row['id']] = row
    return out


def computer_hold(c, runner_id, now=None, status=None, row=None):
    """Why this computer holds a bot's new start, as (wait reason, words for people), or None. Diagnostics on only.

    - Sustained CPU or memory overload (performance.load_status): "computer busy", as before.
    - The latest sample older than two minutes, or none in the retention window after one was ever kept:
      "computer not reporting". A frozen computer stops sampling, so a silent one admits no work. A computer that
      has never sampled is not held: older runners never sent samples.
    - Memory at 85% or more: "computer busy: memory N%". It is admitted again below 80%; between the two it keeps
      the state its newest sample set (performance.ingest), so the queue does not flap.
    A person's own message skips this at each caller, as it skips the overload hold. `row` is the computer's runner
    row from hold_rows when the caller has it: the same columns, read once for all its computers."""
    from .performance import load_status

    def runner():
        nonlocal row
        if row is None:
            row = c.execute("SELECT * FROM runners_with_state WHERE id=?", (runner_id,)).fetchone()
        return row

    def diagnostics_on():
        found = runner()
        return bool(found) and found['revoked_at'] is None and found['performance'] == 'on'
    if status is None:
        if not diagnostics_on():
            return None
        status = load_status(c, runner_id, now)
    latest = status['latest_sample']
    if latest is None:
        # Samples past retention are gone, but a computer that reported before still reports nothing now.
        if not diagnostics_on():
            return None
        return (NOT_REPORTING, NOT_REPORTING) if runner()['last_sampled_at'] else None
    if latest['stale']:
        return NOT_REPORTING, NOT_REPORTING
    if status['overloaded']:
        return 'computer busy', 'computer busy'
    mem = latest['mem_pct']
    if mem is None:
        return None
    held = runner()
    if memory_holds(mem, held and held['memory_hold']):
        return MEMORY_HOLD, f'{MEMORY_HOLD} {round(mem)}%'
    return None


def memory_holds(mem, was_held):
    """Whether a memory reading (percent) holds new starts: at MEM_HOLD_AT or more, or between the two bars while the
    computer is already held (`runners.memory_hold`, set by performance.ingest in reading order)."""
    from .performance import MEM_HOLD_AT, MEM_HOLD_CLEAR
    return mem >= MEM_HOLD_AT or (mem >= MEM_HOLD_CLEAR and bool(was_held))


def memory_held(c, runner_id, now=None):
    """Whether this computer's memory alone holds new starts, whatever else computer_hold reports. Sustained memory over
    90% also marks the computer overloaded, and computer_hold then names only the generic "computer busy": a caller that
    gates on memory asks here. Computers that are off, revoked, silent or never sampled are not memory-held."""
    from .performance import load_status
    found = c.execute("SELECT memory_hold,performance,revoked_at FROM runners_with_state WHERE id=?", (runner_id,)).fetchone()
    if not found or found['revoked_at'] is not None or found['performance'] != 'on':
        return False
    latest = load_status(c, runner_id, now)['latest_sample']
    if latest is None or latest['stale'] or latest['mem_pct'] is None:
        return False
    return memory_holds(latest['mem_pct'], found['memory_hold'])


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
        "SELECT a.bot_id,count(*) FROM attempts a LEFT JOIN start_places p ON p.attempt_id=a.id "
        "WHERE a.runner_id=? AND a.state IN ('leased','running') AND (a.state='running' OR (a.state='leased' AND a.lease_until>? "
        "AND (p.start_until>? OR p.attempt_id IS NULL OR p.managed=0))) GROUP BY a.bot_id",
        (runner_id, now, now))})
    if values is not None:
        values[key] = out
    return out


# The explicit state IN is what lets SQLite search attempts_runner_state (runner_id, state); the OR alone does not.
LIVE_SQL = ("SELECT a.bot_id,count(*) FROM attempts a WHERE a.runner_id=? AND a.state IN ('leased','running') "
            "AND (a.state='running' OR a.lease_until>?) GROUP BY a.bot_id")


def live(c, runner_id):
    """{bot: turns it holds on this computer}: running turns and unexpired leases, whether starting, parked on a wait
    (login, quota, lane, pre-start) or neither. The claim gate counts these against `claim_limit`, so a computer's claimed
    work stays bounded; work past that stays queued on the server, unleased.

    Cached for the read snapshot, like `held`.
    """
    from . import read_cache
    values = read_cache.cache(c)
    key = ("start_places.live", runner_id)
    if values is not None and key in values:
        return values[key]
    now = H.now()
    out = MappingProxyType({r[0]: r[1] for r in c.execute(LIVE_SQL, (runner_id, now))})
    if values is not None:
        values[key] = out
    return out


# Without the hint SQLite walked attempts_runner_finished (every attempt ever run) to skip the GROUP BY sort:
# 48 ms on Tico Team's 37k attempts, 0.1 ms over the live ones.
LIVE_TOTALS_SQL = (
    "SELECT a.runner_id,count(*) FROM attempts a INDEXED BY attempts_live_lease "
    "WHERE a.state IN ('leased','running') AND (a.state='running' OR a.lease_until>?) GROUP BY a.runner_id")


def live_totals(c):
    """{runner_id: turns held} for every computer in one pass, counted as `live` counts them. Health's capacity fence
    used to call `live` once per runner while already walking the fleet."""
    return {r[0]: r[1] for r in c.execute(LIVE_TOTALS_SQL, (H.now(),))}


# held() for every computer in one pass: the places in use, which is what start admission and Health's full check count
# against the turn limit. A parked turn with its place given up does not count.
HELD_TOTALS_SQL = (
    "SELECT a.runner_id,count(*) FROM attempts a INDEXED BY attempts_live_lease LEFT JOIN start_places p "
    "ON p.attempt_id=a.id WHERE a.state IN ('leased','running') AND (a.state='running' OR a.lease_until>?) "
    "AND (a.state='running' OR p.start_until>? OR p.attempt_id IS NULL OR p.managed=0) GROUP BY a.runner_id")


def held_totals(c):
    """{runner_id: places in use} for every computer in one pass, counted as `held` counts them."""
    now = H.now()
    return {r[0]: r[1] for r in c.execute(HELD_TOTALS_SQL, (now, now))}


# A computer's "Turns at once" (runners.turns_at_once, NULL: automatic, its own capacity). A runner that cannot take a
# raised setting has no TURNS_AT_ONCE capability, so it is capped at the capacity it reports.
TURNS_AT_ONCE = "turns_at_once_v1"
BOT_KEY = "turns_at_once"
MAX_TURNS = 256
# The wait a parked turn shows when the bot is at its own limit: the same words Health and the task use for it.
BOT_WAIT = "at its turn limit"


def turn_limit(runner):
    """The most turns this computer may hold now: its setting, or its reported capacity when the setting is clear."""
    reported = runner["capacity"] or 1
    setting = runner["turns_at_once"]
    if setting is None:
        return reported
    if TURNS_AT_ONCE in frozenset(H._json(runner["capabilities"], []) or []):
        return setting
    return min(setting, reported)


# Claims stop once a computer holds this many times its turn limit in claimed work, waiting turns included. A parked
# turn has no harness process and takes no start place, so a login or quota wait does not hold the computer's other
# work; the bound only stops the server from leasing without limit.
LIVE_CAP_FACTOR = 2


def live_limit(runner):
    """The most claimed turns (running, starting and parked) this computer may hold: LIVE_CAP_FACTOR x turn_limit."""
    return LIVE_CAP_FACTOR * turn_limit(runner)


def claim_limit(runner, body=None):
    """The most claimed turns a claim may leave on this computer. A claim that negotiates start places gets `live_limit`:
    a turn past its turn limit is leased and parked, holding no place. Any other claim keeps `turn_limit`, since an older
    runner's leases start unconditionally, so its lease count is its start count. A remote computer's gate passes no body."""
    return live_limit(runner) if getattr(body, 'start_places', False) else turn_limit(runner)


def bot_limit(config):
    """The bot's "Turns at once" from its declared settings, or None for no limit."""
    value = config.get(BOT_KEY) if isinstance(config, dict) else None
    return value if type(value) is int and 1 <= value <= MAX_TURNS else None


def at_turn_limit(c, bot):
    """Whether the bot already holds as many turns as its "Turns at once" allows, on every computer together."""
    row = c.execute(BS.ONE_SQL, (bot,)).fetchone()
    limit = bot_limit(H._json(row[0], {}) if row else {})
    return limit is not None and bot_held(c, bot) >= limit


def held_counts(c, bots):
    """{bot: the turns it holds on every computer} for every bot in `bots`, in set queries: bot_held's predicate,
    grouped by bot."""
    from .hubdb import chunks
    now = H.now()
    counts = {}
    for part in chunks(bots):
        for bot, count in c.execute(
                "SELECT a.bot_id,count(*) FROM attempts a WHERE a.bot_id IN (" + ",".join("?" * len(part)) + ") "
                "AND (a.state='running' OR (a.state='leased' AND a.lease_until>?)) GROUP BY a.bot_id", (*part, now)):
            counts[bot] = count
    return {bot: counts.get(bot, 0) for bot in bots}


def bot_held(c, bot, exclude=None):
    """Turns the bot holds on every computer together: what its "Turns at once" is checked against. A turn holds its
    place from its claim until it ends, whether it is starting, parked on a wait or running: a parked turn still
    belongs to the bot, so the bot's next one cannot start beside it. `exclude` leaves out one attempt."""
    return c.execute("SELECT count(*) FROM attempts a WHERE a.bot_id=? AND a.id IS NOT ? AND (a.state='running' "
                     "OR (a.state='leased' AND a.lease_until>?))", (bot, exclude, H.now())).fetchone()[0]


def claim(c, aid):
    c.execute("INSERT INTO start_places(attempt_id,start_until,managed) VALUES(?,?,1)",
              (aid, H.shift(H.now(), seconds=START_SECONDS)))


def admit(c, aid, runner, settings=None):
    """A claimed turn's start place, decided when its lease is written under the claim's write lock. The row is managed
    from the start, with no start yet, so the new lease does not count as held while it is decided. While the computer
    holds fewer places than its turn limit, `reserve` grants this turn one. A turn claimed past that limit holds no place
    and is not yet waiting: it is leased (it counts toward `live_limit`), and its start is refused "computer busy" until a
    place frees, when the runner asks for it (`reserve`, from start-place or started)."""
    c.execute("INSERT INTO start_places(attempt_id,managed) VALUES(?,1)", (aid,))
    attempt = c.execute("SELECT * FROM attempts WHERE id=?", (aid,)).fetchone()
    if sum(held(c, attempt['runner_id']).values()) >= turn_limit(runner):
        return {"reserved": False}
    return reserve(c, attempt, settings)


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
    a rule's wake), or a bot's message, is not one, even when a person's act caused it; the keeper's carrier of a
    person's message is one (H.person_sent_sql). Two primary-key reads."""
    return bool(c.execute("SELECT 1 FROM jobs j JOIN messages m ON m.id=j.message_id "
                          f"WHERE j.id=? AND {H.person_sent_sql()}", (job_id,)).fetchone())


def person_queued(c, runner_id):
    """Whether a person's message waits in this computer's queue: only then does a held claim look for a job."""
    return bool(c.execute("SELECT 1 FROM assignments x JOIN jobs j ON j.bot_id=x.bot AND j.state='queued' AND j.held=0 "
                          f"JOIN messages m ON m.id=j.message_id WHERE x.runner_id=? AND {H.person_sent_sql()} "
                          "LIMIT 1", (runner_id,)).fetchone())


def reserve(c, attempt, settings=None):
    """Called inside the API writer transaction; two contenders cannot win the last place."""
    aid, rid = attempt['id'], attempt['runner_id']
    if attempt['stop_requested_at']:
        return {"reserved": False, "stop": True}
    person = from_person(c, attempt['job_id'])
    hold = None if person else computer_hold(c, rid)
    if hold:
        waiting(c, aid, hold[0])
        return {"reserved": False, "waiting": hold[1]}
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
    # The bot's own limit is checked before the computer's: a turn held by its bot waits for the bot's turns, not for
    # a place, so it is not "computer busy" and does not hold the computer's other starts. This turn is already one of
    # the bot's turns (bot_held counts it), so the grant checks the others.
    bot_config = c.execute(BS.ONE_SQL, (attempt['bot_id'],)).fetchone()
    bot_cap = bot_limit(H._json(bot_config[0], {}) if bot_config else {})
    if bot_cap is not None and bot_held(c, attempt['bot_id'], exclude=aid) >= bot_cap:
        waiting(c, aid, BOT_WAIT)
        return {'reserved': False, 'waiting': BOT_WAIT}
    capacity = turn_limit(c.execute("SELECT * FROM runners_with_state WHERE id=?", (rid,)).fetchone())
    # Waiters ready to run take their turns by original claim order. Login/lane waiters are not ready yet.
    ahead = c.execute("SELECT 1 FROM start_places p JOIN attempts a ON a.id=p.attempt_id "
                      "WHERE a.runner_id=? AND a.state='leased' AND a.lease_until>? AND a.stop_requested_at IS NULL "
                      "AND (p.wait_reason LIKE 'computer busy%' OR p.wait_reason=?) AND (a.created_at,a.id)<(?,?) LIMIT 1",
                      (rid, H.now(), org_turns.REASON, attempt['created_at'], aid)).fetchone()
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


def summaries(c, runner_ids, settings, default=None, *, classify=True):
    """`summary` for many computers in two scans. Each computer's counts are the ones `summary` would build
    on its own, including the same claim refusal for each queued bot (unless `classify=False`, as `summary`)."""
    from .read_cache import value
    ids = tuple(sorted({rid for rid in runner_ids if rid}))
    return value(c, ("start_summaries", ids, bool(classify)),
                 lambda: _summaries(c, ids, settings, default, classify))


def _summaries(c, runner_ids, settings, default=None, classify=True):
    from . import performance
    from .cloud_machines import ASLEEP, STALE, resting
    out: dict[str, dict[str, Any]] = {rid: dict(running=0, starting=0, waiting=0, queued=0,
                                                start_seconds=START_SECONDS) for rid in runner_ids}
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
    for row in c.execute("SELECT a.runner_id,a.state,a.created_at,a.lease_until,p.start_until,p.wait_reason,"
                         "p.waiting_since,p.managed FROM attempts a LEFT JOIN start_places p ON p.attempt_id=a.id "
                         f"WHERE a.runner_id IN ({marks}) AND a.state IN ('leased','running')", runner_ids):
        rid = row['runner_id']
        if row['state'] == 'running':
            out[rid]['running'] += 1
        elif row['wait_reason']:
            add(rid, row['wait_reason'], 1, row['waiting_since'] or row['created_at'])
        elif row['lease_until'] > now and (not row['managed'] or (row['start_until'] or '') > now):
            out[rid]['starting'] += 1
        else:
            out[rid]['queued'] += 1
    from .execution import claim_refusal, queued_reasons
    from .store import readiness_document
    runners = {row['id']: row for row in c.execute(f"SELECT * FROM runners_with_state WHERE id IN ({marks})", runner_ids)}
    statuses = performance.load_statuses(c, runner_ids)
    asleep = resting(c)
    holds = {}      # a computer's hold, asked once and only for a computer with queued work
    reasons = {'cooling_off': 'quota', 'limit': 'spending limit', 'not_ready': 'computer readiness',
               'readiness_unknown': 'computer readiness', 'sign_in_rejected': 'sign-in',
               'draining': 'bot paused', 'run_hold': 'stopped runs', 'backoff': 'a failed run', 'budget': 'run budget',
               'bot_limit': 'at its turn limit', 'task_wait': 'a snooze, prerequisite, person or check',
               'task_next_run': 'the next run or a stored resume condition'}
    for row in c.execute(f"SELECT a.runner_id,j.bot_id,count(*) n,min(j.created_at) oldest,{BS.CONFIG} AS config,b.state bot_state "
                         "FROM jobs j JOIN assignments a ON a.bot=j.bot_id JOIN bots b ON b.id=j.bot_id "
                         f"JOIN bot_config bc ON bc.bot=j.bot_id {BS.JOIN} WHERE a.runner_id IN (" + marks + ") AND j.state='queued' "
                         "GROUP BY a.runner_id,j.bot_id", runner_ids):
        rid = row['runner_id']
        runner = runners.get(rid)
        ready = readiness_document(runner['readiness']) if runner else {}
        why = 'bot paused' if row['bot_state'] != 'active' else ''
        if not why and rid in asleep:
            # A sleeping computer cannot refresh its samples. Explain each job's claim hold instead of its last load.
            counted = 0
            if classify:
                for job, refused in queued_reasons(c, settings, rid, row['bot_id'], default):
                    reason = reasons.get(refused, 'pre-start requirements') if refused and refused not in STALE else ASLEEP
                    add(rid, reason, 1, job['created_at'])
                    counted += 1
            if counted < row['n']:
                add(rid, ASLEEP, row['n'] - counted, row['oldest'])
            continue
        if not why and runner and classify:
            refused, _ = claim_refusal(c, settings, runner, row['bot_id'], row['config'], False, ready,
                                       default=default)
            why = reasons.get(refused, 'pre-start requirements') if refused else ''
        if not why:
            if rid not in holds:
                holds[rid] = computer_hold(c, rid, status=statuses[rid], row=runners.get(rid))
            why = holds[rid][0] if holds[rid] else ''
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
        since = H.parse_ts(wait['oldest'])
        minutes = max(0, int((now - since).total_seconds() / 60)) if now is not None and since is not None else 0
        age = f"{minutes // 60} h" if minutes >= 60 else f"{minutes} min"
        reason = ("waiting: " + wait['reason'] if wait['reason'].startswith(('computer busy', 'computer not reporting', 'at ', 'asleep;'))
                  else "waiting for " + wait['reason'])
        parts.append(f"{wait['count']} {reason} (oldest {age})")
    if not counts['waiting']:
        parts.append('0 waiting')
    parts.append(f"{counts['queued']} queued")
    return ' · '.join(parts)

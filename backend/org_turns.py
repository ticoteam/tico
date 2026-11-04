"""Company-wide turn levels. Warning is an incident hook, never a refusal or bot alert."""

import json

from .store import H

LEVELS = 'company-turn-levels'
INCIDENT = 'company-turn-incident'
REASON = "Your company is at its limit; Tico is fixing what's stuck."
SCHEMA = """
CREATE TABLE IF NOT EXISTS org_turn_waits(
 job_id TEXT PRIMARY KEY REFERENCES jobs(id) ON DELETE CASCADE, created_at TEXT NOT NULL
);
"""


def saved(c, key):
    row = c.execute('SELECT value FROM registry_metadata WHERE key=?', (key,)).fetchone()
    return json.loads(row[0]) if row else {}


def save(c, key, value):
    c.execute('INSERT INTO registry_metadata VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
              (key, json.dumps(value)))


def levels(c):
    # Guests have no roster sign-in. Hidden/departed people and disabled sign-ins do not count.
    # Count people without building the full roster chart (groups/teams): admission calls this on
    # every claim, and a full roster normalize was only used for this headcount.
    from . import people as P
    from . import read_cache
    def count():
        row = read_cache.metadata(c, 'people')
        doc = json.loads(row[0]) if row else {'people': H.humans(c)}
        return P.signed_in_count(doc)
    people = max(1, read_cache.value(c, ('signed_in_people',), count))
    configured = {'warning_per_person': 40, 'stop_per_person': 100, **saved(c, LEVELS)}
    return {**configured, 'people': people, 'warning': people * configured['warning_per_person'],
            'stop': people * configured['stop_per_person']}


def in_use(c):
    """Turns that hold a place: running, starting, or a leased attempt waiting with its place kept.

    Queued jobs blocked on quota, a spending limit, readiness or a paused bot never became turns, and an attempt
    parked by the stop itself is not counted either: counting them kept the company at its stop with nothing running.

    The first `state IN` term lets SQLite read only the partial index of live attempts (attempts_live_lease):
    without it the OR scanned every attempt ever made, ~90 ms a call on a live install, on every claim.
    """
    now = H.now()
    return c.execute(
        "SELECT count(*) FROM attempts a LEFT JOIN start_places p ON p.attempt_id=a.id "
        "WHERE a.state IN ('leased','running') AND (a.state='running' OR (a.state='leased' AND "
        "((coalesce(p.wait_reason,'')!='' AND p.wait_reason!=?) OR (coalesce(p.wait_reason,'')='' AND a.lease_until>? "
        "AND (NOT coalesce(p.managed,0) OR coalesce(p.start_until,'')>?)))))",
        (REASON, now, now)).fetchone()[0]


def admission(c):
    """The current turn ceiling, without classifying every queued bot's readiness and spending limits."""
    value = {**levels(c), 'in_use': in_use(c)}
    value['at_warning'] = value['in_use'] >= value['warning']
    value['at_stop'] = value['in_use'] >= value['stop']
    return value


def snapshot(c, settings):
    """Classify every computer's queue once for a finding or the turn-levels settings view. Share one company
    Default so each claim_refusal in the pass reuses the same pools/groups/spend memo instead of rebuilding it per bot."""
    from . import usage_limits
    from .start_places import summary
    default = usage_limits.company(c)
    counts = dict(running=0, starting=0, waiting=0, queued=0)
    for row in c.execute('SELECT id FROM runners'):
        here = summary(c, row['id'], settings, default)
        for key in counts:
            counts[key] += here[key]
    return {**admission(c), **counts}


def needs_observation(c, value):
    incident = saved(c, INCIDENT)
    return value['at_warning'] != bool(incident.get('active')) or value['at_stop'] != bool(incident.get('at_stop'))


def observe(c, settings, value=None, *, detailed=True):
    """Caller holds the writer transaction: one durable crossing and hook per incident.

    C will consume turns.warning_crossed and own the detailed fixer finding. Until then Health
    shows this saved incident even with the optional Health Monitor switched off.
    """
    value = value or (snapshot(c, settings) if detailed else admission(c))
    old = saved(c, INCIDENT)
    incident = dict(old)
    if not detailed and value['at_warning'] != bool(old.get('active')):
        # The incident keeps its full crossing counts, but admission under the write
        # lock does not repeatedly check every queued bot's subscription and budget.
        value = {**snapshot(c, settings), **value}
    if value['at_warning'] and not old.get('active'):
        incident = {'id': H.new_id(), 'opened_at': H.now(), 'active': True, 'counts': value}
        H.event(c, H.KEEPER, 'turns.warning_crossed', incident['id'], value)
    elif not value['at_warning'] and old.get('active'):
        incident.update(active=False, resolved_at=H.now(), counts=value)
        H.event(c, H.KEEPER, 'turns.warning_cleared', incident['id'], value)
    incident['at_stop'] = value['at_stop']
    if incident != old:
        save(c, INCIDENT, incident)
    if value['at_stop']:
        # These jobs have not become turns yet: they remain queued and hold no place.
        c.execute("INSERT OR IGNORE INTO org_turn_waits SELECT id,created_at FROM jobs WHERE state='queued'")
    if value['at_warning']:
        # Just below the stop the waiters keep their first claim on each freed place, across computers.
        c.execute("DELETE FROM org_turn_waits WHERE NOT EXISTS "
                  "(SELECT 1 FROM jobs j WHERE j.id=org_turn_waits.job_id AND j.state='queued')")
    else:
        # Below the warning there is room for all of them and the queue's own order applies again. A waiter that
        # cannot run (its bot cooling off a usage limit, its login held) otherwise stayed one for good: every claim
        # from every computer then took the write lock to check each waiter under it, 13-20 thousand queries and
        # holds of 5-10 s per claim on a live install, with every runner's events and renewals queued behind them.
        c.execute("DELETE FROM org_turn_waits")
    return value


def configure(c, actor, body, settings):
    save(c, LEVELS, body.model_dump())
    H.event(c, actor, 'turns.levels_changed', LEVELS, body.model_dump())
    return observe(c, settings)

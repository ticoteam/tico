"""Company-wide turn levels. Warning is an incident hook, never a refusal or bot alert."""

import json

from .store import H

LEVELS = 'company-turn-levels'
INCIDENT = 'company-turn-incident'
REASON = "Your company is at its limit; Tico is fixing what's stuck."
SCHEMA = """
CREATE TABLE IF NOT EXISTS org_turn_waits(
 job_id TEXT PRIMARY KEY REFERENCES jobs(id) ON DELETE CASCADE, created TEXT NOT NULL
);
"""


def saved(c, key):
    row = c.execute('SELECT value_json FROM registry_metadata WHERE key=?', (key,)).fetchone()
    return json.loads(row[0]) if row else {}


def save(c, key, value):
    c.execute('INSERT INTO registry_metadata VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json',
              (key, json.dumps(value)))


def levels(c):
    # Guests have no roster sign-in. Hidden/departed people and disabled sign-ins do not count.
    from .views import roster
    people = max(1, sum(bool(p['email']) and not p['hidden'] and not p['directory_left'] and p['sign_in']
                        for p in roster(c)['people']))
    configured = {'warning_per_person': 40, 'stop_per_person': 100, **saved(c, LEVELS)}
    return {**configured, 'people': people, 'warning': people * configured['warning_per_person'],
            'stop': people * configured['stop_per_person']}


def in_use(c):
    """Turns that hold a place: running, starting, or a leased attempt waiting with its place kept.

    Queued jobs blocked on quota, a spending limit, readiness or a paused bot never became turns, and an attempt
    parked by the stop itself is not counted either: counting them kept the company at its stop with nothing running.
    """
    now = H.now()
    return c.execute(
        "SELECT count(*) FROM attempts a LEFT JOIN start_places p ON p.attempt_id=a.id "
        "WHERE a.state='running' OR (a.state='leased' AND ((coalesce(p.wait_reason,'')!='' AND p.wait_reason!=?) "
        "OR (coalesce(p.wait_reason,'')='' AND a.lease_until>? AND (NOT coalesce(p.managed,0) OR coalesce(p.start_until,'')>?))))",
        (REASON, now, now)).fetchone()[0]


def snapshot(c, settings):
    from .start_places import summary
    counts = dict(running=0, starting=0, waiting=0, queued=0)
    for row in c.execute('SELECT id FROM runners'):
        here = summary(c, row['id'], settings)
        for key in counts:
            counts[key] += here[key]
    value = {**levels(c), **counts, 'in_use': in_use(c)}
    value['at_warning'] = value['in_use'] >= value['warning']
    value['at_stop'] = value['in_use'] >= value['stop']
    return value


def needs_observation(c, value):
    incident = saved(c, INCIDENT)
    return value['at_warning'] != bool(incident.get('active')) or value['at_stop'] != bool(incident.get('at_stop'))


def observe(c, settings, value=None):
    """Caller holds the writer transaction: one durable crossing and hook per incident.

    C will consume turns.warning_crossed and own the detailed fixer finding. Until then Health
    shows this saved incident even with the optional Health Monitor switched off.
    """
    value = value or snapshot(c, settings)
    old = saved(c, INCIDENT)
    incident = dict(old)
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
        c.execute("INSERT OR IGNORE INTO org_turn_waits SELECT id,created FROM jobs WHERE state='queued'")
    c.execute("DELETE FROM org_turn_waits WHERE NOT EXISTS "
              "(SELECT 1 FROM jobs j WHERE j.id=org_turn_waits.job_id AND j.state='queued')")
    return value


def configure(c, actor, body, settings):
    save(c, LEVELS, body.model_dump())
    H.event(c, actor, 'turns.levels_changed', LEVELS, body.model_dump())
    return observe(c, settings)

"""Continuous claim observations, evaluated without holding the scheduler's write lock."""
from typing import Any, cast

from . import execution as E, hubdb as H

EVENT = 'job.refusal-observed'
KEY = 'queue-refusal:'
REFRESH_SECONDS = 60
MAX_GAP_SECONDS = 300


def state(c, bot):
    row = c.execute('SELECT value_json FROM registry_metadata WHERE key=?', (KEY + bot,)).fetchone()
    return H._json(row[0], {}) if row else {}


def states(c, bots):
    """{bot: its state} for these bots, read in one query per 500 bots (a bot with no state is left out)."""
    out = {}
    bots = sorted(set(bots))
    for start in range(0, len(bots), 500):
        part = bots[start:start + 500]
        for key, value in c.execute("SELECT key,value_json FROM registry_metadata WHERE key IN (%s)"
                                    % ",".join("?" * len(part)), [KEY + bot for bot in part]):
            out[key[len(KEY):]] = H._json(value, {})
    return out


def targets(c):
    # Include prior observations even after pause, unassignment, holding or cancellation, to clear them.
    return sorted({r[0] for r in c.execute("SELECT bot FROM assignments WHERE bot IN "
                                         "(SELECT bot FROM jobs WHERE state='queued')")} |
                  {r[0][len(KEY):] for r in c.execute("SELECT key FROM registry_metadata WHERE key LIKE ?", (KEY + '%',))})


def current(c, settings, bot):
    row = c.execute("SELECT a.bot,a.runner_id,b.display_name FROM assignments a JOIN bots b ON b.slug=a.bot "
                    "WHERE a.bot=? AND b.state='active'", (bot,)).fetchone()
    if not row:
        return {}
    # with_job=True always returns four values; cast, so the unpacking below still fails loudly if that changes.
    why, created, _, job = cast(tuple[Any, Any, Any, Any],
                                E.bot_claimable(c, settings, row['runner_id'], bot, oldest=True, with_job=True))
    if not job or not why or why == 'lane_held':
        return {}
    return {'bot': bot, 'runner_id': row['runner_id'], 'name': row['display_name'] or bot,
            'job': job, 'oldest': created, 'reason': why}


def plan(c, settings, bot, at):
    previous = state(c, bot)
    value = current(c, settings, bot)
    if not value:
        return (bot, previous, {}) if previous else None
    continuous = (previous.get('job') == value['job'] and previous.get('reason') == value['reason']
                  and previous.get('observed_at', '') >= H.shift(at, seconds=-MAX_GAP_SECONDS))
    if continuous and previous['observed_at'] > H.shift(at, seconds=-REFRESH_SECONDS):
        return None
    value.update(since=previous['since'] if continuous else at, observed_at=at)
    return bot, previous, value


def pending(c, settings, at, failures):
    out = []
    for bot in targets(c):
        try:
            change = plan(c, settings, bot, at)
            if change:
                out.append(change)
        except Exception as exc:
            failures.append({'queue_refusals': bot, 'error': type(exc).__name__})
    return out


def apply(c, change):
    bot, previous, value = change
    if state(c, bot) != previous:
        return                         # a newer scheduler pass already refreshed this observation
    if value and not c.execute("SELECT 1 FROM jobs j JOIN bots b ON b.slug=j.bot "
                               "JOIN assignments a ON a.bot=b.slug WHERE j.id=? AND j.bot=? "
                               "AND j.state='queued' AND j.held=0 AND b.state='active' AND a.runner_id=?",
                               (value['job'], bot, value['runner_id'])).fetchone():
        value = {}
    if value:
        c.execute('INSERT INTO registry_metadata VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json',
                  (KEY + bot, H._dump(value)))
    else:
        c.execute('DELETE FROM registry_metadata WHERE key=?', (KEY + bot,))
    if (previous.get('job'), previous.get('reason'), previous.get('since')) != (value.get('job'), value.get('reason'), value.get('since')):
        H.event(c, H.KEEPER, EVENT, value.get('job') or previous['job'],
                {'reason': value.get('reason'), 'since': value.get('since')})


def observe(c, settings, at=None):
    """Synchronous helper; the scheduler plans in a read, then applies one short transaction per bot."""
    for change in pending(c, settings, at or H.now(), []):
        apply(c, change)


def aged(c, settings):
    at = H.now()
    bots = targets(c)
    previous_of = states(c, bots)           # one read for every bot, not one per queued bot
    for bot in bots:
        previous = previous_of.get(bot, {})
        if (not previous.get('since') or previous['since'] > H.shift(at, hours=-12)
                or previous.get('observed_at', '') < H.shift(at, seconds=-MAX_GAP_SECONDS)):
            continue
        value = current(c, settings, bot)
        if value and value['job'] == previous['job'] and value['reason'] == previous['reason']:
            yield {**value, 'refused_since': previous['since']}

"""Continuous claim observations, evaluated without holding the scheduler's write lock."""

from typing import Any, cast

from . import execution as E, hubdb as H, login_holds, usage_limits

EVENT = 'job.refusal-observed'
KEY = 'queue-refusal:'
REFRESH_SECONDS = 60
MAX_GAP_SECONDS = 300
# Refusals that hold a job on purpose, each until a person or a date ends it, never an aged refusal: a card's lane
# held, a card On ice (D69) until a person moves it out, and a Tico notice on a snoozed task (task_wait,
# docs/dev/pipelines-v3.md rule 7) until the snooze ends. Health's slow-queue check shows the last as resting.
ON_PURPOSE = frozenset({'lane_held', 'on_ice', 'task_wait'})


def state(c, bot):
    row = c.execute('SELECT value FROM registry_metadata WHERE key=?', (KEY + bot,)).fetchone()
    return H._json(row[0], {}) if row else {}


def states(c, bots):
    """{bot: its state} for these bots, read in one query per 500 bots (a bot with no state is left out)."""
    out = {}
    bots = sorted(set(bots))
    for start in range(0, len(bots), 500):
        part = bots[start : start + 500]
        for key, value in c.execute(
            "SELECT key,value FROM registry_metadata WHERE key IN (%s)" % ",".join("?" * len(part)),
            [KEY + bot for bot in part],
        ):
            out[key[len(KEY) :]] = H._json(value, {})
    return out


def targets(c):
    # Include prior observations even after pause, unassignment, holding or cancellation, to clear them.
    return sorted(
        {
            r[0]
            for r in c.execute(
                "SELECT bot FROM assignments WHERE bot IN (SELECT bot_id FROM jobs WHERE state='queued')"
            )
        }
        | {r[0][len(KEY) :] for r in c.execute("SELECT key FROM registry_metadata WHERE key LIKE ?", (KEY + '%',))}
    )


CURRENT_SQL = (
    "SELECT a.bot,a.runner_id,b.display_name FROM assignments a JOIN bots b ON b.id=a.bot "
    "WHERE a.bot=? AND b.state='active'"
)


def current(c, settings, bot, default=None, loads=None):
    """`default`: the company's limit default, shared by a pass over many bots so its budget and spend reads are made
    once (usage_limits.Default)."""
    from .read_cache import one

    row = one(c, CURRENT_SQL, (bot,))
    if not row:
        return {}
    # with_job=True always returns four values; cast, so the unpacking below still fails loudly if that changes.
    why, created, _, job = cast(
        tuple[Any, Any, Any, Any],
        E.bot_claimable(c, settings, row['runner_id'], bot, default, oldest=True, with_job=True, loads=loads),
    )
    if not job or not why or why in ON_PURPOSE:
        return {}
    value = {
        'bot': bot,
        'runner_id': row['runner_id'],
        'name': row['display_name'] or bot,
        'job': job,
        'oldest': created,
        'reason': why,
    }
    if why == 'payer_wait' and (payer := payer_wait(c, settings, row['runner_id'], bot, loads)):
        value['payer'] = payer
    elif why == 'cooling_off' and (limit := login_limit(c, settings, row['runner_id'], bot)):
        value['payer'] = limit
    return value


def limit_words(payer):
    """ "It hit its Antigravity usage limit (resets Nov 26 17:40 UTC)" from a recorded {'plan', 'until'}, or ""."""
    from .model_subscriptions import _when

    plan, until = (payer or {}).get('plan'), (payer or {}).get('until')
    return f'It hit its {plan} usage limit (resets {_when(until)})' if plan and until else ''


def login_limit(c, settings, runner_id, bot):
    """{'plan', 'until'} when the bot's own login waits out its provider's usage limit (login_holds), else {}: a
    capacity or payment hold on it is no usage limit."""
    from .model_subscriptions import RUNTIME_LABELS
    from .read_cache import one
    from .shared_bots import follow

    config = one(c, E.CLAIM_CONFIG_SQL, (bot,))
    if not config:
        return {}
    login = E._limits_login(c, settings, runner_id, bot, follow(c, bot, H._json(config['config'], {}) or {}))
    if not login:
        return {}
    hold = login_holds.get(c, *login)
    # A busy provider (capacity) says nothing of the plan, and a payment hold ends at a retry, not a reset.
    if not hold.get('until') or hold.get('capacity') or login_holds.payment(hold.get('error')):
        return {}
    runtime = login[2]
    return {'plan': RUNTIME_LABELS.get(runtime, runtime), 'until': hold['until']}


def payer_wait(c, settings, runner_id, bot, loads=None):
    """What the bot's Paid by waits for, so the chat can say it (thread_bot_status): {'text'} as Health says it, and
    'plan' and 'until' when a plan waits out its usage limit. {} when no queued job waits on a payer now."""
    from .read_cache import one
    from .shared_bots import follow

    runner = one(c, E.RUNNER_SQL, (runner_id,))
    config = one(c, E.CLAIM_CONFIG_SQL, (bot,))
    if not runner or not config:
        return {}
    followed = follow(c, bot, H._json(config['config'], {}) or {})
    for _, payment in E.waiting_payers(c, settings, runner, bot, followed, loads):
        out = {'text': payment.reason}
        if payment.limited and payment.until:
            out.update(plan=payment.limited, until=payment.until.isoformat())
        return out
    return {}


def plan(c, settings, bot, at, previous=None):
    previous = state(c, bot) if previous is None else previous
    value = current(c, settings, bot)
    if not value:
        return (bot, previous, {}) if previous else None
    continuous = (
        previous.get('job') == value['job']
        and previous.get('reason') == value['reason']
        and previous.get('observed_at', '') >= H.shift(at, seconds=-MAX_GAP_SECONDS)
    )
    if continuous and previous['observed_at'] > H.shift(at, seconds=-REFRESH_SECONDS):
        return None
    value.update(since=previous['since'] if continuous else at, observed_at=at)
    return bot, previous, value


def pending(c, settings, at, failures):
    out = []
    bots = targets(c)
    previous_of = states(c, bots)  # one read for every bot, as aged() does, not one per bot
    for bot in bots:
        try:
            change = plan(c, settings, bot, at, previous_of.get(bot, {}))
            if change:
                out.append(change)
        except Exception as exc:
            failures.append({'queue_refusals': bot, 'error': type(exc).__name__})
    return out


def apply(c, change):
    """Write one observation. True when it was a real change (a new or changed job, reason or start, or one cleared),
    False for a refresh of the same one or a pass a newer one beat."""
    bot, previous, value = change
    if state(c, bot) != previous:
        return False  # a newer scheduler pass already refreshed this observation
    if (
        value
        and not c.execute(
            "SELECT 1 FROM jobs j JOIN bots b ON b.id=j.bot_id "
            "JOIN assignments a ON a.bot=b.id WHERE j.id=? AND j.bot_id=? "
            "AND j.state='queued' AND j.held=0 AND b.state='active' AND a.runner_id=?",
            (value['job'], bot, value['runner_id']),
        ).fetchone()
    ):
        value = {}
    if value:
        c.execute(
            'INSERT INTO registry_metadata VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
            (KEY + bot, H._dump(value)),
        )
    else:
        c.execute('DELETE FROM registry_metadata WHERE key=?', (KEY + bot,))
    if (previous.get('job'), previous.get('reason'), previous.get('since')) != (
        value.get('job'),
        value.get('reason'),
        value.get('since'),
    ):
        H.event(
            c,
            H.TICO_ACTOR,
            EVENT,
            value.get('job') or previous['job'],
            {'reason': value.get('reason'), 'since': value.get('since')},
        )
        return True
    return False


def observe(c, settings, at=None):
    """Synchronous helper; the scheduler plans in a read, then applies one short transaction per bot."""
    for change in pending(c, settings, at or H.now(), []):
        apply(c, change)


def _prime(c, bots):
    """What `current` reads for each of these bots, one query (read_cache): its assignment. Nothing outside a snapshot:
    then each bot's check reads its own."""
    from .read_cache import cache, prime

    if cache(c) is None or not bots:
        return
    found = {}
    for part in H.chunks(bots):
        found.update(
            (row['bot'], row)
            for row in c.execute(
                "SELECT a.bot,a.runner_id,b.display_name FROM assignments a JOIN bots b ON b.id=a.bot "
                f"WHERE a.bot IN ({','.join('?' * len(part))}) AND b.state='active'",
                part,
            )
        )
    prime(c, CURRENT_SQL, {(bot,): found.get(bot) for bot in bots})


def aged(c, settings, loads=None):
    at = H.now()
    bots = targets(c)
    previous_of = states(c, bots)  # one read for every bot, not one per queued bot
    due = [
        bot
        for bot in bots
        if (previous := previous_of.get(bot, {})).get('since')
        and previous['since'] <= H.shift(at, hours=-12)
        and previous.get('observed_at', '') >= H.shift(at, seconds=-MAX_GAP_SECONDS)
    ]
    _prime(c, due)  # Health asks every refused queue on each poll
    if loads is not None and (unprimed := set(due) - loads.queued_bots):
        E.prime_claimables(c, unprimed, first=None, loads=loads)
    default = usage_limits.company(c)  # one pass: every bot's limit check shares its budget and spend reads
    for bot in due:
        previous = previous_of[bot]
        value = current(c, settings, bot, default, loads)
        if value and value['job'] == previous['job'] and value['reason'] == previous['reason']:
            yield {**value, 'refused_since': previous['since']}

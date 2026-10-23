"""Subscription logins on computers. Every bot runs each AI tool on its computer's one login for that tool: a bot
never has a sign-in of its own, and a different account means another computer (another runner), even on one box."""
import json
from datetime import datetime, timedelta, timezone

from fastapi import Request

from . import hubdb as H, models as M
from .execution import readiness_document
from .subscription_identity import identity
from .store import Problem, encode

COMPUTER_LOGIN_CAPABILITY = 'computer_logins_v1'


def computer_logins(runner, ready=None):
    """The runner has acknowledged the server's one-login contract and named its physical default home."""
    ready = readiness_document(runner['readiness_json']) if ready is None else ready
    return (COMPUTER_LOGIN_CAPABILITY in (H._json(runner['capabilities_json'], []) or [])
            and isinstance(ready.get('computer_profile'), str))


def login_upgrade(runner, ready=None, bot=None, runtime=None):
    """An older runner's named bot login may be a local override; never relabel it or silently change its account."""
    ready = readiness_document(runner['readiness_json']) if ready is None else ready
    if computer_logins(runner, ready):
        return ''
    rows = ready.get('bots') or {}
    if any(row.get('profile') and (bot is None or name == bot)
           and (runtime is None or row.get('runtime') == runtime) for name, row in rows.items()):
        return f"Update {runner['label']} to use one login per AI tool before running bots or changing that login"
    return ''


# The longest a window can run from its reading to its reset: the window itself, plus an allowance for clock
# skew between the provider, the runner and this server. A reset further out is not that window's.
WINDOWS = {'weekly': timedelta(days=8), 'five_hour': timedelta(hours=5, minutes=10)}


def weekly_snapshot(value, window='weekly'):
    """Validate timestamps too; a broken report is unknown, never a fresh allowance."""
    if not value:
        return None
    try:
        value = M.SubscriptionWeekly.model_validate(value).model_dump()
        reported = datetime.fromisoformat(value['reported_at'].replace('Z', '+00:00'))
        if reported.tzinfo is None or reported > datetime.now(timezone.utc) + timedelta(minutes=5):
            return None
        if value['resets_at']:
            reset = datetime.fromisoformat(value['resets_at'].replace('Z', '+00:00'))
            if reset.tzinfo is None or reset <= reported or reset > reported + WINDOWS[window]:
                return None
        return value
    except (ValueError, TypeError, AttributeError):
        return None


def weekly_key(runner, profile, runtime):
    return 'subscription-weekly:' + json.dumps([runner, profile, runtime], separators=(',', ':'))


def reported_runtimes(c, runner, profile, raw):
    from .repositories import metadata
    from . import subscription_refresh as refresh
    runtimes = json.loads(raw or '{}')
    cutoff = refresh.signin_after(c, runner, profile)
    for runtime, state in runtimes.items():
        reported = weekly_snapshot(state.get('weekly'))
        manual = weekly_snapshot(metadata(c, weekly_key(runner, profile, runtime)))
        refreshed = metadata(c, refresh.key(runner, profile, runtime))
        direct = weekly_snapshot(refreshed.get('weekly'))
        if refreshed:
            state['refresh'] = refresh.view(refreshed)
            if refreshed['requested_at'] < cutoff.get(runtime, ''):
                state['refresh']['state'] = 'outdated'
        # Explicitly distinguish human-entered observations from provider telemetry.
        choices = [(v, source) for v, source in ((reported, 'provider'), (manual, 'manual'), (direct, 'provider'))
                   if v and (not cutoff.get(runtime) or H.parse_ts(v['reported_at']) >= H.parse_ts(cutoff[runtime]))]
        state['weekly'] = None
        if choices:
            value, source = max(choices, key=lambda row: datetime.fromisoformat(row[0]['reported_at'].replace('Z', '+00:00')))
            state['weekly'] = {**value, 'source': source}
        five_hour = weekly_snapshot(state.get('five_hour'), 'five_hour')
        state['five_hour'] = ({**five_hour, 'source': 'provider'} if five_hour and (
            not cutoff.get(runtime) or H.parse_ts(five_hour['reported_at']) >= H.parse_ts(cutoff[runtime])) else None)
        # An account names a login, so it goes with a sign-in that is not known to be gone.
        if state.get('signed_in') is False or not state.get('account'):
            state.pop('account', None)
    return runtimes


def may_see_accounts(auth, who, runner):
    """Who may read a computer's signed-in accounts (#610): an administrator or the computer's operator, as for
    /api/v2/operations; never a bot or another member."""
    return who.role in ('human', 'owner') and (auth.bot_admin(who) or runner['operator'] == H.actor_id(who.actor))


def without_accounts(runtimes, windows=False):
    """Take the accounts out; with `windows`, the own login's usage windows on readiness runtime rows too."""
    for state in runtimes.values():
        if isinstance(state, dict):
            state.pop('account', None)
            if windows:
                state.pop('weekly', None), state.pop('five_hour', None)
    return runtimes


def computer_profiles(c, runner, accounts):
    """The subscription profiles a computer reported, each with its runtimes; accounts only when `accounts`."""
    rows = []
    for row in c.execute('SELECT * FROM computer_profiles WHERE runner_id=? ORDER BY profile', (runner['id'],)):
        runtimes = reported_runtimes(c, runner['id'], row['profile'], row['runtimes_json'])
        rows.append({'name': row['profile'], **identity(c, runner['id'], row['profile']),
                     'runtimes': runtimes if accounts else without_accounts(runtimes)})
    return rows


def context(c, settings=None):
    # Per-bot assignments were removed (one login per tool per computer); the table stays one release, unread.
    result = {}
    if settings is not None:
        from . import providers
        result['providers'] = providers.load(c, settings)
    return result


def effective(c, bot, ctx=None):
    """The named profile a bot runs on and where it comes from: always none, on its computer's own login."""
    return None, 'computer'


def covers(config):
    """Whether a subscription profile's own login runs this harness (its manifest's `subscription_profiles`)."""
    from .harnesses import harness_caps
    return bool(harness_caps(config.get('harness') or '', config.get('runtime') or '').get('subscription_profiles'))


def refusal(c, runner_id, bot):
    from .repositories import metadata
    blocked = metadata(c, f'subscription-unavailable:{bot}')
    return blocked if blocked.get('runner_id') == runner_id else {}


def record(c, runner_id, profiles, readiness=None):
    # Omitted by an older computer: retain its last report until it can report again.
    from .repositories import save_metadata
    from .repositories import metadata
    reported = {'reported': profiles is not None}
    if metadata(c, 'computer-profiles:' + runner_id) != reported:
        save_metadata(c, 'computer-profiles:' + runner_id, reported)
    if profiles is None:
        return
    valid = {}
    for row in profiles[:100]:
        try:
            profile = M.ComputerProfile.model_validate(row)
        except ValueError:
            continue
        previous = c.execute('SELECT runtimes_json FROM computer_profiles WHERE runner_id=? AND profile=?',
                             (runner_id, profile.name)).fetchone()
        previous = json.loads(previous[0] or '{}') if previous else {}
        runtimes = {}
        for runtime, state in profile.runtimes.items():
            value = state.model_dump()
            for window in ('weekly', 'five_hour'):
                latest = weekly_snapshot(value.get(window), window)
                older = weekly_snapshot(previous.get(runtime, {}).get(window), window)
                values = [v for v in (latest, older) if v]
                value[window] = max(values, key=lambda v: datetime.fromisoformat(v['reported_at'].replace('Z', '+00:00'))) if values else None
            if not value.get('account'):
                value.pop('account', None)
            runtimes[runtime] = value
        valid[profile.name] = encode(runtimes)
    for bot in c.execute('SELECT bot FROM assignments WHERE runner_id=?', (runner_id,)):
        blocked = refusal(c, runner_id, bot['bot'])
        status = ((readiness_document(readiness).get('runtimes', {}).get(blocked.get('runtime'), {})
                   .get('profiles', {}).get(blocked.get('profile'), {})) if blocked else {})
        signed_in = json.loads(valid.get(blocked.get('profile'), '{}')).get(blocked.get('runtime'), {}).get('signed_in')
        if blocked and (signed_in is True or (signed_in is None and blocked['profile'] in valid
                                             and status.get('authenticated') == 'ready')):
            c.execute("DELETE FROM registry_metadata WHERE key=?", (f"subscription-unavailable:{bot['bot']}",))
    stored = {r['profile']: r['runtimes_json'] for r in c.execute(
        'SELECT profile,runtimes_json FROM computer_profiles WHERE runner_id=?', (runner_id,))}
    if stored == valid:
        return
    c.execute('DELETE FROM computer_profiles WHERE runner_id=?', (runner_id,))
    c.executemany('INSERT INTO computer_profiles VALUES(?,?,?,?)',
                  [(runner_id, name, runtimes, H.now()) for name, runtimes in valid.items()])


def listing(c, auth, who, computer_rows):
    computers = []
    visible = (c.execute("SELECT r.* FROM runners r JOIN assignments a ON a.runner_id=r.id "
                         "WHERE a.bot=? AND r.revoked_at IS NULL", (H.actor_id(who.actor),)).fetchall()
               if who.role == "bot" else computer_rows(c, who))
    for runner in visible:
        if who.role == 'bot' and not c.execute('SELECT 1 FROM assignments WHERE runner_id=? AND bot=?',
                                              (runner['id'], H.actor_id(who.actor))).fetchone():
            continue
        operator = c.execute('SELECT operator FROM runners WHERE id=?', (runner['id'],)).fetchone()
        accounts = bool(operator) and may_see_accounts(auth, who, operator)
        computers.append({'runner_id': runner['id'], 'label': runner['label'],
                          'profiles': computer_profiles(c, runner, accounts)})
    return {'profiles_by_computer': computers}


def bot_subscription(c, bot, settings, ctx=None):
    """The connection a bot runs on: {profile, source ('computer'), computer, runtime, harness, signed_in, problem}:
    its computer's own login for `runtime`. `profile` names that login's folder when the computer reports one."""
    ctx = ctx if ctx is not None else context(c, settings)
    line, config = _bot_line(c, bot, ctx)
    return {**line, 'runtime': config.get('runtime') or '', 'harness': config.get('harness') or ''}


def _bot_line(c, bot, ctx):
    """The connection without its tool, and the bot's filled-in configuration that names the tool."""
    runner = c.execute('SELECT r.* FROM assignments a JOIN runners r ON r.id=a.runner_id WHERE a.bot=? '
                       'AND r.revoked_at IS NULL', (bot,)).fetchone()
    signed_in = None
    computer = {'runner_id': runner['id'], 'label': runner['label']} if runner else None
    from . import providers
    from .shared_bots import follow
    row = c.execute('SELECT config_json FROM bot_config WHERE bot=?', (bot,)).fetchone()
    config = providers.fill(ctx['providers'], follow(c, bot, json.loads(row[0] or '{}') if row else {}))
    runtime = config.get('runtime')
    line = {'profile': None, 'source': 'computer', 'computer': computer}
    if runner:
        report = readiness_document(runner['readiness_json'])
        bot_report = report.get('bots', {}).get(bot, {})
        line['profile'] = bot_report.get('profile') or None
        if problem := login_upgrade(runner, report, bot=bot):
            return {**line, 'signed_in': None, 'problem': problem}, config
        if bot_report.get('data_problem'):
            # Signed in, but its turns can't save a session: the folder is what to fix, not a sign-in.
            return {**line, 'signed_in': None, 'problem': bot_report['data_problem'], 'detail': {'runtime': runtime}}, config
        state = bot_report.get('sign_in') or report.get('runtimes', {}).get(runtime, {}).get('authenticated')
        signed_in = True if state == 'ready' else False if state in ('missing', 'failed', 'rejected') else None
    return {**line, 'signed_in': signed_in, 'problem': ''}, config


def connections(c, runner_id, settings, ctx=None):
    """Each bot on a computer with the connection it actually runs on. The Computers pop-up and the bot editor both
    read bot_subscription, so they name a connection alike."""
    ctx = ctx if ctx is not None else context(c, settings)
    rows = []
    for row in c.execute("SELECT a.bot FROM assignments a JOIN bots b ON b.slug=a.bot "
                         "WHERE a.runner_id=? AND b.state<>'archived' ORDER BY a.bot", (runner_id,)):
        line = bot_subscription(c, row['bot'], settings, ctx)
        if line['profile'] and line['computer']:
            line.update(identity(c, line['computer']['runner_id'], line['profile']))
        line.pop('computer', None)
        rows.append({'bot': row['bot'], **line})
    return rows


def install(app, store, auth, mutate, settings, computer_rows):
    from .subscription_refresh import install as install_refresh
    install_refresh(app, store, auth, mutate)
    from . import subscription_identity
    subscription_identity.install(app, store, auth, mutate)
    @app.get('/api/v2/subscriptions')
    def subscriptions(request: Request):
        who = request.state.identity
        auth.domain(who)
        with store.read() as c:
            return listing(c, auth, who, computer_rows)

    @app.put('/api/v2/subscriptions/weekly')
    def subscription_weekly(request: Request, body: M.SubscriptionWeeklyEdit):
        who = request.state.identity
        def work(c):
            from .repositories import save_metadata
            auth.domain(who)
            runner = c.execute('SELECT * FROM runners WHERE id=? AND revoked_at IS NULL', (body.runner_id,)).fetchone()
            if not runner or not (who.role == 'human' or who.role == 'owner') or not (
                    auth.bot_admin(who) or runner['operator'] == H.actor_id(who.actor)):
                raise Problem('forbidden', 'Only a computer operator or administrator can record weekly usage', 403)
            row = c.execute('SELECT runtimes_json FROM computer_profiles WHERE runner_id=? AND profile=?',
                            (body.runner_id, body.profile)).fetchone()
            if not row or body.runtime not in json.loads(row[0] or '{}'):
                raise Problem('not_found', 'Subscription runtime not found on this computer', 404)
            value = {}
            if body.used_percent is not None or body.resets_at:
                value = weekly_snapshot({'used_percent': body.used_percent, 'resets_at': body.resets_at,
                                         'reported_at': H.now()})
                if value is None:
                    raise Problem('invalid', 'Choose a weekly reset in the next eight days with a timezone', 422)
            save_metadata(c, weekly_key(body.runner_id, body.profile, body.runtime), value)
            H.event(c, who.actor, 'subscription.weekly_recorded', body.profile, body.model_dump())
            return {'weekly': {**value, 'source': 'manual'} if value else None}
        return mutate(request, body, work)

    @app.get('/api/v2/bots/{bot}/subscription')
    def subscription(request: Request, bot: str):
        who = request.state.identity
        auth.domain(who)
        with store.read() as c:
            if not H.bot(c, bot):
                raise Problem('not_found', 'Bot not found', 404)
            auth.require_see(c, who, bot)
            result = bot_subscription(c, bot, settings)
            if result['profile'] and result['computer']:
                result.update(identity(c, result['computer']['runner_id'], result['profile']))
            return result

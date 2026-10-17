"""Which named subscription each bot runs on: its own choice, else its computer's own login."""
import json
from datetime import datetime, timedelta, timezone

from fastapi import Request

from . import hubdb as H, models as M
from .execution import readiness_document
from .subscription_identity import identity
from .store import Problem, encode


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


def same_account(own, profiles):
    """{runtime: profile}: the named subscription signed in to the same account as the computer's own login."""
    def email(state):
        return str(((state or {}).get('account') or {}).get('email') or '').strip().lower() if isinstance(state, dict) else ''
    out = {}
    for runtime, state in own.items():
        mine = isinstance(state, dict) and state.get('authenticated') == 'ready' and email(state)
        match = mine and next((p['name'] for p in profiles if email(p['runtimes'].get(runtime)) == mine), None)
        if match:
            out[runtime] = match
    return out


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
    # Group rows from before groups stopped choosing a subscription stay in the table and are never read.
    result = {'assignments': {r['target']: r['profile'] for r in c.execute(
        "SELECT target,profile FROM subscription_assignments WHERE scope='bot'")}}
    if settings is not None:
        from . import providers
        result['providers'] = providers.load(c, settings)
    return result


def effective(c, bot, ctx=None):
    """A bot runs on its own connection, else on its computer's default: that computer's own login for its tool."""
    ctx = ctx if ctx is not None else context(c)
    profile = ctx['assignments'].get(bot)
    return (profile, 'bot') if profile else (None, 'computer')


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
    access = auth.bot_accesses(c, who)
    assignments = [dict(row) for row in c.execute("SELECT * FROM subscription_assignments WHERE scope='bot' ORDER BY target")
                   if (access.get(row['target']) or {}).get('see')]
    return {'profiles_by_computer': computers, 'assignments': assignments}


def assign(c, auth, who, body):
    if not H.bot(c, body.target):
        raise Problem('not_found', 'Bot not found', 404)
    if not (auth.bot_admin(who) or (who.role == 'human' and c.execute(
            'SELECT 1 FROM assignments a JOIN runners r ON r.id=a.runner_id '
            'WHERE a.bot=? AND r.operator=? AND r.revoked_at IS NULL',
            (body.target, H.actor_id(who.actor))).fetchone())):
        raise Problem('forbidden', 'You cannot change this bot', 403)
    if body.profile is None:
        c.execute('DELETE FROM subscription_assignments WHERE scope=? AND target=?', (body.scope, body.target))
    else:
        c.execute('INSERT INTO subscription_assignments VALUES(?,?,?,?,?) ON CONFLICT(scope,target) DO UPDATE SET '
                  'profile=excluded.profile,updated=excluded.updated,updated_by=excluded.updated_by',
                  (body.scope, body.target, body.profile, H.now(), who.actor))
    H.event(c, who.actor, 'subscription.assigned', body.target, body.model_dump())
    return body.model_dump()


def bot_subscription(c, bot, settings, ctx=None):
    """The connection a bot runs on: {profile, source ('bot' or 'computer'), computer, runtime, harness, signed_in,
    problem}. With no profile it is its computer's own login for `runtime`."""
    ctx = ctx if ctx is not None else context(c, settings)
    line, config = _bot_line(c, bot, ctx)
    return {**line, 'runtime': config.get('runtime') or '', 'harness': config.get('harness') or ''}


def _bot_line(c, bot, ctx):
    """The connection without its tool, and the bot's filled-in configuration that names the tool."""
    profile, source = effective(c, bot, ctx)
    runner = c.execute('SELECT r.* FROM assignments a JOIN runners r ON r.id=a.runner_id WHERE a.bot=? '
                       'AND r.revoked_at IS NULL', (bot,)).fetchone()
    signed_in, problem = None, ''
    computer = {'runner_id': runner['id'], 'label': runner['label']} if runner else None
    from . import providers
    from .shared_bots import follow
    row = c.execute('SELECT config_json FROM bot_config WHERE bot=?', (bot,)).fetchone()
    config = providers.fill(ctx['providers'], follow(c, bot, json.loads(row[0] or '{}') if row else {}))
    runtime = config.get('runtime')
    line = {'profile': profile, 'source': source, 'computer': computer}
    if profile and not covers(config):
        return {**line, 'signed_in': False, 'problem': f"Subscription {profile} doesn't cover {runtime}",
                'detail': {'runtime': runtime}}, config
    if runner:
        report = readiness_document(runner['readiness_json'])
        bot_report = report.get('bots', {}).get(bot, {})
        if source == 'computer':
            line['profile'] = bot_report.get('profile') or None
            state = bot_report.get('sign_in') or report.get('runtimes', {}).get(runtime, {}).get('authenticated')
            signed_in = True if state == 'ready' else False if state in ('missing', 'failed', 'rejected') else None
        else:
            blocked = refusal(c, runner['id'], bot)
            if (blocked.get('profile') == profile and blocked.get('primary_runtime') == runtime
                  and blocked.get('primary_harness') == config.get('harness')):
                return {**line, 'signed_in': False, 'problem': blocked['problem'],
                        'detail': {'runtime': blocked.get('runtime', runtime)}}, config
            from .repositories import metadata
            if not metadata(c, 'computer-profiles:' + runner['id']).get('reported'):
                return {**line, 'signed_in': None, 'problem': f'Update {runner["label"]} to use subscriptions'}, config
            row = c.execute('SELECT runtimes_json FROM computer_profiles WHERE runner_id=? AND profile=?',
                            (runner['id'], profile)).fetchone()
            if row:
                signed_in = json.loads(row[0] or '{}').get(runtime, {}).get('signed_in')
                if signed_in is False:
                    problem = f"Subscription {profile} isn't signed in on {runner['label']}"
            else:
                signed_in = False
                problem = f"Subscription {profile} isn't on {runner['label']}"
    return {**line, 'signed_in': signed_in, 'problem': problem,
            **({'detail': {'runtime': runtime}} if problem else {})}, config


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

    @app.put('/api/v2/subscriptions')
    def subscription_assignment(request: Request, body: M.SubscriptionAssignment):
        who = request.state.identity
        def work(c):
            auth.domain(who)
            return assign(c, auth, who, body)
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

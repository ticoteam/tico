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
    ready = readiness_document(runner['readiness']) if ready is None else ready
    return (COMPUTER_LOGIN_CAPABILITY in (H._json(runner['capabilities'], []) or [])
            and isinstance(ready.get('computer_profile'), str))


def login_upgrade(runner, ready=None, bot=None, runtime=None):
    """An older runner's named bot login may be a local override; never relabel it or silently change its account."""
    ready = readiness_document(runner['readiness']) if ready is None else ready
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


def subscription_meta(c):
    """{key: dict} for every subscription* registry row in one pass, once per read snapshot (Operations profiles and
    renamed logins). A key range, not LIKE, so the primary key is used. Callers only read it."""
    from . import read_cache
    values = read_cache.cache(c)
    if values is not None and "subscriptions.meta" in values:
        return values["subscriptions.meta"]
    out = {}
    for row in c.execute("SELECT key, value FROM registry_metadata WHERE key>='subscription' "
                         "AND key<'subscriptioo'"):
        try:
            value = json.loads(row['value'] or '{}')
        except ValueError:
            value = {}
        out[row['key']] = value if isinstance(value, dict) else {}
    if values is not None:
        values["subscriptions.meta"] = out
    return out


def reported_runtimes(c, runner, profile, raw, meta=None):
    from .repositories import metadata
    from . import subscription_refresh as refresh

    def lookup(key):
        if meta is not None:
            return meta.get(key) or {}
        return metadata(c, key)

    runtimes = json.loads(raw or '{}')
    cutoff = lookup(refresh.signin_key(runner, profile))
    for runtime, state in runtimes.items():
        reported = weekly_snapshot(state.get('weekly'))
        manual = weekly_snapshot(lookup(weekly_key(runner, profile, runtime)))
        refreshed = lookup(refresh.key(runner, profile, runtime))
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
    return who.role in ('human', 'owner') and (auth.bot_admin(who) or runner['owner_id'] == H.actor_id(who.actor))


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
        runtimes = reported_runtimes(c, runner['id'], row['profile'], row['runtimes'])
        rows.append({'name': row['profile'], **identity(c, runner['id'], row['profile']),
                     'runtimes': runtimes if accounts else without_accounts(runtimes)})
    return rows


def profiles_by_runner(c):
    """{runner_id: computer_profiles(... accounts=True)} for every computer in one pass (Operations listing).

    Loads computer_profiles once, shares subscription* registry metadata, and resolves display names from that map.
    """
    from .subscription_identity import connection_id
    meta = subscription_meta(c)
    out = {}
    for row in c.execute('SELECT * FROM computer_profiles ORDER BY profile'):
        rid, profile = row['runner_id'], row['profile']
        sid = connection_id(rid, profile)
        named = meta.get(sid) or {}
        runtimes = reported_runtimes(c, rid, profile, row['runtimes'], meta=meta)
        out.setdefault(rid, []).append({
            'name': profile, 'id': sid, 'display_name': named.get('display_name') or profile,
            'runtimes': runtimes})
    return out


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
        previous = c.execute('SELECT runtimes FROM computer_profiles WHERE runner_id=? AND profile=?',
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
    stored = {r['profile']: r['runtimes'] for r in c.execute(
        'SELECT profile,runtimes FROM computer_profiles WHERE runner_id=?', (runner_id,))}
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
        operator = c.execute('SELECT owner_id FROM runners WHERE id=?', (runner['id'],)).fetchone()
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
    computer = {'runner_id': runner['id'], 'label': runner['label']} if runner else None
    from . import providers
    from .shared_bots import follow
    row = c.execute('SELECT config FROM bot_config WHERE bot=?', (bot,)).fetchone()
    config = providers.fill(ctx['providers'], follow(c, bot, json.loads(row[0] or '{}') if row else {}))
    line = {'profile': None, 'source': 'computer', 'computer': computer}
    if not runner:
        return {**line, 'signed_in': None, 'problem': ''}, config
    return {**line, **_sign_in(runner, readiness_document(runner['readiness']), bot, config)}, config


def _sign_in(runner, report, bot, config):
    """A placed bot's login on its computer: {profile, signed_in, problem} plus `detail` or `warning` when they apply.
    Shared by the one-bot line and the Operations batch so the two name a connection alike."""
    runtime = config.get('runtime')
    bot_report = report.get('bots', {}).get(bot, {})
    out = {'profile': bot_report.get('profile') or None}
    if problem := login_upgrade(runner, report, bot=bot):
        return {**out, 'signed_in': None, 'problem': problem}
    if bot_report.get('data_problem'):
        # Signed in, but its turns can't save a session: the folder is what to fix, not a sign-in.
        return {**out, 'signed_in': None, 'problem': bot_report['data_problem'], 'detail': {'runtime': runtime}}
    state = bot_report.get('sign_in') or report.get('runtimes', {}).get(runtime, {}).get('authenticated')
    signed_in = True if state == 'ready' else False if state in ('missing', 'failed', 'rejected') else None
    from .readiness import login_warning
    if warning := login_warning(report, runtime, config.get('harness') or ''):
        out['warning'] = warning
    return {**out, 'signed_in': signed_in, 'problem': ''}


def connections(c, runner_id, settings, ctx=None):
    """Each bot on a computer with the connection it actually runs on. The Computers pop-up and the bot editor both
    read bot_subscription, so they name a connection alike."""
    ctx = ctx if ctx is not None else context(c, settings)
    rows = []
    for row in c.execute("SELECT a.bot FROM assignments a JOIN bots b ON b.id=a.bot "
                         "WHERE a.runner_id=? AND b.state<>'archived' ORDER BY a.bot", (runner_id,)):
        line = bot_subscription(c, row['bot'], settings, ctx)
        if line['profile'] and line['computer']:
            line.update(identity(c, line['computer']['runner_id'], line['profile']))
        line.pop('computer', None)
        rows.append({'bot': row['bot'], **line})
    return rows


def connections_by_runner(c, settings, ctx=None):
    """{runner_id: connections(...)} for every computer. One assignment/config pass instead of per-bot lookups
    (Operations listing). Identity display names are also read once for every profile that appears."""
    from . import providers
    from .read_cache import configs as load_configs
    from .shared_bots import follow, source_of
    from .subscription_identity import connection_id

    ctx = ctx if ctx is not None else context(c, settings)
    placed, runners = {}, {}
    for row in c.execute("SELECT a.runner_id, a.bot, r.id, r.label, r.readiness, r.capabilities, "
                         "r.revoked_at FROM assignments a JOIN bots b ON b.id=a.bot "
                         "JOIN runners r ON r.id=a.runner_id "
                         "WHERE b.state<>'archived' ORDER BY a.bot"):
        placed.setdefault(row["runner_id"], []).append(row["bot"])
        runners.setdefault(row["runner_id"], row)
    bots = [bot for group in placed.values() for bot in group]
    config_rows = {row["bot"]: row for row in load_configs(c, bots)} if bots else {}
    # A branch follows its original's config, and the original need not be placed anywhere.
    sources = {source_of(json.loads(row["config"] or "{}")) for row in config_rows.values()} - {""}
    if missing := sorted(sources - set(config_rows)):
        config_rows.update({row["bot"]: row for row in load_configs(c, missing)})

    def rows_of(slug):
        return config_rows.get(slug)

    # First pass: build lines without identity, collect connection ids that need a display name.
    built, needed = {}, set()
    for rid, bot_list in placed.items():
        runner = runners[rid]
        # A revoked computer is no bot's connection: its bots are listed as on none, as bot_subscription does.
        computer = None if runner["revoked_at"] else {"runner_id": runner["id"], "label": runner["label"]}
        report = readiness_document(runner["readiness"])
        items = []
        for bot in bot_list:
            row = config_rows.get(bot)
            config = providers.fill(ctx["providers"], follow(
                c, bot, json.loads(row["config"] or "{}") if row else {}, rows=rows_of))
            runtime = config.get("runtime")
            line = {"profile": None, "source": "computer", "computer": computer,
                    "runtime": runtime or "", "harness": config.get("harness") or ""}
            if computer is None:
                line.update(computer=None, signed_in=None, problem="")
                items.append((bot, line))
                continue
            line.update(_sign_in(runner, report, bot, config))
            if line["profile"]:
                needed.add(connection_id(rid, line["profile"]))
            items.append((bot, line))
        built[rid] = items

    names = {}
    if needed:
        for start in range(0, len(needed), 500):
            part = list(needed)[start:start + 500]
            for row in c.execute("SELECT key, value FROM registry_metadata WHERE key IN ("
                                 + ",".join("?" * len(part)) + ")", part):
                try:
                    value = json.loads(row["value"] or "{}")
                except ValueError:
                    value = {}
                if isinstance(value, dict) and value.get("display_name"):
                    names[row["key"]] = value["display_name"]

    out = {}
    for rid, items in built.items():
        rows = []
        for bot, line in items:
            profile = line.get("profile")
            if profile:
                sid = connection_id(rid, profile)
                line["id"] = sid
                line["display_name"] = names.get(sid) or profile
            line.pop("computer", None)
            rows.append({"bot": bot, **line})
        out[rid] = rows
    return out


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
                    auth.bot_admin(who) or runner['owner_id'] == H.actor_id(who.actor)):
                raise Problem('forbidden', 'Only a computer operator or administrator can record weekly usage', 403)
            row = c.execute('SELECT runtimes FROM computer_profiles WHERE runner_id=? AND profile=?',
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
            bot = (H.resolve_bot(c, bot) or {}).get('id', bot)
            if not H.bot(c, bot):
                raise Problem('not_found', 'Bot not found', 404)
            auth.require_see(c, who, bot)
            result = bot_subscription(c, bot, settings)
            if result['profile'] and result['computer']:
                result.update(identity(c, result['computer']['runner_id'], result['profile']))
            return result

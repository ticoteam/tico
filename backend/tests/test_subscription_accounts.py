"""The signed-in account and usage windows a computer reports per AI tool (#610): kept only in the allowed shape,
never anything token-like, and readable only by an administrator or the computer's operator."""
import json
from datetime import datetime, timedelta, timezone

from backend.tests.test_api import api, assign, claim, get, post, ready, restrict, runner  # noqa: F401
from backend.tests.test_bot_access import world  # noqa: F401  (a Legal bot and who may see it)

NOW = datetime.now(timezone.utc)
ACCOUNT = {'email': 'ops@acme.example', 'plan': 'max', 'org': 'Acme Ltd'}


def iso(delta=timedelta()):
    return (NOW + delta).isoformat()


def beat(api, r, runtime_account=ACCOUNT, profile_account=None, five_hour=None, expected=200, own=None):
    codex = {'signed_in': True, 'account': profile_account or {'email': 'dev@acme.example', 'plan': 'pro', 'kind': 'subscription'},
             'weekly': {'used_percent': 38, 'resets_at': iso(timedelta(days=2)), 'reported_at': iso()}}
    if five_hour is not None:
        codex['five_hour'] = five_hour
    body = {'version': 'test', 'platform': 'test', 'readiness': {
                'schema_version': 1, 'bots': {},
                'runtimes': {'claude': {'installed': True, 'authenticated': 'ready', 'account': runtime_account, **(own or {})}}},
            'profiles': [{'name': 'acme', 'runtimes': {'codex': codex}},
                         {'name': 'beta', 'runtimes': {'codex': {'signed_in': False, 'account': {'email': 'old@acme.example'}}}}]}
    return post(api, 'runners/heartbeat', body, token=r['token'], expected=expected)


def machine(api, r, token='ana-test'):
    return next(m for m in get(api, 'operations', token)['computers'] if m['id'] == r['runner_id'])


def test_operator_and_admin_see_each_profiles_account_and_windows(api):
    r = runner(api, operator='cara')
    reply = beat(api, r, five_hour={'used_percent': 9, 'resets_at': iso(timedelta(hours=3)), 'reported_at': iso()})
    # The runner sends accounts only once the server says it takes them.
    assert reply['subscription_accounts'] is True
    for token in ('cara-test', 'ana-test', 'ben-test'):
        m = machine(api, r, token)
        assert m['readiness']['runtimes']['claude']['account'] == ACCOUNT
        profiles = {p['name']: p for p in m['profiles']}
        assert list(profiles) == ['acme', 'beta']
        codex = profiles['acme']['runtimes']['codex']
        assert codex['account'] == {'email': 'dev@acme.example', 'plan': 'pro', 'kind': 'subscription'}
        assert codex['weekly']['used_percent'] == 38 and codex['weekly']['source'] == 'provider'
        assert codex['five_hour']['used_percent'] == 9
        # A signed-out login names no account.
        assert 'account' not in profiles['beta']['runtimes']['codex']
        listed = get(api, 'subscriptions', token)['profiles_by_computer'][0]['profiles'][0]
        assert listed['runtimes']['codex']['account']['email'] == 'dev@acme.example'


def test_other_members_and_bots_never_see_an_account(api):
    r = runner(api, operator='ana')
    assign(api, r, 'ops')
    ready(api, r, ['ops'])
    beat(api, r, own={'weekly': {'used_percent': 20, 'resets_at': iso(timedelta(days=1)), 'reported_at': iso()}})
    with api.app.state.store.transaction() as c:
        c.execute('UPDATE runners SET accepts_member_bots=1 WHERE id=?', (r['runner_id'],))
    # Cara sees the computer on Computers, as every member does, but it is not hers: no accounts, no usage of its own
    # login, and nothing to change.
    seen = machine(api, r, 'cara-test')
    assert seen['may_manage'] is False and seen['bots'] == ['ops']
    own = seen['readiness']['runtimes']['claude']
    assert own['authenticated'] == 'ready' and not {'account', 'weekly', 'five_hour'} & set(own)
    assert machine(api, r, 'ana-test')['readiness']['runtimes']['claude']['weekly']['used_percent'] == 20
    assert all('account' not in state for p in seen['profiles'] for state in p['runtimes'].values())
    assert {p['name']: p['runtimes']['codex']['signed_in'] for p in seen['profiles']} == {'acme': True, 'beta': False}
    assert machine(api, r, 'ana-test')['may_manage'] is True
    computer = next(c for c in get(api, 'computers', 'cara-test')['computers'] if c['id'] == r['runner_id'])
    assert 'account' not in computer['readiness']['runtimes']['claude']
    assert computer['readiness']['runtimes']['claude']['authenticated'] == 'ready'
    listed = get(api, 'subscriptions', 'cara-test')['profiles_by_computer'][0]['profiles'][0]
    assert 'account' not in listed['runtimes']['codex'] and listed['runtimes']['codex']['signed_in'] is True
    # The owner still does.
    owner = next(c for c in get(api, 'computers', 'ana-test')['computers'] if c['id'] == r['runner_id'])
    assert owner['readiness']['runtimes']['claude']['account'] == ACCOUNT
    # A bot on the computer reads its subscriptions without accounts (the profile report is kept).
    ready(api, r, ['ops'])
    post(api, 'chat/ops', {'text': 'Inspect subscriptions'})
    token = claim(api, r)['token']
    rows = get(api, 'subscriptions', token)['profiles_by_computer']
    assert rows and rows[0]['profiles'][0]['runtimes']['codex']['signed_in'] is True
    assert all('account' not in state for row in rows for p in row['profiles'] for state in p['runtimes'].values())


def test_nothing_token_like_is_stored_or_shown(api):
    r = runner(api, operator='cara')
    secrets = ['sk-ant-oat01-' + 'a' * 40, 'eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.sig', 'ghp_' + 'b' * 36]
    for secret in secrets:
        for field in ('email', 'plan', 'org'):
            beat(api, r, runtime_account={**ACCOUNT, field: secret},
                 profile_account={'email': 'dev@acme.example', field: secret})
            with api.app.state.store.read() as c:
                stored = json.dumps([list(row) for row in c.execute('SELECT readiness FROM runners')]
                                    + [list(row) for row in c.execute('SELECT runtimes FROM computer_profiles')])
            assert secret not in stored
            shown = json.dumps(get(api, 'operations', 'cara-test')) + json.dumps(get(api, 'subscriptions', 'cara-test'))
            assert secret not in shown
            # The sign-in itself still counts: only the account is left out.
            m = machine(api, r, 'cara-test')
            assert m['readiness']['runtimes']['claude']['authenticated'] == 'ready'
            assert 'account' not in m['readiness']['runtimes']['claude']
            assert m['profiles'][0]['runtimes']['codex']['signed_in'] is True
    # Fields outside the allow-list are refused, not stored.
    beat(api, r, runtime_account={**ACCOUNT, 'token': 'x'})
    assert 'account' not in machine(api, r, 'cara-test')['readiness']['runtimes']['claude']


def test_an_older_runner_reads_as_today(api):
    r = runner(api, operator='cara')
    post(api, 'runners/heartbeat', {'version': 'test', 'platform': 'test', 'readiness': {
        'schema_version': 1, 'bots': {}, 'runtimes': {'claude': {'installed': True, 'authenticated': 'ready'}}},
        'profiles': [{'name': 'acme', 'runtimes': {'codex': {'signed_in': True}}}]}, token=r['token'])
    m = machine(api, r, 'cara-test')
    assert 'account' not in m['readiness']['runtimes']['claude']
    codex = m['profiles'][0]['runtimes']['codex']
    assert codex['signed_in'] is True and codex['weekly'] is None and codex['five_hour'] is None and 'account' not in codex


def test_a_window_longer_than_its_own_reads_unknown_never_a_value(api):
    r = runner(api, operator='cara')
    # A 5-hour reading whose reset is a week out passes the weekly rule but is no 5-hour window.
    for reset in (timedelta(hours=5, minutes=11), timedelta(days=7), timedelta(days=30)):
        beat(api, r, five_hour={'used_percent': 9, 'resets_at': iso(reset), 'reported_at': iso()})
        codex = machine(api, r, 'cara-test')['profiles'][0]['runtimes']['codex']
        assert codex['five_hour'] is None, reset
        # The sign-in, account and weekly reading in the same report stay.
        assert codex['signed_in'] is True and codex['account']['email'] == 'dev@acme.example'
        assert codex['weekly']['used_percent'] == 38
    beat(api, r, five_hour={'used_percent': 9, 'resets_at': iso(timedelta(hours=5, minutes=9)), 'reported_at': iso()})
    assert machine(api, r, 'cara-test')['profiles'][0]['runtimes']['codex']['five_hour']['used_percent'] == 9


def own_windows(weekly=None, five_hour=None):
    return {'weekly': weekly or {'used_percent': 17, 'resets_at': iso(timedelta(days=3)), 'reported_at': iso(), 'status': None},
            'five_hour': five_hour or {'used_percent': 53, 'resets_at': iso(timedelta(hours=3)), 'reported_at': iso(), 'status': 'allowed'}}


def test_the_own_logins_windows_go_to_whoever_sees_its_account(api):
    r = runner(api, operator='ana')
    assign(api, r, 'ops')
    beat(api, r, own=own_windows())
    with api.app.state.store.transaction() as c:
        c.execute('UPDATE runners SET accepts_member_bots=1 WHERE id=?', (r['runner_id'],))
    claude = machine(api, r, 'ana-test')['readiness']['runtimes']['claude']
    assert (claude['five_hour']['used_percent'], claude['weekly']['used_percent']) == (53, 17)
    assert claude['five_hour']['status'] == 'allowed' and claude['account'] == ACCOUNT
    owner = next(c for c in get(api, 'computers', 'ana-test')['computers'] if c['id'] == r['runner_id'])
    assert owner['readiness']['runtimes']['claude']['weekly']['used_percent'] == 17
    # Cara sees the computer, not its own login's account, so not its usage either.
    other = next(c for c in get(api, 'computers', 'cara-test')['computers'] if c['id'] == r['runner_id'])
    assert not {'account', 'weekly', 'five_hour'} & set(other['readiness']['runtimes']['claude'])
    assert other['readiness']['runtimes']['claude']['authenticated'] == 'ready'


def test_an_own_window_that_fails_its_checks_reads_unknown_and_the_heartbeat_still_counts(api):
    r = runner(api, operator='cara')
    good = own_windows()
    bad = [{'five_hour': {**good['five_hour'], 'resets_at': iso(timedelta(hours=5, minutes=11))}},
           {'weekly': {**good['weekly'], 'resets_at': iso(timedelta(days=8, minutes=1))}},
           {'weekly': {**good['weekly'], 'used_percent': 150}},
           {'five_hour': {**good['five_hour'], 'reported_at': iso(timedelta(hours=1))}},
           {'weekly': {**good['weekly'], 'reported_at': 'yesterday'}},
           {'five_hour': {**good['five_hour'], 'token': 'sk-ant-oat01-' + 'a' * 40}},
           {'weekly': 'full'}]
    for window in bad:
        beat(api, r, own={**good, **window})
        claude = machine(api, r, 'cara-test')['readiness']['runtimes']['claude']
        (name,) = window
        assert name not in claude, window
        other = 'weekly' if name == 'five_hour' else 'five_hour'
        assert claude[other]['used_percent'] == good[other]['used_percent'] and claude['account'] == ACCOUNT
    # A reading with no percentage stays, as unknown: never 0 or 100.
    beat(api, r, own=own_windows(five_hour={'used_percent': None, 'resets_at': iso(timedelta(hours=5, minutes=9)), 'reported_at': iso()}))
    claude = machine(api, r, 'cara-test')['readiness']['runtimes']['claude']
    assert claude['five_hour']['used_percent'] is None and claude['weekly']['used_percent'] == 17
    with api.app.state.store.read() as c:
        assert 'sk-ant-oat01' not in json.dumps([list(row) for row in c.execute('SELECT readiness FROM runners')])


def test_a_bot_a_member_may_not_see_is_left_off_another_persons_computer(world):
    # Ops is seen by everyone; Finance, once placed, only by Ben. The computer then goes to Cara, who runs it; Dee is
    # a member with no say on it.
    api, tokens, _ = world
    r = runner(api)
    for slug in ('finance', 'ops'):
        assign(api, r, slug)
    with api.app.state.store.transaction() as c:
        restrict(c, 'finance', people=['ben'])
        c.execute("UPDATE runners SET operator='cara' WHERE id=?", (r['runner_id'],))
    post(api, 'runners/heartbeat', {'version': 'test', 'platform': 'test', 'readiness': {'schema_version': 1, 'runtimes': {},
         'bots': {slug: {'ready': True, 'runtime': 'fake'} for slug in ('finance', 'ops')}}}, token=r['token'])

    def bots(token):
        m = machine(api, r, token)
        return m['bots'], sorted(m['readiness']['bots']), sorted(line['bot'] for line in m['bot_connections'])
    assert bots(tokens['dee']) == (['ops'], ['ops'], ['ops'])
    assert 'finance' not in json.dumps(machine(api, r, tokens['dee']))
    both = (['finance', 'ops'], ['finance', 'ops'], ['finance', 'ops'])
    assert bots(tokens['ana']) == both, 'an administrator'
    assert bots(tokens['cara']) == both, "the computer's operator"

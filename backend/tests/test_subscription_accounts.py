"""The signed-in account and usage windows a computer reports per AI tool (#610): kept only in the allowed shape,
never anything token-like, and readable only by an administrator or the computer's operator."""
import json
from datetime import datetime, timedelta, timezone

from backend.tests.test_api import api, assign, claim, get, post, ready, runner  # noqa: F401

NOW = datetime.now(timezone.utc)
ACCOUNT = {'email': 'ops@acme.example', 'plan': 'max', 'org': 'Acme Ltd'}


def iso(delta=timedelta()):
    return (NOW + delta).isoformat()


def beat(api, r, runtime_account=ACCOUNT, profile_account=None, five_hour=None, expected=200):
    codex = {'signed_in': True, 'account': profile_account or {'email': 'dev@acme.example', 'plan': 'pro', 'kind': 'subscription'},
             'weekly': {'used_percent': 38, 'resets_at': iso(timedelta(days=2)), 'reported_at': iso()}}
    if five_hour is not None:
        codex['five_hour'] = five_hour
    body = {'version': 'test', 'platform': 'test', 'readiness': {
                'schema_version': 1, 'bots': {},
                'runtimes': {'claude': {'installed': True, 'authenticated': 'ready', 'account': runtime_account}}},
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
    beat(api, r)
    with api.app.state.store.transaction() as c:
        c.execute('UPDATE runners SET accepts_member_bots=1 WHERE id=?', (r['runner_id'],))
    # Cara can see the computer (it takes members' bots) but it is not hers.
    assert all(m['id'] != r['runner_id'] for m in get(api, 'operations', 'cara-test')['computers'])
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
                stored = json.dumps([list(row) for row in c.execute('SELECT readiness_json FROM runners')]
                                    + [list(row) for row in c.execute('SELECT runtimes_json FROM computer_profiles')])
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

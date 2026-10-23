"""A computer login cannot silently turn subscription-dependent bots into API-billed work."""
import json

from backend import readiness
from backend.store import H
from backend.tests.test_api import api, assign, get, post, runner  # noqa: F401
from backend.tests.test_subscriptions import set_runtime


def beat(api, machine, *, codex='ready', kind='api_key'):
    # A retained named home is the computer default, not a second bot login. A dormant home must not decide it.
    state = {'installed': True, 'authenticated': codex,
             'account': {'kind': kind, 'email': 'billing@acme.example'},
             'profiles': {'work': {'installed': True, 'authenticated': codex},
                          'dormant': {'installed': True, 'authenticated': 'missing'}}}
    return post(api, 'runners/heartbeat', {'version': 'test', 'platform': 'test',
        'capabilities': ['computer_logins_v1'], 'readiness': {'schema_version': 1, 'computer_profile': 'work',
        'runtimes': {'codex': state, 'claude': {'installed': True, 'authenticated': 'ready'}},
        'bots': {'ops': {'runtime': 'codex', 'ready': codex == 'ready', 'sign_in': codex,
                         'profile': 'work'}}}}, machine['token'])


def checks(api, token='ana-test'):
    return {row['id']: row for row in get(api, 'health', token)['checks']}


def test_api_login_warns_across_readiness_connections_and_health_without_inventing_credit(api):
    machine = runner(api, label='Acme workstation')
    assign(api, machine, 'ops')
    set_runtime(api, 'codex')
    beat(api, machine)
    with api.app.state.store.read() as c:
        result = readiness.can_run(c, 'ops', 'codex')
    assert result['can_run'] is True  # Paid API work remains allowed; a missing plan is not an exhausted API balance.
    warning = result['warning']
    assert 'API credit is not checked' in warning
    assert get(api, 'bots/ops/subscription')['warning'] == warning
    computer = next(m for m in get(api, 'operations')['machines'] if m['id'] == machine['runner_id'])
    assert computer['bot_connections'][0]['warning'] == warning
    assert checks(api)['computer_logins']['status'] == 'warn'
    member = checks(api, 'cara-test')['computer_logins']
    assert member['status'] == 'warn' and member['fixes'] == []
    assert 'Acme workstation' not in member['summary'] and '@' not in member['summary']
    # Switching the physical default to a subscription clears the warning despite a dormant missing login.
    beat(api, machine, kind='subscription')
    assert 'warning' not in get(api, 'bots/ops/subscription')
    assert 'computer_logins' not in checks(api)


def test_health_flags_an_unusable_fallback_before_work_is_queued_and_ignores_inactive_bots(api):
    machine = runner(api)
    assign(api, machine, 'ops')
    set_runtime(api, 'claude')
    with api.app.state.store.transaction() as c:
        config = json.loads(c.execute("SELECT config_json FROM bot_config WHERE bot='ops'").fetchone()[0])
        config['fallback'] = {'harness': 'codex', 'model': 'gpt-5-codex'}
        c.execute("UPDATE bot_config SET config_json=? WHERE bot='ops'", (json.dumps(config),))
        c.execute("INSERT INTO subscription_assignments VALUES('bot','ops','old-plan',NULL,NULL)")
        H._apply(c, H.ONE_LOGIN_PER_COMPUTER_SCHEMA)
        assert not c.execute("SELECT 1 FROM subscription_assignments WHERE scope='bot'").fetchone()
    beat(api, machine, codex='failed')
    issue = checks(api)['computer_logins']
    assert issue['status'] == 'bad' and 'Codex' in issue['summary'] and 'unavailable' in issue['summary']
    with api.app.state.store.transaction() as c:
        assert not c.execute('SELECT 1 FROM jobs').fetchone()
        c.execute("UPDATE bots SET state='archived' WHERE slug='ops'")
    assert 'computer_logins' not in checks(api)


def test_warning_requires_evidence_of_a_computer_api_login_and_a_capable_harness():
    document = {'runtimes': {'codex': {'authenticated': 'ready'}}}
    assert not readiness.login_warning(document, 'codex')  # Unknown account is not evidence of API billing.
    document['runtimes']['codex']['detail'] = 'Signed in with an API key'
    assert readiness.login_warning(document, 'codex')  # Older runner, before account metadata arrives.
    document['runtimes']['gemini'] = {'authenticated': 'ready', 'key': {'name': 'GEMINI_API_KEY'}}
    assert not readiness.login_warning(document, 'gemini', 'gemini')  # Per-bot credentials are not this login.

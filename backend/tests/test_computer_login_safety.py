"""A computer login cannot silently turn subscription-dependent bots into API-billed work."""
import json

import pytest

from backend import readiness
from backend.store import H
from backend.tests.test_api import api, assign, get, post, runner  # noqa: F401
from backend.tests.test_subscriptions import set_runtime
from backend.tests.test_openapi_v2 import conforms


def beat(api, machine, *, codex='ready', kind='api_key', selected=None):
    selected = selected if selected is not None else codex
    # A retained named home is the computer default, not a second bot login. A dormant home must not decide it.
    state = {'installed': True, 'authenticated': codex,
             'account': {'kind': kind, 'email': 'billing@acme.example'},
             'profiles': {'work': {'installed': True, 'authenticated': selected},
                          'dormant': {'installed': True, 'authenticated': 'missing'}}}
    return post(api, 'runners/heartbeat', {'version': 'test', 'platform': 'test',
        'capabilities': ['computer_logins_v1'], 'readiness': {'schema_version': 1, 'computer_profile': 'work',
        'runtimes': {'codex': state, 'claude': {'installed': True, 'authenticated': 'ready'}},
        'bots': {'ops': {'runtime': 'codex', 'ready': selected == 'ready', 'sign_in': selected,
                         'profile': 'work'}}}}, machine['token'])


def checks(api, token='ana-test'):
    return {row['id']: row for row in get(api, 'health', token)['checks']}


def test_api_login_warns_across_readiness_connections_and_health_without_inventing_credit(api):
    machine = runner(api, label='Acme workstation')
    assign(api, machine, 'ops')
    set_runtime(api, 'codex')
    beat(api, machine)
    document = get(api, 'openapi.json')
    declared = document['components']['schemas']['BotSubscription']
    # Limit this regression to the additive warning; legacy identity fields predate this change.
    warning_contract = {'type': 'object', 'properties': {'warning': declared['properties']['warning']},
                        'required': [key for key in declared['required'] if key == 'warning']}
    with api.app.state.store.read() as c:
        result = readiness.can_run(c, 'ops', 'codex')
    assert result['can_run'] is True  # Paid API work remains allowed; a missing plan is not an exhausted API balance.
    warning = result['warning']
    assert 'API credit is not checked' in warning
    connection = get(api, 'bots/ops/subscription')
    assert connection['warning'] == warning
    assert conforms(connection, warning_contract, document) is None
    computer = next(m for m in get(api, 'operations')['machines'] if m['id'] == machine['runner_id'])
    assert computer['bot_connections'][0]['warning'] == warning
    assert checks(api)['computer_logins']['status'] == 'warn'
    member = checks(api, 'cara-test')['computer_logins']
    assert member['status'] == 'warn' and member['fixes'] == []
    assert 'Acme workstation' not in member['summary'] and '@' not in member['summary']
    # Switching the physical default to a subscription clears the warning despite a dormant missing login.
    beat(api, machine, kind='subscription')
    connection = get(api, 'bots/ops/subscription')
    assert 'warning' not in connection
    assert conforms(connection, warning_contract, document) is None
    assert 'computer_logins' not in checks(api)


def test_health_flags_an_unusable_fallback_before_work_is_queued_and_ignores_inactive_bots(api):
    machine = runner(api)
    assign(api, machine, 'ops')
    set_runtime(api, 'claude')
    with api.app.state.store.transaction() as c:
        config = json.loads(c.execute("SELECT config FROM bot_config WHERE bot='ops'").fetchone()[0])
        config['fallback'] = {'harness': 'codex', 'model': 'gpt-5-codex'}
        c.execute("UPDATE bot_config SET config=? WHERE bot='ops'", (json.dumps(config),))
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


def test_health_preserves_folder_failure_reason_without_exposing_it_to_members(api):
    machine = runner(api, label='Acme workstation')
    assign(api, machine, 'ops')
    set_runtime(api, 'musecode')
    reason = "Muse Code can't save sessions: the bot user can't write /srv/acme/muse/sessions (read-only)"
    row = {'installed': True, 'authenticated': 'failed', 'detail': reason}
    # Older runners report the folder in detail; newer ones also send data_problem.
    for explicit_problem in (False, True):
        if explicit_problem:
            row['data_problem'] = reason
            row['detail'] = 'Sign-in check failed'
        post(api, 'runners/heartbeat', {'version': 'test', 'platform': 'test',
            'capabilities': ['computer_logins_v1'], 'readiness': {'schema_version': 1,
            'runtimes': {'musecode': row}}}, machine['token'])
        issue = checks(api)['computer_logins']
        assert issue['status'] == 'bad' and reason in issue['summary']
        assert 'Sign in or switch account' not in issue['summary']
        member = checks(api, 'cara-test')['computer_logins']
        assert member['status'] == 'bad' and member['fixes'] == []
        assert '/srv/acme' not in member['summary'] and 'Acme workstation' not in member['summary']
    # A normal signed-out report includes detail too, but its fix is to sign in, not inspect a folder.
    post(api, 'runners/heartbeat', {'version': 'test', 'platform': 'test',
        'capabilities': ['computer_logins_v1'], 'readiness': {'schema_version': 1,
        'runtimes': {'musecode': {'installed': True, 'authenticated': 'missing',
                                'detail': 'Muse Code login required'}}}}, machine['token'])
    summary = checks(api)['computer_logins']['summary']
    assert 'Sign in or switch account in Settings > Computers' in summary
    assert reason not in summary and '/srv/acme' not in summary


@pytest.mark.parametrize(('aggregate', 'selected'), [('missing', 'ready'), ('failed', 'ready'),
                                                    ('ready', 'missing'), ('ready', 'failed')])
def test_retained_default_overrides_conflicting_aggregate_but_keeps_account_metadata(api, aggregate, selected):
    machine = runner(api)
    assign(api, machine, 'ops')
    set_runtime(api, 'codex')
    beat(api, machine, codex=aggregate, selected=selected)
    with api.app.state.store.read() as c:
        result = readiness.can_run(c, 'ops', 'codex')
    ready = selected == 'ready'
    assert result['can_run'] is ready
    # Only the runtime row has API-account metadata; selecting a home must retain it.
    assert bool(result.get('warning')) is ready
    assert bool(get(api, 'bots/ops/subscription').get('warning')) is ready
    assert checks(api)['computer_logins']['status'] == ('warn' if ready else 'bad')

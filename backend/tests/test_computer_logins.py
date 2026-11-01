"""One computer login keeps its physical hold identity and crosses mixed versions without changing accounts."""
from backend import execution, login_holds
from backend.store import H
from backend.tests.test_api import api, assign, claim, get, post, runner  # noqa: F401
from backend.tests.test_login_holds import at, limit
from backend.tests.test_model_logout import report
from backend.tests.test_subscriptions import set_runtime


def heartbeat(api, machine, *, current, signed=True):
    state = {'installed': True, 'authenticated': 'ready' if signed else 'missing'}
    body = {'version': 'test', 'platform': 'test', 'capabilities': ['model_sign_out_v1'],
            'readiness': {'schema_version': 1,
                          'runtimes': {rt: state for rt in ('claude', 'codex')},
                          'bots': {bot: {'ready': signed, 'runtime': rt, 'profile': 'work',
                                         'sign_in': state['authenticated']}
                                   for bot, rt in (('ops', 'claude'), ('finance', 'codex'))}}}
    if current:
        body['capabilities'].append('computer_logins_v1')
        body['readiness']['computer_profile'] = 'work'
    return post(api, 'runners/heartbeat', body, machine['token'])


def computers(api):
    machine = runner(api)
    for bot, rt in (('ops', 'claude'), ('finance', 'codex')):
        assign(api, machine, bot)
        set_runtime(api, rt, bot=bot)
    return machine


def task(api, bot, title):
    return post(api, 'tasks', {'owner': bot, 'title': title, 'body': 'Please review it.'})


def test_a_retained_default_home_shares_old_primary_and_new_fallback_holds(api, monkeypatch):
    at(monkeypatch, '2026-10-19T01:00:00.000000Z')
    machine = computers(api)
    rid = machine['runner_id']
    # This hold predates the handshake: do not discard it or alias it to another account.
    with api.app.state.store.transaction() as c:
        c.execute("INSERT INTO subscription_assignments VALUES('bot','ops','engineering',NULL,NULL)")
        before = c.execute("SELECT config FROM bot_config WHERE bot='ops'").fetchone()[0]
        H._apply(c, H.ONE_LOGIN_PER_COMPUTER_SCHEMA)
        assert not c.execute("SELECT 1 FROM subscription_assignments WHERE scope='bot'").fetchone()
        assert c.execute("SELECT config FROM bot_config WHERE bot='ops'").fetchone()[0] == before
        login_holds.record(c, rid, 'work', 'codex', H.now(), bot='finance')
    assert get(api, 'config', machine['token'])['computer_logins_v1'] is True
    heartbeat(api, machine, current=True)
    task(api, 'finance', 'Queued on the old hold')
    assert claim(api, machine, 'finance') is None
    at(monkeypatch, '2026-10-19T01:31:00.000000Z', api)
    heartbeat(api, machine, current=True)
    task(api, 'ops', 'Primary and fallback share one home')
    turn = claim(api, machine, 'ops')
    assert turn
    assert turn['computer_logins_v1'] is True
    assert not turn.get('profile')
    assert all(row['computer_logins_v1'] for row in get(api, 'runners/assignments', machine['token']))
    limit(api, machine, turn, errors=('Quota exceeded.',), fallback='codex',
          primary_limited=True, profile_used='work')
    assert claim(api, machine, 'finance') is None
    with api.app.state.store.read() as c:
        assert execution.login_key(c, api.app.state.store.settings, rid, 'finance', {'runtime': 'codex'}) == (rid, 'work', 'codex')
        assert login_holds.get(c, rid, 'work', 'codex')['streak'] == 2
        assert login_holds.get(c, rid, 'work', 'claude')
        assert not login_holds.get(c, rid, '', 'codex')
    # These keys are also what a restarted Rust runner reads for its fallback cache.
    assert {(h['profile'], h['runtime']) for h in get(api, 'config', machine['token'])['login_holds']} == {
        ('work', 'claude'), ('work', 'codex')}


def test_a_legacy_named_login_waits_for_acknowledgement_and_sign_out_is_reversible(api):
    machine = computers(api)
    rid = machine['runner_id']
    heartbeat(api, machine, current=False)
    task(api, 'finance', 'Keep the legacy account until upgrade')
    assert claim(api, machine, 'finance') is None
    assert 'Update Test Mac' in get(api, 'bots/finance/subscription')['problem']
    get(api, f'runners/{rid}/logouts/bots?runtime=codex', expected=409)
    refused = post(api, f'runners/{rid}/logouts', {'runtime': 'codex'}, expected=409)
    assert refused['error']['code'] == 'runner_version'
    with api.app.state.store.read() as c:
        assert execution.bot_refusal(c, api.app.state.store.settings, rid, 'finance') == 'login_upgrade'
        assert not c.execute('SELECT 1 FROM model_logins').fetchone()
    heartbeat(api, machine, current=True)
    # Paused bots keep their account configuration but do not appear among active bots a sign-out stops.
    assign(api, machine, 'product-design')
    set_runtime(api, 'codex', bot='product-design')
    with api.app.state.store.transaction() as c:
        c.execute("UPDATE bots SET state='paused' WHERE slug='product-design'")
    assert [b['bot'] for b in get(api, f'runners/{rid}/logouts/bots?runtime=codex')['bots']] == ['finance']
    assert [b['bot'] for b in get(api, f'runners/{rid}/logouts/bots?runtime=claude')['bots']] == ['ops']
    logout = post(api, f'runners/{rid}/logouts', {'runtime': 'codex'})
    report(api, machine, logout['id'], state='signed_out')
    heartbeat(api, machine, current=True, signed=False)
    assert claim(api, machine, 'finance') is None
    login = post(api, f'runners/{rid}/logins', {'runtime': 'codex'})
    report(api, machine, login['id'], state='signed_in')
    heartbeat(api, machine, current=True)
    assert claim(api, machine, 'finance')
    # An old runner with no named bot logins is already unambiguous and keeps working.
    other = runner(api)
    assign(api, other, 'cpo')
    post(api, 'runners/heartbeat', {'version': 'old', 'platform': 'test', 'readiness': {'cpo': True}}, other['token'])
    task(api, 'cpo', 'An unnamed legacy runner stays available')
    assert claim(api, other, 'cpo')

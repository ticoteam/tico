"""Server defaults stay independent of computer-local provider credentials."""
import json

from backend.tests.test_api import api, assign, claim, get, post, put, ready, runner  # noqa: F401


def test_the_api_refuses_a_bot_its_own_sign_in(api):
    r = runner(api)
    assign(api, r, 'ops')
    put(api, 'subscriptions', {'scope': 'bot', 'target': 'ops', 'profile': 'engineering'}, expected=405)
    with api.app.state.store.read() as c:
        assert not c.execute('SELECT 1 FROM subscription_assignments').fetchone()
    assert 'assignments' not in get(api, 'subscriptions')
    # Nor a second, named login on the computer for a bot to run on: a different account means another computer.
    refused = post(api, f"runners/{r['runner_id']}/logins", {'runtime': 'codex', 'profile': 'engineering'}, expected=422)
    assert refused['error']['code'] == 'one_login'


def test_sign_in_permissions(api):
    r = runner(api)
    ready(api, r, [])
    # Neither an administrator nor its operator: sees the computer on Computers, but changes nothing on it.
    rid = r['runner_id']
    seen = next(m for m in get(api, 'operations', 'cara-test')['computers'] if m['id'] == rid)
    assert seen['may_manage'] is False
    post(api, f"runners/{rid}/logins", {'runtime': 'codex'}, token='cara-test', expected=403)
    put(api, 'subscriptions/name', {'runner_id': rid, 'profile': 'x', 'display_name': 'X'}, token='cara-test', expected=403)
    put(api, 'subscriptions/weekly', {'runner_id': rid, 'profile': 'x', 'runtime': 'codex', 'used_percent': 10},
        token='cara-test', expected=403)
    post(api, 'subscriptions/refresh', {'runner_id': rid, 'profile': 'x', 'runtime': 'codex'}, token='cara-test', expected=403)
    for path, body in [('inbox-only', {'enabled': True}), ('screen', {'mode': 'view'}), ('performance', {'mode': 'on'}),
                       ('harness-choices', {'harness': 'codex', 'enabled': True}), ('restart', {}), ('revoke', {})]:
        post(api, f"runners/{rid}/{path}", body, token='cara-test', expected=403)
    login = post(api, f"runners/{r['runner_id']}/logins", {'runtime': 'codex'})
    assert login['profile'] == ''
    pending = get(api, 'runner-logins', r['token'])['logins']
    assert pending[0]['profile'] == ''
    post(api, f"runners/{rid}/revoke", {})
    assert all(m['id'] != rid for m in get(api, 'operations', 'cara-test')['computers'])
    assert any(m['id'] == rid for m in get(api, 'operations')['computers'])


def test_botops_signin_uses_requester_rights_by_default():
    from backend.botops_act import default_delegable
    assert not default_delegable('PUT', 'subscriptions')
    assert default_delegable('POST', 'runners/computer-a/logins', {'runtime': 'codex'})
    assert default_delegable('GET', 'runners/computer-a/logins/login-a')
    assert not default_delegable('GET', 'runner-logins')


def test_subscriptions_migrations_are_repeatable_and_follow_repositories(api):
    from backend import hubdb as H
    assert H.MIGRATIONS[16:19] == [H.REPOSITORIES_SCHEMA, H.TASK_LINKS_V2_SCHEMA, H.SUBSCRIPTIONS_SCHEMA]
    with api.app.state.store.transaction() as c:
        H._apply(c, H.SUBSCRIPTIONS_SCHEMA)
        assert c.execute('SELECT 1 FROM cloud_migrations WHERE version=52').fetchone()
        assert c.execute('PRAGMA user_version').fetchone()[0] == len(H.MIGRATIONS)


def test_subscription_computers_use_computer_visibility(api):
    r = runner(api)
    with api.app.state.store.transaction() as c:
        c.execute('UPDATE runners SET accepts_member_bots=0 WHERE id=?', (r['runner_id'],))
    assert get(api, 'subscriptions', 'cara-test')['profiles_by_computer'] == []
    assert get(api, 'computers', 'cara-test')['computers'] == []
    assign(api, r, 'ops')
    ready(api, r, ['ops'])
    post(api, 'chat/ops', {'text': 'Inspect subscriptions'})
    token = claim(api, r)['token']
    assert [row['runner_id'] for row in get(api, 'subscriptions', token)['profiles_by_computer']] == [r['runner_id']]


def test_server_rejection_blocks_only_matching_profile(api):
    from backend.execution import Execution
    r = runner(api)
    body = {'version': 'test', 'platform': 'test', 'readiness': {'schema_version': 1,
        'runtimes': {'codex': {'installed': True, 'authenticated': 'ready'}},
        'bots': {'ops': {'ready': True, 'runtime': 'codex', 'profile': 'one'},
                 'cpo': {'ready': True, 'runtime': 'codex', 'profile': 'two'}}}}
    post(api, 'runners/heartbeat', body, token=r['token'])
    with api.app.state.store.transaction() as c:
        Execution.mark_rejected(c, r['runner_id'], 'codex', 'Unauthorized', 'one')
        report = json.loads(c.execute('SELECT readiness FROM runners WHERE id=?', (r['runner_id'],)).fetchone()[0])
    assert report['bots']['ops']['ready'] is False
    assert report['bots']['cpo']['ready'] is True
    assert report['runtimes']['codex']['authenticated'] == 'ready'


def test_a_refused_bot_key_marks_only_that_bot_and_a_computer_refusal_marks_every_bot(api):
    from backend.execution import Execution
    r = runner(api)
    body = {'version': 'test', 'platform': 'test', 'readiness': {'schema_version': 1,
        'runtimes': {'pi': {'installed': True, 'authenticated': 'ready'},
                     'claude': {'installed': True, 'authenticated': 'ready'}},
        'bots': {'alpha': {'ready': True, 'runtime': 'pi'}, 'beta': {'ready': True, 'runtime': 'pi'},
                 'gamma': {'ready': True, 'runtime': 'claude'}, 'delta': {'ready': True, 'runtime': 'claude'}}}}
    post(api, 'runners/heartbeat', body, token=r['token'])

    def report():
        with api.app.state.store.transaction() as c:
            return json.loads(c.execute('SELECT readiness FROM runners WHERE id=?', (r['runner_id'],)).fetchone()[0])

    with api.app.state.store.transaction() as c:
        Execution.mark_rejected(c, r['runner_id'], 'pi', 'Unauthorized', None, 'alpha')
    refused = report()
    assert (refused['bots']['alpha']['ready'], refused['bots']['alpha']['sign_in']) == (False, 'rejected')
    assert refused['bots']['beta']['ready'] is True
    assert refused['runtimes']['pi']['authenticated'] == 'ready'

    with api.app.state.store.transaction() as c:
        Execution.mark_rejected(c, r['runner_id'], 'claude', 'Unauthorized', None, 'gamma')
    refused = report()
    assert refused['bots']['gamma']['ready'] is False and refused['bots']['delta']['ready'] is False
    assert refused['runtimes']['claude']['authenticated'] == 'rejected'


def set_runtime(api, runtime, harness=None, bot="ops"):
    with api.app.state.store.transaction() as c:
        config = json.loads(c.execute("SELECT config FROM bot_config WHERE bot=?", (bot,)).fetchone()[0])
        config.update(runtime=runtime, harness=harness or runtime)
        c.execute("UPDATE bot_config SET config=? WHERE bot=?", (json.dumps(config), bot))


def test_a_login_whose_turns_cannot_save_a_session_names_the_folder_not_a_sign_in(api):
    """Muse Code signed in, but the bot user can't write its data folder (Dev 329): the runner says so apart from the
    sign-in, and the bot's line names the folder instead of "Signed out"."""
    set_runtime(api, 'musecode')
    r = runner(api)
    assign(api, r, 'ops')
    assert get(api, 'config', r['token'])['readiness_data_problem'] is True
    problem = "Muse Code can't save sessions: the bot user can't write /home/x/musecode/data/muse/sessions (read-only)"
    post(api, 'runners/heartbeat', {'version': 'test', 'platform': 'test', 'readiness': {'schema_version': 1,
         'runtimes': {'musecode': {'installed': True, 'authenticated': 'failed', 'detail': problem, 'data_problem': problem,
                                   'profiles': {'': {'installed': True, 'authenticated': 'failed', 'data_problem': problem}}}},
         'bots': {'ops': {'ready': False, 'runtime': 'musecode', 'sign_in': 'failed', 'data_problem': problem,
                          'problems': [problem]}}}}, token=r['token'])
    line = get(api, 'bots/ops/subscription')
    assert (line['signed_in'], line['problem'], line['source']) == (None, problem, 'computer')
    machine = next(m for m in get(api, 'operations')['machines'] if m['id'] == r['runner_id'])
    assert machine['readiness']['runtimes']['musecode']['data_problem'] == problem
    assert [(row['bot'], row['signed_in'], row['problem']) for row in machine['bot_connections']] == [('ops', None, problem)]

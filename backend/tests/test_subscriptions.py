"""Server defaults stay independent of computer-local provider credentials."""
import json

from backend.tests.test_api import api, assign, claim, get, post, put, ready, runner  # noqa: F401


def group_assignment(api, target, profile):
    return put(api, 'subscriptions', {'scope': 'group', 'target': target, 'profile': profile})


def group_tree(api):
    parent = post(api, 'groups', {'name': 'Engineering'})
    child = post(api, 'groups', {'name': 'Product', 'parent': parent['id'], 'add': {'bots': ['ops']}})
    return parent['id'], child['id']


def test_resolution_bot_nearest_group_then_computer(api):
    set_runtime(api, 'codex')
    parent, child = group_tree(api)
    assert get(api, 'bots/ops/subscription')['source'] == 'computer'
    group_assignment(api, parent, 'one')
    assert get(api, 'bots/ops/subscription')['source'] == 'group:Engineering'
    group_assignment(api, child, 'two')
    assert get(api, 'bots/ops/subscription')['profile'] == 'two'
    put(api, 'subscriptions', {'scope': 'bot', 'target': 'ops', 'profile': 'three'})
    assert get(api, 'bots/ops/subscription')['source'] == 'bot'
    put(api, 'subscriptions', {'scope': 'bot', 'target': 'ops', 'profile': None})
    assert get(api, 'bots/ops/subscription')['profile'] == 'two'
    group_assignment(api, child, None)
    assert get(api, 'bots/ops/subscription')['profile'] == 'one'
    group_assignment(api, parent, None)
    assert get(api, 'bots/ops/subscription')['profile'] is None


def test_heartbeat_profiles_claim_and_old_computer(api):
    r = runner(api)
    assign(api, r, 'ops')
    put(api, 'subscriptions', {'scope': 'bot', 'target': 'ops', 'profile': 'engineering'})
    set_runtime(api, 'codex')
    ready(api, r, ['ops'])
    assert get(api, 'bots/ops/subscription')['signed_in'] is None
    assert get(api, 'bots/ops/subscription')['problem'] == 'Update Test Mac to use subscriptions'
    post(api, 'chat/ops', {'text': 'Wait for the assigned subscription'})
    assert claim(api, r) is None
    body = {'version': 'test', 'platform': 'test', 'readiness': {'ops': True},
            'profiles': [{'name': 'engineering', 'runtimes': {'codex': {'signed_in': True}}}]}
    post(api, 'runners/heartbeat', body, token=r['token'])
    listed = get(api, 'subscriptions')['profiles_by_computer']
    assert listed[0]['profiles'][0]['name'] == 'engineering'
    assert listed[0]['profiles'][0]['display_name'] == 'engineering'
    assert listed[0]['profiles'][0]['runtimes']['codex']['signed_in'] is True
    sub = get(api, 'bots/ops/subscription')
    assert sub['signed_in'] is True and sub['computer']['runner_id'] == r['runner_id']
    assignment = get(api, 'runners/assignments', r['token'])[0]
    assert assignment['profile'] == 'engineering'
    post(api, 'chat/ops', {'text': 'Check the current delivery'})
    work = claim(api, r)
    assert work['profile'] == 'engineering'
    post(api, 'attempts/' + work['id'] + '/started', {'thread_id': 'thread'}, token=r['token'])
    post(api, 'attempts/' + work['id'] + '/complete', {'outcome': 'completed', 'last_seq': 0}, token=r['token'])
    post(api, 'chat/ops', {'text': 'Wait while this subscription is unavailable'})
    # A complete replacement report removes profiles; a missing report from an old runner doesn't.
    post(api, 'runners/heartbeat', {**body, 'profiles': []}, token=r['token'])
    sub = get(api, 'bots/ops/subscription')
    assert sub['signed_in'] is False and sub['problem'] == "Subscription engineering isn't on Test Mac"
    assert claim(api, r) is None
    signed_out = {**body, 'profiles': [{'name': 'engineering', 'runtimes': {'codex': {'signed_in': False}}}]}
    post(api, 'runners/heartbeat', signed_out, token=r['token'])
    assert claim(api, r) is None
    assert get(api, 'bots/ops/subscription')['problem'] == "Subscription engineering isn't signed in on Test Mac"
    health = get(api, 'health')
    assert any(check['id'] == 'subscriptions' for check in health['checks'])
    ready(api, r, ['ops'])
    assert get(api, 'bots/ops/subscription')['signed_in'] is None
    assert get(api, 'bots/ops/subscription')['problem'] == 'Update Test Mac to use subscriptions'


def test_assignment_permissions_and_named_login(api):
    parent, _ = group_tree(api)
    put(api, 'subscriptions', {'scope': 'group', 'target': parent, 'profile': 'one'}, token='cara-test', expected=403)
    put(api, 'subscriptions', {'scope': 'bot', 'target': 'ops', 'profile': 'one'}, token='cara-test', expected=403)
    put(api, 'subscriptions', {'scope': 'bot', 'target': 'missing', 'profile': 'one'}, expected=404)
    r = runner(api)
    ready(api, r, [])
    login = post(api, f"runners/{r['runner_id']}/logins", {'runtime': 'codex', 'profile': 'engineering'})
    assert login['profile'] == 'engineering'
    pending = get(api, 'runner-logins', r['token'])['logins']
    assert pending[0]['profile'] == 'engineering'


def test_botops_assignment_and_signin_use_requester_rights_by_default():
    from backend.botops_act import default_delegable
    assert default_delegable('PUT', 'subscriptions')
    assert default_delegable('POST', 'runners/computer-a/logins', {'runtime': 'codex', 'profile': 'engineering'})
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
    with api.app.state.store.transaction() as c:
        c.execute("INSERT INTO subscription_assignments VALUES('bot','missing','one',NULL,NULL)")
    assert not get(api, 'subscriptions')['assignments']


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
        report = json.loads(c.execute('SELECT readiness_json FROM runners WHERE id=?', (r['runner_id'],)).fetchone()[0])
    assert report['bots']['ops']['ready'] is False
    assert report['bots']['cpo']['ready'] is True
    assert report['runtimes']['codex']['authenticated'] == 'ready'


def set_runtime(api, runtime, harness=None, bot="ops"):
    with api.app.state.store.transaction() as c:
        config = json.loads(c.execute("SELECT config_json FROM bot_config WHERE bot=?", (bot,)).fetchone()[0])
        config.update(runtime=runtime, harness=harness or runtime)
        c.execute("UPDATE bot_config SET config_json=? WHERE bot=?", (json.dumps(config), bot))


def test_subscription_refusal_requeues_until_fresh_ready_report(api):
    r = runner(api)
    assign(api, r, 'ops')
    set_runtime(api, 'codex')
    put(api, 'subscriptions', {'scope': 'bot', 'target': 'ops', 'profile': 'one'})
    heartbeat = {'version': 'test', 'platform': 'test', 'readiness': {'ops': True},
                 'profiles': [{'name': 'one', 'runtimes': {'codex': {'signed_in': True}}}]}
    post(api, 'runners/heartbeat', heartbeat, token=r['token'])
    post(api, 'chat/ops', {'text': 'Wait for the assigned subscription'})
    for problem in ["Subscription one isn't signed in on Test Mac", "Subscription one isn't on Test Mac"]:
        work = claim(api, r)
        assert work
        post(api, 'attempts/' + work['id'] + '/started', {'thread_id': 'thread'}, token=r['token'])
        refusal = {'profile': 'one', 'runtime': 'codex', 'problem': problem}
        post(api, 'attempts/' + work['id'] + '/events', {'events': [
            {'seq': 1, 'kind': 'diagnostic', 'payload': {'text': problem}}]}, token=r['token'])
        post(api, 'attempts/' + work['id'] + '/complete', {'outcome': 'failed', 'text': problem,
             'last_seq': 1, 'retryable': True, 'subscription_unavailable': refusal}, token=r['token'])
        with api.app.state.store.read() as c:
            assert c.execute('SELECT state FROM jobs WHERE attempt_id=?', (work['id'],)).fetchone()[0] == 'queued'
            assert not c.execute("SELECT 1 FROM messages WHERE from_actor='bot:ops'").fetchone()
        assert claim(api, r) is None
        sub = get(api, 'bots/ops/subscription')
        assert sub['problem'] == problem and sub['detail']['runtime'] == 'codex'
        post(api, 'runners/heartbeat', {**heartbeat, 'profiles': [
            {'name': 'one', 'runtimes': {'codex': {'signed_in': None}}}]}, token=r['token'])
        assert claim(api, r) is None
        post(api, 'runners/heartbeat', heartbeat, token=r['token'])


def test_subscription_picker_and_claim_refuse_unmapped_runtimes(api):
    r = runner(api)
    assign(api, r, 'ops')
    put(api, 'subscriptions', {'scope': 'bot', 'target': 'ops', 'profile': 'one'})
    post(api, 'chat/ops', {'text': 'Wait for a covered runtime'})
    for runtime, harness in [('cursor', 'cursor-agent')]:
        set_runtime(api, runtime, harness)
        post(api, 'runners/heartbeat', {'version': 'test', 'platform': 'test', 'readiness': {'ops': True},
             'profiles': [{'name': 'one', 'runtimes': {runtime: {'signed_in': True}}}]}, token=r['token'])
        sub = get(api, 'bots/ops/subscription')
        assert sub['problem'] == f"Subscription one doesn't cover {runtime}"
        assert sub['signed_in'] is False
        assert claim(api, r) is None

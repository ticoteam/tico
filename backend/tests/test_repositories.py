"""Repository contracts: migrations, sync, scoped grants and mixed computers."""
import json
import time
import uuid

import pytest

from backend import github_app, hubdb as H, repositories as R
from backend.store import Store
from backend.tests.test_github_app import api, gh, auth, connect, runner_token, turn_token, put_extras  # noqa: F401


def put(api, path, body, token='owner-test'):
    response = api.put('/api/v2/' + path, json=body, headers={**auth(token), 'Idempotency-Key': uuid.uuid4().hex})
    assert response.status_code == 200, response.text
    return response.json()


def catalog(api, gh):
    connect(api)
    gh.repositories = [{'full_name': 'Acme/' + name, 'default_branch': 'main'}
                       for name in ('product', 'docs', 'bot-sales', 'emp-cpo')]
    response = api.post('/api/v2/repositories/refresh', headers=auth())
    assert response.status_code == 200, response.text
    return response.json()


def test_sync_bot_repos_setup_overrides_and_unreachable(api, gh):
    gh.setup_files['/repos/Acme/product/contents/tico.json'] = {'setup': 'npm ci'}
    gh.setup_files['/repos/Acme/docs/contents/conductor.json'] = {'scripts': {'setup': 'make setup'}}
    rows = {r['full_name']: r for r in catalog(api, gh)['repositories']}
    assert rows['Acme/bot-sales']['bot_repo'] and rows['Acme/emp-cpo']['bot_repo']
    assert not rows['Acme/product']['bot_repo']
    assert rows['Acme/product']['setup_command'] is None
    assert not any('/contents/' in call[1] for call in gh.calls)
    for name in ('product', 'docs'):
        put(api, 'repositories/Acme/' + name, {'enabled': True})
    rows = {r['full_name']: r for r in api.post('/api/v2/repositories/refresh', headers=auth()).json()['repositories']}
    assert rows['Acme/product']['setup_command'] == 'npm ci'
    assert rows['Acme/docs']['setup_source'] == 'conductor.json'
    updated = put(api, 'repositories/Acme/product', {'enabled': True, 'setup_command': 'make install'})
    assert updated['full_name'] == 'Acme/product' and updated['enabled']
    assert updated['setup_command'] == 'make install' and updated['setup_source'] == 'settings'
    gh.repositories = [r for r in gh.repositories if r['full_name'] != 'Acme/docs']
    response = api.post('/api/v2/repositories/refresh', headers=auth()).json()
    assert response == api.get('/api/v2/repositories', headers=auth('person-test')).json()
    rows = {r['full_name']: r for r in response['repositories']}
    assert not rows['Acme/docs']['reachable']
    assert rows['Acme/product']['setup_command'] == 'make install'
    assert rows['Acme/product']['setup_source'] == 'settings'
    assert api.get('/api/v2/repositories', headers=auth('person-test')).status_code == 200
    assert api.get('/api/v2/bots/cpo/repositories', headers=auth('person-test')).status_code == 200
    assert api.put('/api/v2/repositories/Acme/product', json={'enabled': False}, headers=auth('person-test')).status_code == 403


@pytest.mark.parametrize('cleared', ['', '   ', None])
def test_cleared_setup_command_returns_to_the_repository_file(api, gh, cleared):
    gh.setup_files['/repos/Acme/product/contents/tico.json'] = {'setup': 'npm ci'}
    catalog(api, gh)
    put(api, 'repositories/Acme/product', {'enabled': True})
    typed = put(api, 'repositories/Acme/product', {'setup_command': 'make install'})
    assert (typed['setup_command'], typed['setup_source']) == ('make install', 'settings')
    cleared_row = put(api, 'repositories/Acme/product', {'setup_command': cleared})
    assert (cleared_row['setup_command'], cleared_row['setup_source']) == (None, None)
    rows = {r['full_name']: r for r in api.post('/api/v2/repositories/refresh', headers=auth()).json()['repositories']}
    assert (rows['Acme/product']['setup_command'], rows['Acme/product']['setup_source']) == ('npm ci', 'tico.json')



def test_a_command_cleared_before_the_fix_returns_to_the_file_after_the_next_start(api, gh):
    gh.setup_files['/repos/Acme/product/contents/tico.json'] = {'setup': 'npm ci'}
    gh.setup_files['/repos/Acme/docs/contents/tico.json'] = {'setup': 'npm ci'}
    catalog(api, gh)
    for name in ('product', 'docs'):
        put(api, 'repositories/Acme/' + name, {'enabled': True})
    store = api.app_state.store
    with store.transaction() as c:                        # rows saved before a clear released them
        c.execute("UPDATE repositories SET setup_command=' \t',setup_source='settings' WHERE full_name='Acme/product'")
        c.execute("UPDATE repositories SET setup_command='make install',setup_source='settings' WHERE full_name='Acme/docs'")
    store.initialize()
    store.initialize()
    rows = {r['full_name']: r for r in api.post('/api/v2/repositories/refresh', headers=auth()).json()['repositories']}
    assert (rows['Acme/product']['setup_command'], rows['Acme/product']['setup_source']) == ('npm ci', 'tico.json')
    assert (rows['Acme/docs']['setup_command'], rows['Acme/docs']['setup_source']) == ('make install', 'settings')

def test_read_grant_reads_pull_requests_comments_and_deployments_but_never_writes(api, gh):
    # A read-only reviewer needs `gh pr view`, `gh pr diff`, a PR's comments and its preview deployment.
    catalog(api, gh)
    runner_token(api, 'cpo')
    put(api, 'repositories/Acme/docs', {'enabled': True})
    put(api, 'bots/cpo/repositories', {'mode': 'chosen', 'chosen': [{'full_name': 'Acme/docs', 'access': 'read'}]})
    gh.permissions = {'contents': 'write', 'pull_requests': 'write', 'issues': 'write', 'actions': 'read',
                      'checks': 'read', 'statuses': 'read', 'deployments': 'read'}
    assert turn_token(api).status_code == 200
    read = [r[2] for r in gh.of('/access_tokens') if r[2].get('repositories') == ['docs']][-1]
    assert read['permissions'] == {'contents': 'read', 'metadata': 'read', 'actions': 'read', 'checks': 'read',
                                   'statuses': 'read', 'pull_requests': 'read', 'issues': 'read', 'deployments': 'read'}
    # An installation that has not accepted Deployments yet still reads pull requests and comments.
    gh.permissions.pop('deployments')
    api.app_state.github_app.cache.clear()
    api.app_state.github_app.live.clear()
    assert turn_token(api).status_code == 200
    read = [r[2] for r in gh.of('/access_tokens') if r[2].get('repositories') == ['docs']][-1]
    assert 'deployments' not in read['permissions']
    assert read['permissions']['pull_requests'] == read['permissions']['issues'] == 'read'


def test_modes_mixed_scopes_alias_and_default(api, gh):
    catalog(api, gh)
    runner_token(api, 'cpo')
    for name in ('product', 'docs'):
        put(api, 'repositories/Acme/' + name, {'enabled': True})
    data = put(api, 'bots/cpo/repositories', {'mode': 'all', 'all_access': 'read'})
    assert {r['full_name']: r['access'] for r in data['effective']} == {
        'Acme/emp-cpo': 'write', 'Acme/product': 'read', 'Acme/docs': 'read'}
    token = turn_token(api).json()
    assert len(token['tokens']) == 2
    bodies = [r[2] for r in gh.of('/access_tokens')[-2:]]
    assert bodies[0]['repositories'] == ['emp-cpo']
    assert bodies[0]['permissions']['contents'] == 'write'
    assert bodies[1]['repositories'] == ['docs', 'product']
    assert bodies[1]['permissions'] == {'contents': 'read', 'metadata': 'read'}
    specific = api.post('/api/v2/github/token', json={'bot': 'cpo', 'repository': 'Acme/product'}, headers=auth('runner-test'))
    assert specific.status_code == 200 and specific.json()['tokens'][0]['access'] == 'read'
    assert api.post('/api/v2/github/token', json={'bot': 'cpo', 'repository': 'Acme/secret'}, headers=auth('runner-test')).status_code == 403
    put(api, 'bots/cpo/repositories', {'mode': 'chosen', 'chosen': [
        {'full_name': 'Acme/docs', 'access': 'read'}, {'full_name': 'Acme/product', 'access': 'write'}]})
    assert api.get('/api/v2/bots/cpo/github-repos', headers=auth()).json()['repositories'] == ['Acme/product']
    assert put_extras(api, 'cpo', ['legacy']).status_code == 200
    assert api.get('/api/v2/bots/cpo/repositories', headers=auth()).json()['chosen'] == [
        {'full_name': 'Acme/docs', 'access': 'read'}, {'full_name': 'Acme/legacy', 'access': 'write'},
        {'full_name': 'Acme/product', 'access': 'write'}]
    put(api, 'bots/cpo/repositories', {'mode': 'own'})
    assert turn_token(api).json()['repositories'] == ['Acme/emp-cpo']
    put(api, 'repositories/settings', {'new_bot_default': 'all'})
    with api.app_state.store.transaction() as c:
        c.execute("INSERT INTO bots(id,slug,display_name,created_at) VALUES('new-test','new-test','Sam',?)", (H.now(),))
        c.execute("INSERT INTO bot_config(bot,config,team,owner_id,repo) VALUES('new-test','{}','t','ana','bot-new-test')")
        assert json.loads(c.execute("SELECT config FROM bot_config WHERE bot='new-test'").fetchone()[0])['repo_access_mode'] == 'all'


def test_upgrade_migrates_existing_extras_once(api, gh):
    connect(api)
    runner_token(api, 'cpo')
    store = api.app_state.store
    with store.transaction() as c:
        c.execute('DELETE FROM cloud_migrations WHERE version=50')
        c.execute("DELETE FROM registry_metadata WHERE key='repositories-access-migrated'")
        R.save_metadata(c, 'github-extra-repos', {'cpo': ['Acme/product', 'Acme/docs'], 'oldie': ['Acme/archive']})
    Store(store.settings).initialize(seed_market=False)
    with store.transaction() as c:
        data = R.access(c, 'cpo', 'Acme')
        assert data['mode'] == 'chosen'
        assert {r['full_name']: r['access'] for r in data['chosen']} == {'Acme/product': 'write', 'Acme/docs': 'write'}
        assert R.access(c, 'oldie', 'Acme')['mode'] == 'chosen'
        assert R.access(c, 'cmo', 'Acme')['mode'] == 'own'
        assert c.execute('SELECT count(*) FROM repositories WHERE enabled=1').fetchone()[0] == 3
    assert set(turn_token(api).json()['repositories']) == {'Acme/emp-cpo', 'Acme/product', 'Acme/docs'}
    with store.transaction() as c:
        R.set_access(c, 'cpo', R.RepoAccessUpdate(mode='own'), 'Acme', 'human:ana')
        R.migrate(c)
        assert R.access(c, 'cpo', 'Acme')['mode'] == 'own'
        assert c.execute('PRAGMA user_version').fetchone()[0] == len(H.MIGRATIONS)


def test_computer_union_token_and_old_heartbeat(api, gh):
    catalog(api, gh)
    runner_token(api, 'cpo')
    runner_token(api, 'cmo')
    for name in ('product', 'docs'):
        put(api, 'repositories/Acme/' + name, {'enabled': True})
    put(api, 'bots/cpo/repositories', {'mode': 'all', 'all_access': 'read'})
    put(api, 'bots/cmo/repositories', {'mode': 'chosen', 'chosen': [{'full_name': 'Acme/product', 'access': 'write'}]})
    path = '/api/v2/runners/me/repositories'
    response = api.get(path, headers=auth('runner-test'))
    assert response.status_code == 200, response.text
    rows = {r['full_name']: r for r in response.json()['repositories']}
    assert set(rows) == {'Acme/product', 'Acme/docs'}
    assert set(rows['Acme/product']['bots']) == {'cpo', 'cmo'}
    assert rows['Acme/product']['access'] == 'write' and rows['Acme/docs']['access'] == 'read'
    response = api.post(path + '/token', headers=auth('runner-test'))
    assert response.status_code == 200, response.text
    assert gh.of('/access_tokens')[-1][2] == {'repositories': ['docs', 'product'], 'permissions': {'contents': 'read', 'metadata': 'read'}}
    assert api.get(path, headers=auth()).status_code == 403
    body = {'version': '0.2.23', 'platform': 'mac', 'readiness': {}}
    assert api.post('/api/v2/runners/heartbeat', json=body, headers={**auth('runner-test'), 'Idempotency-Key': uuid.uuid4().hex}).status_code == 200
    with api.app_state.store.read() as c:
        assert R.metadata(c, 'computer-repositories:r1')['repositories'] == 'unknown'
    operations = api.get('/api/v2/operations', headers=auth()).json()
    assert next(r for r in operations['computers'] if r['id'] == 'r1')['repositories'] == 'unknown'
    body['repositories'] = [{'full_name': 'Acme/product', 'state': 'cloned', 'size_mb': 12}]
    assert api.post('/api/v2/runners/heartbeat', json=body, headers={**auth('runner-test'), 'Idempotency-Key': uuid.uuid4().hex}).status_code == 200
    with api.app_state.store.read() as c:
        assert R.metadata(c, 'computer-repositories:r1')['repositories'][0]['size_mb'] == 12
    operations = api.get('/api/v2/operations', headers=auth()).json()
    assert next(r for r in operations['computers'] if r['id'] == 'r1')['repositories'][0]['state'] == 'cloned'
    put(api, 'bots/cpo/repositories', {'mode': 'own'})
    put(api, 'bots/cmo/repositories', {'mode': 'own'})
    count = len(gh.of('/access_tokens'))
    assert api.post(path + '/token', headers=auth('runner-test')).json()['token'] is None
    assert len(gh.of('/access_tokens')) == count


@pytest.mark.slow
def test_missing_repo_does_not_take_own_or_computer_tokens_down(api, gh):
    from backend.tests.test_github_app import token_health
    catalog(api, gh)
    runner_token(api, 'cpo')
    for name in ('product', 'docs'):
        put(api, 'repositories/Acme/' + name, {'enabled': True})
    put(api, 'bots/cpo/repositories', {'mode': 'all'})
    gh.missing.add('docs')
    token = turn_token(api)
    assert token.status_code == 200, token.text
    assert set(token.json()['repositories']) == {'Acme/emp-cpo', 'Acme/product'}
    assert gh.of('/access_tokens')[-1][2]['repositories'] == ['emp-cpo', 'product']
    assert token_health(api)
    with api.app_state.store.read() as c:
        detail = json.loads(c.execute("SELECT detail FROM service_health WHERE service='github:token'").fetchone()[0])
        assert 'Acme/docs is not reachable' in detail['message']
    calls = len(gh.calls)
    assert turn_token(api).status_code == 200
    assert len(gh.calls) == calls  # confirmed missing names do not trigger repeated GitHub diagnosis
    effective = api.get('/api/v2/bots/cpo/repositories', headers=auth()).json()['effective']
    assert 'Acme/docs' not in {r['full_name'] for r in effective}
    # A newly missing repo during a computer request gets the same scoped retry.
    gh.missing.add('product')
    response = api.post('/api/v2/runners/me/repositories/token', headers=auth('runner-test'))
    assert response.status_code == 200, response.text
    assert response.json()['repositories'] == [] and response.json()['token'] is None
    assert turn_token(api).json()['repositories'] == ['Acme/emp-cpo']
    assert token_health(api)  # a successful own-repo token must not turn missing grants green


def test_all_excludes_bot_repositories_but_explicit_chosen_keeps_them(api, gh):
    catalog(api, gh)
    runner_token(api, 'cpo')
    put(api, 'repositories/Acme/bot-sales', {'enabled': True})
    data = put(api, 'bots/cpo/repositories', {'mode': 'all'})
    assert data['effective'] == [{'full_name': 'Acme/emp-cpo', 'access': 'write'}]
    assert api.get('/api/v2/runners/me/repositories', headers=auth('runner-test')).json()['repositories'] == []
    put(api, 'bots/cpo/repositories', {'mode': 'chosen', 'chosen': [{'full_name': 'Acme/bot-sales'}]})
    assert 'Acme/bot-sales' in turn_token(api).json()['repositories']


def test_member_manager_cannot_retick_team_repos_or_choose_bot_instructions(api, gh):
    catalog(api, gh)
    put(api, 'bots/cpo/repositories', {'mode': 'chosen', 'chosen': []})
    with api.app_state.store.transaction() as c:
        person = api.app_state.store.settings.test_identities['person-test'].actor.split(':', 1)[1]
        c.execute("UPDATE bot_config SET owner_id=? WHERE bot='cpo'", (person,))
    assert api.get('/api/v2/bots/cpo/github-repos', headers=auth('person-test')).status_code == 200
    refused = put_extras(api, 'cpo', ['docs'], 'person-test')
    assert refused.status_code == 422 and 'tick it' in refused.text
    put(api, 'repositories/Acme/docs', {'enabled': True})
    assert put_extras(api, 'cpo', ['docs'], 'person-test').status_code == 200
    put(api, 'repositories/Acme/bot-sales', {'enabled': True})
    assert put_extras(api, 'cpo', ['bot-sales'], 'person-test').status_code == 403
    with api.app_state.store.read() as c:
        assert c.execute("SELECT enabled FROM repositories WHERE full_name='Acme/docs'").fetchone()[0] == 1
        assert c.execute("SELECT enabled FROM repositories WHERE full_name='Acme/product'").fetchone()[0] == 0


def test_built_in_repository_grants_are_owner_only_and_hidden_bots_stay_hidden(api, gh):
    from backend.tests.test_api import restrict
    catalog(api, gh)
    who = api.app_state.store.settings.test_identities['person-test']
    api.app_state.auth.bot_admins.add(who.email.lower())
    response = api.put('/api/v2/bots/botops/repositories', json={'mode': 'all'}, headers=auth('person-test'))
    assert response.status_code == 403 and 'Owner' in response.text
    api.app_state.auth.bot_admins.remove(who.email.lower())
    with api.app_state.store.transaction() as c:
        restrict(c, 'cpo', people=[])
    assert api.get('/api/v2/bots/cpo/repositories', headers=auth('person-test')).status_code == 403
    assert api.get('/api/v2/bots/cpo/repositories', headers=auth()).status_code == 200


def test_a_github_token_recovery_clears_the_old_failure_message(api):
    from backend.health import note_github_token
    store = api.app_state.store
    note_github_token(store, 'GitHub would not issue an installation token (HTTP 500)', 'Check the App.')
    note_github_token(store)
    with store.read() as c:
        row = c.execute("SELECT last_succeeded_at,last_error,detail FROM service_health WHERE service='github:token'").fetchone()
    assert row['last_succeeded_at'] and row['last_error'] is None and row['detail'] == '{}'


def test_run_workflows_gets_its_own_actions_token_only_once_the_installation_accepts_it(api, gh):
    catalog(api, gh)
    runner_token(api, 'cpo')
    for name in ('product', 'docs'):
        put(api, 'repositories/Acme/' + name, {'enabled': True})
    flagged = [{'full_name': 'Acme/product', 'access': 'write', 'run_workflows': True}]
    refused = api.put('/api/v2/bots/cpo/repositories', json={'mode': 'chosen', 'chosen': [{**flagged[0], 'access': 'read'}]},
                      headers={**auth(), 'Idempotency-Key': uuid.uuid4().hex})
    assert refused.status_code == 422, refused.text
    data = put(api, 'bots/cpo/repositories', {'mode': 'chosen', 'chosen': flagged + [{'full_name': 'Acme/docs', 'access': 'write'}]})
    assert [r for r in data['effective'] if r.get('run_workflows')] == [{'full_name': 'Acme/product', 'access': 'write', 'run_workflows': True}]
    with api.app_state.store.read() as c:
        audit = json.loads(c.execute("SELECT detail FROM events WHERE action='bot.repos_changed' AND target='cpo' "
                                     "ORDER BY rowid DESC").fetchone()[0])
    assert audit['after']['chosen'][1] == flagged[0] and audit['before']['chosen'] == []
    # Actions: read only on the installation: one ordinary write token, and the answer and Health say why.
    gh.permissions = {'actions': 'read', 'checks': 'read', 'statuses': 'read'}
    token = turn_token(api).json()
    assert [t['repositories'] for t in token['tokens']] == [['Acme/docs', 'Acme/emp-cpo', 'Acme/product']]
    assert token['workflows_withheld']['repositories'] == ['Acme/product'] and 'Read and write' in token['workflows_withheld']['reason']
    assert all(r[2]['permissions'].get('actions') != 'write' for r in gh.of('/access_tokens'))
    checks = {x['id']: x for x in api.get('/api/v2/health', headers=auth()).json()['checks']}
    assert checks['github_workflows']['status'] == 'warn' and 'cpo: Acme/product' in checks['github_workflows']['summary']
    # Accepted: the flagged repository gets its own token with Actions: write, and the others never carry it.
    gh.permissions = {'actions': 'write', 'checks': 'read', 'statuses': 'read'}
    api.app_state.github_app.live.clear()
    token = turn_token(api).json()
    assert 'workflows_withheld' not in token
    assert [(t['repositories'], t.get('actions')) for t in token['tokens']] == [
        (['Acme/docs', 'Acme/emp-cpo'], None), (['Acme/product'], 'write')]
    asked = {tuple(r[2]['repositories']): r[2]['permissions'] for r in gh.of('/access_tokens')[-2:]}
    assert asked[('docs', 'emp-cpo')]['actions'] == 'read'
    assert asked[('product',)] == {'contents': 'write', 'pull_requests': 'write', 'issues': 'write', 'metadata': 'read',
                                   'actions': 'write', 'checks': 'read', 'statuses': 'read'}
    assert 'workflows' not in asked[('product',)]
    one = api.post('/api/v2/github/token', json={'bot': 'cpo', 'repository': 'Acme/product', 'purpose': 'git'},
                   headers=auth('runner-test')).json()
    assert one['tokens'][0]['actions'] == 'write' and one['token'] == one['tokens'][0]['token']
    assert 'github_workflows' not in {x['id'] for x in api.get('/api/v2/health', headers=auth()).json()['checks']}
    # Actions dropped back to read inside the live cache: the refused mint falls back and says why.
    gh.permissions = {'actions': 'read', 'checks': 'read', 'statuses': 'read'}
    api.app_state.github_app.cache.clear()
    token = turn_token(api).json()
    assert token['workflows_withheld']['repositories'] == ['Acme/product']
    assert all(t.get('actions') is None for t in token['tokens'])
    assert 'github_workflows' in {x['id'] for x in api.get('/api/v2/health', headers=auth()).json()['checks']}
    # Set right again, the setting warning clears with the live read, even if the mint then fails for another reason.
    gh.permissions = {'actions': 'write', 'checks': 'read', 'statuses': 'read'}
    api.app_state.github_app.cache.clear()
    api.app_state.github_app.live.clear()
    gh.refuse = (500, 'GitHub is down')
    assert turn_token(api).status_code >= 400
    gh.refuse = None
    assert 'github_workflows' not in {x['id'] for x in api.get('/api/v2/health', headers=auth()).json()['checks']}
    # An installation without Checks: the refusal is the reads', not Actions'; the flagged repository still gets it.
    gh.permissions = {'actions': 'write', 'statuses': 'read'}
    api.app_state.github_app.cache.clear()
    api.app_state.github_app.live.clear()
    one = api.post('/api/v2/github/token', json={'bot': 'cpo', 'repository': 'Acme/product', 'purpose': 'git'},
                   headers=auth('runner-test')).json()
    assert one['tokens'][0]['actions'] == 'write' and 'workflows_withheld' not in one
    # Turning it off is a narrowing, said on the reply.
    off = put(api, 'bots/cpo/repositories', {'mode': 'chosen', 'chosen': [{**flagged[0], 'run_workflows': False}]})
    assert off['workflows_stopped'] == ['Acme/product']


def test_a_workflow_mint_refused_twice_falls_back_to_the_ordinary_write_token(api, gh):
    catalog(api, gh)
    runner_token(api, 'cpo')
    put(api, 'repositories/Acme/product', {'enabled': True})
    put(api, 'bots/cpo/repositories', {'mode': 'chosen', 'chosen': [
        {'full_name': 'Acme/product', 'access': 'write', 'run_workflows': True}]})
    # The installation reports Actions: write, but GitHub refuses every token asking for it.
    gh.permissions = {'actions': 'write', 'checks': 'read', 'statuses': 'read'}
    gh.refuse_actions_write = True
    response = turn_token(api)
    assert response.status_code == 200, response.text
    token = response.json()
    assert token['workflows_withheld']['repositories'] == ['Acme/product']
    assert 'although the installation reports it' in token['workflows_withheld']['reason']
    assert all(t.get('actions') is None for t in token['tokens'])
    assert 'Acme/product' in token['repositories']
    # The refusal was Actions', not the reads': the fallback keeps them, and Health names the refusal without
    # blaming the Actions setting or the GitHub connection.
    fallback = gh.of('/access_tokens')[-1][2]['permissions']
    assert fallback['actions'] == 'read' and fallback['checks'] == 'read'
    checks = {x['id']: x for x in api.get('/api/v2/health', headers=auth()).json()['checks']}
    refused = checks['github_workflows']
    assert refused['label'] == 'GitHub refused workflow tokens' and 'cpo: Acme/product' in refused['summary']
    assert 'not Read and write' not in refused['summary']
    assert "Change the App's permissions" not in {f['label'] for f in refused.get('fixes', [])}
    with api.app_state.store.read() as c:
        row = c.execute("SELECT last_error FROM service_health WHERE service='github:token'").fetchone()
    assert row is None or row['last_error'] is None
    # Remembered for LIVE_TTL: the next token (each git call asks) falls back without asking GitHub again.
    asked = len(gh.calls)
    again = api.post('/api/v2/github/token', json={'bot': 'cpo', 'repository': 'Acme/product', 'purpose': 'git'},
                     headers=auth('runner-test')).json()
    assert again['workflows_withheld']['reason'] == token['workflows_withheld']['reason']
    assert again['tokens'][0].get('actions') is None
    assert gh.calls[asked:] == []
    # Once it lapses, GitHub is asked again; still refused, so the reply and Health stay the same.
    api.app_state.github_app.live['workflows-refused'] = 0
    api.app_state.github_app.cache.clear()
    assert turn_token(api).json()['workflows_withheld']['repositories'] == ['Acme/product']
    assert any(r[1].endswith('/access_tokens') and r[2]['permissions'].get('actions') == 'write' for r in gh.calls[asked:])
    # Check permissions forgets it, and the accepted mint clears Health.
    gh.refuse_actions_write = False
    assert api.post('/api/v2/github/app/check-permissions', headers=auth()).status_code == 200
    api.app_state.github_app.cache.clear()
    token = turn_token(api).json()
    assert 'workflows_withheld' not in token and token['tokens'][-1]['actions'] == 'write'
    assert 'github_workflows' not in {x['id'] for x in api.get('/api/v2/health', headers=auth()).json()['checks']}


def test_a_refused_workflow_mint_keeps_a_webhook_reset_of_the_turn_reads(api, gh):
    catalog(api, gh)
    runner_token(api, 'cpo')
    put(api, 'repositories/Acme/product', {'enabled': True})
    put(api, 'bots/cpo/repositories', {'mode': 'chosen', 'chosen': [
        {'full_name': 'Acme/product', 'access': 'write', 'run_workflows': True}]})
    gh.permissions = {'actions': 'write', 'checks': 'read', 'statuses': 'read', 'deployments': 'read'}
    gh.refuse_actions_write = True
    service = api.app_state.github_app
    service.live['turn-reads'] = (time.time() + 3600, {'actions': 'read', 'checks': 'read'})

    def webhook(body):
        # The installation webhook arrives while the narrowed retry's last mint is in flight.
        if body['permissions'].get('actions') == 'write' and 'checks' not in body['permissions']:
            service.live.pop('turn-reads', None)
    gh.on_token = webhook
    assert turn_token(api).json()['workflows_withheld']['repositories'] == ['Acme/product']
    assert 'turn-reads' not in service.live


def flagged_grants(api, gh):
    """cpo with four write grants: product may run workflows and edit workflow files, docs only edit them, site only
    run them, plain neither."""
    connect(api)
    gh.repositories = [{'full_name': 'Acme/' + name, 'default_branch': 'main'}
                       for name in ('product', 'docs', 'site', 'plain', 'emp-cpo')]
    assert api.post('/api/v2/repositories/refresh', headers=auth()).status_code == 200
    runner_token(api, 'cpo')
    for name in ('product', 'docs', 'site', 'plain'):
        put(api, 'repositories/Acme/' + name, {'enabled': True})
    return put(api, 'bots/cpo/repositories', {'mode': 'chosen', 'chosen': [
        {'full_name': 'Acme/product', 'access': 'write', 'run_workflows': True, 'edit_workflows': True},
        {'full_name': 'Acme/docs', 'access': 'write', 'edit_workflows': True},
        {'full_name': 'Acme/site', 'access': 'write', 'run_workflows': True},
        {'full_name': 'Acme/plain', 'access': 'write'}]})


def write_tokens(token):
    """{repository: the permissions its token was raised by}, after checking no repository is in two write tokens: the
    git credential helper picks one token per remote."""
    seen = [name for t in token['tokens'] if t['access'] == 'write' for name in t['repositories']]
    assert len(seen) == len(set(seen)), token['tokens']
    return {name: tuple(p for p in ('actions', 'workflows') if t.get(p) == 'write')
            for t in token['tokens'] if t['access'] == 'write' for name in t['repositories']}


def test_edit_workflows_gets_workflows_write_only_on_a_flagged_grant_the_installation_accepts(api, gh):
    data = flagged_grants(api, gh)
    refused = api.put('/api/v2/bots/cpo/repositories', json={'mode': 'chosen', 'chosen': [
        {'full_name': 'Acme/docs', 'access': 'read', 'edit_workflows': True}]},
        headers={**auth(), 'Idempotency-Key': uuid.uuid4().hex})
    assert refused.status_code == 422 and 'Editing workflow files needs write access' in refused.text
    assert {r['full_name']: (r.get('run_workflows', False), r.get('edit_workflows', False)) for r in data['effective']} == {
        'Acme/docs': (False, True), 'Acme/emp-cpo': (False, False), 'Acme/plain': (False, False),
        'Acme/product': (True, True), 'Acme/site': (True, False)}
    # Actions: write but no Workflows on the installation: the edits are withheld, runs still get their token.
    gh.permissions = {'actions': 'write', 'checks': 'read', 'statuses': 'read'}
    token = turn_token(api).json()
    assert 'workflows_withheld' not in token
    assert token['workflow_edits_withheld']['repositories'] == ['Acme/docs', 'Acme/product']
    assert "Workflows permission" in token['workflow_edits_withheld']['reason']
    assert write_tokens(token) == {'Acme/docs': (), 'Acme/emp-cpo': (), 'Acme/plain': (),
                                   'Acme/product': ('actions',), 'Acme/site': ('actions',)}
    assert all('workflows' not in r[2]['permissions'] for r in gh.of('/access_tokens'))
    checks = {x['id']: x for x in api.get('/api/v2/health', headers=auth()).json()['checks']}
    assert 'github_workflows' not in checks
    edits = checks['github_workflow_edits']
    assert edits['status'] == 'warn' and 'cpo: Acme/docs' in edits['summary'] and 'cpo: Acme/product' in edits['summary']
    assert "Workflows permission" in edits['summary']
    assert "Change the App's permissions" in {f['label'] for f in edits['fixes']}
    # Accepted: a grant flagged for both gets one token with both; every other token goes without Workflows: write.
    gh.permissions = {'actions': 'write', 'workflows': 'write', 'checks': 'read', 'statuses': 'read'}
    service = api.app_state.github_app
    service.live.clear()
    service.cache.clear()
    asked = len(gh.of('/access_tokens'))
    token = turn_token(api).json()
    assert 'workflows_withheld' not in token and 'workflow_edits_withheld' not in token
    assert write_tokens(token) == {'Acme/docs': ('workflows',), 'Acme/emp-cpo': (), 'Acme/plain': (),
                                   'Acme/product': ('actions', 'workflows'), 'Acme/site': ('actions',)}
    minted = {tuple(r[2]['repositories']): r[2]['permissions'] for r in gh.of('/access_tokens')[asked:]}
    assert {repos for repos, permissions in minted.items() if 'workflows' in permissions} == {('docs',), ('product',)}
    assert minted[('product',)]['workflows'] == 'write' and minted[('product',)]['actions'] == 'write'
    assert minted[('docs',)]['actions'] == 'read'
    one = api.post('/api/v2/github/token', json={'bot': 'cpo', 'repository': 'Acme/docs', 'purpose': 'git'},
                   headers=auth('runner-test')).json()
    assert one['tokens'][0]['workflows'] == 'write' and one['token'] == one['tokens'][0]['token']
    assert 'github_workflow_edits' not in {x['id'] for x in api.get('/api/v2/health', headers=auth()).json()['checks']}
    # Turning it off is a narrowing, said on the reply, and leaves Run workflows on.
    off = put(api, 'bots/cpo/repositories', {'mode': 'chosen', 'chosen': [
        {'full_name': 'Acme/product', 'access': 'write', 'edit_workflows': False}] + [
        {'full_name': 'Acme/' + name, 'access': 'write'} for name in ('docs', 'site', 'plain')]})
    assert off['workflow_edits_stopped'] == ['Acme/product'] and 'workflows_stopped' not in off
    assert [r for r in off['effective'] if r['full_name'] == 'Acme/product'] == [
        {'full_name': 'Acme/product', 'access': 'write', 'run_workflows': True}]


def test_all_ticked_repos_lets_one_repository_run_workflows(api, gh):
    """Dev 641: under All ticked repos the Owner ticks Run workflows for one repository. It needs Write, only that
    repository gets the Actions token, and it is kept apart from Chosen repos' ticks, as each mode keeps its own."""
    catalog(api, gh)
    runner_token(api, 'cpo')
    for name in ('product', 'docs'):
        put(api, 'repositories/Acme/' + name, {'enabled': True})
    tick = [{'full_name': 'acme/product', 'run_workflows': True}]
    data = put(api, 'bots/cpo/repositories', {'mode': 'all', 'all_access': 'write', 'all_repos': tick})
    assert data['all_repos'] == [{'full_name': 'Acme/product', 'run_workflows': True}]
    assert {r['full_name']: r.get('run_workflows', False) for r in data['effective']} == {
        'Acme/docs': False, 'Acme/emp-cpo': False, 'Acme/product': True}
    gh.permissions = {'actions': 'write', 'checks': 'read', 'statuses': 'read'}
    assert write_tokens(turn_token(api).json()) == {'Acme/docs': (), 'Acme/emp-cpo': (), 'Acme/product': ('actions',)}

    def refused(body, words):
        r = api.put('/api/v2/bots/cpo/repositories', json=body, headers={**auth(), 'Idempotency-Key': uuid.uuid4().hex})
        assert r.status_code == 422 and words in r.text, r.text
    refused({'mode': 'all', 'all_access': 'read', 'all_repos': tick}, 'needs All ticked repos set to Write')
    # Only a repository All ticked repos reaches: not a bot repository, not the bot's own, not an unticked one.
    for name in ('Acme/bot-sales', 'Acme/emp-cpo', 'Acme/nowhere', 'Other/product'):
        refused({'mode': 'all', 'all_repos': [{'full_name': name, 'run_workflows': True}]}, 'Choose' if
                name.startswith('Other') else 'is not one of the ticked repositories')
    assert put(api, 'bots/cpo/repositories', {'mode': 'all'})['all_repos'] == data['all_repos']
    # Chosen repos with write on product has no tick of its own; back to All ticked repos, the tick applies again.
    chosen = put(api, 'bots/cpo/repositories', {'mode': 'chosen', 'chosen': [{'full_name': 'Acme/product', 'access': 'write'}]})
    assert not any(r.get('run_workflows') for r in chosen['effective'] + chosen['chosen'])
    assert chosen['all_repos'] == data['all_repos'] and chosen['workflows_stopped'] == ['Acme/product']
    back = put(api, 'bots/cpo/repositories', {'mode': 'all'})
    assert [r['full_name'] for r in back['effective'] if r.get('run_workflows')] == ['Acme/product']
    # Health names it while the installation lacks Actions: write.
    gh.permissions = {'actions': 'read', 'checks': 'read', 'statuses': 'read'}
    api.app_state.github_app.live.clear()
    api.app_state.github_app.cache.clear()
    assert turn_token(api).json()['workflows_withheld']['repositories'] == ['Acme/product']
    checks = {x['id']: x for x in api.get('/api/v2/health', headers=auth()).json()['checks']}
    assert 'cpo: Acme/product' in checks['github_workflows']['summary']
    # Unticked, or All ticked repos turned to Read: the tick goes, said on the reply, and Write does not bring it back.
    off = put(api, 'bots/cpo/repositories', {'mode': 'all', 'all_repos': [{'full_name': 'Acme/product', 'run_workflows': False}]})
    assert off['workflows_stopped'] == ['Acme/product'] and off['all_repos'] == []
    put(api, 'bots/cpo/repositories', {'mode': 'all', 'all_repos': tick})
    read = put(api, 'bots/cpo/repositories', {'mode': 'all', 'all_access': 'read'})
    assert read['workflows_stopped'] == ['Acme/product'] and read['all_repos'] == []
    write = put(api, 'bots/cpo/repositories', {'mode': 'all', 'all_access': 'write'})
    assert not any(r.get('run_workflows') for r in write['effective'])
    # A repository since unticked in Settings keeps its tick and may lose it, but never gains one.
    put(api, 'bots/cpo/repositories', {'mode': 'all', 'all_repos': tick})
    put(api, 'repositories/Acme/product', {'enabled': False})
    refused({'mode': 'all', 'all_repos': [{'full_name': 'Acme/product', 'edit_workflows': True}]},
            'is not one of the ticked repositories')
    kept = put(api, 'bots/cpo/repositories', {'mode': 'all', 'all_repos': tick})
    assert kept['all_repos'] == data['all_repos'] and not any(r.get('run_workflows') for r in kept['effective'])
    put(api, 'bots/cpo/repositories', {'mode': 'all', 'all_repos': [{'full_name': 'Acme/product', 'run_workflows': False}]})
    with api.app_state.store.read() as c:
        assert R.metadata(c, R.ALL_STORED['run_workflows']) == {} and R.metadata(c, R.WORKFLOWS) == {}


def test_a_refused_workflows_write_token_falls_back_and_keeps_the_grants_actions(api, gh):
    flagged_grants(api, gh)
    # The installation reports Workflows: write, but GitHub refuses every token asking for it.
    gh.permissions = {'actions': 'write', 'workflows': 'write', 'checks': 'read', 'statuses': 'read'}
    gh.refuse_workflows_write = True
    response = turn_token(api)
    assert response.status_code == 200, response.text
    token = response.json()
    assert token['workflow_edits_withheld']['repositories'] == ['Acme/docs', 'Acme/product']
    assert 'although the installation reports it' in token['workflow_edits_withheld']['reason']
    assert 'workflows_withheld' not in token
    # Asked alone, Actions: write is accepted, so the grant flagged for both still runs workflows.
    assert write_tokens(token) == {'Acme/docs': (), 'Acme/emp-cpo': (), 'Acme/plain': (),
                                   'Acme/product': ('actions',), 'Acme/site': ('actions',)}
    checks = {x['id']: x for x in api.get('/api/v2/health', headers=auth()).json()['checks']}
    assert 'github_workflows' not in checks
    refused = checks['github_workflow_edits']
    assert refused['label'] == 'GitHub refused workflow-editing tokens' and 'cpo: Acme/docs' in refused['summary']
    assert "Change the App's permissions" not in {f['label'] for f in refused.get('fixes', [])}
    # Remembered for LIVE_TTL: the next git call falls back without asking GitHub again.
    asked = len(gh.calls)
    again = api.post('/api/v2/github/token', json={'bot': 'cpo', 'repository': 'Acme/docs', 'purpose': 'git'},
                     headers=auth('runner-test')).json()
    assert again['workflow_edits_withheld']['reason'] == token['workflow_edits_withheld']['reason']
    assert again['tokens'][0].get('workflows') is None and gh.calls[asked:] == []
    # Check permissions forgets it, and the accepted mint clears Health.
    gh.refuse_workflows_write = False
    assert api.post('/api/v2/github/app/check-permissions', headers=auth()).status_code == 200
    api.app_state.github_app.cache.clear()
    token = turn_token(api).json()
    assert 'workflow_edits_withheld' not in token and write_tokens(token)['Acme/product'] == ('actions', 'workflows')
    assert 'github_workflow_edits' not in {x['id'] for x in api.get('/api/v2/health', headers=auth()).json()['checks']}
    # Asked together, GitHub does not say which permission it refuses: each is asked alone, so a refused Actions:
    # write leaves the grant its Workflows: write.
    # The installation lacks Deployments, which the reads ask for until a refusal narrows them.
    gh.refuse_actions_write = True
    api.app_state.github_app.cache.clear()
    api.app_state.github_app.live.pop('turn-reads', None)
    one = api.post('/api/v2/github/token', json={'bot': 'cpo', 'repository': 'Acme/product', 'purpose': 'git'},
                   headers=auth('runner-test')).json()
    assert one['workflows_withheld']['repositories'] == ['Acme/product'] and 'workflow_edits_withheld' not in one
    assert write_tokens(one) == {'Acme/product': ('workflows',)}
    assert not api.app_state.github_app.raise_refused(github_app.RAISES[1])

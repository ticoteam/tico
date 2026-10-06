"""Repository contracts: migrations, sync, scoped grants and mixed computers."""
import json
import uuid

import pytest

from backend import hubdb as H, repositories as R
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
        c.execute("INSERT INTO bots(slug,display_name,created) VALUES('new-test','Sam',?)", (H.now(),))
        c.execute("INSERT INTO bot_config(bot,config_json,team,operator,repo) VALUES('new-test','{}','t','ana','bot-new-test')")
        assert json.loads(c.execute("SELECT config_json FROM bot_config WHERE bot='new-test'").fetchone()[0])['repo_access_mode'] == 'all'


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
        detail = json.loads(c.execute("SELECT detail_json FROM service_health WHERE service='github:token'").fetchone()[0])
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
        c.execute("UPDATE bot_config SET operator=? WHERE bot='cpo'", (person,))
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

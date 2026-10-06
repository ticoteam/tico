"""Phase 3 API contracts; temporary schema fixture replaced by tasks' v2 migration at merge."""
import json
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from backend import hubdb as H
from backend.tests.test_github_app import api, gh, auth, connect, runner_token  # noqa: F401
from backend.tests.test_repositories import catalog, put


@pytest.fixture
def prepared(api, gh):
    catalog(api, gh)
    runner_token(api, 'cmo')
    put(api, 'repositories/Acme/product', {'enabled': True})
    put(api, 'bots/cmo/repositories', {'mode': 'all'})
    with api.app_state.store.transaction() as c:
        # TEMPORARY: task_links v2 is owned by the tasks engineer. Drop this block at merge.
        columns = {'repo': 'TEXT', 'number': 'INTEGER', 'branch': 'TEXT', 'computer_id': 'TEXT', 'path': 'TEXT',
                   'checks': 'TEXT', 'mergeable': 'TEXT', 'review_state': 'TEXT', 'pending_comments': 'INTEGER',
                   'detail_json': 'TEXT', 'updated': 'TEXT'}
        have = {r[1] for r in c.execute('PRAGMA table_info(task_links)')}
        for name, kind in columns.items():
            if name not in have:
                c.execute(f'ALTER TABLE task_links ADD COLUMN {name} {kind}')
        c.execute("UPDATE runners SET readiness_json=? WHERE id='r1'", (json.dumps({'schema_version': 1, 'bots': {}, 'worktrees': True}),))
        task = H.task_create(c, 'human:ana', 'Build product', 'Implement product', 'bot:cmo')
        other = H.task_create(c, 'human:ana', 'Other work', 'Implement docs', 'bot:cpo')
        until = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        c.execute("INSERT INTO messages(id,from_actor,to_actor,kind,body,created) VALUES('m1','human:ana','bot:cmo','say','Work',?)", (H.now(),))
        job = c.execute("SELECT id FROM jobs WHERE message_id='m1'").fetchone()[0]
        c.execute("INSERT INTO attempts(id,job_id,bot,runner_id,generation,token_hash,state,lease_until,created) VALUES('a1',?,'cmo','r1',1,'synthetic','running',?,?)", (job, until, H.now()))
    return api, task['id'], other['id']


def post(api, path, body, token='owner-test'):
    return api.post('/api/v2/' + path, json=body, headers={**auth(token), 'Idempotency-Key': uuid.uuid4().hex})


def test_create_permissions_limit_old_computer_and_attach(prepared):
    api, tid, other = prepared
    path = f'tasks/{tid}/worktrees'
    assert post(api, f'tasks/{other}/worktrees', {'repo': 'Acme/product'}, 'bot-test').status_code == 403
    put(api, 'bots/cmo/repositories', {'mode': 'all', 'all_access': 'read'})
    assert post(api, path, {'repo': 'Acme/product'}).status_code == 403
    put(api, 'bots/cmo/repositories', {'mode': 'all', 'all_access': 'write'})
    with api.app_state.store.transaction() as c:
        c.execute("UPDATE runners SET readiness_json='{}' WHERE id='r1'")
    response = post(api, path, {'repo': 'Acme/product'})
    assert response.status_code == 409 and 'Update this computer' in response.text
    with api.app_state.store.transaction() as c:
        c.execute("UPDATE runners SET readiness_json=? WHERE id='r1'", (json.dumps({'schema_version': 1, 'bots': {}, 'worktrees': True}),))
    response = post(api, path, {'repo': 'Acme/product'}, 'bot-test')
    assert response.status_code == 200, response.text
    link = response.json()
    assert link['path'] == f'tasks/{tid[:8]}/Acme__product'
    assert link['branch'].startswith('tico/' + tid[:8])
    assert post(api, path, {'repo': 'Acme/product'}, 'bot-test').json() == link
    for i in range(9):
        response = post(api, f'tasks/{tid}/worktrees/attach', {'path': f'tasks/{tid[:8]}/extra{i}', 'repo': 'Acme/product'}, 'bot-test')
        assert response.status_code == 200, response.text
    assert post(api, f'tasks/{tid}/worktrees/attach', {'path': 'tasks/eleven', 'repo': 'Acme/product'}, 'bot-test').status_code == 409
    assert post(api, f'tasks/{tid}/worktrees/attach', {'path': '../escape', 'repo': 'Acme/product'}, 'bot-test').status_code == 422
    response = api.patch(f'/api/v2/tasks/{tid}/links/{link["link_id"]}', json={'state': 'present', 'computer_id': 'elsewhere'}, headers={**auth('runner-test'), 'Idempotency-Key': uuid.uuid4().hex})
    assert response.status_code == 403


def test_heartbeat_cleanup_waits_for_prs_restore_and_old_report(prepared):
    api, tid, _ = prepared
    link = post(api, f'tasks/{tid}/worktrees', {'repo': 'Acme/product'}).json()
    body = {'version': '0.3.2', 'platform': 'linux', 'readiness': {'schema_version': 1, 'bots': {}, 'worktrees': True},
            'worktrees': [{'link_id': link['link_id'], 'state': 'present', 'branch': link['branch'], 'ahead': 2, 'dirty_files': 1, 'last_commit': 'abc'}]}
    response = post(api, 'runners/heartbeat', body, 'runner-test')
    assert response.status_code == 200, response.text
    assert response.json()['worktree_actions'] == []
    with api.app_state.store.transaction() as c:
        row = c.execute('SELECT * FROM task_links WHERE id=?', (link['link_id'],)).fetchone()
        assert row['state'] == 'present' and json.loads(row['detail_json'])['ahead'] == 2
        H.task_link(c, 'bot:cmo', tid, 'https://github.com/Acme/product/pull/1')
        c.execute("UPDATE task_links SET state='open' WHERE kind='pr'")
        c.execute("UPDATE tasks SET status='closed' WHERE id=?", (tid,))
    assert post(api, 'runners/heartbeat', body, 'runner-test').json()['worktree_actions'] == []
    with api.app_state.store.transaction() as c:
        c.execute("UPDATE task_links SET state='merged' WHERE kind='pr'")
    actions = post(api, 'runners/heartbeat', body, 'runner-test').json()['worktree_actions']
    assert len(actions) == 1 and actions[0]['action'] == 'remove'
    body['worktrees'][0]['state'] = 'removed'
    assert post(api, 'runners/heartbeat', body, 'runner-test').json()['worktree_actions'] == []
    with api.app_state.store.transaction() as c:
        c.execute("UPDATE tasks SET status='open' WHERE id=?", (tid,))
    assert post(api, 'runners/heartbeat', body, 'runner-test').json()['worktree_actions'][0]['action'] == 'restore'
    body.pop('worktrees')
    body['readiness'].pop('worktrees')
    assert post(api, 'runners/heartbeat', body, 'runner-test').json()['worktree_actions'] == []


def test_archived_cleanup_token_is_repository_scoped_and_stale_wakes_once(prepared, gh):
    api, tid, _ = prepared
    link = post(api, f'tasks/{tid}/worktrees', {'repo': 'Acme/product'}).json()
    body = {'version': '0.3.2', 'platform': 'linux', 'readiness': {'schema_version': 1, 'worktrees': True},
            'worktrees': [{'link_id': link['link_id'], 'state': 'missing'}]}
    old = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
    with api.app_state.store.transaction() as c:
        c.execute('UPDATE task_links SET detail_json=? WHERE id=?', (json.dumps({'missing_since': old}), link['link_id']))
    for _ in range(2):
        assert post(api, 'runners/heartbeat', body, 'runner-test').status_code == 200
    with api.app_state.store.transaction() as c:
        assert c.execute("SELECT count(*) FROM messages WHERE body LIKE 'Worktree missing for a day:%'").fetchone()[0] == 1
        c.execute("UPDATE bots SET state='archived' WHERE slug='cmo'")
        c.execute("DELETE FROM assignments WHERE bot='cmo'")
    token_path = 'runners/me/worktrees/' + link['link_id'] + '/token'
    response = post(api, token_path, {}, 'runner-test')
    assert response.status_code == 200, response.text
    assert gh.of('/access_tokens')[-1][2] == {'repositories': ['product'], 'permissions': {'contents': 'write', 'metadata': 'read'}}
    put(api, 'bots/cmo/repositories', {'mode': 'own'})
    assert post(api, token_path, {}, 'runner-test').status_code == 403


def test_pre_migration_schema_has_no_worktree_reads_or_actions():
    import sqlite3
    from backend import worktrees as W
    from backend.auth import Identity
    with sqlite3.connect(':memory:') as c:
        c.execute('CREATE TABLE task_links(id TEXT,kind TEXT,state TEXT)')
        assert W.inventory(c, 'r1') == []
        assert W.heartbeat(c, Identity('runner:r1', 'runner', runner_id='r1'), [], True) == []
        assert {r[1] for r in c.execute('PRAGMA table_info(task_links)')} == {'id', 'kind', 'state'}


def test_no_app_accepts_computer_git_addresses_but_connected_app_keeps_grants(prepared):
    api, tid, _ = prepared
    url = 'ssh://git@git.example.com/team/product.git'
    assert post(api, f'tasks/{tid}/worktrees', {'repo': url}).status_code == 403
    with api.app_state.store.transaction() as c:
        c.execute('DELETE FROM github_app')
    inventory = api.get('/api/v2/runners/me/repositories', headers=auth('runner-test')).json()
    assert inventory['configured'] is False
    for repo in ('Other/product', url, 'https://git.example.com/team/docs.git', 'file:///tmp/example.git'):
        response = post(api, f'tasks/{tid}/worktrees', {'repo': repo})
        assert response.status_code == 200, response.text
        link = response.json()
        credential = post(api, 'runners/me/worktrees/' + link['link_id'] + '/token', {}, 'runner-test')
        assert credential.json() == {'configured': False, 'token': None}
        with api.app_state.store.read() as c:
            assert c.execute('SELECT repo FROM task_links WHERE id=?', (link['link_id'],)).fetchone()[0] == repo
    assert post(api, f'tasks/{tid}/worktrees', {'repo': 'https://git.example.com/a.git?token=example'}).status_code == 422


def test_unlink_needs_move_rights_and_keeps_inventory_until_cleanup(prepared):
    from backend import worktrees as W
    api, tid, _ = prepared
    link = post(api, f'tasks/{tid}/worktrees', {'repo': 'Acme/product'}).json()
    with api.app_state.store.transaction() as c:
        c.execute("UPDATE task_links SET state='present' WHERE id=?", (link['link_id'],))
        with pytest.raises(H.Refused):
            H.task_unlink(c, 'bot:cpo', tid, link['link_id'])
        H.task_unlink(c, 'human:ana', tid, link['link_id'], mover=True)
        H.task_update(c, 'human:ana', tid, owner='human:ana', mover=True)
        rows = W.inventory(c, 'r1')
        assert len(rows) == 1 and rows[0]['owner'] == 'bot:cmo'
    body = {'version': '0.3.2', 'platform': 'linux', 'readiness': {'schema_version': 1, 'worktrees': True},
            'worktrees': [{'link_id': link['link_id'], 'state': 'present'}]}
    assert post(api, 'runners/heartbeat', body, 'runner-test').json()['worktree_actions'][0]['action'] == 'remove'
    body['worktrees'][0]['state'] = 'removed'
    assert post(api, 'runners/heartbeat', body, 'runner-test').json()['worktree_actions'] == []
    with api.app_state.store.read() as c:
        assert not c.execute('SELECT 1 FROM task_links WHERE id=?', (link['link_id'],)).fetchone()


def test_old_computer_token_needs_an_outstanding_action(prepared):
    api, tid, _ = prepared
    link = post(api, f'tasks/{tid}/worktrees', {'repo': 'Acme/product'}, 'bot-test').json()
    route = 'runners/me/worktrees/' + link['link_id'] + '/token'
    with api.app_state.store.transaction() as c:
        c.execute("UPDATE task_links SET state='present' WHERE id=?", (link['link_id'],))
        c.execute("DELETE FROM assignments WHERE bot='cmo'")
    assert post(api, route, {}, 'runner-test').status_code == 403
    with api.app_state.store.transaction() as c:
        c.execute("UPDATE tasks SET status='closed' WHERE id=?", (tid,))
    assert post(api, route, {}, 'runner-test').status_code == 200

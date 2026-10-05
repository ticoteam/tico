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


def mark_ready(api, tid, link_id):
    return api.patch(f'/api/v2/tasks/{tid}/links/{link_id}',
                     json={'state': 'present', 'checkout_state': 'ready', 'setup_pending': False,
                           'expected_head': 'a' * 40, 'checkout_target': 'refs/heads/tico/test',
                           'expected_base': 'b' * 64},
                     headers={**auth('runner-test'), 'Idempotency-Key': uuid.uuid4().hex})


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
    assert mark_ready(api, tid, link['link_id']).status_code == 200
    body = {'version': '0.3.2', 'platform': 'linux', 'readiness': {'schema_version': 1, 'bots': {}, 'worktrees': True},
            'worktrees': [{'link_id': link['link_id'], 'state': 'present', 'checkout_state': 'ready',
                           'branch': link['branch'], 'ahead': 2, 'dirty_files': 1, 'last_commit': 'abc'}]}
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
    route = f'/api/v2/tasks/{tid}/links/{link["link_id"]}'
    progress = api.patch(route, json={'state': 'pending', 'checkout_state': 'checkout_ready',
                                      'setup_pending': True, 'expected_head': 'a' * 40,
                                      'checkout_target': 'refs/heads/tico/test', 'expected_base': 'b' * 64},
                         headers={**auth('runner-test'), 'Idempotency-Key': uuid.uuid4().hex})
    assert progress.status_code == 200, progress.text
    body['worktrees'] = [{'link_id': link['link_id'], 'state': 'pending', 'checkout_state': 'checkout_ready',
                          'branch': link['branch']}]
    assert post(api, 'runners/heartbeat', body, 'runner-test').json()['worktree_actions'] == []
    body.pop('worktrees')
    body['readiness'].pop('worktrees')
    assert post(api, 'runners/heartbeat', body, 'runner-test').json()['worktree_actions'] == []


def test_archived_cleanup_token_is_repository_scoped_and_stale_wakes_once(prepared, gh):
    api, tid, _ = prepared
    link = post(api, f'tasks/{tid}/worktrees', {'repo': 'Acme/product'}).json()
    assert mark_ready(api, tid, link['link_id']).status_code == 200
    body = {'version': '0.3.2', 'platform': 'linux', 'readiness': {'schema_version': 1, 'worktrees': True},
            'worktrees': [{'link_id': link['link_id'], 'state': 'missing'}]}
    old = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
    with api.app_state.store.transaction() as c:
        saved = c.execute('SELECT detail_json FROM task_links WHERE id=?', (link['link_id'],)).fetchone()
        detail = json.loads(saved['detail_json'])
        detail['missing_since'] = old
        c.execute('UPDATE task_links SET detail_json=? WHERE id=?', (json.dumps(detail), link['link_id']))
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


def test_attached_worktree_branch_follows_computer_report(prepared):
    api, tid, _ = prepared
    response = post(api, f'tasks/{tid}/worktrees/attach', {'path': 'custom/any-folder'}, 'bot-test')
    assert response.status_code == 200, response.text
    link = response.json()
    body = {'version': '0.3.2', 'platform': 'linux', 'readiness': {'schema_version': 1, 'worktrees': True},
            'worktrees': [{'link_id': link['link_id'], 'state': 'present', 'branch': 'feature/custom', 'repo': 'Acme/product'}]}
    assert post(api, 'runners/heartbeat', body, 'runner-test').status_code == 200
    with api.app_state.store.read() as c:
        row = c.execute('SELECT * FROM task_links WHERE id=?', (link['link_id'],)).fetchone()
        assert row['branch'] == 'feature/custom' and row['repo'] == 'Acme/product'


def test_human_request_is_created_on_next_computer_heartbeat(prepared):
    api, tid, _ = prepared
    link = post(api, f'tasks/{tid}/worktrees', {'repo': 'Acme/product'}).json()
    body = {'version': '0.3.2', 'platform': 'linux', 'readiness': {'schema_version': 1, 'worktrees': True},
            'worktrees': [{'link_id': link['link_id'], 'state': 'missing'}]}
    response = post(api, 'runners/heartbeat', body, 'runner-test')
    assert response.status_code == 200, response.text
    assert response.json()['worktree_actions'][0]['action'] == 'restore'


def test_shipped_cleanup_and_branch_identity_survive_snapshot_heartbeat(prepared):
    api, tid, _ = prepared
    link = post(api, f'tasks/{tid}/worktrees', {'repo': 'Acme/product'}).json()
    with api.app_state.store.transaction() as c:
        H.task_link(c, 'bot:cmo', tid, 'https://github.com/Acme/product/pull/1')
        c.execute("UPDATE task_links SET state='shipped' WHERE kind='pr'")
        c.execute("UPDATE tasks SET status='done' WHERE id=?", (tid,))
    body = {'version': '0.3.2', 'platform': 'linux', 'readiness': {'schema_version': 1, 'worktrees': True},
            'worktrees': [{'link_id': link['link_id'], 'state': 'present', 'branch': 'wip/example'}]}
    response = post(api, 'runners/heartbeat', body, 'runner-test')
    assert response.status_code == 200, response.text
    assert response.json()['worktree_actions'][0]['action'] == 'remove'
    assert response.json()['worktree_actions'][0]['prs_finished'] is True
    with api.app_state.store.read() as c:
        row = c.execute('SELECT * FROM task_links WHERE id=?', (link['link_id'],)).fetchone()
        assert row['branch'] == link['branch']
        assert json.loads(row['detail_json'])['current_branch'] == 'wip/example'


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


def test_reviving_removed_link_counts_limit_and_transfers_to_current_owner(prepared):
    api, tid, _ = prepared
    link = post(api, f'tasks/{tid}/worktrees', {'repo': 'Acme/product'}).json()
    with api.app_state.store.transaction() as c:
        c.execute("UPDATE task_links SET state='removed',detail_json=? WHERE id=?", (json.dumps({'owner': 'bot:cpo'}), link['link_id']))
    for i in range(10):
        assert post(api, f'tasks/{tid}/worktrees/attach', {'repo': 'Acme/product', 'path': f'tasks/{tid[:8]}/extra{i}'}).status_code == 200
    response = post(api, f'tasks/{tid}/worktrees', {'repo': 'Acme/product'})
    assert response.status_code == 409 and '10 worktrees' in response.text
    with api.app_state.store.transaction() as c:
        c.execute("UPDATE task_links SET state='removed' WHERE path LIKE '%/extra0'")
    assert post(api, f'tasks/{tid}/worktrees', {'repo': 'Acme/product'}).json() == link
    with api.app_state.store.read() as c:
        row = c.execute('SELECT * FROM task_links WHERE id=?', (link['link_id'],)).fetchone()
        assert row['state'] == 'pending' and json.loads(row['detail_json'])['owner'] == 'bot:cmo'


def test_failed_bot_creation_manual_removal_and_absent_at_close_do_not_restore(prepared):
    api, tid, _ = prepared
    link = post(api, f'tasks/{tid}/worktrees', {'repo': 'Acme/product'}, 'bot-test').json()
    body = {'version': '0.3.2', 'platform': 'linux', 'readiness': {'schema_version': 1, 'worktrees': True},
            'worktrees': [{'link_id': link['link_id'], 'state': 'missing'}]}
    assert post(api, 'runners/heartbeat', body, 'runner-test').json()['worktree_actions'] == []
    with api.app_state.store.transaction() as c:
        c.execute("UPDATE tasks SET status='closed' WHERE id=?", (tid,))
    assert post(api, 'runners/heartbeat', body, 'runner-test').json()['worktree_actions'][0]['action'] == 'remove'
    body['worktrees'][0]['state'] = 'removed'
    post(api, 'runners/heartbeat', body, 'runner-test')
    with api.app_state.store.transaction() as c:
        c.execute("UPDATE tasks SET status='open' WHERE id=?", (tid,))
    assert post(api, 'runners/heartbeat', body, 'runner-test').json()['worktree_actions'] == []
    response = api.patch(f'/api/v2/tasks/{tid}/links/{link["link_id"]}', json={'state': 'removed'}, headers={**auth('bot-test'), 'Idempotency-Key': uuid.uuid4().hex})
    assert response.status_code == 200, response.text
    assert post(api, 'runners/heartbeat', body, 'runner-test').json()['worktree_actions'] == []


def test_case_alias_and_unidentified_token_are_safe(prepared):
    api, tid, other = prepared
    link = post(api, f'tasks/{tid}/worktrees/attach', {'path': 'custom/worktree'}).json()
    response = post(api, f'tasks/{tid}/worktrees/attach', {'path': 'CUSTOM/WORKTREE', 'repo': 'Acme/product'})
    assert response.status_code == 409
    response = post(api, 'runners/me/worktrees/' + link['link_id'] + '/token', {}, 'runner-test')
    assert response.status_code == 409 and 'Repository not identified yet' in response.text


def test_unlink_needs_move_rights_and_keeps_inventory_until_cleanup(prepared):
    from backend import worktrees as W
    api, tid, _ = prepared
    link = post(api, f'tasks/{tid}/worktrees', {'repo': 'Acme/product'}).json()
    with api.app_state.store.transaction() as c:
        c.execute("UPDATE task_links SET state='present',detail_json=? WHERE id=?",
                  (json.dumps({'owner': 'bot:cmo', 'checkout_state': 'ready', 'setup_pending': False}), link['link_id']))
        with pytest.raises(H.Refused):
            H.task_unlink(c, 'bot:cpo', tid, link['link_id'])
        H.task_unlink(c, 'human:ana', tid, link['link_id'], mover=True)
        H.task_update(c, 'human:ana', tid, owner='human:ana', mover=True)
        rows = W.inventory(c, 'r1')
        assert len(rows) == 1 and rows[0]['owner'] == 'bot:cmo'
    body = {'version': '0.3.2', 'platform': 'linux', 'readiness': {'schema_version': 1, 'worktrees': True},
            'worktrees': [{'link_id': link['link_id'], 'state': 'present', 'checkout_state': 'ready'}]}
    assert post(api, 'runners/heartbeat', body, 'runner-test').json()['worktree_actions'][0]['action'] == 'remove'
    body['worktrees'][0]['state'] = 'removed'
    assert post(api, 'runners/heartbeat', body, 'runner-test').json()['worktree_actions'] == []
    with api.app_state.store.read() as c:
        assert not c.execute('SELECT 1 FROM task_links WHERE id=?', (link['link_id'],)).fetchone()


def test_org_fallback_for_path_only_attachment_without_app(prepared):
    api, tid, _ = prepared
    with api.app_state.store.transaction() as c:
        c.execute("UPDATE bot_config SET repo='emp-cmo' WHERE bot='cmo'")
    response = post(api, f'tasks/{tid}/worktrees/attach', {'path': 'custom/fallback'})
    assert response.status_code == 200, response.text
    link = response.json()
    with api.app_state.store.transaction() as c:
        c.execute("DELETE FROM github_app WHERE id='app'")
    body = {'version': '0.3.2', 'platform': 'linux', 'readiness': {'schema_version': 1, 'worktrees': True},
            'worktrees': [{'link_id': link['link_id'], 'state': 'present', 'branch': 'feature/fallback', 'repo': 'Acme/emp-cmo'}]}
    response = post(api, 'runners/heartbeat', body, 'runner-test')
    assert response.status_code == 200, response.text
    with api.app_state.store.read() as c:
        assert c.execute('SELECT repo FROM task_links WHERE id=?', (link['link_id'],)).fetchone()[0] == 'Acme/emp-cmo'


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


def test_reopen_add_reuses_link_and_attach_keeps_original_owner(prepared):
    api, tid, other = prepared
    link = post(api, f'tasks/{tid}/worktrees', {'repo': 'Acme/product'}, 'bot-test').json()
    with api.app_state.store.transaction() as c:
        c.execute("UPDATE task_links SET state='removed',detail_json=? WHERE id=?", (json.dumps({'owner': 'bot:cmo', 'removed_by': 'cleanup', 'restore_on_reopen': True}), link['link_id']))
    added = post(api, f'tasks/{tid}/worktrees', {'repo': 'Acme/product'}, 'bot-test')
    assert added.status_code == 200 and added.json() == link
    with api.app_state.store.read() as c:
        assert c.execute("SELECT count(*) FROM task_links WHERE kind='worktree'").fetchone()[0] == 1
        assert c.execute('SELECT state FROM task_links WHERE id=?', (link['link_id'],)).fetchone()[0] == 'pending'
    assert post(api, f'tasks/{tid}/worktrees/attach', {'path': f'tasks/{other[:8]}/unattached', 'repo': 'Acme/product'}).status_code == 409
    assert post(api, f'tasks/{tid}/worktrees/attach', {'path': f'tasks/{tid[:8]}/manual', 'repo': 'Acme/product'}).status_code == 200
    with api.app_state.store.transaction() as c:
        c.execute("UPDATE task_links SET state='removed',detail_json=? WHERE id=?", (json.dumps({'owner': 'bot:cpo'}), link['link_id']))
    assert post(api, f'tasks/{tid}/worktrees/attach', {'path': link['path'], 'repo': 'Acme/product'}).status_code == 200
    with api.app_state.store.read() as c:
        assert json.loads(c.execute('SELECT detail_json FROM task_links WHERE id=?', (link['link_id'],)).fetchone()[0])['owner'] == 'bot:cmo'


def test_unidentified_missing_link_is_removed_and_frees_limit(prepared):
    api, tid, _ = prepared
    link = post(api, f'tasks/{tid}/worktrees/attach', {'path': f'tasks/{tid[:8]}/manual'}).json()
    with api.app_state.store.transaction() as c:
        c.execute("UPDATE tasks SET status='closed' WHERE id=?", (tid,))
    body = {'version': '0.3.2', 'platform': 'linux', 'readiness': {'schema_version': 1, 'worktrees': True},
            'worktrees': [{'link_id': link['link_id'], 'state': 'missing'}]}
    action = post(api, 'runners/heartbeat', body, 'runner-test').json()['worktree_actions'][0]
    assert action['action'] == 'remove' and action['repo'] is None
    body['worktrees'][0]['state'] = 'removed'
    assert post(api, 'runners/heartbeat', body, 'runner-test').json()['worktree_actions'] == []
    with api.app_state.store.read() as c:
        assert c.execute("SELECT count(*) FROM task_links WHERE kind='worktree' AND state<>'removed'").fetchone()[0] == 0


def test_reassigned_task_can_create_fresh_worktree_while_old_owner_keeps_cleanup(prepared):
    api, tid, _ = prepared
    old = post(api, f'tasks/{tid}/worktrees', {'repo': 'Acme/product'}).json()
    with api.app_state.store.transaction() as c:
        c.execute("UPDATE task_links SET detail_json=? WHERE id=?", (json.dumps({'owner': 'bot:cpo'}), old['link_id']))
    new = post(api, f'tasks/{tid}/worktrees', {'repo': 'Acme/product'})
    assert new.status_code == 200, new.text
    new = new.json()
    assert new['path'] != old['path'] and new['branch'] != old['branch']
    assert post(api, f'tasks/{tid}/worktrees', {'repo': 'Acme/product'}).json() == new
    with api.app_state.store.read() as c:
        assert json.loads(c.execute('SELECT detail_json FROM task_links WHERE id=?', (old['link_id'],)).fetchone()[0])['owner'] == 'bot:cpo'


def test_initializing_checkout_stays_pending_until_checkout_and_setup_complete(prepared):
    api, tid, _ = prepared
    link = post(api, f'tasks/{tid}/worktrees', {'repo': 'Acme/product'}).json()
    assert link['checkout_state'] == 'queued' and link['setup_pending'] is True
    beat = {'version': '0.3.21', 'platform': 'test', 'readiness': {'schema_version': 1, 'worktrees': True},
            'worktrees': [{'link_id': link['link_id'], 'state': 'present', 'checkout_state': 'initializing',
                           'branch': link['branch'], 'repo': 'Acme/product'}]}
    response = post(api, 'runners/heartbeat', beat, 'runner-test')
    assert response.status_code == 200, response.text
    with api.app_state.store.read() as c:
        saved = c.execute('SELECT state,detail_json FROM task_links WHERE id=?', (link['link_id'],)).fetchone()
        assert saved['state'] == 'pending'
        assert json.loads(saved['detail_json'])['checkout_state'] == 'initializing'

    route = f'/api/v2/tasks/{tid}/links/{link["link_id"]}'
    headers = {**auth('runner-test'), 'Idempotency-Key': uuid.uuid4().hex}
    response = api.patch(route, json={'state': 'present', 'checkout_state': 'ready', 'setup_pending': True}, headers=headers)
    assert response.status_code == 409 and response.json()['error']['code'] == 'worktree_not_ready'
    response = api.patch(route, json={'state': 'present', 'checkout_state': 'ready', 'setup_pending': False},
                         headers={**headers, 'Idempotency-Key': uuid.uuid4().hex})
    assert response.status_code == 409 and response.json()['error']['code'] == 'worktree_not_ready'
    response = api.patch(route, json={'state': 'present', 'checkout_state': 'ready', 'setup_pending': False,
                                      'expected_head': 'a' * 40, 'checkout_target': 'refs/heads/tico/test',
                                      'expected_base': 'b' * 64},
                         headers={**headers, 'Idempotency-Key': uuid.uuid4().hex})
    assert response.status_code == 200, response.text
    with api.app_state.store.read() as c:
        saved = c.execute('SELECT state,detail_json FROM task_links WHERE id=?', (link['link_id'],)).fetchone()
        assert saved['state'] == 'present'
        assert json.loads(saved['detail_json'])['checkout_state'] == 'ready'


def test_legacy_pending_link_is_not_promoted_by_git_directory_alone(prepared):
    api, tid, _ = prepared
    link = post(api, f'tasks/{tid}/worktrees', {'repo': 'Acme/product'}, 'bot-test').json()
    with api.app_state.store.transaction() as c:
        c.execute("UPDATE task_links SET state='present',detail_json=? WHERE id=?", (json.dumps({'owner': 'bot:cmo'}), link['link_id']))
    beat = {'version': '0.3.21', 'platform': 'test', 'readiness': {'schema_version': 1, 'worktrees': True},
            'worktrees': [{'link_id': link['link_id'], 'state': 'present', 'branch': link['branch'],
                           'repo': 'Acme/product'}]}
    response = post(api, 'runners/heartbeat', beat, 'runner-test')
    assert response.status_code == 200, response.text
    with api.app_state.store.read() as c:
        saved = c.execute('SELECT state FROM task_links WHERE id=?', (link['link_id'],)).fetchone()
        assert saved['state'] == 'pending'


def test_verified_legacy_present_heartbeat_preserves_present_but_never_promotes_pending(prepared):
    api, tid, _ = prepared
    present = post(api, f'tasks/{tid}/worktrees', {'repo': 'Acme/product'}, 'bot-test').json()
    pending = post(api, f'tasks/{tid}/worktrees/attach',
                   {'path': f'tasks/{tid[:8]}/legacy-pending', 'repo': 'Acme/product'}, 'bot-test').json()
    pending_stale = post(api, f'tasks/{tid}/worktrees/attach',
                         {'path': f'tasks/{tid[:8]}/legacy-stale', 'repo': 'Acme/product'}, 'bot-test').json()
    with api.app_state.store.transaction() as c:
        c.execute("UPDATE task_links SET state='present',detail_json=? WHERE id=?",
                  (json.dumps({'owner': 'bot:cmo'}), present['link_id']))
        c.execute("UPDATE task_links SET detail_json=? WHERE id=?",
                  (json.dumps({'owner': 'bot:cmo'}), pending['link_id']))
        c.execute("UPDATE task_links SET detail_json=? WHERE id=?",
                  (json.dumps({'owner': 'bot:cmo', 'checkout_state': 'legacy_present'}), pending_stale['link_id']))
    beat = {'version': '0.3.21', 'platform': 'test', 'readiness': {'schema_version': 1, 'worktrees': True},
            'worktrees': [
                {'link_id': present['link_id'], 'state': 'present', 'checkout_state': 'legacy_present',
                 'branch': present['branch'], 'repo': 'Acme/product', 'dirty_files': 2},
                {'link_id': pending['link_id'], 'state': 'present', 'checkout_state': 'legacy_present',
                 'branch': pending['branch'], 'repo': 'Acme/product'},
                {'link_id': pending_stale['link_id'], 'state': 'present', 'checkout_state': 'legacy_present',
                 'branch': pending_stale['branch'], 'repo': 'Acme/product'}]}
    response = post(api, 'runners/heartbeat', beat, 'runner-test')
    assert response.status_code == 200, response.text
    with api.app_state.store.read() as c:
        rows = {r['id']: r for r in c.execute(
            'SELECT id,state,detail_json FROM task_links WHERE id IN (?,?,?)',
            (present['link_id'], pending['link_id'], pending_stale['link_id']))}
    assert rows[present['link_id']]['state'] == 'present'
    present_detail = json.loads(rows[present['link_id']]['detail_json'])
    assert present_detail['checkout_state'] == 'legacy_present' and present_detail['dirty_files'] == 2
    assert not any(key in present_detail for key in ('expected_head', 'checkout_target', 'expected_base'))
    assert rows[pending['link_id']]['state'] == 'pending'
    pending_detail = json.loads(rows[pending['link_id']]['detail_json'])
    assert pending_detail.get('checkout_state') is None
    assert rows[pending_stale['link_id']]['state'] == 'pending'
    stale_detail = json.loads(rows[pending_stale['link_id']]['detail_json'])
    assert stale_detail['checkout_state'] == 'unverified'


def test_human_pending_link_stops_repeating_restore_after_checkout_progress(prepared):
    api, tid, _ = prepared
    link = post(api, f'tasks/{tid}/worktrees', {'repo': 'Acme/product'}, 'bot-test').json()
    with api.app_state.store.transaction() as c:
        c.execute("UPDATE task_links SET added_by='human:ana',state='pending',detail_json=? WHERE id=?",
                  (json.dumps({'owner': 'bot:cmo'}), link['link_id']))
    beat = {'version': '0.3.21', 'platform': 'test', 'readiness': {'schema_version': 1, 'worktrees': True},
            'worktrees': []}
    first = post(api, 'runners/heartbeat', beat, 'runner-test')
    assert first.status_code == 200, first.text
    assert len(first.json()['worktree_actions']) == 1
    assert first.json()['worktree_actions'][0]['action'] == 'restore'

    route = f'/api/v2/tasks/{tid}/links/{link["link_id"]}'
    patched = api.patch(route, json={'state': 'pending', 'checkout_state': 'checkout_ready',
                                     'setup_pending': True, 'expected_head': 'a' * 40,
                                     'checkout_target': 'refs/heads/tico/test', 'expected_base': 'b' * 64},
                        headers={**auth('runner-test'), 'Idempotency-Key': uuid.uuid4().hex})
    assert patched.status_code == 200, patched.text
    second = post(api, 'runners/heartbeat', beat, 'runner-test')
    assert second.status_code == 200, second.text
    assert second.json()['worktree_actions'] == []

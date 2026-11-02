"""Phase 3 API contracts; temporary schema fixture replaced by tasks' v2 migration at merge."""
import json
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from backend import hubdb as H
from backend import worktrees as W
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
                   'detail': 'TEXT', 'updated_at': 'TEXT'}
        have = {r[1] for r in c.execute('PRAGMA table_info(task_links)')}
        for name, kind in columns.items():
            if name not in have:
                c.execute(f'ALTER TABLE task_links ADD COLUMN {name} {kind}')
        c.execute("UPDATE runners SET readiness=? WHERE id='r1'", (json.dumps({'schema_version': 1, 'bots': {}, 'worktrees': True}),))
        task = H.task_create(c, 'human:ana', 'Build product', 'Implement product', 'bot:cmo')
        c.execute("UPDATE tasks SET status='doing' WHERE status='open'")  # a started task gets a worktree
        other = H.task_create(c, 'human:ana', 'Other work', 'Implement docs', 'bot:cpo')
        c.execute("UPDATE tasks SET status='doing' WHERE status='open'")  # a started task gets a worktree
        until = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        c.execute("INSERT INTO messages(id,sender_id,recipient_id,kind,body,created_at) VALUES('m1','human:ana','bot:cmo','say','Work',?)", (H.now(),))
        job = c.execute("SELECT id FROM jobs WHERE message_id='m1'").fetchone()[0]
        c.execute("INSERT INTO attempts(id,job_id,bot_id,runner_id,generation,token_hash,state,lease_until,created_at) VALUES('a1',?,'cmo','r1',1,'synthetic','running',?,?)", (job, until, H.now()))
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
        c.execute("UPDATE runners SET readiness='{}' WHERE id='r1'")
    response = post(api, path, {'repo': 'Acme/product'})
    assert response.status_code == 409 and 'Update this computer' in response.text
    with api.app_state.store.transaction() as c:
        c.execute("UPDATE runners SET readiness=? WHERE id='r1'", (json.dumps({'schema_version': 1, 'bots': {}, 'worktrees': True}),))
    response = post(api, path, {'repo': 'Acme/product'}, 'bot-test')
    assert response.status_code == 200, response.text
    link = response.json()
    assert link['path'] == f'tasks/{tid[:8]}/Acme__product'
    assert link['branch'].startswith('tico/' + tid[:8])
    assert post(api, path, {'repo': 'Acme/product'}, 'bot-test').json() == link
    for i in range(W.LIMIT - 1):
        response = post(api, f'tasks/{tid}/worktrees/attach', {'path': f'tasks/{tid[:8]}/extra{i}', 'repo': 'Acme/product'}, 'bot-test')
        assert response.status_code == 200, response.text
    refused = post(api, f'tasks/{tid}/worktrees/attach', {'path': f'tasks/{tid[:8]}/over', 'repo': 'Acme/product'}, 'bot-test')
    assert refused.status_code == 409 and 'Oldest idle: ' in refused.json()['error']['detail']
    assert post(api, f'tasks/{tid}/worktrees/attach', {'path': '../escape', 'repo': 'Acme/product'}, 'bot-test').status_code == 422
    response = api.patch(f'/api/v2/tasks/{tid}/links/{link["link_id"]}', json={'state': 'present', 'computer_id': 'elsewhere'}, headers={**auth('runner-test'), 'Idempotency-Key': uuid.uuid4().hex})
    assert response.status_code == 403


def test_a_bot_can_create_its_51st_worktree_but_not_its_101st(prepared):
    api, tid, _ = prepared
    with api.app_state.store.transaction() as c:
        filler = H.task_create(c, 'human:ana', 'Parked work', 'Existing checkouts', 'bot:cmo')['id']
        c.execute("UPDATE tasks SET status='doing' WHERE id=?", (filler,))

    def seed(start, stop):
        with api.app_state.store.transaction() as c:
            for i in range(start, stop):
                link = uuid.uuid4().hex
                c.execute("INSERT INTO task_links(id,task_id,kind,url,title,state,added_by,created_at,repo,path,detail) "
                          "VALUES(?,?,'worktree',?,'Parked','present','bot:cmo',?,'Acme/product',?,?)",
                          (link, filler, 'worktree:' + link, H.now(), f'tasks/{filler[:8]}/parked{i}',
                           json.dumps({'owner': 'bot:cmo'})))

    seed(0, 50)
    response = post(api, f'tasks/{tid}/worktrees', {'repo': 'Acme/product'}, 'bot-test')
    assert response.status_code == 200, response.text
    seed(50, 99)  # 99 parked checkouts plus the newly created one fill all 100 places.
    refused = post(api, f'tasks/{filler}/worktrees', {'repo': 'Acme/product'}, 'bot-test')
    assert refused.status_code == 409, refused.text
    assert refused.json()['error']['code'] == 'worktree_limit'
    assert refused.json()['error']['limit'] == 100


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
        assert row['state'] == 'present' and json.loads(row['detail'])['ahead'] == 2
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
        saved = c.execute('SELECT detail FROM task_links WHERE id=?', (link['link_id'],)).fetchone()
        detail = json.loads(saved['detail'])
        detail['missing_since'] = old
        c.execute('UPDATE task_links SET detail=? WHERE id=?', (json.dumps(detail), link['link_id']))
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


def test_unlink_retries_exhausted_cleanup_with_move_rights_and_keeps_work_until_removed(prepared):
    api, tid, _ = prepared
    link = post(api, f'tasks/{tid}/worktrees', {'repo': 'Acme/product'}).json()
    assert mark_ready(api, tid, link['link_id']).status_code == 200
    error = 'Worktree action failed; history kept. kept: ignored files'
    with api.app_state.store.transaction() as c:
        detail = json.loads(c.execute('SELECT detail FROM task_links WHERE id=?', (link['link_id'],)).fetchone()[0])
        detail.update(cleanup_requested=True, cleanup_attempts=W.CLEANUP_TRIES, cleanup_gave_up=True,
                      cleanup_retry_at=H.shift(H.now(), minutes=60), error=error, cleanup_retry_generation='previous-request')
        c.execute('UPDATE task_links SET detail=? WHERE id=?', (json.dumps(detail), link['link_id']))
        c.execute("UPDATE tasks SET status='closed' WHERE id=?", (tid,))
    report = {'link_id': link['link_id'], 'state': 'present', 'checkout_state': 'ready',
              'branch': link['branch'], 'ahead': 1, 'dirty_files': 1, 'error': error}
    body = {'version': '0.3.2', 'platform': 'linux', 'readiness': {'schema_version': 1, 'worktrees': True},
            'worktrees': [report]}
    assert post(api, 'runners/heartbeat', body, 'runner-test').json()['worktree_actions'] == []
    with api.app_state.store.transaction() as c:
        with pytest.raises(H.Refused):
            H.task_unlink(c, 'bot:cpo', tid, link['link_id'])
        saved = json.loads(c.execute('SELECT detail FROM task_links WHERE id=?', (link['link_id'],)).fetchone()[0])
        assert saved['cleanup_attempts'] == W.CLEANUP_TRIES and saved['cleanup_gave_up']
        assert saved['cleanup_retry_generation'] == 'previous-request'
        H.task_unlink(c, 'human:ana', tid, link['link_id'], mover=True)
        H.task_update(c, 'human:ana', tid, owner='human:ana', mover=True)
        rows = W.inventory(c, 'r1')
        assert len(rows) == 1 and rows[0]['owner_id'] == 'bot:cmo'
        assert rows[0]['state'] == 'present' and rows[0]['path'] == link['path']
        saved = json.loads(rows[0]['detail'])
        assert not any(key in saved for key in ('cleanup_attempts', 'cleanup_retry_at', 'cleanup_gave_up'))
        assert saved['delete_requested'] and saved['error'] == error, 'the last refusal is not reported as success'
        generation = saved['cleanup_retry_generation']
        assert generation != 'previous-request' and uuid.UUID(generation)
    actions = post(api, 'runners/heartbeat', body, 'runner-test').json()['worktree_actions']
    assert len(actions) == 1 and actions[0]['action'] == 'remove' and actions[0]['owner_id'] == 'cmo'
    assert actions[0]['cleanup_retry_generation'] == generation
    # A fresh refusal keeps the files/link and resumes bounded retries; requesting removal does not bypass it.
    assert post(api, 'runners/heartbeat', body, 'runner-test').json()['worktree_actions'] == []
    with api.app_state.store.read() as c:
        saved = c.execute('SELECT state,detail FROM task_links WHERE id=?', (link['link_id'],)).fetchone()
        assert saved['state'] == 'present' and json.loads(saved['detail'])['error'] == error
    report['state'] = 'removed'
    assert post(api, 'runners/heartbeat', body, 'runner-test').json()['worktree_actions'] == []
    with api.app_state.store.read() as c:
        assert not c.execute('SELECT 1 FROM task_links WHERE id=?', (link['link_id'],)).fetchone()


def test_owner_unlink_finishes_a_missing_worktree_after_cleanup_gave_up(prepared):
    # The folder and its git registration were removed outside Tico after the computer stopped retrying.
    api, tid, _ = prepared
    link = post(api, f'tasks/{tid}/worktrees', {'repo': 'Acme/product'}).json()
    assert mark_ready(api, tid, link['link_id']).status_code == 200
    error = 'Worktree action failed; history kept. kept: ignored files'
    with api.app_state.store.transaction() as c:
        detail = json.loads(c.execute('SELECT detail FROM task_links WHERE id=?', (link['link_id'],)).fetchone()[0])
        detail.update(delete_requested=True, cleanup_requested=True, cleanup_attempts=W.CLEANUP_TRIES,
                      cleanup_gave_up=True, cleanup_retry_at=H.shift(H.now(), minutes=-60), error=error)
        c.execute('UPDATE task_links SET detail=? WHERE id=?', (json.dumps(detail), link['link_id']))
    report = {'link_id': link['link_id'], 'state': 'missing', 'checkout_state': 'ready',
              'branch': link['branch'], 'error': error}
    body = {'version': '0.3.2', 'platform': 'linux', 'readiness': {'schema_version': 1, 'worktrees': True},
            'worktrees': [report]}
    assert post(api, 'runners/heartbeat', body, 'runner-test').json()['worktree_actions'] == []
    with api.app_state.store.read() as c:
        assert c.execute('SELECT state FROM task_links WHERE id=?', (link['link_id'],)).fetchone()[0] == 'missing'
    with api.app_state.store.transaction() as c:
        H.task_unlink(c, 'bot:cmo', tid, link['link_id'])
        saved = json.loads(c.execute('SELECT detail FROM task_links WHERE id=?', (link['link_id'],)).fetchone()[0])
        assert not any(key in saved for key in ('cleanup_attempts', 'cleanup_retry_at', 'cleanup_gave_up'))
        generation = saved['cleanup_retry_generation']
    actions = post(api, 'runners/heartbeat', body, 'runner-test').json()['worktree_actions']
    assert len(actions) == 1 and actions[0]['action'] == 'remove' and actions[0]['path'] == link['path']
    assert actions[0]['cleanup_retry_generation'] == generation
    # The computer prunes the absent folder and reports it removed; the link row goes.
    report.pop('error')
    report['state'] = 'removed'
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


def test_heartbeat_writes_only_changes_and_backs_off_a_refused_cleanup(prepared):
    api, tid, _ = prepared
    link = post(api, f'tasks/{tid}/worktrees', {'repo': 'Acme/product'}).json()
    report = {'link_id': link['link_id'], 'state': 'present', 'branch': link['branch'], 'ahead': 0, 'dirty_files': 0,
              'last_commit': 'abc'}
    body = {'version': '0.3.2', 'platform': 'linux', 'readiness': {'schema_version': 1, 'bots': {}, 'worktrees': True},
            'worktrees': [report]}

    def changes():
        with api.app_state.store.read() as c:
            return c.execute("SELECT count(*) FROM changes WHERE topic='tasks' AND kind='link' AND subject_id=?",
                             (tid,)).fetchone()[0]
    post(api, 'runners/heartbeat', body, 'runner-test')
    seen = changes()
    for _ in range(3):
        assert post(api, 'runners/heartbeat', body, 'runner-test').status_code == 200
    assert changes() == seen, 'an unchanged report is not a task change'
    # Closed and finished: cleanup is asked. The computer refuses (it keeps files), so it is asked again only after
    # a wait, and not at all after CLEANUP_TRIES.
    with api.app_state.store.transaction() as c:
        c.execute("UPDATE tasks SET status='closed' WHERE id=?", (tid,))
    assert post(api, 'runners/heartbeat', body, 'runner-test').json()['worktree_actions'][0]['action'] == 'remove'
    report['error'] = 'Worktree action failed; history kept. kept: ignored files'
    asked = [post(api, 'runners/heartbeat', body, 'runner-test').json()['worktree_actions'] for _ in range(4)]
    assert sum(1 for a in asked if a) == 1, asked
    from backend import worktrees
    with api.app_state.store.transaction() as c:
        detail = json.loads(c.execute('SELECT detail FROM task_links WHERE id=?', (link['link_id'],)).fetchone()[0])
        detail.update(cleanup_attempts=worktrees.CLEANUP_TRIES, cleanup_retry_at=None)
        c.execute('UPDATE task_links SET detail=? WHERE id=?', (json.dumps(detail), link['link_id']))
    assert post(api, 'runners/heartbeat', body, 'runner-test').json()['worktree_actions'] == []
    with api.app_state.store.read() as c:
        assert json.loads(c.execute('SELECT detail FROM task_links WHERE id=?', (link['link_id'],)).fetchone()[0])['cleanup_gave_up']


def test_a_removed_checkout_reported_every_heartbeat_is_not_a_task_change(prepared):
    api, tid, _ = prepared
    link = post(api, f'tasks/{tid}/worktrees', {'repo': 'Acme/product'}).json()
    report = {'link_id': link['link_id'], 'state': 'present', 'branch': link['branch'], 'ahead': 0, 'dirty_files': 0,
              'last_commit': 'abc', 'last_activity': 1792560695.4}
    body = {'version': '0.3.2', 'platform': 'linux', 'readiness': {'schema_version': 1, 'bots': {}, 'worktrees': True},
            'worktrees': [report]}
    post(api, 'runners/heartbeat', body, 'runner-test')
    # Gone from disk, the computer reports only the link and its state: no commit, no activity.
    body['worktrees'] = [{'link_id': link['link_id'], 'state': 'removed'}]
    post(api, 'runners/heartbeat', body, 'runner-test')

    def written():
        with api.app_state.store.read() as c:
            return (c.execute("SELECT count(*) FROM changes WHERE topic='tasks' AND kind='link' AND subject_id=?",
                              (tid,)).fetchone()[0],
                    c.execute('SELECT updated_at,detail FROM task_links WHERE id=?', (link['link_id'],)).fetchone())
    seen = written()
    for _ in range(3):
        assert post(api, 'runners/heartbeat', body, 'runner-test').status_code == 200
    assert written() == seen, 'a removed checkout reported again is not activity'


def test_a_refused_cleanup_keeps_its_error_across_a_computer_restart(prepared):
    api, tid, _ = prepared
    link = post(api, f'tasks/{tid}/worktrees', {'repo': 'Acme/product'}).json()
    assert mark_ready(api, tid, link['link_id']).status_code == 200
    refused = 'Worktree action failed; history kept. kept: ignored files'
    report = {'link_id': link['link_id'], 'state': 'present', 'checkout_state': 'ready', 'branch': link['branch'],
              'last_commit': 'abc'}
    body = {'version': '0.3.2', 'platform': 'linux', 'readiness': {'schema_version': 1, 'bots': {}, 'worktrees': True},
            'worktrees': [report]}

    def detail():
        with api.app_state.store.read() as c:
            return json.loads(c.execute('SELECT detail FROM task_links WHERE id=?', (link['link_id'],)).fetchone()[0])
    with api.app_state.store.transaction() as c:
        c.execute("UPDATE tasks SET status='closed' WHERE id=?", (tid,))
    assert post(api, 'runners/heartbeat', body, 'runner-test').json()['worktree_actions'][0]['action'] == 'remove'
    report['error'] = refused
    assert post(api, 'runners/heartbeat', body, 'runner-test').json()['worktree_actions'], 'the first refusal is asked again'
    assert detail()['cleanup_attempts'] == 1
    # The computer restarts and no longer remembers the refusal: the error and the wait before the next ask stay.
    del report['error']
    assert post(api, 'runners/heartbeat', body, 'runner-test').json()['worktree_actions'] == []
    assert detail()['error'] == refused
    with api.app_state.store.transaction() as c:
        c.execute('UPDATE task_links SET detail=? WHERE id=?',
                  (json.dumps({**detail(), 'cleanup_attempts': W.CLEANUP_TRIES, 'cleanup_retry_at': None}), link['link_id']))
    assert post(api, 'runners/heartbeat', body, 'runner-test').json()['worktree_actions'] == []
    assert detail()['error'] == refused and detail()['cleanup_gave_up'], 'a cleanup given up stays given up'
    # Reopened, removal is no longer asked: the refusal is forgotten.
    with api.app_state.store.transaction() as c:
        c.execute("UPDATE tasks SET status='open' WHERE id=?", (tid,))
    assert post(api, 'runners/heartbeat', body, 'runner-test').json()['worktree_actions'] == []
    post(api, 'runners/heartbeat', body, 'runner-test')
    assert not any(key in detail() for key in ('error', 'cleanup_requested', 'cleanup_attempts', 'cleanup_gave_up'))
    # Closed again, a removal that succeeds clears the refusal it followed.
    with api.app_state.store.transaction() as c:
        c.execute("UPDATE tasks SET status='closed' WHERE id=?", (tid,))
    report['error'] = refused
    post(api, 'runners/heartbeat', body, 'runner-test')
    post(api, 'runners/heartbeat', body, 'runner-test')
    assert detail()['error'] == refused and detail()['cleanup_attempts'] == 1
    del report['error']
    report['state'] = 'removed'
    post(api, 'runners/heartbeat', body, 'runner-test')
    assert 'error' not in detail()


def test_finished_pr_releases_an_open_tasks_worktree_and_the_limit_names_it(prepared):
    api, tid, _ = prepared
    link = post(api, f'tasks/{tid}/worktrees', {'repo': 'Acme/product'}, 'bot-test').json()
    assert mark_ready(api, tid, link['link_id']).status_code == 200      # a checkout that finished, not one still starting
    body = {'version': '0.3.2', 'platform': 'linux', 'readiness': {'schema_version': 1, 'bots': {}, 'worktrees': True},
            'worktrees': [{'link_id': link['link_id'], 'state': 'present', 'branch': link['branch'], 'last_commit': 'abc'}]}
    with api.app_state.store.transaction() as c:
        H.task_link(c, 'bot:cmo', tid, 'https://github.com/Acme/product/pull/1')
        c.execute("UPDATE tasks SET status='waiting' WHERE id=?", (tid,))
    assert post(api, 'runners/heartbeat', body, 'runner-test').json()['worktree_actions'] == []
    with api.app_state.store.transaction() as c:
        c.execute("UPDATE task_links SET state='merged' WHERE kind='pr'")
    actions = post(api, 'runners/heartbeat', body, 'runner-test').json()['worktree_actions']
    assert len(actions) == 1 and actions[0]['action'] == 'remove' and actions[0]['release'] is True
    for i in range(W.LIMIT - 1):
        assert post(api, f'tasks/{tid}/worktrees/attach', {'path': f'tasks/{tid[:8]}/extra{i}', 'repo': 'Acme/product'},
                    'bot-test').status_code == 200
    refused = post(api, f'tasks/{tid}/worktrees/attach', {'path': f'tasks/{tid[:8]}/eleven', 'repo': 'Acme/product'}, 'bot-test')
    assert refused.status_code == 409
    error = refused.json()['error']
    assert error['code'] == 'worktree_limit' and 'free themselves once you are idle' in error['detail']
    assert len(error['worktrees']) == W.LIMIT and {w['clear'] for w in error['worktrees']} == {'merged'}
    # A new open pull request on the task means its worktree is in use again: no release.
    with api.app_state.store.transaction() as c:
        H.task_link(c, 'bot:cmo', tid, 'https://github.com/Acme/product/pull/2')
    assert post(api, 'runners/heartbeat', body, 'runner-test').json()['worktree_actions'] == []
    # Once the follow-up also merges, the computer can finish cleanup while the ticket still waits for release.
    with api.app_state.store.transaction() as c:
        c.execute("UPDATE task_links SET state='merged' WHERE task_id=? AND kind='pr'", (tid,))
    body['worktrees'][0].update(ahead=0, dirty_files=0)
    assert post(api, 'runners/heartbeat', body, 'runner-test').json()['worktree_actions'][0]['release'] is True
    body['worktrees'][0]['state'] = 'removed'
    post(api, 'runners/heartbeat', body, 'runner-test')
    with api.app_state.store.read() as c:
        assert H.task(c, tid)['status'] == 'waiting'
        assert c.execute('SELECT state FROM task_links WHERE id=?', (link['link_id'],)).fetchone()[0] == 'removed'
        released = c.execute("SELECT detail FROM events WHERE action='worktree.released' AND target=? "
                             "AND json_extract(detail,'$.link')=?", (tid, link['link_id'])).fetchone()
        assert json.loads(released[0])['reason'] == 'pull request merged'


def test_a_released_worktree_refused_for_unpushed_commits_is_tried_again_after_a_push(prepared):
    api, tid, _ = prepared
    link = post(api, f'tasks/{tid}/worktrees', {'repo': 'Acme/product'}, 'bot-test').json()
    assert mark_ready(api, tid, link['link_id']).status_code == 200
    report = {'link_id': link['link_id'], 'state': 'present', 'checkout_state': 'ready', 'branch': link['branch'],
              'last_commit': 'abc', 'ahead': 2}
    body = {'version': '0.3.2', 'platform': 'linux', 'readiness': {'schema_version': 1, 'bots': {}, 'worktrees': True},
            'worktrees': [report]}

    def detail():
        with api.app_state.store.read() as c:
            return json.loads(c.execute('SELECT detail FROM task_links WHERE id=?', (link['link_id'],)).fetchone()[0])
    with api.app_state.store.transaction() as c:
        H.task_link(c, 'bot:cmo', tid, 'https://github.com/Acme/product/pull/1')
        c.execute("UPDATE task_links SET state='merged' WHERE kind='pr'")
    assert post(api, 'runners/heartbeat', body, 'runner-test').json()['worktree_actions'][0]['release'] is True
    report['error'] = 'Worktree action failed; history kept. kept: unpushed commits; push them to free this worktree'
    post(api, 'runners/heartbeat', body, 'runner-test')
    with api.app_state.store.transaction() as c:
        c.execute('UPDATE task_links SET detail=? WHERE id=?',
                  (json.dumps({**detail(), 'cleanup_attempts': W.CLEANUP_TRIES, 'cleanup_retry_at': None}), link['link_id']))
    assert post(api, 'runners/heartbeat', body, 'runner-test').json()['worktree_actions'] == []
    assert detail()['cleanup_gave_up'] and detail()['release_requested']
    # The bot pushes: last commit, dirty files and activity are unchanged, only the unpushed count drops.
    report['ahead'] = 0
    actions = post(api, 'runners/heartbeat', body, 'runner-test').json()['worktree_actions']
    assert len(actions) == 1 and actions[0]['action'] == 'remove' and actions[0]['release'] is True
    assert 'cleanup_gave_up' not in detail() and detail()['cleanup_attempts'] == 1


def mark_ready(api, tid, link_id):
    return api.patch(f'/api/v2/tasks/{tid}/links/{link_id}',
                     json={'state': 'present', 'checkout_state': 'ready', 'setup_pending': False,
                           'expected_head': 'a' * 40, 'checkout_target': 'refs/heads/tico/test',
                           'expected_base': 'b' * 64},
                     headers={**auth('runner-test'), 'Idempotency-Key': uuid.uuid4().hex})


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
        saved = c.execute('SELECT state,detail FROM task_links WHERE id=?', (link['link_id'],)).fetchone()
        assert saved['state'] == 'pending'
        assert json.loads(saved['detail'])['checkout_state'] == 'initializing'

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
        saved = c.execute('SELECT state,detail FROM task_links WHERE id=?', (link['link_id'],)).fetchone()
        assert saved['state'] == 'present'
        assert json.loads(saved['detail'])['checkout_state'] == 'ready'


def test_a_ready_setup_clears_a_failed_restore_and_the_health_monitor_follows(prepared, monkeypatch):
    from backend import health_monitor, watchers
    api, tid, _ = prepared
    monkeypatch.setattr(health_monitor, 'active', lambda c: True)
    monkeypatch.setattr(watchers, 'deliver', lambda *args: True)
    with api.app_state.store.transaction() as c:
        second = H.task_create(c, 'human:ana', 'More product', 'Implement more', 'bot:cmo')['id']
        c.execute("UPDATE tasks SET status='doing' WHERE status='open'")  # a started task gets a worktree
    links = [post(api, f'tasks/{task}/worktrees', {'repo': 'Acme/product'}).json() for task in (tid, second)]
    failed = 'Worktree action failed; history kept. Restore path already exists and is not a worktree'
    beat = {'version': '0.3.46', 'platform': 'linux', 'readiness': {'schema_version': 1, 'worktrees': True},
            'worktrees': [{'link_id': link['link_id'], 'state': 'missing', 'branch': None, 'error': failed}
                          for link in links]}

    def scanned():
        with api.app_state.store.transaction() as c:
            health_monitor.scan(c, api.app_state.auth, api.app_state.store.settings)
            found = [f for f in health_monitor.findings(c, api.app_state.store.settings) if f['check_id'] == 'worktree_error']
            present = c.execute("SELECT count(*) FROM health_findings WHERE check_id='worktree_error' AND present=1")
            return found, present.fetchone()[0]

    def error(link):
        with api.app_state.store.read() as c:
            row = c.execute('SELECT detail FROM task_links WHERE id=?', (link['link_id'],)).fetchone()
            return json.loads(row[0]).get('error')
    assert post(api, 'runners/heartbeat', beat, 'runner-test').status_code == 200
    found, present = scanned()
    assert len(found) == 1 and present == 1 and failed in found[0]['detail']
    # The owner's setup succeeds on the first link; the computer still repeats its last restore error for it.
    assert mark_ready(api, tid, links[0]['link_id']).status_code == 200
    assert error(links[0]) is None and error(links[1]) == failed
    beat['worktrees'][0].update(state='present', checkout_state='ready', branch=links[0]['branch'])
    assert post(api, 'runners/heartbeat', beat, 'runner-test').status_code == 200
    assert error(links[0]) is None and error(links[1]) == failed
    found, present = scanned()
    assert len(found) == 1 and present == 1, 'the second worktree is still failing'
    # Once the second is set up too, the next scan clears the finding.
    assert mark_ready(api, second, links[1]['link_id']).status_code == 200
    beat['worktrees'][1].update(state='present', checkout_state='ready', branch=links[1]['branch'])
    assert post(api, 'runners/heartbeat', beat, 'runner-test').status_code == 200
    assert scanned() == ([], 0)
    # A failure seen in the ready tree itself is current, and a report without one clears it.
    beat['worktrees'][0]['error'] = 'Could not read its status: permission denied'
    post(api, 'runners/heartbeat', beat, 'runner-test')
    assert len(scanned()[0]) == 1
    del beat['worktrees'][0]['error']
    post(api, 'runners/heartbeat', beat, 'runner-test')
    assert error(links[0]) is None and scanned() == ([], 0)
    # A link left with the old error by an earlier release clears on the next scan, without a heartbeat or data edit.
    with api.app_state.store.transaction() as c:
        detail = json.loads(c.execute('SELECT detail FROM task_links WHERE id=?', (links[1]['link_id'],)).fetchone()[0])
        c.execute('UPDATE task_links SET detail=? WHERE id=?', (json.dumps({**detail, 'error': failed}), links[1]['link_id']))
    assert scanned() == ([], 0) and error(links[1]) == failed


def test_a_heartbeat_built_before_checkout_finished_never_moves_it_back(prepared):
    api, tid, _ = prepared
    link = post(api, f'tasks/{tid}/worktrees', {'repo': 'Acme/product'}).json()
    assert mark_ready(api, tid, link['link_id']).status_code == 200

    def beat(**report):
        body = {'version': '0.3.42', 'platform': 'test', 'readiness': {'schema_version': 1, 'worktrees': True},
                'worktrees': [{'link_id': link['link_id'], 'branch': link['branch'], 'repo': 'Acme/product',
                               **report}]}
        response = post(api, 'runners/heartbeat', body, 'runner-test')
        assert response.status_code == 200, response.text
        with api.app_state.store.read() as c:
            saved = c.execute('SELECT state,detail FROM task_links WHERE id=?', (link['link_id'],)).fetchone()
        return saved['state'], json.loads(saved['detail'])

    # Reports the computer built from the record it read before setup finished.
    for report in ({'state': 'pending', 'checkout_state': 'queued', 'dirty_files': 0},
                   {'state': 'pending', 'checkout_state': 'checkout_ready', 'expected_head': 'c' * 40},
                   {'state': 'present', 'checkout_state': 'unverified'}):
        state, detail = beat(**report)
        assert state == 'present' and detail['checkout_state'] == 'ready'
        assert detail['expected_head'] == 'a' * 40 and detail['setup_pending'] is False
    state, detail = beat(state='present', checkout_state='ready', expected_head='a' * 40,
                         checkout_target='refs/heads/tico/test', expected_base='b' * 64, dirty_files=3)
    assert state == 'present' and detail['checkout_state'] == 'ready' and detail['dirty_files'] == 3


def test_a_report_built_from_an_older_record_never_moves_it_forward_either(prepared):
    api, tid, _ = prepared
    link = post(api, f'tasks/{tid}/worktrees', {'repo': 'Acme/product'}).json()
    assert mark_ready(api, tid, link['link_id']).status_code == 200
    with api.app_state.store.read() as c:
        read_before = c.execute('SELECT updated_at FROM task_links WHERE id=?', (link['link_id'],)).fetchone()[0]
    # Re-queued after the computer read it, the way adding a removed worktree again does.
    requeued = H.now()
    with api.app_state.store.transaction() as c:
        c.execute("UPDATE task_links SET state='pending',detail=?,updated_at=? WHERE id=?",
                  (json.dumps({'owner': 'bot:cmo', 'checkout_state': 'queued', 'setup_pending': True,
                               'record_changed': requeued}), requeued, link['link_id']))

    def beat(**report):
        body = {'version': '0.3.43', 'platform': 'test', 'readiness': {'schema_version': 1, 'worktrees': True},
                'worktrees': [{'link_id': link['link_id'], 'branch': link['branch'], 'repo': 'Acme/product',
                               **report}]}
        response = post(api, 'runners/heartbeat', body, 'runner-test')
        assert response.status_code == 200, response.text
        assert response.json()['worktree_record_stamp'] is True
        with api.app_state.store.read() as c:
            saved = c.execute('SELECT state,detail,updated_at FROM task_links WHERE id=?',
                              (link['link_id'],)).fetchone()
        return saved['state'], json.loads(saved['detail']), saved['updated_at']

    state, detail, updated = beat(state='present', checkout_state='ready', expected_head='a' * 40,
                                  checkout_target='refs/heads/tico/test', expected_base='b' * 64,
                                  dirty_files=4, record_updated=read_before)
    assert state == 'pending' and detail['checkout_state'] == 'queued' and detail['setup_pending'] is True
    assert 'expected_head' not in detail and 'record_updated' not in detail and detail['dirty_files'] == 4
    # A report read after the re-queue still moves it on, though the last heartbeat wrote the row since.
    assert updated > requeued
    state, detail, _ = beat(state='pending', checkout_state='initializing', dirty_files=5, record_updated=requeued)
    assert state == 'pending' and detail['checkout_state'] == 'initializing'
    # A stale 'present' doesn't promote a finished record the computer last reported pending.
    assert mark_ready(api, tid, link['link_id']).status_code == 200
    with api.app_state.store.transaction() as c:
        c.execute("UPDATE task_links SET state='pending' WHERE id=?", (link['link_id'],))
    state, detail, _ = beat(state='present', checkout_state='ready', record_updated=requeued)
    assert state == 'pending' and detail['checkout_state'] == 'ready'


@pytest.mark.parametrize('cleanup_requested', [False, True])
@pytest.mark.parametrize('stamp', ['stale', 'equal', 'newer', 'unstamped'])
def test_removed_heartbeat_respects_the_record_stamp(prepared, cleanup_requested, stamp):
    api, tid, _ = prepared
    link = post(api, f'tasks/{tid}/worktrees', {'repo': 'Acme/product'}).json()
    assert mark_ready(api, tid, link['link_id']).status_code == 200
    changed = '2026-10-24T03:06:28.000000Z'
    with api.app_state.store.transaction() as c:
        row = c.execute('SELECT detail FROM task_links WHERE id=?', (link['link_id'],)).fetchone()
        detail = {**json.loads(row['detail']), 'record_changed': changed}
        if cleanup_requested:
            detail['cleanup_requested'] = True
        c.execute('UPDATE task_links SET detail=?,updated_at=? WHERE id=?',
                  (json.dumps(detail), changed, link['link_id']))
    report = {'link_id': link['link_id'], 'state': 'removed'}
    if stamp != 'unstamped':
        report['record_updated'] = {'stale': '2026-10-24T03:06:27.000000Z', 'equal': changed,
                                    'newer': '2026-10-24T03:06:29.000000Z'}[stamp]
    response = post(api, 'runners/heartbeat', {**HEARTBEAT, 'worktrees': [report]}, 'runner-test')
    assert response.status_code == 200, response.text
    with api.app_state.store.read() as c:
        row = c.execute('SELECT state,detail FROM task_links WHERE id=?', (link['link_id'],)).fetchone()
    detail = json.loads(row['detail'])
    assert row['state'] == ('present' if stamp == 'stale' else 'removed')
    assert detail['checkout_state'] == 'ready' and detail['setup_pending'] is False
    assert detail.get('removed_by') == ('cleanup' if cleanup_requested and stamp != 'stale' else None)
    assert len(released_events(api, tid)) == int(cleanup_requested and stamp != 'stale')


def test_an_older_runner_can_still_add_a_worktree_and_finish_its_setup(prepared):
    """A 0.3.34 runner adds as the bot, reports present with only its path, then setup_pending True and False."""
    api, tid, _ = prepared
    link = post(api, f'tasks/{tid}/worktrees', {'repo': 'Acme/product'}, 'bot-test').json()
    route = f'/api/v2/tasks/{tid}/links/{link["link_id"]}'
    send = lambda body: api.patch(route, json=body, headers={**auth('bot-test'), 'Idempotency-Key': uuid.uuid4().hex})
    assert send({'state': 'present', 'path': link['path']}).status_code == 200
    with api.app_state.store.read() as c:
        saved = c.execute('SELECT state,detail FROM task_links WHERE id=?', (link['link_id'],)).fetchone()
    detail = json.loads(saved['detail'])
    assert saved['state'] == 'present' and detail['checkout_state'] == 'legacy_present' and detail['setup_pending'] is True
    assert send({'setup_pending': True}).status_code == 200
    assert send({'setup_pending': False}).status_code == 200
    assert send({'state': 'present', 'path': link['path']}).status_code == 200    # its closing report, a confirm
    with api.app_state.store.read() as c:
        assert json.loads(c.execute('SELECT detail FROM task_links WHERE id=?', (link['link_id'],)).fetchone()[0])['setup_pending'] is False
    # Only that bare first report on a link still queued: a second link once past queued still needs the proof.
    other = post(api, f'tasks/{tid}/worktrees', {'repo': 'Acme/product'}, 'bot-test').json()
    with api.app_state.store.transaction() as c:
        c.execute("UPDATE task_links SET detail=? WHERE id=?",
                  (json.dumps({'owner': 'bot:cmo', 'checkout_state': 'initializing', 'setup_pending': True}), other['link_id']))
    refused = api.patch(f'/api/v2/tasks/{tid}/links/{other["link_id"]}', json={'state': 'present', 'path': other['path']},
                        headers={**auth('bot-test'), 'Idempotency-Key': uuid.uuid4().hex})
    assert refused.status_code == 409 and refused.json()['error']['code'] == 'worktree_not_ready'


def test_an_older_runner_can_still_attach_an_existing_worktree(prepared):
    """A 0.3.34 attach: the link comes back attached_pending, then a bare present (attach runs no setup), twice."""
    api, tid, _ = prepared
    link = post(api, f'tasks/{tid}/worktrees', {'repo': 'Acme/product'}, 'bot-test').json()
    with api.app_state.store.transaction() as c:
        c.execute("UPDATE task_links SET detail=? WHERE id=?",
                  (json.dumps({'owner': 'bot:cmo', 'checkout_state': 'attached_pending', 'setup_pending': False}), link['link_id']))
    route = f'/api/v2/tasks/{tid}/links/{link["link_id"]}'
    for _ in range(2):
        r = api.patch(route, json={'state': 'present', 'path': link['path']}, headers={**auth('bot-test'), 'Idempotency-Key': uuid.uuid4().hex})
        assert r.status_code == 200, r.text
    with api.app_state.store.read() as c:
        saved = c.execute('SELECT state,detail FROM task_links WHERE id=?', (link['link_id'],)).fetchone()
    detail = json.loads(saved['detail'])
    assert saved['state'] == 'present' and detail['checkout_state'] == 'legacy_present' and detail['setup_pending'] is False


def test_old_runner_without_checkout_state_preserves_existing_legacy_present_link(prepared):
    api, tid, _ = prepared
    link = post(api, f'tasks/{tid}/worktrees', {'repo': 'Acme/product'}, 'bot-test').json()
    with api.app_state.store.transaction() as c:
        c.execute("UPDATE task_links SET state='present',detail=? WHERE id=?", (json.dumps({'owner': 'bot:cmo'}), link['link_id']))
    beat = {'version': '0.3.21', 'platform': 'test', 'readiness': {'schema_version': 1, 'worktrees': True},
            'worktrees': [{'link_id': link['link_id'], 'state': 'present', 'branch': link['branch'],
                           'repo': 'Acme/product'}]}
    response = post(api, 'runners/heartbeat', beat, 'runner-test')
    assert response.status_code == 200, response.text
    with api.app_state.store.read() as c:
        saved = c.execute('SELECT state,detail FROM task_links WHERE id=?', (link['link_id'],)).fetchone()
        assert saved['state'] == 'present'
        detail = json.loads(saved['detail'])
        assert 'checkout_state' not in detail
        assert not any(key in detail for key in ('expected_head', 'checkout_target', 'expected_base'))


def test_old_runner_without_checkout_state_does_not_promote_queued_pending_link(prepared):
    api, tid, _ = prepared
    link = post(api, f'tasks/{tid}/worktrees', {'repo': 'Acme/product'}, 'bot-test').json()
    assert link['state'] == 'pending' and link['checkout_state'] == 'queued'
    beat = {'version': '0.3.21', 'platform': 'test', 'readiness': {'schema_version': 1, 'worktrees': True},
            'worktrees': [{'link_id': link['link_id'], 'state': 'present', 'branch': link['branch'],
                           'repo': 'Acme/product'}]}
    response = post(api, 'runners/heartbeat', beat, 'runner-test')
    assert response.status_code == 200, response.text
    with api.app_state.store.read() as c:
        saved = c.execute('SELECT state,detail FROM task_links WHERE id=?', (link['link_id'],)).fetchone()
        detail = json.loads(saved['detail'])
    assert saved['state'] == 'pending'
    assert detail['checkout_state'] == 'queued' and detail['setup_pending'] is True


def test_verified_legacy_present_heartbeat_preserves_present_but_never_promotes_pending(prepared):
    api, tid, _ = prepared
    present = post(api, f'tasks/{tid}/worktrees', {'repo': 'Acme/product'}, 'bot-test').json()
    pending = post(api, f'tasks/{tid}/worktrees/attach',
                   {'path': f'tasks/{tid[:8]}/legacy-pending', 'repo': 'Acme/product'}, 'bot-test').json()
    pending_stale = post(api, f'tasks/{tid}/worktrees/attach',
                         {'path': f'tasks/{tid[:8]}/legacy-stale', 'repo': 'Acme/product'}, 'bot-test').json()
    demoted = post(api, f'tasks/{tid}/worktrees/attach',
                   {'path': f'tasks/{tid[:8]}/legacy-demoted', 'repo': 'Acme/product'}, 'bot-test').json()
    demoted_branch = f'tico/{tid[:8]}-legacy-demoted'
    with api.app_state.store.transaction() as c:
        c.execute("UPDATE task_links SET state='present',detail=? WHERE id=?",
                  (json.dumps({'owner': 'bot:cmo'}), present['link_id']))
        c.execute("UPDATE task_links SET detail=? WHERE id=?",
                  (json.dumps({'owner': 'bot:cmo'}), pending['link_id']))
        c.execute("UPDATE task_links SET detail=? WHERE id=?",
                  (json.dumps({'owner': 'bot:cmo', 'checkout_state': 'legacy_present'}), pending_stale['link_id']))
        c.execute("UPDATE task_links SET state='pending',branch=?,detail=? WHERE id=?",
                  (demoted_branch, json.dumps({'owner': 'bot:cmo', 'current_branch': demoted_branch,
                               'last_commit': 'a' * 40, 'dirty_files': 2}), demoted['link_id']))
    beat = {'version': '0.3.21', 'platform': 'test', 'readiness': {'schema_version': 1, 'worktrees': True},
            'worktrees': [
                {'link_id': present['link_id'], 'state': 'present', 'checkout_state': 'legacy_present',
                 'branch': present['branch'], 'repo': 'Acme/product', 'dirty_files': 2},
                {'link_id': pending['link_id'], 'state': 'present', 'checkout_state': 'legacy_present',
                 'branch': pending['branch'], 'repo': 'Acme/product'},
                {'link_id': pending_stale['link_id'], 'state': 'present', 'checkout_state': 'legacy_present',
                 'branch': pending_stale['branch'], 'repo': 'Acme/product'},
                {'link_id': demoted['link_id'], 'state': 'present', 'checkout_state': 'legacy_present',
                 'branch': demoted_branch, 'repo': 'Acme/product', 'dirty_files': 2}]}
    response = post(api, 'runners/heartbeat', beat, 'runner-test')
    assert response.status_code == 200, response.text
    with api.app_state.store.read() as c:
        rows = {r['id']: r for r in c.execute(
            'SELECT id,state,detail FROM task_links WHERE id IN (?,?,?,?)',
            (present['link_id'], pending['link_id'], pending_stale['link_id'], demoted['link_id']))}
    assert rows[present['link_id']]['state'] == 'present'
    present_detail = json.loads(rows[present['link_id']]['detail'])
    assert present_detail['checkout_state'] == 'legacy_present' and present_detail['dirty_files'] == 2
    assert not any(key in present_detail for key in ('expected_head', 'checkout_target', 'expected_base'))
    assert rows[pending['link_id']]['state'] == 'pending'
    pending_detail = json.loads(rows[pending['link_id']]['detail'])
    assert pending_detail.get('checkout_state') is None
    assert rows[pending_stale['link_id']]['state'] == 'pending'
    stale_detail = json.loads(rows[pending_stale['link_id']]['detail'])
    assert stale_detail['checkout_state'] == 'unverified'
    assert rows[demoted['link_id']]['state'] == 'present'
    demoted_detail = json.loads(rows[demoted['link_id']]['detail'])
    assert demoted_detail['checkout_state'] == 'legacy_present'
    assert not any(key in demoted_detail for key in ('expected_head', 'checkout_target', 'expected_base'))


def test_human_pending_link_stops_repeating_restore_after_checkout_progress(prepared):
    api, tid, _ = prepared
    link = post(api, f'tasks/{tid}/worktrees', {'repo': 'Acme/product'}, 'bot-test').json()
    with api.app_state.store.transaction() as c:
        c.execute("UPDATE task_links SET added_by='human:ana',state='pending',detail=? WHERE id=?",
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


def test_a_computer_reads_the_status_only_of_tasks_it_ran_that_bots_session_for(prepared):
    api, tid, other = prepared
    path = 'runners/me/session-tasks'
    with api.app_state.store.transaction() as c:
        cols = [r[1] for r in c.execute('PRAGMA table_info(runners)')]
        fresh = {'id': 'r2', 'token_hash': 'synthetic-r2'}
        c.execute(f"INSERT INTO runners({','.join(cols)}) SELECT {','.join('?' if col in fresh else col for col in cols)} "
                  "FROM runners WHERE id='r1'", [fresh[col] for col in cols if col in fresh])
        job = c.execute("SELECT job_id FROM attempts WHERE id='a1'").fetchone()[0]
        for aid, runner, lane in (('a2', 'r1', 'task:' + tid), ('a3', 'r2', 'task:' + other)):
            c.execute("INSERT INTO attempts(id,job_id,bot_id,runner_id,generation,token_hash,state,lease_until,created_at,lane) "
                      "VALUES(?,?,'cmo',?,1,?,'completed',?,?,?)", (aid, job, runner, 'synthetic-' + aid, H.now(), H.now(), lane))
        c.execute("UPDATE tasks SET status='done' WHERE id IN (?,?)", (tid, other))
    asked = {'sessions': [{'bot': 'cmo', 'task': tid}, {'bot': 'cpo', 'task': tid},
                          {'bot': 'cmo', 'task': other}, {'bot': 'cmo', 'task': 'no-such-task'}]}
    response = post(api, path, asked, 'runner-test')
    assert response.status_code == 200, response.text
    # Another bot's lane, another computer's turn and an unknown task are left out, not refused.
    assert response.json() == {'sessions': [{'bot_id': 'cmo', 'task': tid, 'status': 'done'}]}
    assert post(api, path, asked, 'bot-test').status_code == 403
    assert post(api, path, {'sessions': [{'bot': 'cmo', 'task': tid}] * 501}, 'runner-test').status_code == 422


def test_readiness_skips_a_private_worktree_it_cannot_read_and_keeps_the_ones_its_bot_can(prepared):
    api, tid, _ = prepared
    link = post(api, f'tasks/{tid}/worktrees', {'repo': 'Acme/product'}).json()
    quiet = {'version': '0.3.2', 'platform': 'linux', 'readiness': {'schema_version': 1, 'bots': {}, 'worktrees': True},
             'worktrees': []}
    with api.app_state.store.transaction() as c:
        c.execute("UPDATE tasks SET private=1,title='Secret plan' WHERE id=?", (tid,))
    # The computer is no task participant, but the bot it hosts owns this private task: its checkout is sent.
    beat = post(api, 'runners/heartbeat', quiet, 'runner-test')
    assert beat.status_code == 200, beat.text
    assert [(a['task_id'], a['action']) for a in beat.json()['worktree_actions']] == [(tid, 'restore')]
    assert 'worktree_actions_skipped' not in beat.json()
    # Owned by a bot this computer does not host: readiness still passes, counts the skip and says nothing of it.
    with api.app_state.store.transaction() as c:
        c.execute("UPDATE tasks SET owner_id='bot:cpo' WHERE id=?", (tid,))
        c.execute("UPDATE task_links SET detail=json_set(detail,'$.owner','bot:cpo') WHERE id=?", (link['link_id'],))
    beat = post(api, 'runners/heartbeat', quiet, 'runner-test')
    assert beat.status_code == 200, beat.text
    assert beat.json()['worktree_actions'] == [] and beat.json()['worktree_actions_skipped'] == 1
    assert tid not in beat.text and tid[:8] not in beat.text and 'Secret plan' not in beat.text
    # Back with the hosted bot, the computer's report on its own worktree is answered, not refused.
    with api.app_state.store.transaction() as c:
        c.execute("UPDATE tasks SET owner_id='bot:cmo' WHERE id=?", (tid,))
        c.execute("UPDATE task_links SET detail=json_set(detail,'$.owner','bot:cmo') WHERE id=?", (link['link_id'],))
    assert mark_ready(api, tid, link['link_id']).status_code == 200
    # A removal always reaches the computer holding the worktree, even once nobody it hosts can read the task.
    with api.app_state.store.transaction() as c:
        c.execute("UPDATE tasks SET status='closed',owner_id='human:bea' WHERE id=?", (tid,))
    present = {**quiet, 'worktrees': [{'link_id': link['link_id'], 'state': 'present', 'checkout_state': 'ready',
                                       'branch': link['branch']}]}
    beat = post(api, 'runners/heartbeat', present, 'runner-test')
    assert beat.status_code == 200, beat.text
    assert [a['action'] for a in beat.json()['worktree_actions']] == ['remove']
    assert 'worktree_actions_skipped' not in beat.json() and 'Secret plan' not in beat.text
    from backend import task_privacy
    from backend.auth import Identity
    computer = Identity('runner:r1', 'runner', runner_id='r1')
    with api.app_state.store.read() as c, pytest.raises(task_privacy.Problem):
        task_privacy.require_payload(c, computer, {'worktree_actions': [{'task_id': tid, 'owner': 'cmo', 'action': 'restore'}]})



def test_a_finished_task_pr_never_releases_a_tree_whose_own_commits_no_pr_carries(prepared):
    api, tid, _ = prepared
    link = post(api, f'tasks/{tid}/worktrees', {'repo': 'Acme/product'}, 'bot-test').json()
    assert mark_ready(api, tid, link['link_id']).status_code == 200
    body = {'version': '0.3.2', 'platform': 'linux', 'readiness': {'schema_version': 1, 'bots': {}, 'worktrees': True},
            'worktrees': [{'link_id': link['link_id'], 'state': 'present', 'branch': link['branch'], 'last_commit': 'f' * 40}]}
    with api.app_state.store.transaction() as c:
        merged = H.task_link(c, 'bot:cmo', tid, 'https://github.com/Acme/product/pull/1')
        c.execute("UPDATE task_links SET state='merged',branch='tico/elsewhere' WHERE id=?", (merged['id'],))
    assert post(api, 'runners/heartbeat', body, 'runner-test').json()['worktree_actions'] == []
    with api.app_state.store.transaction() as c:
        held = {w['link_id']: w for w in W.holdings(c, 'bot:cmo')}[link['link_id']]
    assert held['kept'] == f"held: commits on {link['branch']} with no pull request linked"
    with api.app_state.store.transaction() as c:
        c.execute("UPDATE task_links SET branch=? WHERE id=?", (link['branch'], merged['id']))
    actions = post(api, 'runners/heartbeat', body, 'runner-test').json()['worktree_actions']
    assert [a['release'] for a in actions] == [True]


# Dev 265 part b: a tree starts only for work that can run, obsolete reservations end, a fix-forward keeps its tree,
# and every release says why.
HEARTBEAT = {'version': '0.3.2', 'platform': 'linux', 'readiness': {'schema_version': 1, 'bots': {}, 'worktrees': True}}


def released_events(api, tid):
    with api.app_state.store.read() as c:
        return [json.loads(r[0]) for r in c.execute("SELECT detail FROM events WHERE action='worktree.released' "
                                                     "AND target=? ORDER BY created_at", (tid,))]


def test_a_new_worktree_starts_only_for_work_a_coding_step_can_run_on(prepared):
    api, tid, other = prepared
    path = f'tasks/{tid}/worktrees'
    for status, words in (('open', 'move it to In Progress first'), ('done', 'finished task'),
                          ('declined', 'finished task')):
        with api.app_state.store.transaction() as c:
            c.execute('UPDATE tasks SET status=? WHERE id=?', (status, tid))
        refused = post(api, path, {'repo': 'Acme/product'}, 'bot-test')
        assert refused.status_code == 409 and refused.json()['error']['code'] == 'worktree_start', refused.text
        assert words in refused.json()['error']['detail']
    with api.app_state.store.transaction() as c:
        c.execute("UPDATE tasks SET status='doing' WHERE id=?", (tid,))
        c.execute("INSERT INTO task_relations(from_task,to_task,kind,created_at) VALUES(?,?,'blocks',?)", (other, tid, H.now()))
    refused = post(api, path, {'repo': 'Acme/product'}, 'bot-test')
    assert refused.status_code == 409 and 'blocked by' in refused.json()['error']['detail']
    assert 'Other work' in refused.json()['error']['detail']
    with api.app_state.store.transaction() as c:
        c.execute("UPDATE tasks SET status='done' WHERE id=?", (other,))      # a done blocker blocks nothing
    for status in ('doing', 'review', 'waiting'):                          # a fix-forward or review fix at any step
        with api.app_state.store.transaction() as c:
            c.execute('UPDATE tasks SET status=? WHERE id=?', (status, tid))
        assert post(api, path, {'repo': 'Acme/product'}, 'bot-test').status_code == 200


def test_a_blocker_counts_once_its_code_is_merged_not_once_it_ships(prepared):
    """A worktree builds from source: a blocker at Pending Release (or later) or with its pull request merged lets the
    dependent start; one still before the merge holds it, and the refusal says what is missing."""
    api, tid, other = prepared
    path = f'tasks/{tid}/worktrees'
    with api.app_state.store.transaction() as c:
        dev = H.type_create(c, 'human:ana', 'Dev tickets', numbered=True, steps=[
            {'name': 'On Deck', 'status': 'open'}, {'name': 'In Progress', 'status': 'doing'},
            {'name': 'Review 1', 'status': 'review'}, {'name': 'Review 2', 'status': 'review'},
            {'name': 'Pending Release', 'status': 'review'}, {'name': 'Post Release QA', 'status': 'review'},
            {'name': 'Done', 'status': 'done'}, {'name': 'Closed', 'status': 'closed'}])
        steps = {s['name']: s['id'] for s in dev['steps']}
        c.execute("INSERT INTO task_relations(from_task,to_task,kind,created_at) VALUES(?,?,'blocks',?)", (other, tid, H.now()))
        pr = H.task_link(c, 'bot:cpo', other, 'https://github.com/Acme/product/pull/7')

    def at(step, pr_state):
        with api.app_state.store.transaction() as c:
            c.execute('UPDATE tasks SET type_id=?,step_id=?,status=? WHERE id=?',
                      (dev['id'], steps[step], 'review', other))
            c.execute('UPDATE task_links SET state=? WHERE id=?', (pr_state, pr['id']))
        return post(api, path, {'repo': 'Acme/product'}, 'bot-test')

    refused = at('Review 2', 'open')
    assert refused.status_code == 409 and refused.json()['error']['code'] == 'worktree_start', refused.text
    assert refused.json()['error']['detail'] == ('This task is blocked by Other work, still at Review 2 with pull '
                                                 'request #7 not merged yet: it gets a worktree once that code is merged.')
    def starts(step, pr_state):
        started = at(step, pr_state)
        assert started.status_code == 200, (step, pr_state, started.text)
        with api.app_state.store.transaction() as c:     # released, so the next check asks for a new tree
            c.execute("UPDATE task_links SET state='removed' WHERE id=?", (started.json()['link_id'],))

    starts('Pending Release', 'merged')
    for step, pr_state in (('Pending Release', 'open'), ('Post Release QA', 'open'), ('Review 2', 'merged')):
        starts(step, pr_state)              # the step alone, or the merged pull request alone, is enough
    # A closed, unmerged pull request was abandoned: it neither counts as merged nor holds a merged one back.
    with api.app_state.store.transaction() as c:
        H.task_link(c, 'bot:cpo', other, 'https://github.com/Acme/product/pull/8')
        c.execute("UPDATE task_links SET state='closed' WHERE kind='pr' AND number=8")
    starts('Review 2', 'merged')
    refused = at('Review 2', 'closed')
    assert refused.status_code == 409
    assert 'still at Review 2 with no merged pull request' in refused.json()['error']['detail']
    # A merged pull request beside one still open is not enough: the open one names what is missing.
    with api.app_state.store.transaction() as c:
        c.execute("UPDATE task_links SET state='open' WHERE kind='pr' AND number=8")
    refused = at('Review 2', 'merged')
    assert 'still at Review 2 with pull request #8 not merged yet' in refused.json()['error']['detail']
    # Every blocker counts, not only the first: a met one does not let an unmet one behind it through.
    with api.app_state.store.transaction() as c:
        later = H.task_create(c, 'human:ana', 'Later work', 'Implement more', 'bot:cpo')
        c.execute("UPDATE tasks SET status='doing' WHERE id=?", (later['id'],))
        c.execute("INSERT INTO task_relations(from_task,to_task,kind,created_at) VALUES(?,?,'blocks',?)",
                  (later['id'], tid, H.now()))
    refused = at('Pending Release', 'merged')
    assert refused.json()['error']['detail'].startswith('This task is blocked by Later work, still at '), refused.text


def test_only_the_pending_release_step_marks_a_blocker_merged(prepared):
    """A step with a ready status can come before work that still changes code: a blocker there counts only by its
    status, and one moved on to a later doing step still holds the dependent."""
    api, tid, other = prepared
    path = f'tasks/{tid}/worktrees'
    with api.app_state.store.transaction() as c:
        flow = H.type_create(c, 'human:ana', 'Custom flow', steps=[
            {'name': 'Build', 'status': 'doing'}, {'name': 'Ready', 'status': 'ready'},
            {'name': 'Follow-up', 'status': 'doing'}, {'name': 'Pending Release', 'status': 'review'},
            {'name': 'Done', 'status': 'done'}])
        steps = {s['name']: s['id'] for s in flow['steps']}
        c.execute("INSERT INTO task_relations(from_task,to_task,kind,created_at) VALUES(?,?,'blocks',?)", (other, tid, H.now()))

    def at(step, status):
        with api.app_state.store.transaction() as c:
            c.execute('UPDATE tasks SET type_id=?,step_id=?,status=? WHERE id=?', (flow['id'], steps[step], status, other))
        return post(api, path, {'repo': 'Acme/product'}, 'bot-test')

    refused = at('Follow-up', 'doing')
    assert refused.status_code == 409, refused.text
    assert 'still at Follow-up with no merged pull request' in refused.json()['error']['detail']
    started = at('Ready', 'ready')
    assert started.status_code == 200, started.text


def test_an_obsolete_pending_reservation_is_cancelled_without_touching_a_checkout(prepared):
    api, tid, _ = prepared
    link = post(api, f'tasks/{tid}/worktrees', {'repo': 'Acme/product'}, 'bot-test').json()
    assert link['state'] == 'pending' and link['checkout_state'] == 'queued'
    with api.app_state.store.transaction() as c:
        H.task_link(c, 'bot:cmo', tid, 'https://github.com/Acme/product/pull/1')
        c.execute("UPDATE task_links SET state='merged' WHERE kind='pr'")
    # Nothing was checked out: the place frees on the server, no computer is asked to remove anything.
    actions = post(api, 'runners/heartbeat', {**HEARTBEAT, 'worktrees': []}, 'runner-test').json()['worktree_actions']
    assert actions == []
    with api.app_state.store.read() as c:
        row = c.execute('SELECT state,detail FROM task_links WHERE id=?', (link['link_id'],)).fetchone()
    assert row['state'] == 'removed' and json.loads(row['detail'])['removed_by'] == 'cancelled'
    assert [e['reason'] for e in released_events(api, tid)] == ['pending cancelled']


def test_a_pending_tree_with_a_checkout_on_disk_is_removed_the_normal_way_when_its_task_closes(prepared):
    api, tid, _ = prepared
    link = post(api, f'tasks/{tid}/worktrees', {'repo': 'Acme/product'}, 'bot-test').json()
    report = {'link_id': link['link_id'], 'state': 'pending', 'checkout_state': 'checkout_ready',
              'branch': link['branch'], 'last_commit': 'c' * 40}
    with api.app_state.store.transaction() as c:
        c.execute("UPDATE tasks SET status='closed' WHERE id=?", (tid,))
    actions = post(api, 'runners/heartbeat', {**HEARTBEAT, 'worktrees': [report]}, 'runner-test').json()['worktree_actions']
    assert [a['action'] for a in actions] == ['remove']        # the computer keeps any unsaved work, as always
    report['state'] = 'removed'
    post(api, 'runners/heartbeat', {**HEARTBEAT, 'worktrees': [report]}, 'runner-test')
    assert [e['reason'] for e in released_events(api, tid)] == ['task finished']


def test_a_fix_forward_keeps_its_tree_while_a_turn_runs_or_the_branch_moved_past_the_merged_head(prepared):
    api, tid, _ = prepared
    link = post(api, f'tasks/{tid}/worktrees', {'repo': 'Acme/product'}, 'bot-test').json()
    assert mark_ready(api, tid, link['link_id']).status_code == 200
    report = {'link_id': link['link_id'], 'state': 'present', 'checkout_state': 'ready', 'branch': link['branch'],
              'last_commit': 'd' * 40, 'ahead': 0}
    body = {**HEARTBEAT, 'worktrees': [report]}
    with api.app_state.store.transaction() as c:
        pr = H.task_link(c, 'bot:cmo', tid, 'https://github.com/Acme/product/pull/7')
        c.execute("UPDATE task_links SET state='merged',branch=?,detail=? WHERE id=?",
                  (link['branch'], json.dumps({'head_sha': 'e' * 40}), pr['id']))
        c.execute("UPDATE tasks SET carried_by='a1' WHERE id=?", (tid,))     # the bot's turn on this task is running

    def held():
        with api.app_state.store.read() as c:
            return {w['link_id']: w for w in W.holdings(c, 'bot:cmo')}[link['link_id']].get('kept')
    # A live turn: held, whatever the branch says.
    assert post(api, 'runners/heartbeat', body, 'runner-test').json()['worktree_actions'] == []
    assert held() == 'held: live turn'
    # The turn ended, but the pushed branch has commits past the merged pull request's head: held.
    with api.app_state.store.transaction() as c:
        c.execute("UPDATE tasks SET carried_by=NULL WHERE id=?", (tid,))
    assert post(api, 'runners/heartbeat', body, 'runner-test').json()['worktree_actions'] == []
    assert held() == 'held: new commits since PR #7'
    # The branch is where the merged pull request left it: released, and the release says why once removed.
    report['last_commit'] = 'e' * 40
    actions = post(api, 'runners/heartbeat', body, 'runner-test').json()['worktree_actions']
    assert [(a['action'], a.get('release')) for a in actions] == [('remove', True)]
    report['state'] = 'removed'
    post(api, 'runners/heartbeat', body, 'runner-test')
    post(api, 'runners/heartbeat', body, 'runner-test')
    assert released_events(api, tid) == [{'link': link['link_id'], 'path': link['path'], 'reason': 'pull request merged'}]


def test_a_checkout_stuck_past_the_deadline_fails_once_owned_and_retries_on_the_same_link(prepared):
    api, tid, _ = prepared
    link = post(api, f'tasks/{tid}/worktrees', {'repo': 'Acme/product'}, 'bot-test').json()
    old = H.shift(H.now(), hours=-(W.PENDING_HOURS + 1))
    with api.app_state.store.transaction() as c:
        c.execute('UPDATE task_links SET updated_at=? WHERE id=?', (old, link['link_id']))

    def told():
        with api.app_state.store.read() as c:
            return c.execute("SELECT count(*) FROM messages WHERE recipient_id='bot:cmo' AND body LIKE '%Setup did not finish%'"
                             ).fetchone()[0]
    for _ in range(2):
        assert post(api, 'runners/heartbeat', {**HEARTBEAT, 'worktrees': []}, 'runner-test').json()['worktree_actions'] == []
    assert told() == 1
    with api.app_state.store.read() as c:
        assert '`hub task worktree add Acme/product`' in c.execute(           # nothing checked out: add starts it
            "SELECT body FROM messages WHERE body LIKE '%Setup did not finish%'").fetchone()[0]
        held = {w['link_id']: w for w in W.holdings(c, 'bot:cmo')}
        assert W.current_error('pending', json.loads(c.execute('SELECT detail FROM task_links WHERE id=?',
                                                               (link['link_id'],)).fetchone()[0]))
    assert link['link_id'] in held                                   # it keeps its place, visibly
    again = post(api, f'tasks/{tid}/worktrees', {'repo': 'Acme/product'}, 'bot-test').json()
    assert (again['link_id'], again['path'], again['branch']) == (link['link_id'], link['path'], link['branch'])
    with api.app_state.store.read() as c:
        detail = json.loads(c.execute('SELECT detail FROM task_links WHERE id=?', (link['link_id'],)).fetchone()[0])
    assert detail['checkout_state'] == 'queued' and 'pending_expired' not in detail


def test_a_retry_after_the_deadline_keeps_the_setup_phase_and_names_the_registered_setup_command(prepared):
    api, tid, _ = prepared
    link = post(api, f'tasks/{tid}/worktrees', {'repo': 'Acme/product'}, 'bot-test').json()
    proof = {'expected_head': 'a' * 40, 'checkout_target': 'refs/remotes/origin/main', 'expected_base': 'b' * 64}
    route = f'/api/v2/tasks/{tid}/links/{link["link_id"]}'

    def patch(**fields):
        return api.patch(route, json=fields, headers={**auth('runner-test'), 'Idempotency-Key': uuid.uuid4().hex})
    for fields in ({'state': 'pending', 'checkout_state': 'initializing', 'setup_pending': True, **proof},
                   {'state': 'pending', 'checkout_state': 'checkout_ready', 'setup_pending': True},
                   {'state': 'pending', 'checkout_state': 'setup_running', 'setup_pending': True}):   # setup began
        assert patch(**fields).status_code == 200
    with api.app_state.store.transaction() as c:
        c.execute('UPDATE task_links SET updated_at=? WHERE id=?',
                  (H.shift(H.now(), hours=-(W.PENDING_HOURS + 1)), link['link_id']))
    post(api, 'runners/heartbeat', {**HEARTBEAT, 'worktrees': []}, 'runner-test')
    with api.app_state.store.read() as c:
        told = c.execute("SELECT body FROM messages WHERE recipient_id='bot:cmo' AND body LIKE '%Setup did not finish%'"
                         ).fetchone()[0]
    assert '`hub task worktree setup Acme/product`' in told        # the folder exists: setup resumes it in place
    again = post(api, f'tasks/{tid}/worktrees', {'repo': 'Acme/product'}, 'bot-test').json()
    assert again['link_id'] == link['link_id'] and again['checkout_state'] == 'setup_running'
    assert {k: again[k] for k in proof} == proof
    # The registered setup command's own reports (runner/worktrees.py _run_registered_setup) finish it.
    assert patch(state='pending', checkout_state='setup_running', setup_pending=True, **proof).status_code == 200
    assert patch(state='present', checkout_state='ready', setup_pending=False, **proof).status_code == 200
    with api.app_state.store.read() as c:
        row = c.execute('SELECT state,detail FROM task_links WHERE id=?', (link['link_id'],)).fetchone()
    assert row['state'] == 'present' and 'pending_expired' not in json.loads(row['detail'])


def test_the_registered_setup_retry_after_the_deadline_clears_the_failure_at_its_first_report(prepared):
    api, tid, _ = prepared
    link = post(api, f'tasks/{tid}/worktrees', {'repo': 'Acme/product'}, 'bot-test').json()
    proof = {'expected_head': 'a' * 40, 'checkout_target': 'refs/remotes/origin/main', 'expected_base': 'b' * 64}
    route = f'/api/v2/tasks/{tid}/links/{link["link_id"]}'

    def patch(**fields):
        return api.patch(route, json=fields, headers={**auth('runner-test'), 'Idempotency-Key': uuid.uuid4().hex})

    def stored():
        with api.app_state.store.read() as c:
            row = c.execute('SELECT state,detail FROM task_links WHERE id=?', (link['link_id'],)).fetchone()
        return row['state'], json.loads(row['detail'])
    for fields in ({'state': 'pending', 'checkout_state': 'initializing', 'setup_pending': True, **proof},
                   {'state': 'pending', 'checkout_state': 'setup_running', 'setup_pending': True}):
        assert patch(**fields).status_code == 200
    with api.app_state.store.transaction() as c:
        c.execute('UPDATE task_links SET updated_at=? WHERE id=?',
                  (H.shift(H.now(), hours=-(W.PENDING_HOURS + 1)), link['link_id']))
    post(api, 'runners/heartbeat', {**HEARTBEAT, 'worktrees': []}, 'runner-test')
    state, detail = stored()        # expired, with the phase and proof the setup command resumes from
    assert state == 'pending' and detail['pending_expired'] and detail['checkout_state'] == 'setup_running'
    assert {k: detail[k] for k in proof} == proof
    # `hub task worktree setup` straight away, no add: its first progress report clears the failure.
    assert patch(state='pending', checkout_state='setup_running', setup_pending=True, **proof).status_code == 200
    state, detail = stored()
    assert state == 'pending' and 'pending_expired' not in detail and detail['checkout_state'] == 'setup_running'
    assert patch(state='present', checkout_state='ready', setup_pending=False, **proof).status_code == 200
    assert stored()[0] == 'present'


def test_a_finished_task_with_unsaved_work_names_its_wip_branch_even_when_a_stale_push_was_cleared(prepared):
    api, tid, _ = prepared
    with api.app_state.store.transaction() as c:
        W.released_event(c, {'id': 'l' * 36, 'path': 'p', 'task_id': tid},
                         {'release_reason': 'task finished', 'stale_push_cleared': 'a' * 40, 'dirty_files': 2})
    assert released_events(api, tid)[-1]['wip_branch'] == f"wip/{tid[:8]}-llllll"


def test_a_delayed_old_push_clears_on_the_next_clean_report_and_a_real_fix_forward_push_holds(prepared):
    api, tid, _ = prepared
    link = post(api, f'tasks/{tid}/worktrees', {'repo': 'Acme/product'}, 'bot-test').json()
    assert mark_ready(api, tid, link['link_id']).status_code == 200
    report = {'link_id': link['link_id'], 'state': 'present', 'checkout_state': 'ready', 'branch': link['branch'],
              'last_commit': 'b' * 40, 'ahead': 0, 'dirty_files': 0}
    body = {**HEARTBEAT, 'worktrees': [report]}
    with api.app_state.store.transaction() as c:
        pr = H.task_link(c, 'bot:cmo', tid, 'https://github.com/Acme/product/pull/7')
        c.execute("UPDATE task_links SET state='merged',branch=?,detail=? WHERE id=?",
                  (link['branch'], json.dumps({'head_sha': 'b' * 40}), pr['id']))

    def deliver(sha):
        """The push webhook for `sha` on the worktree branch, through the real handler."""
        from backend import github
        with api.app_state.store.transaction() as c:
            github.push(c, {'ref': 'refs/heads/' + link['branch'], 'after': sha,
                            'repository': {'full_name': 'Acme/product', 'default_branch': 'main'}})

    def held():
        with api.app_state.store.read() as c:
            row = dict(c.execute('SELECT * FROM task_links WHERE id=?', (link['link_id'],)).fetchone())
            return W.held_reason(c, {**row, 'owner': 'bot:cmo'}, json.loads(row['detail']))
    # A real fix-forward: C pushed, and the computer is at C too. Held until C's pull request finishes.
    deliver('c' * 40)
    report['last_commit'] = 'c' * 40
    assert post(api, 'runners/heartbeat', body, 'runner-test').json()['worktree_actions'] == []
    assert held() == 'held: new commits since PR #7'
    with api.app_state.store.transaction() as c:
        pr2 = H.task_link(c, 'bot:cmo', tid, 'https://github.com/Acme/product/pull/8')
        c.execute("UPDATE task_links SET state='open',branch=?,detail=? WHERE id=?",
                  (link['branch'], json.dumps({'head_sha': 'c' * 40}), pr2['id']))
    assert post(api, 'runners/heartbeat', body, 'runner-test').json()['worktree_actions'] == []
    # C's pull request merges; then an old push of A is delivered late. It holds only until the next report.
    with api.app_state.store.transaction() as c:
        c.execute("UPDATE task_links SET state='merged',pr_merged_at=? WHERE id=?", (H.now(), pr2['id']))
    deliver('a' * 40)
    assert held() == 'held: new commits since PR #8'
    actions = post(api, 'runners/heartbeat', body, 'runner-test').json()['worktree_actions']
    assert [(a['action'], a.get('release')) for a in actions] == [('remove', True)]
    report['state'] = 'removed'
    post(api, 'runners/heartbeat', body, 'runner-test')
    assert released_events(api, tid)[-1]['reason'] == f"pull request merged (stale push {'a' * 12} cleared)"


def test_a_stale_pending_tree_whose_pull_requests_finished_is_released_without_a_retry_nudge(prepared):
    api, tid, _ = prepared
    link = post(api, f'tasks/{tid}/worktrees', {'repo': 'Acme/product'}, 'bot-test').json()
    old = H.shift(H.now(), hours=-(W.PENDING_HOURS + 1))
    with api.app_state.store.transaction() as c:
        detail = json.loads(c.execute('SELECT detail FROM task_links WHERE id=?', (link['link_id'],)).fetchone()[0])
        detail.update(checkout_state='checkout_ready', current_branch=link['branch'], last_commit='c' * 40,
                      expected_head='a' * 40, checkout_target='refs/remotes/origin/main', expected_base='b' * 64)
        c.execute('UPDATE task_links SET detail=?,updated_at=? WHERE id=?', (json.dumps(detail), old, link['link_id']))
        H.task_link(c, 'bot:cmo', tid, 'https://github.com/Acme/product/pull/1')
        c.execute("UPDATE task_links SET state='merged' WHERE kind='pr'")
    actions = post(api, 'runners/heartbeat', {**HEARTBEAT, 'worktrees': []}, 'runner-test').json()['worktree_actions']
    assert [(a['action'], a.get('release')) for a in actions] == [('remove', True)]
    with api.app_state.store.read() as c:
        assert not c.execute("SELECT count(*) FROM messages WHERE recipient_id='bot:cmo' AND body LIKE '%Setup did not finish%'"
                             ).fetchone()[0]
        assert 'pending_expired' not in json.loads(c.execute('SELECT detail FROM task_links WHERE id=?',
                                                             (link['link_id'],)).fetchone()[0])


def test_only_the_task_owners_own_turn_holds_its_tree(prepared):
    api, tid, _ = prepared
    with api.app_state.store.transaction() as c:
        c.execute("UPDATE tasks SET carried_by='a1' WHERE id=?", (tid,))        # cmo's turn on the task is running
        assert W.live_turn(c, tid, 'bot:cmo')
        assert not W.live_turn(c, tid, 'bot:cpo')                               # a reviewer's or other bot's view
        c.execute("UPDATE tasks SET owner_id='bot:cpo' WHERE id=?", (tid,))        # the task moved to another bot
        assert not W.live_turn(c, tid, 'bot:cmo')                               # the former owner's turn holds nothing
        row = {'task_id': tid, 'owner': 'bot:cmo', 'repo': 'Acme/product', 'branch': None}
        assert W.held_reason(c, row, {}) == ''


def test_a_fault_reading_turns_is_raised_not_taken_for_no_live_turn(prepared):
    import sqlite3
    api, tid, _ = prepared

    class Failing:
        def __init__(self, c, message):
            self.c, self.message = c, message

        def execute(self, sql, *args):
            if 'FROM attempts' in sql:
                raise sqlite3.OperationalError(self.message)
            return self.c.execute(sql, *args)
    with api.app_state.store.read() as c:
        assert not W.live_turn(Failing(c, 'no such table: attempts'), tid, 'bot:cmo')    # a local hub
        with pytest.raises(sqlite3.OperationalError):
            W.live_turn(Failing(c, 'database disk image is malformed'), tid, 'bot:cmo')


def test_a_new_push_after_a_stale_push_was_cleared_resets_the_cleared_flag(prepared):
    from backend import github
    api, tid, _ = prepared
    link = post(api, f'tasks/{tid}/worktrees', {'repo': 'Acme/product'}, 'bot-test').json()
    with api.app_state.store.transaction() as c:
        detail = json.loads(c.execute('SELECT detail FROM task_links WHERE id=?', (link['link_id'],)).fetchone()[0])
        c.execute('UPDATE task_links SET detail=? WHERE id=?',
                  (json.dumps({**detail, 'stale_push_cleared': 'a' * 40}), link['link_id']))
        github.push(c, {'ref': 'refs/heads/' + link['branch'], 'after': 'd' * 40,
                        'repository': {'full_name': 'Acme/product', 'default_branch': 'main'}})
        detail = json.loads(c.execute('SELECT detail FROM task_links WHERE id=?', (link['link_id'],)).fetchone()[0])
    assert detail['pushed'] == 'd' * 40 and 'stale_push_cleared' not in detail


@pytest.mark.parametrize('guard', ['merge_time', 'branch', 'repository', 'matching', 'legacy'])
def test_finished_pr_must_match_the_working_allocation(prepared, guard):
    api, tid, _ = prepared
    link = post(api, f'tasks/{tid}/worktrees', {'repo': 'Acme/product'}, 'bot-test').json()
    assert mark_ready(api, tid, link['link_id']).status_code == 200
    allocation = '2026-10-23T08:00:00.500000Z'
    merged = '2026-10-23T08:00:00Z' if guard == 'merge_time' else '2026-10-23T08:00:01Z'
    branch = 'tico/other' if guard == 'branch' else link['branch']
    repo = 'Acme/other' if guard == 'repository' else 'acme/PRODUCT'
    if guard == 'legacy':
        repo = branch = None
    with api.app_state.store.transaction() as c:
        detail = json.loads(c.execute('SELECT detail FROM task_links WHERE id=?', (link['link_id'],)).fetchone()[0])
        detail['allocation_started'] = allocation
        detail['remote'] = {'at': H.now(), 'heads': ['a' * 40]}
        c.execute('UPDATE task_links SET detail=? WHERE id=?', (json.dumps(detail), link['link_id']))
        c.execute("UPDATE attempts SET state='completed' WHERE id='a1'")
        pr = H.task_link(c, 'bot:cmo', tid, 'https://github.com/Acme/product/pull/7')
        # Late linking must not make a historical merge belong to the renewed allocation.
        c.execute("UPDATE task_links SET state='merged',created_at=?,pr_merged_at=?,repo=?,branch=?,detail=? WHERE id=?",
                  ('2026-10-23T08:00:02.000000Z', merged, repo, branch, json.dumps({'head_sha': 'a' * 40}), pr['id']))
    body = {**HEARTBEAT, 'worktrees': [
        {'link_id': link['link_id'], 'state': 'present', 'checkout_state': 'ready',
         'branch': link['branch'], 'last_commit': 'a' * 40, 'ahead': 0, 'dirty_files': 0}]}
    response = post(api, 'runners/heartbeat', body, 'runner-test')
    assert response.status_code == 200, response.text
    released = guard in ('matching', 'legacy')
    assert [(a['link_id'], a['action']) for a in response.json()['worktree_actions']] == (
        [(link['link_id'], 'remove')] if released else [])
    with api.app_state.store.read() as c:
        held = {w['link_id']: w for w in W.holdings(c, 'bot:cmo')}
        assert held[link['link_id']]['clear'] == ('merged' if released else 'in_progress')
        detail = json.loads(c.execute('SELECT detail FROM task_links WHERE id=?', (link['link_id'],)).fetchone()[0])
        assert any(detail.get(k) for k in W.REMOVAL) == released


def test_old_merged_pr_releases_its_tree_but_keeps_a_newer_doing_allocation(prepared):
    api, tid, _ = prepared
    original = post(api, f'tasks/{tid}/worktrees', {'repo': 'Acme/product'}, 'bot-test').json()
    assert mark_ready(api, tid, original['link_id']).status_code == 200
    with api.app_state.store.transaction() as c:
        pr = H.task_link(c, 'bot:cmo', tid, 'https://github.com/Acme/product/pull/7')
        old = H.shift(H.now(), days=-2)
        c.execute('UPDATE task_links SET created_at=? WHERE id=?', (H.shift(old, days=-1), original['link_id']))
        c.execute("UPDATE task_links SET state='merged',created_at=?,branch=?,detail=? WHERE id=?",
                  (old, original['branch'], json.dumps({'head_sha': 'a' * 40}), pr['id']))
    newer = post(api, f'tasks/{tid}/worktrees/attach',
                 {'repo': 'Acme/product', 'path': f'tasks/{tid[:8]}/new', 'branch': 'tico/new'}, 'bot-test').json()
    assert mark_ready(api, tid, newer['link_id']).status_code == 200
    # A missed webhook can link the historical PR after the newer allocation starts.
    with api.app_state.store.transaction() as c:
        c.execute('UPDATE task_links SET created_at=?,pr_merged_at=? WHERE id=?', (H.now(), old, pr['id']))
    body = {**HEARTBEAT, 'worktrees': [
        {'link_id': tree['link_id'], 'state': 'present', 'checkout_state': 'ready',
         'branch': tree['branch'], 'last_commit': 'a' * 40} for tree in (original, newer)]}
    actions = post(api, 'runners/heartbeat', body, 'runner-test').json()['worktree_actions']
    assert [(a['link_id'], a['action']) for a in actions] == [(original['link_id'], 'remove')]
    with api.app_state.store.read() as c:
        detail = json.loads(c.execute('SELECT detail FROM task_links WHERE id=?', (newer['link_id'],)).fetchone()[0])
        assert not any(detail.get(k) for k in W.REMOVAL)
        held = {w['link_id']: w for w in W.holdings(c, 'bot:cmo')}
        assert held[newer['link_id']]['clear'] == 'in_progress'
        assert held[original['link_id']]['clear'] == 'merged'
    # Review-step cleanup keeps its established behavior, including task-wide finished PRs.
    with api.app_state.store.transaction() as c:
        c.execute("UPDATE tasks SET status='review' WHERE id=?", (tid,))
    actions = post(api, 'runners/heartbeat', body, 'runner-test').json()['worktree_actions']
    assert {a['link_id'] for a in actions if a['action'] == 'remove'} == {original['link_id'], newer['link_id']}


@pytest.mark.parametrize('manager', ['bot', 'person'])
def test_owner_managers_provision_reports_worktrees_without_granting_repo_access(prepared, manager):
    """Managers act on the owner's computer using the owner's existing repository grants."""
    from backend.auth import Identity
    api, tid, _ = prepared
    identities = api.app_state.store.settings.test_identities
    identities['manager-test'] = (Identity('bot:cpo', 'bot', runner_id='r2', attempt_id='manager-attempt') if manager == 'bot'
                                  else Identity('human:riley', 'human', 'riley@acme.example'))
    if manager == 'bot':
        from backend.tests.test_github_app import _box
        _box(api)
        with api.app_state.store.transaction() as c:
            c.execute("INSERT OR REPLACE INTO assignments VALUES('cpo','r2',1,?,'ana')", (H.now(),))
            c.execute("INSERT INTO messages(id,sender_id,recipient_id,kind,body,created_at) VALUES('manager-message','human:ana','bot:cpo','say','Manage work',?)", (H.now(),))
            job = c.execute("SELECT id FROM jobs WHERE message_id='manager-message'").fetchone()[0]
            c.execute("INSERT INTO attempts(id,job_id,bot_id,runner_id,generation,token_hash,state,lease_until,created_at) VALUES('manager-attempt',?,'cpo','r2',1,'synthetic-manager','running',?,?)",
                      (job, H.shift(H.now(), hours=1), H.now()))
    with api.app_state.store.transaction() as c:
        c.execute("UPDATE bot_config SET reports_to='cpo' WHERE bot='cmo'")
        c.execute("UPDATE bot_config SET reports_to='human:riley' WHERE bot='cpo'")
        c.execute("UPDATE humans SET teams='[\"sales\"]' WHERE id='riley'")
    response = post(api, f'tasks/{tid}/worktrees', {'repo': 'Acme/product'}, 'manager-test')
    assert response.status_code == 200, response.text
    with api.app_state.store.read() as c:
        link = H.task_links(c, tid)[0]
        assert link['computer_id'] == 'r1' and link['added_by'] == identities['manager-test'].actor
        assert json.loads(link['detail'])['owner'] == 'bot:cmo'
    # Removing the owner's repository grant still refuses its manager.
    put(api, 'bots/cmo/repositories', {'mode': 'all', 'all_access': 'read'})
    assert post(api, f'tasks/{tid}/worktrees', {'repo': 'Acme/product'}, 'manager-test').status_code == 403

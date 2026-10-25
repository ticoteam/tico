"""Recovery generations preserve history and require independent runner confirmation."""
import json
import uuid
from unittest.mock import Mock

import pytest

from backend import hubdb as H, worktrees as W
from backend.tests.test_worktrees import prepared, post, mark_ready  # noqa: F401
from backend.tests.test_github_app import api, gh, auth  # noqa: F401
from backend.tests.test_openapi_v2 import conforms
from clients import hubcli, hubtools


def finished(prepared, *, state='removed', pr_state='merged', status='ready'):
    api, tid, other = prepared
    old = post(api, f'tasks/{tid}/worktrees', {'repo': 'Acme/product'}, 'bot-test').json()
    with api.app_state.store.transaction() as c:
        pr = H.task_link(c, 'bot:cmo', tid, 'https://github.com/Acme/product/pull/1438')
        c.execute("UPDATE task_links SET repo='Acme/product',branch=?,state=?,pr_sha=?,pr_merged_at=? WHERE id=?",
                  (old['branch'], pr_state, 'a' * 40, '2026-01-01T00:00:00Z', pr['id']))
        detail = {'owner': 'bot:cmo', 'release_requested': True, 'cleanup_requested': True,
                  'cleanup_gave_up': True, 'cleanup_attempts': 6, 'restore_on_reopen': True,
                  'checkout_state': 'ready', 'expected_head': 'a' * 40, 'expected_base': 'b' * 64,
                  'checkout_target': old['branch'], 'removed_by': 'cleanup'}
        c.execute('UPDATE task_links SET state=?,detail_json=? WHERE id=?', (state, json.dumps(detail), old['link_id']))
        step = {'ready': 'Pending Release', 'doing': 'QA Fixes', 'review': 'Review 1'}[status]
        c.execute("INSERT INTO task_types(id,name) VALUES('recovery-dev','Recovery Dev')")
        c.execute("INSERT INTO task_steps(id,type_id,name,position,status) VALUES('recovery-step','recovery-dev',?,1,?)", (step, status))
        c.execute("UPDATE tasks SET status=?,type_id='recovery-dev',step_id='recovery-step' WHERE id=?", (status, tid))
        old_row = dict(c.execute('SELECT * FROM task_links WHERE id=?', (old['link_id'],)).fetchone())
        pr_row = dict(c.execute('SELECT * FROM task_links WHERE id=?', (pr['id'],)).fetchone())
        task = dict(c.execute('SELECT status,step_id FROM tasks WHERE id=?', (tid,)).fetchone())
    return api, tid, old, old_row, pr_row, task


def rebind(api, tid, token='bot-test', **extra):
    return post(api, f'tasks/{tid}/worktrees/rebind',
                {'branch': f'tico/{tid[:8]}-recovery', 'reason': 'Recover after merged work', **extra}, token)


def beat(api, reports):
    response = post(api, 'runners/heartbeat', {
        'version': '0.5.4', 'platform': 'linux',
        'readiness': {'schema_version': 1, 'bots': {}, 'worktrees': True}, 'worktrees': reports}, 'runner-test')
    assert response.status_code == 200, response.text
    return response.json()['worktree_actions']


@pytest.mark.parametrize('status', ['ready', 'doing', 'review'])
def test_owner_rebind_keeps_history_and_requires_patch_before_presence(prepared, status):
    api, tid, old, original, pr, task = finished(prepared, status=status)
    response = rebind(api, tid)
    assert response.status_code == 200, response.text
    receipt = response.json()
    assert receipt['old_link_id'] == old['link_id'] and receipt['old_branch'] == old['branch']
    assert receipt['new_link_id'] != old['link_id'] and receipt['confirmation_state'] == 'unconfirmed'
    assert receipt['path'] != old['path'] and receipt['state'] == 'pending'
    document = api.get('/api/v2/openapi.json', headers=auth()).json()
    assert conforms(receipt, document['components']['schemas']['WorktreeRebindReceipt'], document) is None
    new = receipt['new_link_id']
    assert post(api, f'tasks/{tid}/worktrees/attach', {
        'path': receipt['path'], 'repo': 'Acme/product', 'branch': receipt['branch']}, 'bot-test').json()['link_id'] == new
    assert post(api, f'tasks/{tid}/worktrees/attach', {
        'path': old['path'], 'repo': 'Acme/product', 'branch': old['branch']}, 'bot-test').status_code == 409
    report = {'link_id': new, 'state': 'present', 'branch': receipt['branch'], 'checkout_state': 'ready',
              'expected_head': 'c' * 40, 'expected_base': 'd' * 64,
              'checkout_target': receipt['branch'], 'ahead': 0}
    assert beat(api, [report, {'link_id': old['link_id'], 'state': 'present', 'branch': old['branch']}]) == []
    with api.app_state.store.read() as c:
        assert c.execute('SELECT state FROM task_links WHERE id=?', (new,)).fetchone()[0] == 'pending'
        assert W.attached(c, tid, 'r1', 'cmo') == []
        detail = json.loads(c.execute('SELECT detail_json FROM task_links WHERE id=?', (new,)).fetchone()[0])
        assert not any(detail.get(k) for k in (*W.REMOVAL, 'cleanup_gave_up', 'removed_by', 'restore_on_reopen'))
        event = c.execute('SELECT * FROM events WHERE id=?', (receipt['event_id'],)).fetchone()
        assert event['actor'] == 'bot:cmo' and json.loads(event['detail_json'])['reason'] == 'Recover after merged work'
        assert c.execute("SELECT count(*) FROM task_events WHERE task_id=? AND field='worktree.rebound'", (tid,)).fetchone()[0] == 1
    # Legacy bare PATCH cannot certify a recovery generation.
    route = f'/api/v2/tasks/{tid}/links/{new}'
    assert api.patch(route, json={'state': 'present'}, headers={**auth('runner-test'), 'Idempotency-Key': uuid.uuid4().hex}).status_code == 409
    assert mark_ready(api, tid, new).status_code == 200
    assert beat(api, []) == []       # the old merged PR never releases this allocation, even at Pending Release
    with api.app_state.store.read() as c:
        assert len(W.attached(c, tid, 'r1', 'cmo')) == 1
        assert dict(c.execute('SELECT * FROM task_links WHERE id=?', (old['link_id'],)).fetchone()) == original
        assert dict(c.execute('SELECT * FROM task_links WHERE id=?', (pr['id'],)).fetchone()) == pr
        assert dict(c.execute('SELECT status,step_id FROM tasks WHERE id=?', (tid,)).fetchone()) == task
    # A PR created for the recovery branch releases it normally after merge.
    with api.app_state.store.transaction() as c:
        fresh_pr = H.task_link(c, 'bot:cmo', tid, 'https://github.com/Acme/product/pull/1439')
        c.execute("UPDATE task_links SET repo='Acme/product',branch=?,state='merged' WHERE id=?", (receipt['branch'], fresh_pr['id']))
        c.execute("UPDATE attempts SET state='completed' WHERE id='a1'")
    assert any(a['link_id'] == new and a['action'] == 'remove' for a in beat(api, []))


def test_rebind_owner_live_branch_and_path_boundaries(prepared):
    api, tid, old, original, pr, _ = finished(prepared)
    denied = rebind(api, tid, token='person-test')
    assert denied.status_code == 403 and denied.json()['error']['code'] == 'worktree_owner'
    assert rebind(api, tid, token='runner-test').status_code == 403
    assert rebind(api, tid, branch=old['branch']).json()['error']['code'] == 'worktree_branch_linked'
    for path in ('../outside', '/tmp/outside', 'repos/product', 'elsewhere/product', 'tasks/other/recovery', old['path']):
        assert rebind(api, tid, path=path).status_code in (409, 422)
    assert rebind(api, tid, branch='tico/bad/.hidden').status_code == 422
    assert rebind(api, tid, reason='  ').status_code == 422
    with api.app_state.store.transaction() as c:
        c.execute("UPDATE task_links SET state='present' WHERE id=?", (old['link_id'],))
    assert rebind(api, tid).json()['error']['code'] == 'worktree_live'
    with api.app_state.store.transaction() as c:
        c.execute("UPDATE task_links SET state='removed' WHERE id=?", (old['link_id'],))
        c.execute("UPDATE task_links SET state='open' WHERE id=?", (pr['id'],))
    assert rebind(api, tid).json()['error']['code'] == 'worktree_live'
    with api.app_state.store.transaction() as c:
        c.execute("UPDATE task_links SET state='merged' WHERE id=?", (pr['id'],))
        c.execute("INSERT INTO task_links(id,task_id,kind,url,repo,branch,created) VALUES('other',?,'worktree','worktree:other','Acme/product',?,?)",
                  (prepared[2], f'tico/{tid[:8]}-recovery', H.now()))
    assert rebind(api, tid).json()['error']['code'] == 'worktree_branch_linked'
    with api.app_state.store.transaction() as c:
        c.execute("UPDATE tasks SET owner='bot:cpo' WHERE id=?", (tid,))
    denied = rebind(api, tid)
    assert denied.status_code == 403 and denied.json()['error']['code'] == 'worktree_owner'


def test_human_owner_rebind_idempotency_and_retired_generation_protection(prepared):
    api, tid, old, original, _, _ = finished(prepared)
    route = f'/api/v2/tasks/{tid}/worktrees/rebind'
    headers = {**auth(), 'Idempotency-Key': uuid.uuid4().hex}
    body = {'branch': f'tico/{tid[:8]}-recovery', 'reason': 'Human verified recovery'}
    response = api.post(route, json=body, headers=headers)
    assert response.status_code == 200, response.text
    assert api.post(route, json=body, headers=headers).json() == response.json()
    assert api.patch(f'/api/v2/tasks/{tid}/links/{old["link_id"]}', json={'state': 'pending'},
                     headers={**auth('runner-test'), 'Idempotency-Key': uuid.uuid4().hex}).status_code == 409
    assert api.delete(f'/api/v2/tasks/{tid}/links/{old["link_id"]}',
                      headers={**auth(), 'Idempotency-Key': uuid.uuid4().hex}).json()['error']['code'] == 'worktree_history'
    with api.app_state.store.read() as c:
        assert dict(c.execute('SELECT * FROM task_links WHERE id=?', (old['link_id'],)).fetchone()) == original


def test_cli_and_mcp_share_the_cloud_transition(monkeypatch):
    args = hubcli.parser().parse_args(['task', 'worktree', 'rebind', '12345678', '--branch', 'tico/12345678-recovery',
                                       '--path', 'tasks/12345678/recovery', '--reason', 'Recover merged work'])
    assert args.fn == 'task worktree rebind'
    api = Mock()
    tool = hubtools.BY_NAME['hub_task_worktree_rebind']
    assert not tool['local'] and tool['writes']
    tool['fn'](api, {'task': args.task, 'branch': args.branch, 'path': args.path, 'reason': args.reason, 'operation_id': 'rebind-once'})
    api.post.assert_called_once_with('tasks/12345678/worktrees/rebind',
                                    {'branch': args.branch, 'path': args.path, 'reason': args.reason}, key='rebind-once')
    from clients import remotecli
    api.reset_mock()
    monkeypatch.setattr(remotecli, 'Client', lambda *a, **kw: api)
    monkeypatch.setenv('HUB_API_URL', 'https://tico.acme.example/api/v2')
    monkeypatch.setenv('HUB_OPERATION_ID', 'rebind-once')
    remotecli.run(args)
    api.post.assert_called_once_with('tasks/12345678/worktrees/rebind',
                                    {'branch': args.branch, 'path': args.path, 'reason': args.reason}, key='rebind-once')

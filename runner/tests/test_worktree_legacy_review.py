"""Independent contract checks for legacy add and pending setup reporting."""
import json

import pytest

from backend.tests.test_github_app import api, gh  # noqa: F401
from backend.tests.test_worktrees import prepared, post  # noqa: F401
from runner import worktrees as W
from runner.tests.test_worktrees import git, trees  # noqa: F401


@pytest.mark.parametrize('pending', [False, True])
def test_legacy_add_consumes_actual_provision_response(prepared, trees, monkeypatch, pending):
    api, task, _ = prepared
    workspace, base, remote, row, client = trees
    route = f'tasks/{task}/worktrees'
    link = post(api, route, {'repo': 'Acme/product'}, 'bot-test').json()
    assert link['state'] == 'pending'
    path = workspace / link['path']
    path.parent.mkdir(parents=True)
    git(base, 'config', 'remote.origin.url', 'https://github.com/Acme/product.git')
    git(base, 'config', f'url.{remote}.insteadOf', 'https://github.com/Acme/product.git')
    git(base, 'worktree', 'add', '--no-track', '-b', link['branch'], str(path), 'origin/main')
    (path / 'file').write_text('kept user edit')
    (path / 'notes.txt').write_text('kept untracked note')
    detail = {'owner': 'bot:cmo'}
    if pending:
        detail.update(checkout_state='legacy_present', setup_pending=True)
    with api.app_state.store.transaction() as c:
        c.execute('UPDATE task_links SET state=?,detail_json=? WHERE id=?',
                  ('pending' if pending else 'present', json.dumps(detail), link['link_id']))
    response = post(api, route, {'repo': 'Acme/product'}, 'bot-test')
    assert response.status_code == 200, response.text
    assert response.json()['state'] == ('pending' if pending else 'present')
    client.post.return_value = response.json()
    client.get.return_value = {'configured': True, 'repositories': [{
        'full_name': 'Acme/product', 'access': 'write', 'default_branch': 'main',
    }]}
    monkeypatch.setenv('HUB_TASK_ID', task)
    monkeypatch.setattr(W.repositories, 'worktree_base', lambda *args: (base, 'main'))

    result = W.command(client, 'add', 'Acme/product')

    assert (path / 'file').read_text() == 'kept user edit'
    assert (path / 'notes.txt').read_text() == 'kept untracked note'
    assert W._read_marker(workspace, link['link_id']) is None
    client.patch.assert_not_called()
    if pending:
        assert result['state'] != 'present', result
    else:
        assert result['state'] == 'present', result
        assert result['checkout_state'] == 'legacy_present', result


def test_legacy_heartbeat_does_not_report_pending_setup_present(trees):
    workspace, base, remote, row, client = trees
    path = workspace / row['path']
    path.parent.mkdir(parents=True)
    git(base, 'worktree', 'add', '--no-track', '-b', row['branch'], str(path), 'origin/main')
    row.update(state='present', detail_json=json.dumps({
        'checkout_state': 'legacy_present', 'setup_pending': True,
    }))
    report = W.inspect(workspace, row)
    assert report['state'] != 'present', report
    assert W._read_marker(workspace, row['id']) is None


@pytest.mark.parametrize(('setup_pending', 'missing_tracked'), [(False, False), (True, False), (False, True)])
def test_previously_demoted_legacy_link_recovers_only_after_runner_verification(
        prepared, trees, setup_pending, missing_tracked):
    api, task, _ = prepared
    workspace, base, remote, _, _ = trees
    link = post(api, f'tasks/{task}/worktrees', {'repo': 'Acme/product'}, 'bot-test').json()
    path = workspace / link['path']
    path.parent.mkdir(parents=True)
    git(base, 'config', 'remote.origin.url', 'https://github.com/Acme/product.git')
    git(base, 'config', f'url.{remote}.insteadOf', 'https://github.com/Acme/product.git')
    git(base, 'worktree', 'add', '--no-track', '-b', link['branch'], str(path), 'origin/main')
    git(path, 'config', 'user.name', 'Synthetic User')
    git(path, 'config', 'user.email', 'synthetic@example.invalid')
    tracked = path / 'file'
    if missing_tracked:
        tracked.unlink()
    else:
        tracked.write_text('kept tracked edit')
    (path / 'notes.txt').write_text('kept untracked note')
    legacy_history = {
        'owner': 'bot:cmo', 'current_branch': link['branch'],
        'last_commit': git(path, 'rev-parse', 'HEAD'), 'dirty_files': 2,
    }
    if setup_pending:
        legacy_history['setup_pending'] = True
    with api.app_state.store.transaction() as c:
        c.execute("UPDATE task_links SET state='pending',detail_json=? WHERE id=?",
                  (json.dumps(legacy_history), link['link_id']))
    with api.app_state.store.read() as c:
        row = dict(c.execute('SELECT * FROM task_links WHERE id=?', (link['link_id'],)).fetchone())

    report = W.inspect(workspace, row)
    if setup_pending or missing_tracked:
        assert report['state'] == 'pending' and report['checkout_state'] == 'unverified', report
    else:
        assert report['state'] == 'present' and report['checkout_state'] == 'legacy_present', report
        assert report['dirty_files'] == 2

    beat = {'version': '0.3.22', 'platform': 'test',
            'readiness': {'schema_version': 1, 'worktrees': True}, 'worktrees': [report]}
    response = post(api, 'runners/heartbeat', beat, 'runner-test')
    assert response.status_code == 200, response.text
    with api.app_state.store.read() as c:
        saved = c.execute('SELECT state,detail_json FROM task_links WHERE id=?', (link['link_id'],)).fetchone()
    detail = json.loads(saved['detail_json'])
    if setup_pending or missing_tracked:
        assert saved['state'] == 'pending'
        assert detail.get('checkout_state') != 'legacy_present'
    else:
        assert saved['state'] == 'present'
        assert detail['checkout_state'] == 'legacy_present'
        assert not any(key in detail for key in ('expected_head', 'checkout_target', 'expected_base'))
        assert tracked.read_text() == 'kept tracked edit'
        assert (path / 'notes.txt').read_text() == 'kept untracked note'
    if missing_tracked:
        assert not tracked.exists()
        assert (path / 'notes.txt').read_text() == 'kept untracked note'
    assert W._read_marker(workspace, link['link_id']) is None

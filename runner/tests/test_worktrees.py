"""Real Git worktree lifecycle, using only temporary repositories and synthetic credentials."""
import os
import json
from pathlib import Path
import shutil
import subprocess
from unittest import mock

import pytest

from clients.tico import APIError
from runner import worktrees as W
from runner.repositories import GB
from runner.service import Runner


def git(path, *args):
    return subprocess.run(['git', '-C', str(path), *args], check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture
def trees(tmp_path, monkeypatch):
    remote = tmp_path / 'remote.git'
    subprocess.run(['git', 'init', '--bare', '--initial-branch=main', str(remote)], check=True, capture_output=True)
    source = tmp_path / 'source'
    subprocess.run(['git', 'init', '--initial-branch=main', str(source)], check=True, capture_output=True)
    git(source, 'config', 'user.name', 'Sam')
    git(source, 'config', 'user.email', 'sam@example.com')
    (source / 'file').write_text('initial')
    git(source, 'add', '.')
    git(source, 'commit', '-m', 'Initial')
    git(source, 'remote', 'add', 'origin', str(remote))
    git(source, 'push', 'origin', 'main')
    workspace = tmp_path / 'workspace'
    base = workspace / 'repos' / 'org__product'
    base.parent.mkdir(parents=True)
    subprocess.run(['git', 'clone', str(remote), str(base)], check=True, capture_output=True)
    git(base, 'config', 'remote.origin.url', 'https://github.com/org/product.git')
    git(base, 'config', f'url.{remote}.insteadOf', 'https://github.com/org/product.git')
    monkeypatch.setattr(W.shutil, 'disk_usage', lambda p: shutil._ntuple_diskusage(100 * GB, 0, 100 * GB))
    monkeypatch.setenv('HUB_WORKSPACE', str(workspace))
    monkeypatch.setenv('HUB_BOT', 'engineer')
    monkeypatch.setenv('HUB_TASK_ID', '12345678abcdef')
    row = {'id': 'link1', 'link_id': 'link1', 'task_id': '12345678abcdef', 'path': 'tasks/12345678/org__product',
           'branch': 'tico/12345678-build', 'repo': 'org/product', 'full_name': 'org/product', 'default_branch': 'main',
           'state': 'present', 'owner': 'bot:engineer', 'setup_command': 'touch setup-ran'}
    client = mock.Mock()
    client.get.return_value = {'repositories': [{**row, 'access': 'write'}]}
    client.post.return_value = {'link_id': row['id'], 'branch': row['branch'], 'path': row['path'],
                                'checkout_state': 'queued', 'setup_pending': True}
    def patch_link(_route, fields):
        row.update({key: value for key, value in fields.items() if key in ('state', 'path', 'checkout_state', 'setup_pending')})
        detail = json.loads(row.get('detail_json') or '{}')
        detail.update({key: value for key, value in fields.items() if key in
                       ('checkout_state', 'setup_pending', 'expected_head', 'checkout_target', 'expected_base')})
        row['detail_json'] = json.dumps(detail)
        client.post.return_value.update(fields)
        return row
    client.patch.side_effect = patch_link
    return workspace, base, remote, row, client


def test_add_setup_report_attach_dirty_push_remove_restore(trees):
    workspace, base, remote, row, client = trees
    result = W.command(client, 'add', 'org/product')
    path = Path(result['workspace_path'])
    assert (path / 'setup-ran').exists()
    assert git(path, 'rev-parse', 'HEAD') == git(remote, 'rev-parse', 'main')
    (path / 'file').write_text('changed')
    report = W.inspect(workspace, row)
    assert report['state'] == 'present' and report['dirty_files'] == 2 and report['last_commit']
    attached = W.command(client, 'attach', str(path))
    assert attached['path'] == row['path']
    assert client.post.call_args.args[0].endswith('/worktrees/attach')
    assert W.act(workspace, row, 'remove', os.environ.copy()) == 'removed'
    assert not path.exists()
    saved = git(remote, 'show', W.wip_branch(row) + ':file')
    assert saved == 'changed'
    assert W.inspect(workspace, {**row, 'state': 'removed'})['state'] == 'removed'
    assert W.act(workspace, row, 'restore', os.environ.copy()) == 'present'
    assert git(path, 'symbolic-ref', '--short', 'HEAD') == row['branch']
    assert (path / 'file').read_text() == 'changed'
    assert (path / 'setup-ran').exists()  # saved file, not a rerun of setup


def test_interrupted_add_never_restores_a_tracked_deletion(trees, monkeypatch):
    workspace, base, remote, row, client = trees
    link = {**client.post.return_value}
    client.post.return_value = link
    client.patch.side_effect = lambda route, body: (link.update(body), dict(link))[1]
    real_git = W.git
    interrupted = [False]

    def interrupted_add(path, *args, **kwargs):
        result = real_git(path, *args, **kwargs)
        if args[:2] == ('worktree', 'add') and not interrupted[0]:
            interrupted[0] = True
            (Path(link['path']) if Path(link['path']).is_absolute() else workspace / link['path']).joinpath('file').unlink()
            raise ValueError('synthetic interruption after worktree creation')
        return result

    monkeypatch.setattr(W, 'git', interrupted_add)
    with pytest.raises(ValueError, match='synthetic interruption'):
        W.command(client, 'add', 'org/product')
    assert link['checkout_state'] == 'initializing' and link['state'] == 'pending'
    path = workspace / link['path']
    assert not (path / 'file').exists()

    monkeypatch.setattr(W, 'git', real_git)
    with pytest.raises(ValueError, match='changed or are incomplete; kept worktree without restoring files'):
        W.command(client, 'add', 'org/product')
    assert not (path / 'file').exists()
    assert link['checkout_state'] == 'initializing' and link['state'] == 'pending'


def test_interrupted_clean_materialization_resumes_with_its_marker(trees, monkeypatch):
    workspace, base, remote, row, client = trees
    link = {**client.post.return_value}
    client.post.return_value = link
    client.patch.side_effect = lambda route, body: (link.update(body), dict(link))[1]
    real_resume = W._resume_interrupted_checkout
    interrupted = [False]

    def crash_after_git_add(*args, **kwargs):
        if not interrupted[0]:
            interrupted[0] = True
            raise OSError('synthetic crash after clean Git materialization')
        return real_resume(*args, **kwargs)

    monkeypatch.setattr(W, '_resume_interrupted_checkout', crash_after_git_add)
    with pytest.raises(OSError, match='synthetic crash'):
        W.command(client, 'add', 'org/product')
    path = workspace / link['path']
    assert (path / 'file').read_text() == 'initial'
    assert link['checkout_state'] == 'initializing'

    monkeypatch.setattr(W, '_resume_interrupted_checkout', real_resume)
    result = W.command(client, 'add', 'org/product')
    assert result['state'] == 'present' and result['checkout_state'] == 'ready'
    assert (path / 'setup-ran').exists()


def test_retry_after_cloud_checkout_ready_patch_failure(trees):
    workspace, base, remote, row, client = trees
    link = {**client.post.return_value}
    client.post.return_value = link
    original_patch = client.patch.side_effect
    failed = [False]

    def fail_checkout_ready(route, fields):
        if fields.get('checkout_state') == 'checkout_ready' and not failed[0]:
            failed[0] = True
            raise OSError('synthetic API response loss')
        return original_patch(route, fields)

    client.patch.side_effect = fail_checkout_ready
    with pytest.raises(OSError, match='synthetic API'):
        W.command(client, 'add', 'org/product')
    path = workspace / link['path']
    assert (path / 'file').read_text() == 'initial'
    assert link['checkout_state'] == 'initializing'

    client.patch.side_effect = original_patch
    result = W.command(client, 'add', 'org/product')
    assert result['state'] == 'present' and link['checkout_state'] == 'ready'
    assert (path / 'setup-ran').exists()


def test_checkout_ready_patch_failure_then_user_deletion_is_never_restored(trees):
    workspace, base, remote, row, client = trees
    link = {**client.post.return_value}
    client.post.return_value = link
    original_patch = client.patch.side_effect
    failed = [False]

    def fail_checkout_ready(route, fields):
        if fields.get('checkout_state') == 'checkout_ready' and not failed[0]:
            failed[0] = True
            raise OSError('synthetic API response loss after checkout')
        return original_patch(route, fields)

    client.patch.side_effect = fail_checkout_ready
    with pytest.raises(OSError, match='after checkout'):
        W.command(client, 'add', 'org/product')
    path = workspace / link['path']
    marker = W._read_marker(workspace, link['link_id'])
    assert marker['phase'] == 'checkout_complete'
    assert link['checkout_state'] == 'initializing'

    (path / 'file').unlink()
    client.patch.side_effect = original_patch
    with pytest.raises(ValueError, match='changed or are incomplete; kept worktree without restoring files'):
        W.command(client, 'add', 'org/product')
    assert not (path / 'file').exists()
    assert link['checkout_state'] == 'initializing' and link['state'] == 'pending'


def test_explicit_new_add_can_reuse_removed_link_without_old_marker_confusion(trees):
    workspace, base, remote, row, client = trees
    result = W.command(client, 'add', 'org/product')
    path = Path(result['workspace_path'])
    head = git(path, 'rev-parse', 'HEAD')
    git(base, 'worktree', 'remove', '--force', str(path))
    client.post.return_value.update(checkout_state='queued', setup_pending=True)
    for key in ('expected_head', 'checkout_target', 'expected_base'):
        client.post.return_value.pop(key, None)

    result = W.command(client, 'add', 'org/product')
    assert result['state'] == 'present' and result['checkout_state'] == 'ready'
    assert git(path, 'rev-parse', 'HEAD') == head and (path / 'setup-ran').exists()


def test_initializing_link_rejects_same_repo_branch_and_head_from_wrong_base(trees):
    workspace, base, remote, row, client = trees
    other_base = workspace / 'repos' / 'second_clone'
    subprocess.run(['git', 'clone', str(remote), str(other_base)], check=True, capture_output=True)
    git(other_base, 'config', 'remote.origin.url', 'https://github.com/org/product.git')
    git(other_base, 'config', f'url.{remote}.insteadOf', 'https://github.com/org/product.git')
    path = workspace / row['path']
    path.parent.mkdir(parents=True)
    git(other_base, 'worktree', 'add', '--no-track', '-b', row['branch'], str(path), 'origin/main')
    head = git(path, 'rev-parse', 'HEAD')
    assert head == git(base, 'rev-parse', 'origin/main')
    expected_base = W._base_identity(W._common_dir(base, W.safe_git.environment()))
    link = {'link_id': row['id'], 'branch': row['branch'], 'path': row['path'],
            'checkout_state': 'initializing', 'setup_pending': True, 'expected_head': head,
            'checkout_target': 'refs/remotes/origin/main', 'expected_base': expected_base}
    client.post.return_value = link
    before = (path / 'file').read_text()
    with pytest.raises(ValueError, match='different managed base clone'):
        W.command(client, 'add', 'org/product')
    assert git(path, 'symbolic-ref', '--short', 'HEAD') == row['branch']
    assert git(path, 'rev-parse', 'HEAD') == head and (path / 'file').read_text() == before


def test_initialization_marker_rejects_a_mismatched_registered_path(trees):
    workspace, base, remote, row, client = trees
    result = W.command(client, 'add', 'org/product')
    original_path = Path(result['workspace_path'])
    original_content = (original_path / 'file').read_text()
    link = client.post.return_value
    link.update(state='pending', checkout_state='initializing', setup_pending=True,
                path='tasks/12345678/recovery-other')
    with pytest.raises(ValueError, match='marker does not match this task registration'):
        W.command(client, 'add', 'org/product')
    assert (original_path / 'file').read_text() == original_content
    assert not (workspace / link['path']).exists()


@pytest.mark.parametrize('setup_command', [None, 'true'])
def test_setup_and_heartbeat_keep_legacy_partial_checkout_unverified(trees, setup_command):
    workspace, base, remote, row, client = trees
    row['kind'] = 'worktree'
    path = workspace / row['path']
    path.parent.mkdir(parents=True)
    git(base, 'worktree', 'add', '--no-track', '-b', row['branch'], str(path), 'origin/main')
    (path / 'file').unlink()
    row.update(state='present', detail_json='{}', setup_command=setup_command)
    report = W.inspect(workspace, row)
    assert report['state'] == 'pending' and report['checkout_state'] == 'unverified'
    client.get.side_effect = lambda route: {'links': [row]} if route.endswith('/links') else {
        'repositories': [{**row, 'access': 'write'}]}
    with mock.patch.object(W, 'setup', return_value=None) as setup:
        with pytest.raises(ValueError, match='Legacy or unknown worktree initialization is unverified'):
            W.command(client, 'setup', 'org/product')
        setup.assert_not_called()
    assert not client.patch.called
    assert not (path / 'file').exists()
    assert W._porcelain_records(path, W.safe_git.environment())


def test_legacy_present_checkout_remains_usable_without_new_marker(trees):
    workspace, base, remote, row, client = trees
    row['kind'] = 'worktree'
    path = workspace / row['path']
    path.parent.mkdir(parents=True)
    git(base, 'worktree', 'add', '--no-track', '-b', row['branch'], str(path), 'origin/main')
    (path / 'file').write_text('legacy user edit')
    (path / 'notes.txt').write_text('legacy untracked note')
    row.update(state='present', detail_json='{}')

    report = W.inspect(workspace, row)
    assert report['state'] == 'present' and report['checkout_state'] == 'legacy_present'
    assert report['dirty_files'] == 2
    marker = W._marker_path(workspace, row['id'])
    assert marker is None or not marker.exists()

    link = client.post.return_value
    link.update(state='present', checkout_state='legacy_present', setup_pending=False)
    result = W.command(client, 'add', 'org/product')
    assert result['state'] == 'present' and result['checkout_state'] == 'legacy_present'
    assert (path / 'file').read_text() == 'legacy user edit'
    assert (path / 'notes.txt').read_text() == 'legacy untracked note'
    marker = W._marker_path(workspace, row['id'])
    assert marker is None or not marker.exists()
    client.patch.assert_not_called()


def test_short_task_option_is_resolved_before_worktree_registration(trees, monkeypatch):
    from clients import hubcli, remotecli

    workspace, base, remote, row, client = trees
    canonical = row['task_id']

    def get(route):
        if route == 'tasks/' + canonical[:8]:
            return {'task': {'id': canonical}}
        if route == 'runners/me/repositories':
            return {'repositories': [{**row, 'access': 'write'}]}
        raise AssertionError('unexpected API route: ' + route)

    client.get.side_effect = get
    monkeypatch.setenv('HUB_API_URL', 'https://tico.example.invalid')
    monkeypatch.setattr(remotecli, 'Client', lambda *args, **kwargs: client)
    args = hubcli.parser().parse_args(['task', 'worktree', 'add', 'org/product', '--task', canonical[:8]])
    remotecli.run(args)

    assert client.post.call_args.args[0] == f'tasks/{canonical}/worktrees'
    assert any(call.args[0] == f'tasks/{canonical}/links/link1' for call in client.patch.call_args_list)
    marker = W._read_marker(workspace, row['id'])
    assert marker['task_id'] == canonical


def test_ready_registration_without_local_completion_marker_is_not_reported_present(trees):
    workspace, base, remote, row, client = trees
    result = W.command(client, 'add', 'org/product')
    path = Path(result['workspace_path'])
    marker_path = W._marker_path(workspace, row['id'])
    assert W._read_marker(workspace, row['id'])['phase'] == 'ready'
    marker_path.unlink()

    report = W.inspect(workspace, row)
    assert report['state'] == 'unknown'
    assert report['error'] == 'Could not inspect worktree; check disk space, Git state and workspace permissions'
    with pytest.raises(ValueError, match='no local initialization proof'):
        W.command(client, 'add', 'org/product')
    assert (path / 'file').read_text() == 'initial'


def test_interrupted_add_keeps_user_edits_and_untracked_files(trees, monkeypatch):
    workspace, base, remote, row, client = trees
    link = {**client.post.return_value}
    client.post.return_value = link
    client.patch.side_effect = lambda route, body: (link.update(body), dict(link))[1]
    real_git = W.git
    interrupted = [False]

    def interrupted_add(path, *args, **kwargs):
        result = real_git(path, *args, **kwargs)
        if args[:2] == ('worktree', 'add') and not interrupted[0]:
            interrupted[0] = True
            task_path = workspace / link['path']
            (task_path / 'file').write_text('user edit to retain')
            (task_path / 'notes.txt').write_text('untracked user file')
            raise ValueError('synthetic interruption after worktree creation')
        return result

    monkeypatch.setattr(W, 'git', interrupted_add)
    with pytest.raises(ValueError, match='synthetic interruption'):
        W.command(client, 'add', 'org/product')
    monkeypatch.setattr(W, 'git', real_git)
    path = workspace / link['path']
    with pytest.raises(ValueError, match='changed or are incomplete; kept worktree without restoring files'):
        W.command(client, 'add', 'org/product')
    assert (path / 'file').read_text() == 'user edit to retain'
    assert (path / 'notes.txt').read_text() == 'untracked user file'
    assert link['checkout_state'] == 'initializing' and link['state'] == 'pending'


def test_add_does_not_claim_or_overwrite_an_unrecorded_path(trees):
    workspace, base, remote, row, client = trees
    path = workspace / row['path']
    path.mkdir(parents=True)
    (path / 'keep.txt').write_text('existing user data')
    with pytest.raises(ValueError, match='without an initialization record'):
        W.command(client, 'add', 'org/product')
    assert (path / 'keep.txt').read_text() == 'existing user data'


def test_interrupted_add_refuses_a_worktree_on_a_different_branch(trees):
    workspace, base, remote, row, client = trees
    path = workspace / row['path']
    path.parent.mkdir(parents=True)
    git(base, 'worktree', 'add', '-b', 'unrelated', str(path), 'origin/main')
    head = git(path, 'rev-parse', 'HEAD')
    link = {'link_id': row['id'], 'branch': row['branch'], 'path': row['path'], 'checkout_state': 'initializing',
            'setup_pending': True, 'expected_head': head, 'checkout_target': 'refs/remotes/origin/main',
            'expected_base': W._base_identity(W._common_dir(base, W.safe_git.environment()))}
    client.post.return_value = link
    before = (path / 'file').read_text()
    with pytest.raises(ValueError, match='branch does not match'):
        W.command(client, 'add', 'org/product')
    assert git(path, 'symbolic-ref', '--short', 'HEAD') == 'unrelated'
    assert (path / 'file').read_text() == before


def test_failed_push_keeps_tree_and_retry_preserves_saved_branch(trees):
    workspace, base, remote, row, client = trees
    path = Path(W.command(client, 'add', 'org/product')['workspace_path'])
    (path / 'file').write_text('keep this')
    hook = remote / 'hooks' / 'pre-receive'
    hook.write_text('#!/bin/sh\nexit 1\n')
    hook.chmod(0o755)
    with pytest.raises(ValueError, match='push failed'):
        W.act(workspace, row, 'remove', os.environ.copy())
    assert path.exists() and (path / 'file').read_text() == 'keep this'
    hook.unlink()
    W.act(workspace, row, 'remove', os.environ.copy())
    W.act(workspace, row, 'restore', os.environ.copy())
    assert (path / 'file').read_text() == 'keep this'


def test_restore_remote_branch_then_default(trees):
    workspace, base, remote, row, client = trees
    path = Path(W.command(client, 'add', 'org/product')['workspace_path'])
    git(path, 'push', 'origin', row['branch'])
    git(base, 'worktree', 'remove', '--force', str(path))
    git(base, 'branch', '-D', row['branch'])
    W.act(workspace, row, 'restore', os.environ.copy())
    assert git(path, 'symbolic-ref', '--short', 'HEAD') == row['branch']
    git(base, 'worktree', 'remove', '--force', str(path))
    git(base, 'branch', '-D', row['branch'])
    git(base, 'push', 'origin', '--delete', row['branch'])
    W.act(workspace, row, 'restore', os.environ.copy())
    assert git(path, 'rev-parse', 'HEAD') == git(base, 'rev-parse', 'origin/main')


def test_disk_floor_paths_old_server_and_setup_failure(trees, monkeypatch):
    workspace, base, remote, row, client = trees
    monkeypatch.setattr(W.shutil, 'disk_usage', lambda p: shutil._ntuple_diskusage(100 * GB, 99 * GB, GB))
    with pytest.raises(ValueError, match='Not enough disk'):
        W.command(client, 'add', 'org/product')
    assert not client.post.called
    monkeypatch.setattr(W.shutil, 'disk_usage', lambda p: shutil._ntuple_diskusage(100 * GB, 0, 100 * GB))
    client.post.side_effect = APIError('not_found', 'Missing', 404)
    with pytest.raises(ValueError, match='server is too old'):
        W.command(client, 'add', 'org/product')
    client.post.side_effect = None
    client.get.return_value['repositories'][0]['setup_command'] = 'exit 7'
    with pytest.raises(ValueError, match='setup failed'):
        W.command(client, 'add', 'org/product')
    assert client.patch.called
    assert client.post.return_value['state'] == 'pending'
    assert client.post.return_value['checkout_state'] == 'setup_failed'
    assert client.post.return_value['setup_pending'] is True
    outside = workspace.parent / 'outside'
    outside.mkdir()
    (workspace / 'escape').symlink_to(outside, target_is_directory=True)
    for path in ('../outside', 'escape/child', 'repos/org__product', str(outside)):
        with pytest.raises(ValueError):
            W.safe_path(workspace, path)


def test_mixed_version_heartbeat_drops_worktree_fields():
    runner = Runner.__new__(Runner)
    runner.client = mock.Mock()
    runner.client.post.side_effect = [APIError('validation', 'worktrees: Extra inputs', 422),
                                    APIError('validation', 'readiness.worktrees: Extra inputs', 422), {'ok': True}]
    body = {'worktrees': [], 'readiness': {'schema_version': 1, 'worktrees': True}}
    assert runner.report_heartbeat(body) == {'ok': True}
    assert 'worktrees' not in body and 'worktrees' not in body['readiness']


def test_unselected_base_with_linked_worktree_is_not_deleted(trees):
    from runner.repositories import Repositories, REMOVE_AFTER
    workspace, base, remote, row, client = trees
    W.command(client, 'add', 'org/product')
    manager = Repositories(workspace, workspace / 'state.json', client)
    manager.rows = {'org/product': {'full_name': 'org/product', 'managed': True, 'left_at': 0}}
    try:
        manager.sync({}, None, REMOVE_AFTER + 1)
        assert base.exists()
        git(base, 'worktree', 'remove', '--force', str(workspace / row['path']))
        git(base, 'branch', '-D', row['branch'])
        manager.sync({}, None, REMOVE_AFTER + 1)
        assert not base.exists()
    finally:
        manager.close()


def test_computer_heartbeat_actions_use_scoped_token_and_wait_for_idle(trees):
    workspace, base, remote, row, client = trees
    W.command(client, 'add', 'org/product')
    saved = {**row, 'task_status': 'closed', 'bot_state': 'active'}
    repo = {**row, 'access': 'write'}
    client.get.side_effect = lambda route: {'worktrees': [saved]} if route.endswith('/worktrees') else {'repositories': [repo]}
    client.post.return_value = {'token': 'synthetic-worktree-token'}
    client.patch.side_effect = lambda route, body: saved.update(body)
    idle = [False]
    manager = W.Worktrees(workspace, client, idle=lambda: idle[0])
    action = {**row, 'action': 'remove'}
    try:
        manager.sync([action])
        assert (workspace / row['path']).exists()
        idle[0] = True
        manager.sync([action])
        assert not (workspace / row['path']).exists()
        assert manager.reports[0]['state'] == 'removed'
        assert client.post.call_args.args[0] == 'runners/me/worktrees/link1/token'
        assert 'synthetic-worktree-token' not in str(manager.reports)
        saved['task_status'] = 'open'
        manager.sync([{**action, 'action': 'restore'}])
        assert manager.reports[0]['state'] == 'pending'
        assert any(call.args[1].get('checkout_state') == 'checkout_ready' for call in client.patch.call_args_list)
        saved['task_status'] = 'closed'
        client.post.side_effect = APIError('forbidden', 'This bot needs write access', 403)
        manager.sync([action])
        manager.sync([])
        assert 'write access' in manager.reports[0]['error']
        assert (workspace / row['path']).exists()
    finally:
        manager.close()


def test_actions_arriving_during_poll_are_queued(trees):
    import threading
    workspace, base, remote, row, client = trees
    manager = W.Worktrees(workspace, client)
    entered, finish = threading.Event(), threading.Event()
    batches = []
    def sync(actions):
        batches.append(actions)
        entered.set()
        assert finish.wait(2)
    manager.sync = sync
    try:
        manager.poll()
        assert entered.wait(2)
        action = {**row, 'action': 'remove'}
        manager.poll([action])
        finish.set()
        manager.pending.result(timeout=2)
        manager.poll()
        manager.pending.result(timeout=2)
        assert batches == [[], [action]]
    finally:
        finish.set()
        manager.close()


def test_git_hooks_fsmonitor_and_setup_never_receive_vault_environment(trees, monkeypatch):
    workspace, base, remote, row, client = trees
    monkeypatch.setenv('VAULT_TEST_SECRET', 'synthetic-private-value')
    monkeypatch.setenv('HUB_INGEST_TOKEN', 'synthetic-runner-secret')
    marker = workspace / 'hook-ran'
    for name in ('post-checkout', 'pre-commit', 'pre-push'):
        hook = base / '.git' / 'hooks' / name
        hook.write_text('#!/bin/sh\ntouch "' + str(marker) + '"\nexit 1\n')
        hook.chmod(0o755)
    git(base, 'config', 'core.fsmonitor', 'touch ' + str(marker))
    client.get.return_value['repositories'][0]['setup_command'] = 'test -z "$VAULT_TEST_SECRET$HUB_INGEST_TOKEN" && touch setup-ran'
    path = Path(W.command(client, 'add', 'org/product')['workspace_path'])
    assert W.inspect(workspace, row)['state'] == 'present'
    (path / 'file').write_text('saved')
    W.act(workspace, row, 'remove', os.environ.copy())
    with mock.patch.object(W, 'setup') as setup:
        W.act(workspace, row, 'restore', os.environ.copy())
        setup.assert_not_called()
    assert not marker.exists()


def test_long_branch_does_not_break_heartbeat_and_4xx_retries_without_worktrees(trees):
    workspace, base, remote, row, client = trees
    path = Path(W.command(client, 'add', 'org/product')['workspace_path'])
    git(path, 'switch', '-c', 'feature/' + 'x' * 220)
    report = W.inspect(workspace, row)
    from backend.models import WorktreeStatus
    WorktreeStatus(**report)
    assert report['state'] == 'present' and report['branch'] is None and 'oversized' in report['error']
    for status in (400, 409, 422):
        runner = Runner.__new__(Runner)
        runner.client = mock.Mock()
        runner.client.post.side_effect = [APIError('validation', 'worktrees.0.branch too long', status), {'ok': True}]
        body = {'worktrees': [report], 'readiness': {'bots': {}}}
        assert runner.report_heartbeat(body) == {'ok': True}
        assert 'worktrees' not in body


def test_cleanup_never_replaces_divergent_task_history(trees):
    workspace, base, remote, row, client = trees
    path = Path(W.command(client, 'add', 'org/product')['workspace_path'])
    (path / 'file').write_text('task commit')
    git(path, 'add', 'file')
    git(path, '-c', 'user.name=Tico', '-c', 'user.email=bot@example.com', 'commit', '-m', 'Task history')
    task_head = git(path, 'rev-parse', 'HEAD')
    git(path, 'switch', '-c', 'experiment', 'origin/main')
    (path / 'file').write_text('experiment')
    with pytest.raises(ValueError, match='separate history'):
        W.act(workspace, row, 'remove', os.environ.copy())
    assert git(base, 'rev-parse', row['branch']) == task_head
    assert path.exists() and (path / 'file').read_text() == 'experiment'


def test_fetch_failure_is_not_missing_branch(trees, monkeypatch):
    workspace, base, remote, row, client = trees
    path = Path(W.command(client, 'add', 'org/product')['workspace_path'])
    git(path, 'push', 'origin', row['branch'])
    head = git(path, 'rev-parse', 'HEAD')
    git(base, 'worktree', 'remove', '--force', str(path))
    real_git = W.git
    def failed_fetch(path, *args, **kwargs):
        if args[0] == 'fetch' and any(row['branch'] in a for a in args):
            raise ValueError('Git fetch failed; synthetic network failure')
        return real_git(path, *args, **kwargs)
    monkeypatch.setattr(W, 'git', failed_fetch)
    W.act(workspace, row, 'restore', os.environ.copy())
    assert path.exists() and git(path, 'rev-parse', 'HEAD') == head
    assert row['restore_source'] == 'local (remote unavailable)'
    git(base, 'worktree', 'remove', str(path))
    git(base, 'branch', '-D', row['branch'])
    with pytest.raises(ValueError, match='fetch failed'):
        W.act(workspace, row, 'restore', os.environ.copy())
    assert not path.exists()


def test_missing_folder_registration_is_pruned_for_restore_and_base_cleanup(trees):
    workspace, base, remote, row, client = trees
    path = Path(W.command(client, 'add', 'org/product')['workspace_path'])
    shutil.rmtree(path)
    assert (base / '.git' / 'worktrees').exists()
    W.act(workspace, row, 'restore', os.environ.copy())
    assert path.exists()
    shutil.rmtree(path)
    git(base, 'worktree', 'prune', '--expire', 'now')
    git(base, 'branch', '-D', row['branch'])
    from runner.repositories import Repositories, REMOVE_AFTER
    manager = Repositories(workspace, workspace / 'state.json', client)
    manager.rows = {'org/product': {'full_name': 'org/product', 'managed': True, 'left_at': 0}}
    try:
        manager.sync({}, None, REMOVE_AFTER + 1)
        assert not base.exists()
    finally:
        manager.close()


def test_new_base_clone_detects_default_and_restores_remote_snapshot(trees, monkeypatch):
    workspace, base, remote, row, client = trees
    path = Path(W.command(client, 'add', 'org/product')['workspace_path'])
    (path / 'file').write_text('saved snapshot')
    W.act(workspace, row, 'remove', os.environ.copy())
    shutil.rmtree(base)
    real_run = W.isolation.run
    cloned = []
    def local_clone(args, **kwargs):
        if 'clone' in args:
            cloned.append(args)
            result = real_run([*(['git', '-c', 'protocol.file.allow=always'] if args[0] == 'git' else [args[0]]), *[str(remote) if a == 'https://github.com/org/product.git' else a for a in args[1:]]], **kwargs)
            if result.returncode == 0:
                git(base, 'config', 'remote.origin.url', 'https://github.com/org/product.git')
                git(base, 'config', f'url.{remote}.insteadOf', 'https://github.com/org/product.git')
            return result
        return real_run(args, **kwargs)
    monkeypatch.setattr(W.isolation, 'run', local_clone)
    with mock.patch.object(W, 'setup') as setup:
        W.act(workspace, {**row, 'default_branch': None}, 'restore', os.environ.copy())
        setup.assert_not_called()
    assert len(cloned) == 1 and '--single-branch' in cloned[0]
    assert (base / '.git' / 'tico-managed').exists()
    assert (path / 'file').read_text() == 'saved snapshot'
    assert git(path, 'symbolic-ref', '--short', 'HEAD') == row['branch']


def test_unpushed_means_task_branch_and_clean_finished_cleanup_does_not_push(trees):
    workspace, base, remote, row, client = trees
    client.get.return_value['repositories'][0]['setup_command'] = None
    path = Path(W.command(client, 'add', 'org/product')['workspace_path'])
    (path / 'file').write_text('task commit')
    git(path, 'add', 'file')
    git(path, '-c', 'user.name=Tico', '-c', 'user.email=bot@example.com', 'commit', '-m', 'Work')
    assert W.inspect(workspace, row)['ahead'] == 1
    git(path, 'push', 'origin', row['branch'])
    assert W.inspect(workspace, row)['ahead'] == 0
    git(remote, 'update-ref', '-d', 'refs/heads/' + row['branch'])
    with mock.patch.object(W, 'git', wraps=W.git) as calls:
        W.act(workspace, {**row, 'prs_finished': True}, 'remove', os.environ.copy())
    assert not any(call.args[1] == 'push' for call in calls.call_args_list)
    assert git(remote, 'show-ref', '--heads') == git(remote, 'show-ref', '--heads', 'main')


@pytest.mark.parametrize('name', ['.env.local', 'private.pem', 'credentials.json', 'large.bin', 'valuable.db'])
def test_cleanup_keeps_unsafe_large_and_ignored_files(trees, name):
    workspace, base, remote, row, client = trees
    path = Path(W.command(client, 'add', 'org/product')['workspace_path'])
    file = path / name
    file.write_text('keep')
    if name == 'large.bin':
        with file.open('ab') as handle:
            handle.truncate(21 * 1024 ** 2)
    if name == 'valuable.db':
        (path / '.gitignore').write_text('valuable.db\n')
    with pytest.raises(ValueError, match='kept:'):
        W.act(workspace, row, 'remove', os.environ.copy())
    assert file.exists()
    assert git(remote, 'show-ref', '--heads') == git(remote, 'show-ref', '--heads', 'main')


def test_backoff_does_not_mint_more_tokens_and_alias_paths_are_refused(trees, monkeypatch):
    workspace, base, remote, row, client = trees
    path = Path(W.command(client, 'add', 'org/product')['workspace_path'])
    (workspace / 'alias').symlink_to(path.parent, target_is_directory=True)
    with pytest.raises(ValueError, match='symlinks'):
        W.command(client, 'attach', 'alias/org__product')
    saved = {**row, 'task_status': 'closed', 'bot_state': 'active'}
    client.get.side_effect = lambda route: {'worktrees': [saved]} if route.endswith('/worktrees') else {'repositories': [{**row, 'access': 'write'}]}
    client.post.reset_mock()
    client.post.side_effect = APIError('forbidden', 'Synthetic refusal', 403)
    clock = mock.Mock()
    clock.monotonic.return_value = 1000
    monkeypatch.setattr(W, 'time', clock)
    manager = W.Worktrees(workspace, client)
    try:
        action = {**row, 'action': 'remove'}
        manager.sync([action])
        until, failures = manager.retry[row['id']]
        assert (until, failures) == (1300, 1)
        manager.sync([action])
        assert client.post.call_count == 1
        assert path.exists()
    finally:
        manager.close()


def test_readiness_advertises_worktrees_and_setup_cli_runs_in_turn(trees):
    from clients.hubcli import parser
    workspace, base, remote, row, client = trees
    runner = Runner.__new__(Runner)
    runner.config = {}
    runner.tools = None
    runner._harness_after = 0
    runner.runtime_report = lambda assignments: {'fake': {}}
    runner.preflight = lambda assignments, runtimes: [{'bot': 'engineer', 'ready': True}]
    assert runner.readiness([])['worktrees'] is True
    prompt = runner.prompt({'bot': 'engineer', 'task': {'id': row['task_id'], 'requester': 'human:ana'},
                            'message': {'id': 'm1', 'from_actor': 'human:ana', 'body': 'Work', 'refs': {}},
                            'conversation': {'id': 'room', 'scope': 'personal', 'kind': 'chat', 'owner_actor': 'human:ana'}})
    assert 'setup_pending=true' in prompt and 'hub task worktree setup <repo>' in prompt
    args = parser().parse_args(['task', 'worktree', 'setup', 'org/product'])
    assert args.fn == 'task worktree setup'
    W.command(client, 'add', 'org/product')
    repo = client.get.return_value['repositories'][0]
    client.get.side_effect = lambda route: {'links': [{**row, 'kind': 'worktree'}]} if route.endswith('/links') else {'repositories': [repo]}
    W.command(client, 'setup', 'org/product')
    fields = client.patch.call_args.args[1]
    assert fields['state'] == 'present' and fields['setup_pending'] is False and fields['checkout_state'] == 'ready'
    assert fields['expected_head'] and fields['checkout_target'] and len(fields['expected_base']) == 64


def test_unlisted_managed_base_is_adopted_and_clone_failure_cleans_partial_folder(trees, monkeypatch):
    from runner.repositories import Repositories
    workspace, base, remote, row, client = trees
    (base / '.git' / 'tico-managed').touch()
    client.get.return_value = {'repositories': []}
    manager = Repositories(workspace, workspace / 'state.json', client)
    try:
        manager.poll()
        manager.pending.result(timeout=2)
        assert manager.rows['org/product']['managed'] is True
        assert manager.rows['org/product']['left_at'] > 0
        from backend.models import RepositoryStatus
        RepositoryStatus(**manager.report()[0])
    finally:
        manager.close()
    shutil.rmtree(base)
    real_run = W.isolation.run
    def failed_clone(args, **kwargs):
        if 'clone' in args:
            Path(args[-1]).mkdir(parents=True)
            return subprocess.CompletedProcess(args, 128, '', 'synthetic refusal')
        return real_run(args, **kwargs)
    monkeypatch.setattr(W.isolation, 'run', failed_clone)
    with pytest.raises(ValueError, match='clone failed'):
        W.repositories.worktree_base(workspace, row, W.safe_git.process_environment())
    assert not base.exists()


def test_setup_timeout_kills_process_group(trees):
    workspace, base, remote, row, client = trees
    process = mock.MagicMock()
    process.__enter__.return_value = process
    process.pid = 12345
    process.wait.side_effect = [subprocess.TimeoutExpired('setup', 600), 0]
    with mock.patch.object(W.isolation, 'popen', return_value=process) as popen, mock.patch.object(W.os, 'killpg') as kill:
        with pytest.raises(ValueError, match='timed out'):
            W.setup(workspace, 'sleep 999', os.environ.copy())
        assert popen.call_args.kwargs['start_new_session'] is True
        kill.assert_called_once_with(12345, W.signal.SIGKILL)
        assert process.wait.call_count == 2


def test_add_using_task_prefix_waits_for_the_same_link_restore_lock(trees):
    import concurrent.futures
    import threading
    workspace, base, remote, row, client = trees
    W.command(client, 'add', 'org/product')
    posted = threading.Event()
    response = client.post.return_value
    original_get = client.get.return_value
    client.get.side_effect = lambda route: ({'task': {'id': row['task_id']}}
                                            if route == 'tasks/' + row['task_id'][:8]
                                            else original_get)
    def posted_link(*args, **kwargs):
        posted.set()
        return response
    client.post.side_effect = posted_link
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        with W.locked(workspace, row['path']):
            future = pool.submit(W.command, client, 'add', 'org/product', row['task_id'][:8])
            assert posted.wait(2)
            assert not future.done()
        assert future.result(timeout=5)['link_id'] == row['id']


@pytest.mark.parametrize('secret', ['github_pat_' + 'x' * 30, 'unusual-database-password'])
def test_snapshot_refusal_preserves_head_index_and_files(trees, secret):
    workspace, base, remote, row, client = trees
    path = Path(W.command(client, 'add', 'org/product')['workspace_path'])
    (path / 'file').write_text('staged change')
    git(path, 'add', 'file')
    (path / 'file').write_text('unstaged change')
    (path / 'notes').write_text(secret)
    before = (git(path, 'symbolic-ref', 'HEAD'), git(path, 'diff', '--cached'), git(path, 'diff'), git(path, 'status', '--porcelain'))
    with pytest.raises(ValueError, match='Possible secret'):
        W.act(workspace, row, 'remove', os.environ.copy(), [secret])
    after = (git(path, 'symbolic-ref', 'HEAD'), git(path, 'diff', '--cached'), git(path, 'diff'), git(path, 'status', '--porcelain'))
    assert after == before
    assert (path / 'notes').read_text() == secret
    assert git(base, 'show-ref', '--verify', 'refs/heads/' + row['branch'])


@pytest.mark.parametrize('cache', ['.pytest_cache', '.mypy_cache', '.ruff_cache', '.tox', '.gradle', '.terraform/providers'])
def test_cleanup_removes_ignored_build_caches(trees, cache):
    workspace, base, remote, row, client = trees
    path = Path(W.command(client, 'add', 'org/product')['workspace_path'])
    (path / '.gitignore').write_text(cache.split('/')[0] + '/\n')
    (path / cache).mkdir(parents=True)
    (path / cache / 'cache').write_text('cached output')
    assert W.act(workspace, row, 'remove', os.environ.copy()) == 'removed'
    assert not path.exists()


def test_setup_has_no_tokens_or_socket_but_keeps_toolchain(trees, monkeypatch):
    workspace, base, remote, row, client = trees
    monkeypatch.setenv('HUB_TOKEN', 'synthetic-attempt-token')
    monkeypatch.setenv('GH_TOKEN', 'synthetic-repository-token')
    monkeypatch.setenv('TICO_GITHUB_EXTRA', 'synthetic-helper-key')
    monkeypatch.setenv(W.git_credentials.credential_socket.SOCKET_ENV, 'synthetic-socket')
    monkeypatch.setenv('JAVA_HOME', '/example/toolchain')
    command = 'env > setup-env'
    W.setup(workspace, command, os.environ.copy())
    environment = dict(line.split('=', 1) for line in (workspace / 'setup-env').read_text().splitlines())
    assert environment['JAVA_HOME'] == '/example/toolchain'
    assert 'PATH' in environment and 'HOME' in environment
    assert not any('TOKEN' in key or key.startswith(('HUB_', 'TICO_GITHUB_')) or 'synthetic-' in value for key, value in environment.items())


@pytest.mark.parametrize('dirty', [False, True])
def test_detached_cleanup_and_finished_pr_save_unpushed_history(trees, dirty):
    workspace, base, remote, row, client = trees
    client.get.return_value['repositories'][0]['setup_command'] = None
    path = Path(W.command(client, 'add', 'org/product')['workspace_path'])
    git(path, 'checkout', '--detach')
    (path / 'file').write_text('detached work')
    if not dirty:
        git(path, 'add', '.')
        git(path, '-c', 'user.name=Tico', '-c', 'user.email=bot@example.com', 'commit', '-m', 'Offline work')
    W.act(workspace, {**row, 'prs_finished': True}, 'remove', os.environ.copy())
    assert not path.exists()
    assert git(remote, 'show', W.wip_branch(row) + ':file') == 'detached work'


def test_reopen_just_before_remove_defers_and_per_bot_lock_blocks_turn(trees):
    import concurrent.futures
    workspace, base, remote, row, client = trees
    path = Path(W.command(client, 'add', 'org/product')['workspace_path'])
    (path / 'file').write_text('saved before deferral')
    checks = iter((True, False))
    with pytest.raises(W.Deferred):
        W.act(workspace, row, 'remove', os.environ.copy(), before_remove=lambda: next(checks))
    assert git(path, 'symbolic-ref', '--short', 'HEAD') == row['branch']
    assert git(remote, 'show', W.wip_branch(row) + ':file') == 'saved before deferral'
    with pytest.raises(W.Deferred):
        W.act(workspace, row, 'remove', os.environ.copy(), before_remove=lambda: False)
    assert path.exists()
    saved = {**row, 'task_status': 'closed', 'bot_state': 'active'}
    client.get.side_effect = lambda route: {'worktrees': [saved]} if route.endswith('/worktrees') else {'repositories': [{**row, 'access': 'write'}]}
    client.post.return_value = {'token': 'synthetic-scoped-token'}
    client.patch.side_effect = lambda route, body: saved.update(body)
    manager = W.Worktrees(workspace, client, idle=lambda bot: bot != 'bot:busy')
    try:
        # A different bot is busy: this owner still cleans up.
        manager.sync([{**row, 'action': 'remove'}])
        assert not path.exists()
        saved['task_status'] = 'open'
        saved['state'] = 'removed'
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            with manager.bot_lock(row['owner']):
                pool.submit(manager.sync, [{**row, 'action': 'restore'}]).result(timeout=3)
                assert not path.exists()
        manager.sync([{**row, 'action': 'restore'}])
        assert path.exists()
        saved['task_status'] = 'closed'
        reads = [0]
        def reopening(route):
            if route.endswith('/worktrees'):
                reads[0] += 1
                if reads[0] > 1:
                    saved['task_status'] = 'open'
                return {'worktrees': [saved]}
            return {'repositories': [{**row, 'access': 'write'}]}
        client.get.side_effect = reopening
        manager.sync([{**row, 'action': 'remove'}])
        assert path.exists() and row['id'] not in manager.retry
    finally:
        manager.close()


def test_path_only_missing_cleanup_needs_no_token(trees):
    workspace, base, remote, row, client = trees
    row = {**row, 'repo': None, 'branch': None, 'task_status': 'closed', 'bot_state': 'active', 'state': 'missing'}
    client.get.return_value = {'worktrees': [row]}
    manager = W.Worktrees(workspace, client)
    try:
        manager.sync([{**row, 'action': 'remove'}])
        client.post.assert_not_called()
        assert client.patch.call_args.args[1]['state'] == 'removed'
        assert manager.reports[0]['state'] == 'removed'
    finally:
        manager.close()


def test_snapshot_skipped_is_reported_and_branch_check_ignores_cwd(trees, monkeypatch):
    workspace, base, remote, row, client = trees
    path = Path(W.command(client, 'add', 'org/product')['workspace_path'])
    (path / 'file').write_text('saved work')
    W.act(workspace, row, 'remove', os.environ.copy())
    git(base, 'update-ref', 'refs/heads/' + row['branch'], 'origin/main')
    git(base, 'worktree', 'add', str(path), row['branch'])
    (path / 'file').write_text('rebased work')
    git(path, 'add', '.')
    git(path, '-c', 'user.name=Tico', '-c', 'user.email=bot@example.com', 'commit', '-m', 'New history')
    git(base, 'worktree', 'remove', str(path))
    monkeypatch.chdir(base.parent.parent)
    with mock.patch.object(W.isolation, 'run', wraps=W.isolation.run) as run:
        W.checked_branch(row['branch'])
        assert run.call_args.kwargs['cwd'] == '/'
        assert '-C' not in run.call_args.args[0]
    W.act(workspace, row, 'restore', os.environ.copy())
    assert row['snapshot_skipped'].endswith(W.wip_branch(row))
    assert (path / 'file').read_text() == 'rebased work'


def test_unrelated_heartbeat_errors_keep_worktree_fields():
    for status, detail in [(401, 'Invalid token'), (403, 'Forbidden'), (409, 'Conflict'), (422, 'goals.0 invalid')]:
        runner = Runner.__new__(Runner)
        runner.client = mock.Mock()
        runner.client.post.side_effect = APIError('validation', detail, status)
        body = {'worktrees': [], 'readiness': {'bots': {}}}
        with pytest.raises(APIError):
            runner.report_heartbeat(body)
        assert 'worktrees' in body


def test_full_mirror_base_offline_commit_push_cleanup_recreate(trees, monkeypatch):
    from runner.repositories import Repositories, REMOVE_AFTER
    from runner import credential_socket
    workspace, base, remote, row, client = trees
    shutil.rmtree(base)
    supervisor = mock.Mock()
    supervisor.post.return_value = {'token': 'synthetic-computer-read-token', 'repositories': ['org/product']}
    supervisor.get.return_value = {'repositories': [{**row, 'access': 'write'}]}
    manager = Repositories(workspace, workspace / 'state' / 'repositories.json', supervisor)
    manager.state_file.parent.mkdir()
    manager.rows = {'org/product': {'full_name': 'org/product'}}
    launch = manager.run_git
    calls = []
    def local_git(args, **kwargs):
        calls.append((args, dict(kwargs['env']), kwargs.get('bot', False)))
        if kwargs.get('bot'):
            assert 'GH_TOKEN' not in kwargs['env'] and 'HUB_TOKEN' not in kwargs['env']
        else:
            assert str(base) not in args
        remapped = [remote.as_uri() if a == 'https://github.com/org/product.git' and 'config' not in args else 'protocol.file.allow=always' if a == 'protocol.file.allow=never' else a for a in args]
        return launch(remapped, **kwargs)
    monkeypatch.setattr(manager, 'run_git', local_git)
    try:
        manager.update('org/product', row, 1)
        assert manager.rows['org/product']['state'] == 'cloned'
        assert (base / '.git' / 'tico-managed').exists()
        assert git(base, 'symbolic-ref', 'refs/remotes/origin/HEAD') == 'refs/remotes/origin/main'
        git(base, 'config', f'url.{remote}.insteadOf', 'https://github.com/org/product.git')
        source = remote.parent / 'source'
        (source / 'file').write_text('new default branch work')
        git(source, 'add', '.')
        git(source, 'commit', '-m', 'New default branch work')
        git(source, 'push', 'origin', 'main')
        socket_dir = __import__('tempfile').TemporaryDirectory(prefix='tico-refresh-', dir='/tmp')
        socket = credential_socket.Server(Path(socket_dir.name) / 'refresh.sock', lambda bot, repo: 'synthetic-bot-write-token', refresh=manager.refresh_mirror).start()
        socket.register('synthetic-attempt', 'engineer')
        monkeypatch.setenv(credential_socket.SOCKET_ENV, socket.path)
        monkeypatch.setenv('HUB_TOKEN', 'synthetic-attempt')
        original_run = W.isolation.run
        def offline_run(args, **kwargs):
            if 'fetch' in args and 'origin' in args:
                raise AssertionError('Worktree creation must use the local mirror')
            return original_run(args, **kwargs)
        monkeypatch.setattr(W.isolation, 'run', offline_run)
        path = Path(W.command(client, 'add', 'org/product')['workspace_path'])
        assert git(path, 'rev-parse', 'HEAD') == git(remote, 'rev-parse', 'main')
        socket.stop()
        # An unavailable supervisor leaves an explicit age on the cached mirror result.
        mirror_repo = dict(row)
        W.repositories.worktree_base(workspace, mirror_repo, os.environ.copy())
        assert 'minutes old' in mirror_repo['fetch_warning']
        (path / 'file').write_text('offline commit')
        git(path, 'add', '.')
        git(path, '-c', 'user.name=Tico', '-c', 'user.email=bot@example.com', 'commit', '-m', 'Offline task work')
        head = git(path, 'rev-parse', 'HEAD')
        monkeypatch.setattr(W.isolation, 'run', original_run)
        scoped = {**W.safe_git.process_environment(), **W.git_credentials.environment('synthetic-bot-write-token')}
        with mock.patch.object(W.isolation, 'run', wraps=original_run) as pushes:
            W.git(path, 'push', 'origin', row['branch'], env=scoped)
            assert pushes.call_args.kwargs['env']['GH_TOKEN'] == 'synthetic-bot-write-token'
            assert 'synthetic-computer-read-token' not in str(pushes.call_args)
        assert git(remote, 'rev-parse', row['branch']) == head
        manager.rows['org/product']['left_at'] = 0
        manager.sync({}, None, REMOVE_AFTER + 1)
        assert manager.rows['org/product']['error'] == 'kept: 1 task worktrees'
        assert manager.mirror_path('org/product').exists(), 'retiring a bot keeps the mirror while a task worktree needs its base'
        W.act(workspace, {**row, 'prs_finished': True}, 'remove', scoped)
        assert not path.exists()
        W.act(workspace, row, 'restore', scoped)
        assert git(path, 'rev-parse', 'HEAD') == head
        assert (path / 'file').read_text() == 'offline commit'
        assert all(str(manager.mirror_path('org/product')) in args for args, env, bot in calls if env.get('GH_TOKEN') and 'fetch' in args)
        assert 'synthetic-' not in (base / '.git' / 'config').read_text()
    finally:
        if 'socket' in locals() and Path(socket.path).exists():
            socket.stop()
        if 'socket_dir' in locals():
            socket_dir.cleanup()
        manager.close()


def test_retirement_keeps_local_branches_and_broken_bases(trees):
    from runner.repositories import Repositories, REMOVE_AFTER
    workspace, base, remote, row, client = trees
    manager = Repositories(workspace, workspace / 'state.json', client)
    manager.rows = {'org/product': {'full_name': 'org/product', 'managed': True, 'left_at': 0}}
    mirror = manager.mirror_path('org/product')
    mirror.mkdir(parents=True)
    (mirror / 'retained-data').write_text('cached repository data')
    try:
        git(base, 'branch', 'offline-branch')
        git(base, 'checkout', 'offline-branch')
        (base / 'file').write_text('unpublished')
        git(base, 'add', '.')
        git(base, '-c', 'user.name=Tico', '-c', 'user.email=bot@example.com', 'commit', '-m', 'Local work')
        manager.sync({}, None, REMOVE_AFTER + 1)
        assert base.exists() and 'unpublished commits' in manager.rows['org/product']['error']
        assert (mirror / 'retained-data').read_text() == 'cached repository data'
        # A damaged clone is retained rather than replaced, even with local branches.
        (base / '.git' / 'HEAD').unlink()
        manager.sync({}, None, REMOVE_AFTER + 1)
        assert base.exists() and (base / 'file').read_text() == 'unpublished'
        assert mirror.exists()
        (base / '.git' / 'HEAD').write_text('ref: refs/heads/offline-branch\n')
        git(base, 'push', 'origin', 'HEAD:refs/heads/saved-work')
        manager.sync({}, None, REMOVE_AFTER + 1)
        assert not base.exists()  # every commit is remote, even with different branch names
        assert not mirror.exists(), 'an unneeded mirror still retires once the local work is saved'
    finally:
        manager.close()


def test_claims_skip_only_bot_under_maintenance(trees):
    workspace, base, remote, row, client = trees
    manager = W.Worktrees(workspace, client)
    try:
        manager.maintaining.add('engineer')
        client.post.return_value = {'attempt': {'bot': 'other'}}
        assert manager.claim(client, [{'bot': 'engineer'}, {'bot': 'other'}])['attempt']['bot'] == 'other'
        client.post.assert_called_once_with('jobs/claim', {'next_run': True, 'bot': 'other'})
        manager.maintaining.clear()
        manager.claim(client, [])
        assert client.post.call_args.args == ('jobs/claim', {'next_run': True})
    finally:
        manager.close()


@pytest.mark.parametrize('use_file_url', [False, True])
def test_no_app_clone_add_snapshot_cleanup_restore_and_retirement(trees, use_file_url):
    from runner.repositories import Repositories, REMOVE_AFTER, base_folder
    workspace, base, remote, row, client = trees
    shutil.rmtree(base)
    value = remote.as_uri() if use_file_url else 'org/product'
    row = {**row, 'repo': value, 'full_name': value, 'path': 'tasks/12345678/' + base_folder(value)}
    client.get.return_value = {'repositories': [], 'configured': False}
    client.post.return_value = {'link_id': row['id'], 'branch': row['branch'], 'path': row['path']}
    real_run = W.isolation.run
    def local_git(args, **kwargs):
        if not use_file_url and 'clone' in args:
            args = [remote.as_uri() if a == 'https://github.com/org/product.git' else a for a in args]
            done = real_run(args, **kwargs)
            new_base = workspace / 'repos' / base_folder(value)
            git(new_base, 'config', 'remote.origin.url', 'https://github.com/org/product.git')
            git(new_base, 'config', f'url.{remote}.insteadOf', 'https://github.com/org/product.git')
            return done
        assert not kwargs.get('env', {}).get('GH_TOKEN')
        return real_run(args, **kwargs)
    with mock.patch.object(W.isolation, 'run', side_effect=local_git):
        path = Path(W.command(client, 'add', value)['workspace_path'])
        new_base = workspace / 'repos' / base_folder(value)
        assert not git(new_base, 'remote').splitlines() == ['origin', 'tico-mirror']
        assert W.command(client, 'attach', str(path))['link_id'] == row['id']
        (path / 'file').write_text('saved with computer git')
        saved = {**row, 'task_status': 'closed', 'bot_state': 'active'}
        client.get.side_effect = lambda route: {'worktrees': [saved]} if route.endswith('/worktrees') else {'repositories': [], 'configured': False}
        client.post.return_value = {'configured': False, 'token': None}
        client.patch.side_effect = lambda route, body: saved.update(body)
        manager = W.Worktrees(workspace, client)
        try:
            manager.sync([{**row, 'action': 'remove'}])
            assert not path.exists(), manager.errors
            assert git(remote, 'show', W.wip_branch(row) + ':file') == 'saved with computer git'
            saved['task_status'] = 'open'
            manager.sync([{**row, 'action': 'restore'}])
            assert (path / 'file').read_text() == 'saved with computer git'
        finally:
            manager.close()
        W.act(workspace, {**row, 'machine_git': True}, 'remove', W.safe_git.machine_environment())
        clones = Repositories(workspace, workspace / 'repositories.json', client)
        try:
            client.get.side_effect = None
            clones.poll()
            clones.pending.result(timeout=5)
            assert value in clones.rows
            clones.sync({}, None, __import__('time').time() + REMOVE_AFTER + 1)
            assert not new_base.exists()
        finally:
            clones.close()


@pytest.mark.parametrize('name', ['pkg.egg-info/PKG-INFO', '.DS_Store', '.idea/settings.xml', '.vscode/settings.json', 'pkg/file.pyc'])
def test_ignored_build_outputs_do_not_hold_cleanup(trees, name):
    workspace, base, remote, row, client = trees
    path = Path(W.command(client, 'add', 'org/product')['workspace_path'])
    git(base, 'config', 'core.excludesFile', str(workspace / 'ignores'))
    (workspace / 'ignores').write_text(name.split('/')[0] + '\n')
    artifact = path / name
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_text('build output')
    row['prs_finished'] = True
    assert W.act(workspace, row, 'remove', os.environ.copy()) == 'removed'


def test_heartbeat_creation_refreshes_mirror_before_cloning(trees):
    workspace, base, remote, row, client = trees
    shutil.rmtree(base)
    saved = {**row, 'task_status': 'open', 'bot_state': 'active', 'state': 'pending'}
    client.get.side_effect = lambda route: {'worktrees': [saved]} if route.endswith('/worktrees') else {'repositories': [row]}
    client.post.return_value = {'token': 'synthetic-worktree-token'}
    refresh = mock.Mock(return_value={'refreshed': True, 'mirror': remote.as_uri(), 'default_branch': 'main'})
    manager = W.Worktrees(workspace, client, refresh=refresh)
    run = W.isolation.run
    def local_git(args, **kwargs):
        if args[0] == 'git':
            args = [args[0], '-c', f'url.{remote}.insteadOf=https://github.com/org/product.git', *args[1:]]
        return run(args, **kwargs)
    try:
        with mock.patch.object(W.isolation, 'run', side_effect=local_git):
            manager.sync([{**row, 'action': 'restore'}])
        assert not manager.errors, manager.errors
        refresh.assert_called_once_with('org/product', 'synthetic-worktree-token')
        assert (workspace / row['path'] / 'file').read_text() == 'initial', manager.errors
        assert git(base, 'remote', 'get-url', 'tico-mirror') == remote.as_uri()
        assert 'synthetic-worktree-token' not in (base / '.git' / 'config').read_text()
    finally:
        manager.close()


def test_no_app_failed_clone_explains_computer_login(trees):
    workspace, base, remote, row, client = trees
    shutil.rmtree(base)
    client.get.return_value = {'repositories': [], 'configured': False}
    run = W.isolation.run
    def unavailable(args, **kwargs):
        if 'clone' in args:
            return subprocess.CompletedProcess(args, 128, '', 'access refused\nmore detail')
        return run(args, **kwargs)
    with mock.patch.object(W.isolation, 'run', side_effect=unavailable):
        with pytest.raises(ValueError, match="git could not reach org/product with this computer's git login: access refused"):
            W.command(client, 'add', 'org/product')


@pytest.mark.parametrize('kind', ['modified', 'untracked', 'stash', 'detached'])
def test_retirement_keeps_all_local_work(trees, kind):
    from runner.repositories import base_kept
    workspace, base, remote, row, client = trees
    if kind == 'untracked':
        (base / 'untracked').write_text('local work')
    else:
        (base / 'file').write_text('local work')
        if kind == 'stash':
            git(base, 'stash', 'push')
        elif kind == 'detached':
            git(base, 'checkout', '--detach')
            git(base, 'add', '.')
            git(base, '-c', 'user.name=Tico', '-c', 'user.email=bot@example.com', 'commit', '-m', 'Detached work')
    assert base_kept(base).startswith('kept:')
    assert base.exists()


def test_failed_snapshot_fast_forward_returns_to_task_branch(trees):
    workspace, base, remote, row, client = trees
    path = Path(W.command(client, 'add', 'org/product')['workspace_path'])
    (path / 'file').write_text('saved task changes')
    with mock.patch.object(W, 'fast_forward', side_effect=ValueError('failed fast-forward')):
        with pytest.raises(ValueError, match='failed fast-forward'):
            W.act(workspace, row, 'remove', os.environ.copy())
    assert git(path, 'symbolic-ref', '--short', 'HEAD') == row['branch']
    assert git(remote, 'show', W.wip_branch(row) + ':file') == 'saved task changes'

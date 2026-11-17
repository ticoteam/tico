"""Real Git worktree lifecycle, using only temporary repositories and synthetic credentials."""

import os
import json
from pathlib import Path
import shutil
import subprocess
from unittest import mock

import pytest

from clients.tico import APIError
from runner_support import credential_socket
from runner_support import worktrees as W
from runner_support.repositories import GB
from runner_support.tests.helpers.worktrees import git, trees


@pytest.fixture
def light(tmp_path, monkeypatch):
    """The `trees` row and client without real git repositories, for tests that never touch one."""
    workspace = tmp_path / 'workspace'
    workspace.mkdir()
    monkeypatch.setenv('HUB_WORKSPACE', str(workspace))
    monkeypatch.setenv('HUB_BOT', 'engineer')
    monkeypatch.setenv('HUB_TASK_ID', '12345678abcdef')
    row = {
        'id': 'link1',
        'link_id': 'link1',
        'task_id': '12345678abcdef',
        'path': 'tasks/12345678/org__product',
        'branch': 'tico/12345678-build',
        'repo': 'org/product',
        'full_name': 'org/product',
        'default_branch': 'main',
        'state': 'present',
        'owner': 'bot:engineer',
        'setup_command': None,
    }
    client = mock.Mock()
    client.get.return_value = {'repositories': [{**row, 'access': 'write'}]}
    return workspace, None, None, row, client


def retired_allocation(row, client, published=None):
    client.post.return_value.update(
        state='pending',
        recovery={
            'branch': row['branch'],
            'published_head': published,
            'fallback_branch': 'tico/12345678-recovery-unique',
        },
        retired_worktree={
            'computer_id': 'retired',
            'branch': row['branch'],
            'last_commit': 'f' * 40,
            'ahead': 2,
            'dirty_files': 1,
        },
    )


def publish_task_branch(base, row):
    git(base, 'checkout', '-b', row['branch'])
    (base / 'file').write_text('published task work')
    git(base, 'add', 'file')
    git(base, '-c', 'user.name=Sam', '-c', 'user.email=sam@example.com', 'commit', '-m', 'Task work')
    head = git(base, 'rev-parse', 'HEAD')
    git(base, 'push', 'origin', row['branch'])
    git(base, 'checkout', 'main')
    git(base, 'branch', '-D', row['branch'])
    return head


@pytest.mark.slow
def test_retired_worktree_add_fetches_published_task_branch_and_keeps_unpushed_facts(trees):
    workspace, base, remote, row, client = trees
    head = publish_task_branch(base, row)
    retired_allocation(row, client, head)
    result = W.command(client, 'add', 'org/product')
    assert git(Path(result['workspace_path']), 'rev-parse', 'HEAD') == head
    assert result['branch'] == row['branch']
    assert result['retired_worktree']['ahead'] == 2
    assert '2 unpushed commits stayed on the retired computer' in result['warning']
    assert git(remote, 'rev-parse', row['branch']) == head


@pytest.mark.slow
def test_retired_worktree_missing_origin_branch_uses_new_branch_and_keeps_old_local_history(trees):
    workspace, base, remote, row, client = trees
    old = row['branch']
    git(base, 'branch', old)
    prior = git(base, 'rev-parse', old)
    retired_allocation(row, client)
    result = W.command(client, 'add', 'org/product')
    assert result['branch'] == 'tico/12345678-recovery-unique' != old
    assert git(Path(result['workspace_path']), 'rev-parse', 'HEAD') == git(remote, 'rev-parse', 'main')
    assert git(base, 'rev-parse', old) == prior
    assert 'not published' in result['warning'] and 'unpushed commits stayed' in result['warning']
    assert any(call.args[1] == {'recovery_branch_missing': True} for call in client.patch.call_args_list)


@pytest.mark.slow
@pytest.mark.parametrize('separate', ['local', 'origin'])
def test_retired_recovery_never_moves_a_branch_backwards_or_replaces_separate_history(trees, separate):
    workspace, base, remote, row, client = trees
    published = publish_task_branch(base, row)
    if separate == 'local':
        git(base, 'branch', row['branch'], 'main')
        git(base, 'checkout', row['branch'])
        (base / 'file').write_text('local work that must stay')
        git(base, 'add', 'file')
        git(base, '-c', 'user.name=Sam', '-c', 'user.email=sam@example.com', 'commit', '-m', 'Local work')
        before = git(base, 'rev-parse', 'HEAD')
        git(base, 'checkout', 'main')
    else:
        git(base, 'push', '--force', 'origin', 'main:refs/heads/' + row['branch'])
        before = git(base, 'rev-parse', 'main')
    retired_allocation(row, client, published)
    with pytest.raises(ValueError, match='separate history|last published head'):
        W.command(client, 'add', 'org/product')
    assert not (workspace / row['path']).exists()
    if separate == 'local':
        assert git(base, 'rev-parse', row['branch']) == before
    assert not any(call.args[1].get('recovery_branch_missing') for call in client.patch.call_args_list)


@pytest.mark.slow
def test_retired_recovery_does_not_treat_remote_failure_as_an_unpublished_branch(trees, monkeypatch):
    workspace, base, remote, row, client = trees
    retired_allocation(row, client)

    def unavailable(*args):
        raise ValueError('Git fetch failed; cannot determine remote branch, retry later')

    monkeypatch.setattr(W, 'remote_branch', unavailable)
    with pytest.raises(ValueError, match='cannot determine remote branch'):
        W.command(client, 'add', 'org/product')
    assert not (workspace / row['path']).exists()
    assert [call.args[1] for call in client.patch.call_args_list] == [{'state': 'pending'}]


@pytest.mark.slow
def test_retired_recovery_checks_the_reported_published_head_when_the_webhook_is_older(trees):
    workspace, base, remote, row, client = trees
    older = publish_task_branch(base, row)
    git(base, 'checkout', '-b', 'later', older)
    (base / 'file').write_text('later published head')
    git(base, 'add', 'file')
    git(base, '-c', 'user.name=Sam', '-c', 'user.email=sam@example.com', 'commit', '-m', 'Later head')
    later = git(base, 'rev-parse', 'HEAD')
    git(base, 'checkout', 'main')
    retired_allocation(row, client, older)
    client.post.return_value['recovery']['published_heads'] = [older, later]
    with pytest.raises(ValueError, match='last published head'):
        W.command(client, 'add', 'org/product')
    assert not (workspace / row['path']).exists()
    assert (
        subprocess.run(
            ['git', '-C', str(base), 'show-ref', '--verify', 'refs/heads/' + row['branch']], capture_output=True
        ).returncode
        != 0
    )


@pytest.mark.slow
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


@pytest.mark.slow
def test_the_setup_cli_marks_the_worktree_ready(trees):
    from clients.hubcli import parser

    workspace, base, remote, row, client = trees
    args = parser().parse_args(['task', 'worktree', 'setup', 'org/product'])
    assert args.fn == 'task worktree setup'
    W.command(client, 'add', 'org/product')
    repo = client.get.return_value['repositories'][0]
    client.get.side_effect = lambda route: (
        {'links': [{**row, 'kind': 'worktree'}]} if route.endswith('/links') else {'repositories': [repo]}
    )
    W.command(client, 'setup', 'org/product')
    fields = client.patch.call_args.args[1]
    assert fields['state'] == 'present' and fields['setup_pending'] is False and fields['checkout_state'] == 'ready'
    assert fields['expected_head'] and fields['checkout_target'] and len(fields['expected_base']) == 64


def test_a_write_refusal_names_what_the_server_listed_for_the_run():
    client = mock.Mock()
    client.get.return_value = {
        'configured': True,
        'repositories': [{'full_name': 'org/docs', 'access': 'read'}, {'full_name': 'org/bot-ops', 'access': 'write'}],
    }
    with pytest.raises(ValueError) as refused:
        W.metadata(client, 'org/docs')
    assert str(refused.value) == (
        'This bot needs write access to this repository: Tico lists org/docs as read-only '
        'for this run (writable here: org/bot-ops)'
    )
    with pytest.raises(ValueError, match='does not list org/product for this run'):
        W.metadata(client, 'org/product')
    assert W.metadata(client, 'ORG/bot-ops')['full_name'] == 'org/bot-ops'
    client.get.return_value = {
        'configured': True,
        'repositories': [{'full_name': f'org/r{n:02}', 'access': 'write'} for n in range(12)],
    }
    with pytest.raises(ValueError, match=r'writable here: org/r00, .*, org/r09 and 2 more\)$'):
        W.metadata(client, 'org/docs')


def test_setup_timeout_kills_process_group(light):
    workspace, base, remote, row, client = light
    process = mock.MagicMock()
    process.__enter__.return_value = process
    process.pid = 12345
    process.wait.side_effect = [subprocess.TimeoutExpired('setup', 600), 0]
    with (
        mock.patch.object(W.isolation, 'popen', return_value=process) as popen,
        mock.patch.object(W.os, 'killpg') as kill,
    ):
        with pytest.raises(ValueError, match='timed out'):
            W.setup(workspace, 'sleep 999', os.environ.copy())
        assert popen.call_args.kwargs['start_new_session'] is True
        kill.assert_called_once_with(12345, W.signal.SIGKILL)
        assert process.wait.call_count == 2


@pytest.mark.slow
def test_add_using_task_prefix_waits_for_the_same_link_restore_lock(trees):
    import concurrent.futures
    import threading

    workspace, base, remote, row, client = trees
    W.command(client, 'add', 'org/product')
    posted = threading.Event()
    response = client.post.return_value
    original_get = client.get.return_value
    client.get.side_effect = lambda route: (
        {'task': {'id': row['task_id']}} if route == 'tasks/' + row['task_id'][:8] else original_get
    )

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


def test_setup_has_no_tokens_or_socket_but_keeps_toolchain(light, monkeypatch):
    workspace, base, remote, row, client = light
    monkeypatch.setenv('HUB_TOKEN', 'synthetic-attempt-token')
    monkeypatch.setenv('GH_TOKEN', 'synthetic-repository-token')
    monkeypatch.setenv('TICO_GITHUB_EXTRA', 'synthetic-helper-key')
    monkeypatch.setenv(credential_socket.SOCKET_ENV, 'synthetic-socket')
    monkeypatch.setenv('JAVA_HOME', '/example/toolchain')
    command = 'env > setup-env'
    W.setup(workspace, command, os.environ.copy())
    environment = dict(line.split('=', 1) for line in (workspace / 'setup-env').read_text().splitlines())
    assert environment['JAVA_HOME'] == '/example/toolchain'
    assert 'PATH' in environment and 'HOME' in environment
    assert not any(
        'TOKEN' in key or key.startswith(('HUB_', 'TICO_GITHUB_')) or 'synthetic-' in value
        for key, value in environment.items()
    )


@pytest.mark.slow
@pytest.mark.parametrize('kind', ['untracked', 'stash'])
def test_retirement_keeps_all_local_work(trees, kind):
    from runner_support.repositories import base_kept

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
            (Path(link['path']) if Path(link['path']).is_absolute() else workspace / link['path']).joinpath(
                'file'
            ).unlink()
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
    link = {
        'link_id': row['id'],
        'branch': row['branch'],
        'path': row['path'],
        'checkout_state': 'initializing',
        'setup_pending': True,
        'expected_head': head,
        'checkout_target': 'refs/remotes/origin/main',
        'expected_base': expected_base,
    }
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
    link.update(
        state='pending', checkout_state='initializing', setup_pending=True, path='tasks/12345678/recovery-other'
    )
    with pytest.raises(ValueError, match='marker does not match this task registration'):
        W.command(client, 'add', 'org/product')
    assert (original_path / 'file').read_text() == original_content
    assert not (workspace / link['path']).exists()


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
    link = {
        'link_id': row['id'],
        'branch': row['branch'],
        'path': row['path'],
        'checkout_state': 'initializing',
        'setup_pending': True,
        'expected_head': head,
        'checkout_target': 'refs/remotes/origin/main',
        'expected_base': W._base_identity(W._common_dir(base, W.safe_git.environment())),
    }
    client.post.return_value = link
    before = (path / 'file').read_text()
    with pytest.raises(ValueError, match='branch does not match'):
        W.command(client, 'add', 'org/product')
    assert git(path, 'symbolic-ref', '--short', 'HEAD') == 'unrelated'
    assert (path / 'file').read_text() == before


@pytest.mark.slow
@pytest.mark.parametrize('phase', ['setup_running', 'setup_failed'])
def test_the_advertised_retry_resumes_an_expired_setup_in_place(trees, phase):
    """A setup that stopped part-way (the server's deadline then names `tico task worktree setup`): the registered setup
    command re-runs setup in the same folder on the same branch, keeping its files and proof, and reports it ready."""
    workspace, base, remote, row, client = trees
    first = W.command(client, 'add', 'org/product')  # no folder yet: the checkout is made
    path = Path(first['workspace_path'])
    (path / 'notes.txt').write_text('kept')
    (path / 'setup-ran').unlink()
    marker = W._read_marker(workspace, row['id'])
    W._write_marker(workspace, {**marker, 'phase': phase})
    row.update(state='pending', checkout_state=phase, setup_pending=True)
    detail = json.loads(row['detail_json'])
    detail.update(checkout_state=phase, setup_pending=True)
    row['detail_json'] = json.dumps(detail)
    repo = client.get.return_value['repositories'][0]
    client.get.side_effect = lambda route: (
        {'links': [{**row, 'kind': 'worktree'}]} if route.endswith('/links') else {'repositories': [repo]}
    )
    W.command(client, 'setup', 'org/product')
    fields = client.patch.call_args.args[1]
    assert fields['state'] == 'present' and fields['checkout_state'] == 'ready' and fields['setup_pending'] is False
    assert fields['expected_head'] == detail['expected_head'] and fields['expected_base'] == detail['expected_base']
    assert (path / 'setup-ran').exists() and (path / 'notes.txt').read_text() == 'kept'
    assert git(path, 'symbolic-ref', '--short', 'HEAD') == row['branch']

"""Computer base-clone contracts, without a GitHub account or running server."""
import json
from pathlib import Path
import shutil
import subprocess
import time
from unittest import mock

import pytest

from clients.tico import APIError
from runner.repositories import FETCH_INTERVAL, GB, REMOVE_AFTER, Repositories


@pytest.fixture
def repos(tmp_path):
    client = mock.Mock()
    client.get.return_value = {'repositories': [dict(full_name='org/one', default_branch='main'), dict(full_name='org/two', default_branch='main')]}
    client.post.return_value = {'token': 'synthetic-install-token', 'repositories': ['org/one', 'org/two']}
    manager = Repositories(tmp_path, tmp_path / 'state' / 'repositories.json', client)
    yield manager
    manager.close()


def fake_git(args, **kwargs):
    assert kwargs['env']['GIT_TERMINAL_PROMPT'] == '0'
    assert kwargs['timeout'] >= 10
    assert 'core.fsmonitor=false' in args
    assert 'synthetic-install-token' not in ' '.join(args)
    if 'init' in args:
        Path(args[-1]).mkdir(parents=True)
        (Path(args[-1]) / 'config').write_text('[core]\nbare = true\n')
    if 'clone' in args:
        path = Path(args[-1])
        (path / '.git').mkdir(parents=True)
        (path / '.git' / 'config').write_text('[remote "origin"]\nurl = ' + args[-2])
    return subprocess.CompletedProcess(args, 0, '', '')


def cycle(manager):
    result = manager.poll()
    if manager.pending:
        manager.pending.result(timeout=5)
    return result


def test_first_sync_is_lazy_tokens_not_saved_and_fetch_interval_survives_restart(repos):
    with mock.patch('runner.repositories.Repositories.run_git', side_effect=fake_git) as git, mock.patch('runner.repositories.shutil.disk_usage', return_value=shutil._ntuple_diskusage(100 * GB, 0, 100 * GB)):
        cycle(repos)
        first_count = git.call_count
        assert first_count > 1
        assert repos.path('org/one').exists()
        assert not repos.path('org/two').exists()
        cycle(repos)
        both_count = git.call_count
        assert both_count > first_count
        cycle(repos)
        assert git.call_count == both_count
        saved = repos.state_file.read_text()
        assert 'synthetic-install-token' not in saved
        assert 'synthetic-install-token' not in (repos.path('org/one') / '.git/config').read_text()
        restarted = Repositories(repos.root.parent, repos.state_file, repos.client)
        try:
            with mock.patch('runner.repositories.time.time', return_value=time.time() + FETCH_INTERVAL + 1):
                cycle(restarted)
                assert git.call_count > both_count
                fetches = [call.args[0] for call in git.call_args_list[both_count:] if 'fetch' in call.args[0]]
                assert any('+refs/heads/main:refs/remotes/origin/main' in args for args in fetches)
        finally:
            restarted.pool.shutdown(wait=True)


@pytest.mark.parametrize('total,free,needed,size_kb', [(100 * GB, 8 * GB, '9 GB', 2 * 1024 * 1024)])
def test_disk_floor_reports_and_does_not_mint_or_clone(repos, total, free, needed, size_kb):
    repos.client.get.return_value["repositories"][0]["size_kb"] = size_kb
    with mock.patch('runner.repositories.shutil.disk_usage', return_value=shutil._ntuple_diskusage(total, total-free, free)), mock.patch('runner.repositories.Repositories.run_git') as git:
        cycle(repos)
        row = repos.report()[0]
        assert row['state'] == 'disk_low'
        assert needed in row['error']
        assert 'Not enough disk to clone org/one' in row['error']
        assert not git.called
        assert not repos.client.post.called


def test_removal_waits_30_days_and_never_follows_paths_outside_repos(repos, tmp_path):
    with mock.patch('runner.repositories.Repositories.run_git', side_effect=fake_git), mock.patch('runner.repositories.shutil.disk_usage', return_value=shutil._ntuple_diskusage(100 * GB, 0, 100 * GB)):
        cycle(repos)
        cycle(repos)
    outside = tmp_path / 'bot-existing'
    outside.mkdir()
    (outside / 'keep').write_text('keep')
    repos.path('org/two').rename(tmp_path / 'saved-two')
    (repos.root / 'org__two').symlink_to(outside, target_is_directory=True)
    repos.client.get.return_value = {'repositories': []}
    now = time.time()
    with mock.patch('runner.repositories.isolation.run', side_effect=fake_git), mock.patch('runner.repositories.time.time', return_value=now):
        cycle(repos)
    with mock.patch('runner.repositories.isolation.run', side_effect=fake_git), mock.patch('runner.repositories.time.time', return_value=now + REMOVE_AFTER - 1):
        cycle(repos)
        assert repos.path('org/one').exists()
    with mock.patch('runner.repositories.isolation.run', side_effect=fake_git), mock.patch('runner.repositories.time.time', return_value=now + REMOVE_AFTER + 1):
        cycle(repos)
        assert not repos.path('org/one').exists()
        assert (outside / 'keep').read_text() == 'keep'
        assert repos.rows['org/two']['state'] == 'failed'
        cycle(repos)
        assert 'org/one' not in repos.rows  # removed reports do not accumulate


def test_old_server_and_transient_error_do_nothing(repos):
    repos.rows['org/one'] = {'full_name': 'org/one', 'state': 'cloned'}
    for status in (404, 503):
        repos.client.get.side_effect = APIError('http_error', 'unavailable', status)
        assert repos.poll() is None
        assert repos.report() == []
        assert 'left_at' not in repos.rows['org/one']
        assert repos.pending is None
        assert not repos.client.post.called


def test_clone_failure_redacts_and_does_not_stall_next_repo(repos):
    with mock.patch('runner.repositories.Repositories.run_git', side_effect=ValueError('synthetic-install-token was rejected')), mock.patch('runner.repositories.shutil.disk_usage', return_value=shutil._ntuple_diskusage(100 * GB, 0, 100 * GB)):
        cycle(repos)
    assert repos.report()[0]['state'] == 'failed'
    assert 'synthetic-install-token' not in json.dumps(repos.report())
    with mock.patch('runner.repositories.Repositories.run_git', side_effect=fake_git), mock.patch('runner.repositories.shutil.disk_usage', return_value=shutil._ntuple_diskusage(100 * GB, 0, 100 * GB)):
        cycle(repos)
    assert repos.rows['org/two']['state'] == 'cloned'


def test_root_symlink_and_unmanaged_folder_are_left_alone(repos, tmp_path):
    outside = tmp_path / 'outside'
    outside.mkdir()
    repos.root.symlink_to(outside, target_is_directory=True)
    cycle(repos)
    assert repos.rows['org/one']['state'] == 'failed'
    assert not list(outside.iterdir())
    repos.root.unlink()
    repos.root.mkdir()
    repos.path('org/two').mkdir()
    (repos.path('org/two') / 'keep').write_text('keep')
    cycle(repos)
    assert repos.rows['org/two']['state'] == 'failed'
    assert (repos.path('org/two') / 'keep').exists()


@pytest.mark.slow
@pytest.mark.parametrize('upgrade', [False, True])
def test_real_git_clone_keeps_token_out_of_config_and_fetches_default_branch(repos, tmp_path, upgrade):
    source = tmp_path / 'source'
    subprocess.run(['git', 'init', '-q', '-b', 'main', str(source)], check=True)
    subprocess.run(['git', '-C', str(source), 'config', 'uploadpack.allowFilter', 'true'], check=True)
    (source / 'README.md').write_text('first')
    subprocess.run(['git', '-C', str(source), 'add', '.'], check=True)
    subprocess.run(['git', '-C', str(source), '-c', 'user.name=Example', '-c', 'user.email=example@example.com', 'commit', '-qm', 'first'], check=True)
    repos.client.get.return_value['repositories'] = repos.client.get.return_value['repositories'][:1]
    real_run = subprocess.run
    original_git = repos.run_git

    if upgrade:
        repos.root.mkdir()
        real_run(['git', 'clone', '-q', source.as_uri(), str(repos.path('org/one'))], check=True)
        (repos.path('org/one') / 'local-work').write_text('committed work')
        real_run(['git', '-C', str(repos.path('org/one')), 'add', '.'], check=True)
        real_run(['git', '-C', str(repos.path('org/one')), '-c', 'user.name=Example', '-c', 'user.email=example@example.com', 'commit', '-qm', 'local work'], check=True)
        local_head = subprocess.check_output(['git', '-C', str(repos.path('org/one')), 'rev-parse', 'HEAD'], text=True).strip()
        (repos.path('org/one') / 'local-work').write_text('keep my work')
        (repos.path('org/one') / 'untracked-work').write_text('keep untracked')
        repos.rows['org/one'] = {'full_name': 'org/one', 'managed': True}

    launches = []
    mirror_has_overrides = [False]
    def local_git(args, **kwargs):
        bot = kwargs.get('bot', False)
        launches.append((args, dict(kwargs['env']), bot))
        if 'fetch' in args and kwargs['env'].get('GH_TOKEN') and mirror_has_overrides[0]:
            for key in ('proxy', 'sslCAPath', 'curloptResolve', 'cookieFile', 'saveCookies'):
                assert f'http.https://github.com/.{key.lower()}=' in [a.lower() for a in args]
            assert 'http.https://github.com/.sslverify=true' in [a.lower() for a in args]
        if bot:
            assert 'GH_TOKEN' not in kwargs['env'] and 'GITHUB_TOKEN' not in kwargs['env']
        else:
            assert str(repos.path('org/one')) not in args
        args = [source.as_uri() if a.startswith('https://github.com/') and 'config' not in args else ('protocol.file.allow=always' if a == 'protocol.file.allow=never' else a) for a in args]
        return original_git(args, **kwargs)

    with mock.patch('runner.repositories.Repositories.run_git', side_effect=local_git), mock.patch('runner.repositories.shutil.disk_usage', return_value=shutil._ntuple_diskusage(100 * GB, 0, 100 * GB)):
        cycle(repos)
        assert repos.report()[0]['state'] == 'cloned'
        config = (repos.path('org/one') / '.git/config').read_text()
        assert 'synthetic-install-token' not in config
        assert 'credential' not in config
        assert 'promisor' not in config
        assert (repos.path('org/one') / 'README.md').read_text() == 'first'
        if upgrade:
            assert (repos.path('org/one') / 'local-work').read_text() == 'keep my work'
            assert (repos.path('org/one') / 'untracked-work').read_text() == 'keep untracked'
            assert subprocess.check_output(['git', '-C', str(repos.path('org/one')), 'rev-parse', 'HEAD'], text=True).strip() == local_head
        mirror = repos.mirror_path('org/one')
        assert (mirror.stat().st_mode & 0o777) == 0o755
        for root, dirs, files in __import__('os').walk(mirror):
            assert Path(root).stat().st_mode & 0o777 == 0o755
            for name in files:
                assert (Path(root) / name).stat().st_mode & 0o777 == 0o644
        assert not (mirror / 'FETCH_HEAD').exists()
        assert subprocess.check_output(['git', '-C', str(repos.path('org/one')), 'remote', 'get-url', 'tico-mirror'], text=True).strip() == mirror.as_uri()
        assert subprocess.check_output(['git', '-C', str(repos.path('org/one')), 'remote', 'get-url', 'origin'], text=True).strip() == 'https://github.com/org/one.git'
        victim = tmp_path / 'registration.json'
        victim.write_text('registration must survive')
        (repos.path('org/one') / '.git/FETCH_HEAD').unlink(missing_ok=True)
        (repos.path('org/one') / '.git/FETCH_HEAD').symlink_to(victim)

        worktree = tmp_path / 'offline-task'
        offline = {'PATH': __import__('os').environ['PATH'], 'GIT_NO_LAZY_FETCH': '1'}
        real_run(['git', '-C', str(repos.path('org/one')), 'worktree', 'add', '-b', 'tico/offline', str(worktree), 'origin/main'], env=offline, check=True, capture_output=True)
        (worktree / 'README.md').write_text('offline change')
        real_run(['git', '-C', str(worktree), 'add', '.'], env=offline, check=True)
        real_run(['git', '-C', str(worktree), '-c', 'user.name=Example', '-c', 'user.email=example@example.com', 'commit', '-qm', 'offline'], env=offline, check=True)
        real_run(['git', '-C', str(repos.path('org/one')), 'worktree', 'remove', str(worktree)], check=True)
        marker = tmp_path / 'hook-ran'
        hook = repos.path('org/one') / '.git/hooks/reference-transaction'
        hook.write_text('#!/bin/sh\nprintf "%s" "$GH_TOKEN" > ' + str(marker) + '\n')
        hook.chmod(0o755)
        monitor = tmp_path / 'fsmonitor'
        monitor.write_text('#!/bin/sh\nprintf "%s" "$GH_TOKEN" > ' + str(marker) + '\n')
        monitor.chmod(0o755)
        real_run(['git', '-C', str(repos.path('org/one')), 'config', 'core.fsmonitor', str(monitor)], check=True)
        real_run(['git', '-C', str(repos.path('org/one')), 'config', 'http.https://github.com/.proxy', 'http://127.0.0.1:1'], check=True)
        real_run(['git', '-C', str(repos.path('org/one')), 'config', 'http.https://github.com/.sslVerify', 'false'], check=True)
        for key, value in [('proxy', 'http://127.0.0.1:1'), ('sslVerify', 'false'), ('sslCAPath', '/example/ca'),
                           ('curloptResolve', 'github.com:443:127.0.0.1'), ('cookieFile', str(victim)), ('saveCookies', 'true')]:
            real_run(['git', '-C', str(mirror), 'config', 'http.https://github.com/.' + key, value], check=True)
        mirror_has_overrides[0] = True
        (source / 'README.md').write_text('second')
        subprocess.run(['git', '-C', str(source), 'add', '.'], check=True)
        subprocess.run(['git', '-C', str(source), '-c', 'user.name=Example', '-c', 'user.email=example@example.com', 'commit', '-qm', 'second'], check=True)
        with mock.patch('runner.repositories.time.time', return_value=time.time() + FETCH_INTERVAL + 1):
            cycle(repos)
        head = subprocess.check_output(['git', '-C', str(source), 'rev-parse', 'HEAD'], text=True).strip()
        fetched = subprocess.check_output(['git', '-C', str(repos.path('org/one')), 'rev-parse', 'origin/main'], text=True).strip()
        assert fetched == head
        assert not marker.exists()
        assert victim.read_text() == 'registration must survive'
        assert all('--no-write-fetch-head' in args for args, env, bot in launches if 'fetch' in args)
        assert all(str(repos.mirror_path('org/one')) in args for args, env, bot in launches if env.get('GH_TOKEN') and 'fetch' in args)
        assert 'synthetic-install-token' not in (repos.path('org/one') / '.git/config').read_text()


def test_slow_git_does_not_block_heartbeat_poll(repos):
    import threading
    entered, release = threading.Event(), threading.Event()

    def slow_git(args, **kwargs):
        entered.set()
        assert release.wait(timeout=5)
        return fake_git(args, **kwargs)

    with mock.patch('runner.repositories.Repositories.run_git', side_effect=slow_git) as git, mock.patch('runner.repositories.shutil.disk_usage', return_value=shutil._ntuple_diskusage(100 * GB, 0, 100 * GB)):
        try:
            repos.poll()
            assert entered.wait(timeout=5)
            rows = repos.poll()
            assert rows[0]['state'] == 'cloning'
            assert git.call_count == 1
            assert repos.client.get.call_count == 2
        finally:
            release.set()
            repos.pending.result(timeout=5)


def test_shutdown_interrupts_git_and_its_children(repos, tmp_path):
    import sys
    import threading
    pid_file = tmp_path / 'child.pid'
    errors = []
    def run():
        try:
            repos.run_git([sys.executable, '-c',
                'import subprocess,time; p=subprocess.Popen(["sleep","60"]); '
                'open(__import__("sys").argv[1],"w").write(str(p.pid)); time.sleep(60)', str(pid_file)],
                env={}, timeout=7200)
        except ValueError as exc:
            errors.append(str(exc))
    # This test substitutes a stalled Git executable but keeps the real process lifecycle.
    real_popen = subprocess.Popen
    def stalled(command, **kwargs):
        return real_popen([command[0], *command[3:]], **kwargs)
    with mock.patch('runner.repositories.subprocess.Popen', side_effect=stalled):
        thread = threading.Thread(target=run)
        thread.start()
        deadline = time.monotonic() + 5
        while not pid_file.exists() and time.monotonic() < deadline:
            time.sleep(.01)
        assert pid_file.exists()
        process = repos.process
        started = time.monotonic()
        repos.close()
        thread.join(timeout=5)
        assert not thread.is_alive()
        assert time.monotonic() - started < 10
        assert process.poll() is not None
        assert errors or process.returncode < 0
        child = pid_file.read_text()
        status = real_popen(['ps', '-o', 'stat=', '-p', child], stdout=subprocess.PIPE, text=True)
        output, _ = status.communicate(timeout=2)
        assert not output.strip() or output.strip().startswith('Z')


def test_broken_managed_clone_keeps_local_work_with_filtered_environment(repos, monkeypatch):
    monkeypatch.setenv('AWS_SECRET_ACCESS_KEY', 'synthetic-host-secret')
    monkeypatch.setenv('GIT_CONFIG_COUNT', '99')
    monkeypatch.setenv('HTTPS_PROXY', 'http://proxy.example.com:8080')
    monkeypatch.setenv('SSL_CERT_FILE', '/example/ca.pem')
    path = repos.path('org/one')
    path.mkdir(parents=True)
    (path / '.git').mkdir()
    (path / 'broken').write_text('local work')
    repos.rows['org/one'] = {'full_name': 'org/one', 'managed': True}
    def git(args, **kwargs):
        env = kwargs['env']
        assert 'AWS_SECRET_ACCESS_KEY' not in env
        assert env['GIT_CONFIG_SYSTEM'] == '/dev/null'
        assert env['HTTPS_PROXY'] == 'http://proxy.example.com:8080'
        assert env['SSL_CERT_FILE'] == '/example/ca.pem'
        if kwargs.get('bot'):
            assert 'GH_TOKEN' not in env and 'GIT_CONFIG_COUNT' not in env
        else:
            assert 'GIT_CONFIG_COUNT' not in env or env['GIT_CONFIG_COUNT'] == '3'
        if 'rev-parse' in args:
            assert kwargs['bot']
            return subprocess.CompletedProcess(args, 128, '', '')
        return fake_git(args, **kwargs)
    with mock.patch.object(repos, 'run_git', side_effect=git), mock.patch('runner.repositories.shutil.disk_usage', return_value=shutil._ntuple_diskusage(100 * GB, 0, 100 * GB)):
        cycle(repos)
    assert repos.rows['org/one']['state'] == 'failed'
    assert (path / 'broken').read_text() == 'local work'


def test_mirror_symlinks_are_refused_before_tokens_or_git(repos, tmp_path):
    mirror = repos.mirror_path('org/one')
    mirror.mkdir(parents=True)
    victim = tmp_path / 'victim'
    victim.write_text('keep')
    (mirror / 'FETCH_HEAD').symlink_to(victim)
    with mock.patch.object(repos, 'run_git') as git, mock.patch('runner.repositories.shutil.disk_usage', return_value=shutil._ntuple_diskusage(100 * GB, 0, 100 * GB)):
        cycle(repos)
    assert repos.rows['org/one']['state'] == 'failed'
    assert 'symlink' in repos.rows['org/one']['error']
    assert victim.read_text() == 'keep'
    assert not git.called and not repos.client.post.called


def test_isolated_state_exposes_only_mirrors(tmp_path, monkeypatch):
    import os
    from runner.state import State
    directory = tmp_path / 'state'
    directory.mkdir()
    private = directory / 'previous.json'
    private.write_text('private state')
    monkeypatch.setattr('runner.isolation.enabled', lambda: True)
    old_umask = os.umask(0o022)
    try:
        state = State(directory)
        assert directory.stat().st_mode & 0o777 == 0o711
        assert private.stat().st_mode & 0o777 == 0o600
        assert state.path.stat().st_mode & 0o777 == 0o600
    finally:
        os.umask(old_umask)


def test_isolated_mirrors_reject_bot_writable_files_and_parent(repos, monkeypatch):
    mirror = repos.mirror_path('org/one')
    mirror.mkdir(parents=True)
    (mirror / 'config').write_text('[core]\nbare = true\n')
    repos.mirror_permissions(mirror)
    repos.mirrors.chmod(0o755)
    monkeypatch.setattr('runner.isolation.enabled', lambda: True)
    (mirror / 'config').chmod(0o664)
    with pytest.raises(ValueError, match='writable'):
        repos.mirror_path('org/one')
    (mirror / 'config').chmod(0o644)
    repos.mirrors.chmod(0o775)
    with pytest.raises(ValueError, match='writable'):
        repos.mirror_path('org/one')


def test_git_in_bot_directories_uses_isolation_launcher(repos):
    with mock.patch('runner.repositories.isolation.popen', wraps=subprocess.Popen) as launch:
        result = repos.run_git(['git', '--version'], env={'PATH': __import__('os').environ['PATH']}, timeout=10, bot=True)
    assert result.returncode == 0
    assert launch.call_count == 1


def test_real_isolated_mirror_is_readable_but_not_writable(monkeypatch):
    import os
    import tempfile
    from runner import isolation
    from runner.state import State
    if not __import__('sys').platform.startswith('linux') or os.geteuid() != 0 or not Path(isolation.SETPRIV).exists():
        pytest.skip('Requires a Linux supervisor able to switch Unix users')
    old_umask = os.umask(0o022)
    with tempfile.TemporaryDirectory(prefix='tico-mirror-isolation-', dir='/tmp') as directory:
        root = Path(directory)
        root.chmod(0o755)
        monkeypatch.setenv('TICO_RUNNER_BOT_UID', '65534')
        monkeypatch.setenv('TICO_RUNNER_BOT_GID', '65534')
        try:
            state = State(root / 'state')
            manager = Repositories(root / 'workspace', state.directory / 'repositories.json', mock.Mock())
            mirror = manager.mirror_path('org/one')
            mirror.mkdir(parents=True)
            (mirror / 'config').write_text('mirror data')
            manager.mirror_permissions(mirror)
            manager.mirrors.chmod(0o755)
            private = state.directory / 'private.json'
            private.write_text('private')
            private.chmod(0o600)
            read = isolation.run(['cat', str(mirror / 'config')], capture_output=True, text=True)
            assert read.returncode == 0 and read.stdout == 'mirror data'
            for target in (mirror / 'config', mirror / 'new-file'):
                write = isolation.run(['sh', '-c', 'echo changed > "$1"', 'sh', str(target)], capture_output=True)
                assert write.returncode != 0
            assert isolation.run(['cat', str(private)], capture_output=True).returncode != 0
            manager.close()
        finally:
            os.umask(old_umask)


def test_poll_skips_unsafe_managed_markers(repos, tmp_path):
    path = repos.root / 'acme__product' / '.git'
    path.mkdir(parents=True)
    outside = tmp_path / 'outside.json'
    outside.write_text(json.dumps({'full_name': 'Acme/Product'}))
    (path / 'tico-managed').symlink_to(outside)
    repos.client.get.return_value = {'repositories': []}
    with mock.patch.object(repos, 'sync'):
        cycle(repos)
    assert repos.rows == {}

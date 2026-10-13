"""Computer base-clone contracts, without a GitHub account or running server."""
import json
import os
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
    # Fixture writes use an explicit mask, independent of the invoking shell's group-write mask.
    previous_mask = os.umask(0o022)
    try:
        yield manager
    finally:
        try:
            manager.close()
            manager.pool.shutdown(wait=True, cancel_futures=True)
        finally:
            os.umask(previous_mask)


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
        # Completion is the condition; this is a hang guard, not a Git speed requirement.
        manager.pending.result(timeout=120)
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


# ---------------------------------------------------------------- a base clone made in a turn
TURN_TOKEN = 'ghs_' + 'T' * 36


def failing_clone(stderr='', returncode=128, raise_timeout=False, leave='partial'):
    """isolation.run for worktree_base: the clone leaves a half-made folder and fails as GitHub would."""
    from runner import repositories as R
    def run(args, **kwargs):
        if 'clone' in args:
            path = Path(args[-1])
            if leave == 'partial':
                (path / '.git' / 'objects').mkdir(parents=True)
                (path / '.git' / 'objects' / 'pack-partial').write_text('half a pack')
            if raise_timeout:
                raise subprocess.TimeoutExpired(args, R.CLONE_TIMEOUT)
            return subprocess.CompletedProcess(args, returncode, '', stderr)
        return subprocess.CompletedProcess(args, 0, '', '')
    return run


@pytest.mark.parametrize('stderr,wanted', [
    (f"Cloning into 'x'...\nremote: Repository not found.\nfatal: repository 'https://x-access-token:{TURN_TOKEN}@github.com/acme/web.git/' not found\n",
     'remote: Repository not found.'),
    ("error: unable to create file app/" + 'a' * 30 + ".tsx: Filename too long\nfatal: unable to checkout working tree\n"
     "warning: Clone succeeded, but checkout failed.\n", 'fatal: unable to checkout working tree'),
    ('error: RPC failed; HTTP 500 curl 22 The requested URL returned error: 500\nfatal: expected flush after ref listing\n',
     'RPC failed; HTTP 500'),
])
def test_turn_clone_failure_names_gits_reason_and_removes_the_partial_clone(tmp_path, stderr, wanted):
    from runner import repositories as R
    with mock.patch('runner.repositories.isolation.run', side_effect=failing_clone(stderr)):
        with pytest.raises(ValueError) as caught:
            R.worktree_base(tmp_path, {'full_name': 'acme/web'}, {'GH_TOKEN': TURN_TOKEN, 'PATH': os.environ['PATH']})
    message = str(caught.value)
    assert message.startswith('Git clone of acme/web failed: ') and wanted in message
    assert TURN_TOKEN not in message and 'check repository access, network and disk space' not in message
    assert not (tmp_path / 'repos' / R.base_folder('acme/web')).exists()     # the next try starts clean


def test_turn_clone_timeout_says_so_and_removes_the_partial_clone(tmp_path):
    from runner import repositories as R
    with mock.patch('runner.repositories.isolation.run', side_effect=failing_clone(raise_timeout=True)):
        with pytest.raises(ValueError, match=r'Git clone of acme/web timed out after 120 seconds'):
            R.worktree_base(tmp_path, {'full_name': 'acme/web'}, {'PATH': os.environ['PATH']})
    assert not (tmp_path / 'repos' / R.base_folder('acme/web')).exists()


def test_turn_clone_never_removes_a_folder_it_did_not_make(tmp_path):
    from runner import repositories as R
    target = tmp_path / 'repos' / R.base_folder('acme/web')
    def appeared(args, **kwargs):
        if 'clone' in args:
            target.mkdir(parents=True)
            (target / 'someone-elses-work').write_text('keep')
            return subprocess.CompletedProcess(args, 128, '', f"fatal: destination path '{target}' already exists and is not an empty directory.\n")
        return subprocess.CompletedProcess(args, 0, '', '')
    with mock.patch('runner.repositories.isolation.run', side_effect=appeared):
        with pytest.raises(ValueError, match='already exists'):
            R.worktree_base(tmp_path, {'full_name': 'acme/web'}, {'PATH': os.environ['PATH']})
    assert (target / 'someone-elses-work').read_text() == 'keep'


def test_partial_clone_with_a_registered_worktree_is_kept(tmp_path):
    from runner.repositories import discard_failed_clone
    path = tmp_path / 'base'
    (path / '.git' / 'worktrees' / 'task').mkdir(parents=True)
    with mock.patch('runner.repositories.base_kept', side_effect=ValueError('cannot prune')):
        discard_failed_clone(path, fresh=True)
    assert path.exists()


# ---------------------------------------------------------------- a computer base clone that fails
def roomy():
    return mock.patch('runner.repositories.shutil.disk_usage', return_value=shutil._ntuple_diskusage(100 * GB, 0, 100 * GB))


def mirror_fetch_fails(stderr):
    def git(args, **kwargs):
        if 'fetch' in args and kwargs['env'].get('GH_TOKEN'):
            return subprocess.CompletedProcess(args, 128, '', stderr)
        return fake_git(args, **kwargs)
    return git


def only_one(repos):
    repos.client.get.return_value['repositories'] = repos.client.get.return_value['repositories'][:1]


def test_base_clone_failure_reports_gits_reason_without_the_token(repos):
    only_one(repos)
    stderr = ("remote: Repository not found.\nfatal: repository "
              "'https://x-access-token:synthetic-install-token@github.com/org/one.git/' not found\n")
    with mock.patch('runner.repositories.Repositories.run_git', side_effect=mirror_fetch_fails(stderr)), roomy():
        cycle(repos)
    row = repos.report()[0]
    assert row['state'] == 'failed'
    assert row['error'].startswith('Git mirror fetch of org/one failed: remote: Repository not found.')
    assert 'Retrying in 5 minutes' in row['error'] and 'restart' in row['error']
    assert 'synthetic-install-token' not in json.dumps(repos.report()) + repos.state_file.read_text()
    assert 'check GitHub access, disk space and network' not in row['error']


def test_real_git_reason_reaches_the_report(repos, tmp_path):
    """Real git, a local path only: what git itself said is what the computer reports."""
    only_one(repos)
    missing = tmp_path / 'no-such-repository.git'
    original_git = repos.run_git
    def local_git(args, **kwargs):
        args = [missing.as_uri() if a.startswith('https://github.com/') else
                ('protocol.file.allow=always' if a == 'protocol.file.allow=never' else a) for a in args]
        return original_git(args, **kwargs)
    with mock.patch('runner.repositories.Repositories.run_git', side_effect=local_git), roomy():
        cycle(repos)
    error = repos.report()[0]['error']
    assert error.startswith('Git mirror fetch of org/one failed: fatal: ')
    assert 'no-such-repository.git' in error and 'does not appear to be a git repository' in error


def test_retry_starts_at_minutes_doubles_and_stops_at_six_hours(repos):
    only_one(repos)
    delays = []
    with mock.patch('runner.repositories.Repositories.run_git', side_effect=mirror_fetch_fails('fatal: unable to access: HTTP 500')), roomy():
        for _ in range(9):
            repos.rows.setdefault('org/one', {'full_name': 'org/one', 'state': 'cloning'}).update(attempted_at=0)
            cycle(repos)
            delays.append(repos.rows['org/one']['retry_delay'])
    assert delays == [300, 600, 1200, 2400, 4800, 9600, 19200, 21600, 21600]
    assert 'Retrying in 6 hours' in repos.rows['org/one']['error']


def test_restarting_the_runner_retries_a_failed_clone_at_once(repos):
    only_one(repos)
    with mock.patch('runner.repositories.Repositories.run_git', side_effect=mirror_fetch_fails('fatal: HTTP 500')), roomy():
        for _ in range(4):
            repos.rows.setdefault('org/one', {'full_name': 'org/one'}).update(attempted_at=0)
            cycle(repos)
    assert repos.rows['org/one']['retry_delay'] == 2400
    with mock.patch('runner.repositories.Repositories.run_git', side_effect=fake_git) as git, roomy():
        cycle(repos)
        assert not git.called                           # still waiting out the backoff
        restarted = Repositories(repos.root.parent, repos.state_file, repos.client)
        try:
            assert restarted.rows['org/one']['failures'] == 0
            cycle(restarted)
            assert git.called
            assert restarted.rows['org/one']['state'] == 'cloned'
            assert 'error' not in restarted.rows['org/one']
        finally:
            restarted.pool.shutdown(wait=True)


def test_token_refusal_names_the_cause(repos):
    only_one(repos)
    repos.client.post.side_effect = APIError('github_repo_not_accessible', 'org/one is not reachable; check the GitHub App installation', 409)
    with mock.patch('runner.repositories.Repositories.run_git', side_effect=fake_git), roomy():
        cycle(repos)
    assert 'Tico could not get a GitHub token for org/one: the GitHub App is not installed on it' in repos.rows['org/one']['error']
    repos.client.post.side_effect = None
    repos.client.post.return_value = {'token': 'synthetic-install-token', 'repositories': ['org/two']}
    repos.rows['org/one']['attempted_at'] = 0
    with mock.patch('runner.repositories.Repositories.run_git', side_effect=fake_git), roomy():
        cycle(repos)
    assert 'Tico could not get a GitHub read token for org/one: the GitHub App is not installed on it' in repos.rows['org/one']['error']


def test_a_clone_stopped_halfway_is_removed_and_cloned_again(repos):
    only_one(repos)
    path = repos.root / 'org__one'
    (path / '.git' / 'objects').mkdir(parents=True)
    (path / '.git' / 'objects' / 'tmp_pack_partial').write_text('half a pack')
    repos.rows['org/one'] = {'full_name': 'org/one', 'state': 'cloning', 'managed': True, 'partial': True}
    with mock.patch('runner.repositories.Repositories.run_git', side_effect=fake_git), roomy():
        cycle(repos)
    assert repos.rows['org/one']['state'] == 'cloned'
    assert 'partial' not in repos.rows['org/one']
    assert not (path / '.git' / 'objects' / 'tmp_pack_partial').exists()


def test_a_failed_clone_leaves_no_half_made_folder(repos):
    only_one(repos)
    def clone_fails(args, **kwargs):
        if 'clone' in args:
            (Path(args[-1]) / '.git').mkdir(parents=True)
            return subprocess.CompletedProcess(args, 128, '', 'error: unable to create file a: Filename too long\nfatal: unable to checkout working tree\n')
        return fake_git(args, **kwargs)
    with mock.patch('runner.repositories.Repositories.run_git', side_effect=clone_fails), roomy():
        cycle(repos)
    row = repos.rows['org/one']
    assert 'Filename too long' in row['error'] and 'unable to checkout working tree' in row['error']
    assert not (repos.root / 'org__one').exists() and 'partial' not in row


def local_source(tmp_path):
    source = tmp_path / 'source'
    subprocess.run(['git', 'init', '-q', '-b', 'main', str(source)], check=True)
    (source / 'README.md').write_text('first')
    subprocess.run(['git', '-C', str(source), 'add', '.'], check=True)
    subprocess.run(['git', '-C', str(source), '-c', 'user.name=Example', '-c', 'user.email=example@example.com',
                    'commit', '-qm', 'first'], check=True)
    return source


def from_local(repos, source, before=None):
    """run_git with GitHub replaced by a local repository: real git, no network. `before(args)` may answer first."""
    original_git = repos.run_git
    def local_git(args, **kwargs):
        args = [source.as_uri() if a.startswith('https://github.com/') and 'config' not in args else
                ('protocol.file.allow=always' if a == 'protocol.file.allow=never' else a) for a in args]
        assert not any(a.startswith(('https://', 'http://')) for a in args if 'config' not in args)
        answer = before(args) if before else None
        return answer if answer is not None else original_git(args, **kwargs)
    return mock.patch('runner.repositories.Repositories.run_git', side_effect=local_git)


def test_a_mirror_the_bot_cannot_clone_is_rebuilt_and_the_clone_succeeds(repos, tmp_path):
    """GitHub is fine, the copy from the mirror into the workspace is not (the reported case). Real git."""
    only_one(repos)
    source = local_source(tmp_path)
    failed = []
    def once(args):
        if 'clone' in args and not failed:
            failed.append(args)
            return subprocess.CompletedProcess(args, 128, '', 'error: unable to read 0123abcd\nfatal: remote did not send all necessary objects\n')
        return None
    with from_local(repos, source, once), roomy():
        cycle(repos)
    row = repos.rows['org/one']
    assert failed and row['state'] == 'cloned', row.get('error')
    assert (repos.path('org/one') / 'README.md').read_text() == 'first'


def test_a_corrupt_mirror_is_rebuilt_when_its_fetch_fails(repos, tmp_path):
    """A mirror whose own objects are corrupt fails to fetch while GitHub is fine. Real git."""
    only_one(repos)
    source = local_source(tmp_path)
    with from_local(repos, source), roomy():
        cycle(repos)
        mirror = repos.mirror_path('org/one')
        for root, dirs, files in os.walk(mirror / 'objects'):
            for name in files:
                target = Path(root) / name
                target.chmod(0o644)
                target.write_bytes(b'not a git object')
        (source / 'README.md').write_text('second')
        subprocess.run(['git', '-C', str(source), '-c', 'user.name=Example', '-c', 'user.email=example@example.com',
                        'commit', '-qam', 'second'], check=True)
        first = subprocess.check_output(['git', '-C', str(repos.path('org/one')), 'rev-parse', 'origin/main'], text=True).strip()
        with mock.patch('runner.repositories.time.time', return_value=time.time() + FETCH_INTERVAL + 1), \
                mock.patch.object(repos, 'discard_mirror', wraps=repos.discard_mirror) as discard:
            cycle(repos)
    row = repos.rows['org/one']
    assert row['state'] == 'cloned', row.get('error')
    assert discard.call_count == 1
    head = subprocess.check_output(['git', '-C', str(source), 'rev-parse', 'HEAD'], text=True).strip()
    assert head != first
    assert subprocess.check_output(['git', '-C', str(repos.path('org/one')), 'rev-parse', 'origin/main'], text=True).strip() == head


def test_a_mirror_that_stays_broken_reports_both_reasons(repos):
    only_one(repos)
    def clone_always_fails(args, **kwargs):
        if 'clone' in args:
            return subprocess.CompletedProcess(args, 128, '', 'error: Could not read 0123abcd\nfatal: remote did not send all necessary objects\n')
        return fake_git(args, **kwargs)
    with mock.patch('runner.repositories.Repositories.run_git', side_effect=clone_always_fails) as git, roomy():
        cycle(repos)
    error = repos.rows['org/one']['error']
    assert error.startswith('Cloning the local mirror of org/one failed: ')
    assert 'remote did not send all necessary objects' in error
    assert 'rebuilt the mirror from GitHub and cloning it failed again' in error
    assert sum('clone' in call.args[0] for call in git.call_args_list) == 2      # rebuilt once, not in a loop
    assert sum(bool('fetch' in call.args[0] and call.kwargs['env'].get('GH_TOKEN')) for call in git.call_args_list) == 2


def test_turn_clone_falls_back_to_github_when_the_mirror_cannot_be_copied(tmp_path):
    from runner import repositories as R
    clones = []
    def run(args, **kwargs):
        if 'clone' in args:
            clones.append(args)
            path = Path(args[-1])
            (path / '.git').mkdir(parents=True)
            if 'tico-mirror' in args:
                return subprocess.CompletedProcess(args, 128, '', 'error: unable to read 0123abcd\nfatal: remote did not send all necessary objects\n')
            assert args[-2] == 'https://github.com/acme/web.git'
        return subprocess.CompletedProcess(args, 0, '', '')
    repo = {'full_name': 'acme/web', '_mirror_refresh': {'mirror': (tmp_path / 'mirror.git').as_uri(), 'default_branch': 'main'}}
    with mock.patch('runner.repositories.isolation.run', side_effect=run):
        path, branch = R.worktree_base(tmp_path, repo, {'PATH': os.environ['PATH']})
    assert branch == 'main' and len(clones) == 2 and path.exists()
    assert "Could not copy this computer's mirror of acme/web; cloned from GitHub instead" == repo['fetch_warning']


def test_turn_clone_reports_both_reasons_when_mirror_and_github_fail(tmp_path):
    from runner import repositories as R
    def run(args, **kwargs):
        if 'clone' in args:
            if 'tico-mirror' in args:
                return subprocess.CompletedProcess(args, 128, '', 'fatal: remote did not send all necessary objects\n')
            return subprocess.CompletedProcess(args, 128, '', 'remote: Repository not found.\nfatal: repository not found\n')
        return subprocess.CompletedProcess(args, 0, '', '')
    repo = {'full_name': 'acme/web', '_mirror_refresh': {'mirror': (tmp_path / 'mirror.git').as_uri(), 'default_branch': 'main'}}
    with mock.patch('runner.repositories.isolation.run', side_effect=run):
        with pytest.raises(ValueError) as caught:
            R.worktree_base(tmp_path, repo, {'PATH': os.environ['PATH']})
    assert str(caught.value) == ('Git clone of acme/web failed: fatal: remote did not send all necessary objects; cloning from '
                                 'GitHub instead also failed: remote: Repository not found. / fatal: repository not found')


# ---------------------------------------------------------------- a clone a person already made (a Mac)
def own_clone(repos, source, origin='git@github.com:org/one.git'):
    path = repos.root / 'org__one'
    repos.root.mkdir(exist_ok=True)
    subprocess.run(['git', 'clone', '-q', source.as_uri(), str(path)], check=True)
    subprocess.run(['git', '-C', str(path), 'remote', 'set-url', 'origin', origin], check=True)
    (path / 'notes.txt').write_text('my uncommitted notes')
    return path


def test_a_mac_takes_over_a_clone_of_the_same_repository_and_keeps_its_origin(repos, tmp_path):
    only_one(repos)
    source = local_source(tmp_path)
    path = own_clone(repos, source)
    with from_local(repos, source), roomy():
        cycle(repos)
    row = repos.rows['org/one']
    assert row['state'] == 'cloned' and row['adopted'], row.get('error')
    assert (path / 'notes.txt').read_text() == 'my uncommitted notes'
    assert subprocess.check_output(['git', '-C', str(path), 'remote', 'get-url', 'origin'], text=True).strip() == 'git@github.com:org/one.git'
    assert subprocess.check_output(['git', '-C', str(path), 'remote', 'get-url', 'tico-mirror'], text=True).strip() == repos.mirror_path('org/one').as_uri()
    assert json.loads((path / '.git' / 'tico-managed').read_text()) == {'full_name': 'org/one', 'adopted': True}
    # No longer wanted here: Tico forgets it, and the person's clone stays.
    repos.client.get.return_value = {'repositories': []}
    now = time.time()
    with mock.patch('runner.repositories.time.time', return_value=now):
        cycle(repos)
    with mock.patch('runner.repositories.time.time', return_value=now + REMOVE_AFTER + 1):
        cycle(repos)
        cycle(repos)
    assert (path / 'notes.txt').read_text() == 'my uncommitted notes'
    assert not (path / '.git' / 'tico-managed').exists()
    assert 'org/one' not in repos.rows


@pytest.mark.parametrize('origin,reason', [
    ('git@github.com:org/other.git', 'its origin is git@github.com:org/other.git, not org/one'),
    ('https://notgithub.com/org/one.git', 'its origin is https://notgithub.com/org/one.git, not org/one'),
])
def test_a_clone_of_something_else_is_left_alone_and_says_why(repos, tmp_path, origin, reason):
    only_one(repos)
    source = local_source(tmp_path)
    path = own_clone(repos, source, origin)
    before = (path / '.git' / 'config').read_text()
    with from_local(repos, source), roomy():
        cycle(repos)
    row = repos.rows['org/one']
    assert row['state'] == 'failed' and reason in row['error'] and 'adopted' not in row
    assert (path / '.git' / 'config').read_text() == before and not (path / '.git' / 'tico-managed').exists()


def test_a_computer_that_isolates_bots_never_takes_over_a_folder(repos, tmp_path, monkeypatch):
    only_one(repos)
    source = local_source(tmp_path)
    own_clone(repos, source)
    monkeypatch.setattr('runner.repositories.isolation.enabled', lambda: True)
    assert 'separate user' in repos.adoption_problem(repos.root / 'org__one', 'org/one')

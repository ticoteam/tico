"""Computer-owned task worktrees. Never removes a tree before preserving its history."""
import concurrent.futures
from contextlib import contextmanager
import fcntl
import fnmatch
import hashlib
import json
import signal
import time
import os
import re
from pathlib import Path
import shutil
import subprocess
import threading
import uuid
import weakref

from clients.tico import APIError
from clients import git_repository
from . import git_credentials, isolation, repositories, safe_git


def safe_path(workspace, relative):
    root = Path(workspace).resolve()
    if not isinstance(relative, str) or len(relative) > 1000 or any(ord(ch) < 32 for ch in relative):
        raise ValueError('Invalid worktree path')
    raw = Path(relative)
    path = root / raw
    if raw.is_absolute() or '..' in raw.parts or not raw.parts or path.is_symlink():
        raise ValueError('Use a worktree inside the team workspace')
    resolved = path.resolve()
    if resolved != path:
        raise ValueError("Worktree path must not contain symlinks")
    try:
        insensitive = root.samefile(Path(str(root).swapcase()))
    except OSError:
        insensitive = False
    repo_path = str(root / 'repos')
    candidate = str(resolved)
    if insensitive:
        repo_path, candidate = repo_path.casefold(), candidate.casefold()
    if candidate == repo_path or candidate.startswith(repo_path + os.sep):
        raise ValueError('Worktree must stay outside repos')
    if resolved == root or root not in resolved.parents or (root / 'repos') == resolved or (root / 'repos') in resolved.parents:
        raise ValueError('Worktree must stay inside the team workspace, outside repos')
    return resolved


def disk_floor(workspace):
    usage = shutil.disk_usage(workspace)
    floor = 5 * repositories.GB
    if usage.free < floor:
        raise ValueError(f'Not enough disk to create worktree: {usage.free / repositories.GB:.1f} GB free, needs {floor / repositories.GB:g} GB. Free space on this volume')


def git(path, *args, env=None, check=True):
    if args[:2] == ('worktree', 'prune') and (Path(path) / '.git' / 'worktrees').is_symlink():
        raise ValueError('Worktree registrations point outside the base clone; left as they are')
    done = isolation.run([*safe_git.prefix(path), '-C', str(path), *args], env=safe_git.environment(env), capture_output=True, text=True,
                         stdin=subprocess.DEVNULL, timeout=120)
    if check and done.returncode:
        error = ValueError(f'Git {args[0]} failed (exit {done.returncode}); check access, network, disk space and repository state')
        if args[0] in ('push', 'fetch', 'ls-remote'):
            error.git_detail = done.stderr or f'exit {done.returncode}'
        raise error
    return done


@contextmanager
def locked(workspace, task):
    folder = isolation.mkdir(Path(workspace) / 'tasks' / '.locks', mode=0o755)
    path = folder / hashlib.sha256(str(task).encode()).hexdigest()[:2]
    with path.open('a') as handle:
        isolation.chown(path)
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def setup_environment(env):
    keys = {'PATH', 'HOME', 'SHELL', 'LANG', 'TZ', 'TMPDIR', 'TMP', 'TEMP', 'VIRTUAL_ENV', 'CONDA_PREFIX',
            'PYENV_ROOT', 'CARGO_HOME', 'RUSTUP_HOME', 'GOPATH', 'JAVA_HOME'}
    return {key: value for key, value in env.items() if key in keys or key.startswith('LC_')}


def setup(path, command, env):
    if command:
        with isolation.popen(['/bin/sh', '-c', command], cwd=path, env=setup_environment(env),
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL,
                             start_new_session=True) as process:
            try:
                code = process.wait(timeout=600)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
                raise ValueError('Worktree setup timed out; run setup again') from None
        if code:
            raise ValueError(f'Worktree created but setup failed (exit {code}); run setup again; setup has no credentials, so network authentication must be configured outside setup')


def metadata(client, name):
    try:
        reply = client.get('runners/me/repositories')
    except APIError as exc:
        if exc.status != 404:
            raise
        reply = {'repositories': []}
    repos = reply['repositories']
    if reply.get('configured') is False or not repos and reply.get('configured') is not True:
        git_repository.address(name)
        return {'full_name': name, 'machine_git': True}
    repo = next((r for r in repos if r['full_name'].lower() == name.lower() and r.get('access') == 'write'), None)
    if not repo:
        raise ValueError('This bot needs write access to this repository')
    return repo


def _detail(link):
    value = link.get('detail_json') or '{}'
    return json.loads(value) if isinstance(value, str) else dict(value)


def _patch_link(client, task, link_id, **fields):
    return client.patch(f'tasks/{task}/links/{link_id}', fields)


def _common_dir(path, env):
    return Path(git(path, 'rev-parse', '--path-format=absolute', '--git-common-dir', env=env).stdout.strip()).resolve()


def _base_identity(common_dir):
    # Keep the runner's filesystem path local; the service needs only a stable comparison token.
    return hashlib.sha256(str(Path(common_dir).resolve()).encode('utf-8')).hexdigest()


def _marker_path(workspace, link_id, *, create=False):
    if not isinstance(link_id, str) or not link_id or len(link_id) > 200:
        raise ValueError('Worktree initialization has an invalid link identity; left as it is')
    root = Path(workspace).resolve()
    tasks = root / 'tasks'
    folder = tasks / '.checkout-state'
    if tasks.is_symlink() or folder.is_symlink():
        raise ValueError('Worktree initialization state points through a symlink; left as it is')
    if create:
        isolation.mkdir(folder, mode=0o700)
    if not folder.exists():
        return None
    marker = folder / (hashlib.sha256(link_id.encode()).hexdigest() + '.json')
    if marker.is_symlink():
        raise ValueError('Worktree initialization marker is a symlink; left as it is')
    return marker


def _read_marker(workspace, link_id):
    marker = _marker_path(workspace, link_id)
    if marker is None or not marker.exists():
        return None
    if not marker.is_file():
        raise ValueError('Worktree initialization marker is not a regular file; left as it is')
    try:
        value = json.loads(marker.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        raise ValueError('Worktree initialization marker cannot be read; left as it is') from None
    if not isinstance(value, dict) or value.get('schema') != 1:
        raise ValueError('Worktree initialization marker is invalid; left as it is')
    return value


def _write_marker(workspace, value):
    marker = _marker_path(workspace, value.get('link_id'), create=True)
    if marker is None:
        raise ValueError('Worktree initialization marker directory is unavailable')
    temp = marker.with_name(marker.name + '.' + uuid.uuid4().hex + '.tmp')
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0)
    try:
        fd = os.open(temp, flags, 0o600)
        with os.fdopen(fd, 'w', encoding='utf-8') as handle:
            json.dump(value, handle, sort_keys=True, separators=(',', ':'))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, marker)
    except OSError:
        try:
            temp.unlink(missing_ok=True)
        except OSError:
            pass
        raise ValueError('Worktree initialization marker could not be committed atomically') from None


def _marker_identity(link_id, task, relative_path, repo, branch, expected_head, checkout_target, expected_base):
    return {'schema': 1, 'link_id': link_id, 'task_id': task, 'path': relative_path,
            'repo': repo.get('full_name') or repo.get('repo'), 'branch': branch,
            'expected_head': expected_head, 'checkout_target': checkout_target,
            'expected_base': expected_base}


def _canonical_task_id(task, marker=None):
    recorded = marker.get('task_id') if isinstance(marker, dict) else None
    if isinstance(recorded, str) and (recorded == task or
                                      isinstance(task, str) and len(task) >= 8 and recorded.startswith(task)):
        return recorded
    current = os.environ.get('HUB_TASK_ID')
    if isinstance(current, str) and isinstance(task, str) and len(task) >= 8 and current.startswith(task):
        return current
    return task


def _check_marker(marker, expected):
    if not marker:
        raise ValueError('Worktree has no local initialization proof; kept unchanged for inspection')
    if any(marker.get(key) != value for key, value in expected.items()):
        raise ValueError('Worktree initialization marker does not match this task registration; kept unchanged')


def _verify_ready_registration(workspace, row, repo, task=None):
    link_id = row.get('id') or row.get('link_id')
    detail = _detail(row)
    expected_head = row.get('expected_head') or detail.get('expected_head')
    checkout_target = row.get('checkout_target') or detail.get('checkout_target')
    expected_base = row.get('expected_base') or detail.get('expected_base')
    marker = _read_marker(workspace, link_id)
    task_id = row.get('task_id') or _canonical_task_id(task, marker)
    if not all((link_id, task_id, row.get('path'), row.get('branch'), expected_head,
                checkout_target, expected_base)):
        raise ValueError('Ready worktree has no complete registration proof; kept unverified')
    identity = _marker_identity(link_id, task_id, row['path'], repo, row['branch'],
                                expected_head, checkout_target, expected_base)
    _check_marker(marker, identity)
    if marker.get('phase') != 'ready':
        raise ValueError('Ready worktree has no completed local setup record; kept unverified')


def _verify_worktree(path, workspace, repo, branch, env, expected_base=None):
    if not (path / '.git').is_file():
        raise ValueError('Tracked path is not a Git worktree; left as it is')
    origin = git(path, 'config', '--get', 'remote.origin.url', env=env).stdout.strip()
    matches = git_repository.matches(origin, repo.get('full_name') or repo['repo']) if repo.get('machine_git') else git_credentials._same_repository(origin, repo.get('full_name') or repo['repo'])
    if not matches:
        raise ValueError('Worktree repository does not match its task link; left as it is')
    if git(path, 'symbolic-ref', '--short', 'HEAD', env=env).stdout.strip() != branch:
        raise ValueError('Worktree branch does not match its task link; left as it is')
    common = _common_dir(path, env)
    if Path(workspace).resolve() not in common.parents:
        raise ValueError('Worktree base must be inside the team workspace')
    if expected_base and _base_identity(common) != expected_base:
        raise ValueError('Worktree is attached to a different managed base clone; left as it is')
    return common.parent


def _porcelain_records(path, env):
    done = isolation.run([*safe_git.prefix(path), '-C', str(path), 'status', '--porcelain=v1', '-z', '--untracked-files=all'],
                         env=safe_git.environment(env), capture_output=True, text=True,
                         stdin=subprocess.DEVNULL, timeout=120)
    if done.returncode:
        raise ValueError('Could not verify checkout contents; left as they are')
    raw = done.stdout
    return [record for record in raw.split('\0') if record]


def _resume_interrupted_checkout(path, workspace, repo, branch, expected_head, expected_base,
                                 marker_identity, env):
    """Accept only a complete clean materialization; never restore or overwrite its contents."""
    if not re.fullmatch(r'(?:[0-9a-f]{40}|[0-9a-f]{64})', expected_head or ''):
        raise ValueError('Interrupted checkout has no valid expected commit; left as it is')
    _verify_worktree(path, workspace, repo, branch, env, expected_base)
    if git(path, 'rev-parse', 'HEAD', env=env).stdout.strip() != expected_head:
        raise ValueError('Interrupted checkout moved from its recorded commit; left as it is')
    marker = _read_marker(workspace, marker_identity['link_id'])
    _check_marker(marker, marker_identity)
    if marker.get('phase') not in ('materializing', 'checkout_complete'):
        raise ValueError('Worktree is not in a resumable checkout phase; kept unchanged')
    records = _porcelain_records(path, env)
    if records:
        raise ValueError('Checkout contents changed or are incomplete; kept worktree without restoring files')
    if marker.get('phase') == 'materializing':
        _write_marker(workspace, {**marker, 'phase': 'checkout_complete'})


def _checkout_target(base, branch, default, env):
    local = 'refs/heads/' + branch
    remote = 'refs/remotes/origin/' + default
    target = local if git(base, 'show-ref', '--verify', local, env=env, check=False).returncode == 0 else remote
    return target, git(base, 'rev-parse', '--verify', target, env=env).stdout.strip()


def _worktree_registered(base, path, env):
    expected = Path(path).resolve()
    for line in git(base, 'worktree', 'list', '--porcelain', env=env).stdout.splitlines():
        if line.startswith('worktree ') and Path(line[9:]).resolve() == expected:
            return True
    return False


def _create_or_resume_checkout(path, workspace, base, default, link, repo, task, env, client):
    detail = _detail(link)
    state = link.get('checkout_state') or detail.get('checkout_state') or 'queued'
    fresh_queued = state == 'queued'
    expected = link.get('expected_head') or detail.get('expected_head')
    target = link.get('checkout_target') or detail.get('checkout_target')
    expected_base = link.get('expected_base') or detail.get('expected_base')
    branch = checked_branch(link['branch'])
    actual_base = _base_identity(_common_dir(base, env))
    if state == 'initializing':
        if not expected or not target or not expected_base:
            raise ValueError('Initialization is missing its registered commit or base; kept worktree unchanged')
        if expected_base != actual_base:
            raise ValueError('Managed base clone changed since initialization; kept worktree unchanged')
        if target not in ('refs/heads/' + branch, 'refs/remotes/origin/' + default):
            raise ValueError('Interrupted checkout target does not match its task; left as it is')
        target_head = git(base, 'rev-parse', '--verify', target, env=env, check=False)
        if target_head.returncode or target_head.stdout.strip() != expected:
            raise ValueError('Task branch changed since checkout initialization; left as it is')
    elif state == 'queued':
        target, expected = _checkout_target(base, branch, default, env)
        expected_base = actual_base
        _patch_link(client, task, link['link_id'], state='pending', setup_pending=True,
                    checkout_state='initializing', expected_head=expected, checkout_target=target,
                    expected_base=expected_base)
        state = 'initializing'
    else:
        raise ValueError('Checkout is not in a resumable initialization state; leave it unchanged')

    marker = _read_marker(workspace, link['link_id'])
    identity = _marker_identity(link['link_id'], _canonical_task_id(task, marker), link['path'], repo, branch,
                                expected, target, expected_base)
    if path.exists():
        _resume_interrupted_checkout(path, workspace, repo, branch, expected, expected_base,
                                     identity, env)
    else:
        marker = _read_marker(workspace, link['link_id'])
        if marker:
            registered = _worktree_registered(base, path, env)
            if fresh_queued and not registered:
                # An explicit new add can reuse a removed link only when its worktree path is absent
                # and Git no longer registers it. Keep the same task/repo/branch/base identity.
                stable = {key: identity[key] for key in ('link_id', 'task_id', 'path', 'repo', 'branch', 'expected_base')}
                _check_marker(marker, stable)
                marker = {**identity, 'phase': 'planned'}
                _write_marker(workspace, marker)
            else:
                _check_marker(marker, identity)
                if marker.get('phase') == 'materializing':
                    if registered:
                        raise ValueError('Git still registers the missing interrupted worktree; preserved for bounded recovery')
                    marker = {**marker, 'phase': 'planned'}
                    _write_marker(workspace, marker)
                elif marker.get('phase') == 'planned' and registered:
                    raise ValueError('Git registers a worktree without a materialized path; preserved for bounded recovery')
                elif marker.get('phase') != 'planned':
                    raise ValueError('A previously materialized worktree is missing; preserved for bounded recovery')
        else:
            if state != 'initializing' or _worktree_registered(base, path, env):
                raise ValueError('Worktree is missing without safe initialization proof; preserved for recovery')
            _write_marker(workspace, {**identity, 'phase': 'planned'})
        isolation.mkdir(path.parent, mode=0o755)
        _write_marker(workspace, {**identity, 'phase': 'materializing'})
        if target == 'refs/heads/' + branch:
            git(base, 'worktree', 'add', str(path), branch, env=env)
        else:
            git(base, 'worktree', 'add', '--no-track', '-b', branch, str(path), target, env=env)
        _resume_interrupted_checkout(path, workspace, repo, branch, expected, expected_base,
                                     identity, env)
    _patch_link(client, task, link['link_id'], state='pending', setup_pending=True,
                checkout_state='checkout_ready', expected_head=expected, checkout_target=target,
                expected_base=expected_base)
    return expected, target


def _link_value(link, key):
    return link.get(key) if link.get(key) is not None else _detail(link).get(key)


def _run_registered_setup(path, workspace, repo, branch, link, task, env, client):
    link_id = link.get('link_id') or link.get('id')
    state = _link_value(link, 'checkout_state')
    expected = _link_value(link, 'expected_head')
    target = _link_value(link, 'checkout_target')
    expected_base = _link_value(link, 'expected_base')
    if state not in ('checkout_ready', 'setup_failed', 'setup_running'):
        raise ValueError('Legacy or unknown worktree initialization is unverified; setup was not run')
    if not expected or not target or not expected_base:
        raise ValueError('Worktree setup lacks recorded commit and base identity; kept unchanged')
    marker = _read_marker(workspace, link_id)
    identity = _marker_identity(link_id, _canonical_task_id(task, marker), link['path'], repo, branch,
                                expected, target, expected_base)
    _check_marker(marker, identity)
    phase = marker.get('phase')
    if phase not in ('checkout_complete', 'restored', 'setup_failed', 'setup_running', 'ready'):
        raise ValueError('Worktree setup has no completed checkout proof; kept unchanged')
    _verify_worktree(path, workspace, repo, branch, env, expected_base)
    if git(path, 'rev-parse', 'HEAD', env=env).stdout.strip() != expected:
        raise ValueError('Worktree moved from its recorded setup commit; kept unchanged')
    if state == 'checkout_ready' and phase == 'checkout_complete' and _porcelain_records(path, env):
        raise ValueError('Checkout changed before setup; kept worktree without running setup')
    if phase == 'ready' and state == 'setup_running':
        return _patch_link(client, task, link_id, state='present', setup_pending=False,
                           checkout_state='ready', expected_head=expected, checkout_target=target,
                           expected_base=expected_base)

    _write_marker(workspace, {**marker, 'phase': 'setup_running'})
    _patch_link(client, task, link_id, state='pending', setup_pending=True,
                checkout_state='setup_running', expected_head=expected, checkout_target=target,
                expected_base=expected_base)
    try:
        setup(path, repo.get('setup_command'), env)
    except (ValueError, OSError, subprocess.SubprocessError):
        _write_marker(workspace, {**marker, 'phase': 'setup_failed'})
        _patch_link(client, task, link_id, state='pending', setup_pending=True,
                    checkout_state='setup_failed', expected_head=expected, checkout_target=target,
                    expected_base=expected_base)
        raise
    _write_marker(workspace, {**marker, 'phase': 'ready'})
    return _patch_link(client, task, link_id, state='present', setup_pending=False,
                       checkout_state='ready', expected_head=expected, checkout_target=target,
                       expected_base=expected_base)


def _record_restored_checkout(workspace, row, repo, env):
    path = safe_path(workspace, row['path'])
    base, _ = repositories.worktree_base(workspace, repo, env)
    expected_base = _base_identity(_common_dir(base, env))
    branch = checked_branch(row['branch'])
    _verify_worktree(path, workspace, repo, branch, env, expected_base)
    expected = git(path, 'rev-parse', 'HEAD', env=env).stdout.strip()
    target = 'refs/heads/' + branch
    if git(base, 'rev-parse', '--verify', target, env=env, check=False).returncode:
        raise ValueError('Restored task branch is missing from its managed base; kept worktree')
    identity = _marker_identity(row['id'], row['task_id'], row['path'], repo,
                                branch, expected, target, expected_base)
    _write_marker(workspace, {**identity, 'phase': 'restored'})
    return expected, target, expected_base


def _record_attached_checkout(workspace, link, repo, env):
    path = safe_path(workspace, link['path'])
    branch = checked_branch(link['branch'])
    common = _common_dir(path, env)
    expected_base = _base_identity(common)
    _verify_worktree(path, workspace, repo, branch, env, expected_base)
    target = 'refs/heads/' + branch
    link_id = link.get('link_id') or link.get('id')
    task_id = link.get('task_id') or _canonical_task_id(None)
    if not link_id or not task_id:
        raise ValueError('Attached worktree has no task registration identity; kept unchanged')
    if link.get('setup_pending') or _detail(link).get('setup_pending'):
        raise ValueError('Attached worktree setup is still pending; kept unchanged')
    repo_name = repo.get('full_name') or repo.get('repo')
    if not repo_name:
        raise ValueError('Attached worktree repository is not identified; kept unchanged')
    if not _worktree_registered(common.parent, path, env):
        raise ValueError('Attached path is not registered in its managed base; kept unchanged')

    marker = _read_marker(workspace, link_id)
    if marker is not None:
        # A prior local proof may outlive a lost cloud heartbeat. Match its registration and
        # base, then replay that proof; a later commit or edit must not rewrite the marker.
        stable = {'schema': 1, 'link_id': link_id, 'task_id': task_id,
                  'path': link['path'], 'repo': repo_name, 'branch': branch,
                  'expected_base': expected_base}
        _check_marker(marker, stable)
        expected = marker.get('expected_head')
        recorded_target = marker.get('checkout_target')
        target_valid = recorded_target == target
        if (not target_valid and isinstance(recorded_target, str)
                and recorded_target.startswith('refs/remotes/origin/')):
            remote_branch = recorded_target.removeprefix('refs/remotes/origin/')
            try:
                target_valid = checked_branch(remote_branch) == remote_branch
            except ValueError:
                target_valid = False
        if (marker.get('phase') != 'ready'
                or not re.fullmatch(r'(?:[0-9a-f]{40}|[0-9a-f]{64})', expected or '')
                or not target_valid):
            raise ValueError('Attached worktree has no matching completed local proof; kept unchanged')
        return expected, recorded_target, expected_base

    # An explicit attachment is not enough to distinguish an unfinished materialization
    # from a user deletion. Refuse to certify it, but never restore or modify the path.
    if git(path, 'ls-files', '--deleted', '-z', env=env).stdout:
        raise ValueError('Attached worktree has missing tracked files; kept unchanged and unverified')
    expected = git(path, 'rev-parse', 'HEAD', env=env).stdout.strip()
    if git(path, 'rev-parse', '--verify', target, env=env).stdout.strip() != expected:
        raise ValueError('Attached worktree branch does not point at its current commit; kept unchanged')
    identity = _marker_identity(link_id, task_id, link['path'], repo, branch,
                                expected, target, expected_base)
    _write_marker(workspace, {**identity, 'phase': 'ready'})
    return expected, target, expected_base


def command(client, operation, value, task=None):
    workspace = os.environ.get('HUB_WORKSPACE')
    task = task or os.environ.get('HUB_TASK_ID')
    if not workspace or not os.environ.get('HUB_BOT') or not task:
        raise ValueError('Task worktrees need a bot run and a task; use --task when this run has no current task')
    env = safe_git.environment()
    try:
        if operation == 'setup':
            repo = metadata(client, value)
            rows = client.get(f'tasks/{task}/links')
            rows = rows.get('links', []) if isinstance(rows, dict) else rows
            candidates = [r for r in rows if r['kind'] == 'worktree' and (r.get('repo') or '').lower() == value.lower()
                          and r.get('state') in ('present', 'pending')]
            candidates.sort(key=lambda r: not (_detail(r).get('setup_pending') or _detail(r).get('checkout_state') in
                                                ('checkout_ready', 'setup_failed', 'setup_running', 'initializing')))
            row = candidates[0] if candidates else None
            if not row:
                raise ValueError('No worktree needing setup for this repository on the task')
            path = safe_path(workspace, row['path'])
            with locked(workspace, row['path']):
                detail = _detail(row)
                state = detail.get('checkout_state')
                if state == 'initializing':
                    base, default = repositories.worktree_base(workspace, repo, env)
                    expected, target = _create_or_resume_checkout(
                        path, workspace, base, default, {**row, **detail, 'link_id': row['id']},
                        repo, task, env, client)
                    detail.update(checkout_state='checkout_ready', expected_head=expected,
                                  checkout_target=target)
                    detail['expected_base'] = detail.get('expected_base') or _base_identity(_common_dir(base, env))
                if not path.exists():
                    raise ValueError('Worktree is missing; restore it before setup')
                if state == 'ready':
                    _verify_worktree(path, workspace, repo, row['branch'], env,
                                     detail.get('expected_base'))
                    _verify_ready_registration(workspace, row, repo, task)
                    return row
                link = {**row, **detail, 'link_id': row['id']}
                return _run_registered_setup(path, workspace, repo, row['branch'], link, task, env, client)
        if operation == 'add':
            disk_floor(workspace)
            repo = metadata(client, value)
            if repo.get('machine_git'):
                env = safe_git.machine_environment()
            link = client.post(f'tasks/{task}/worktrees', {'repo': repo['full_name']})
            with locked(workspace, link['path']):
                checked_branch(link['branch'])
                path = safe_path(workspace, link['path'])
                detail = _detail(link)
                state = link.get('checkout_state') or detail.get('checkout_state')
                setup_pending = bool(link.get('setup_pending', detail.get('setup_pending', False)))
                if state is None and path.exists():
                    _verify_worktree(path, workspace, repo, link['branch'], env)
                    return {**link, 'state': 'pending', 'checkout_state': 'unverified',
                            'setup_pending': True, 'workspace_path': str(path),
                            'warning': 'Legacy worktree has no completion proof; kept unchanged and not reported ready'}
                if state is None:
                    state = 'queued'
                if path.exists():
                    if state == 'queued':
                        raise ValueError('Worktree path already exists without an initialization record; kept unchanged')
                    base, default = repositories.worktree_base(workspace, repo, env)
                    expected_base = link.get('expected_base') or detail.get('expected_base')
                    if state == 'initializing':
                        _, target = _create_or_resume_checkout(
                            path, workspace, base, default, {**link, **detail}, repo, task, env, client)
                        state = 'checkout_ready'
                        expected_base = expected_base or _base_identity(_common_dir(base, env))
                        detail.update(checkout_state=state, checkout_target=target,
                                      expected_base=expected_base)
                    elif state == 'checkout_ready':
                        expected = link.get('expected_head') or detail.get('expected_head')
                        target = link.get('checkout_target') or detail.get('checkout_target')
                        if not expected_base or not expected or not target:
                            raise ValueError('Checkout lacks recorded base and commit identity; kept unchanged')
                        identity = _marker_identity(link['link_id'], task, link['path'], repo,
                                                    link['branch'], expected, target, expected_base)
                        _resume_interrupted_checkout(path, workspace, repo, link['branch'],
                                                     expected, expected_base, identity, env)
                    elif state in ('setup_running', 'setup_failed'):
                        raise ValueError('Setup did not finish; use hub task worktree setup to retry explicitly')
                    elif state == 'ready':
                        if not expected_base:
                            raise ValueError('Ready worktree has no recorded base identity; kept unchanged')
                        _verify_worktree(path, workspace, repo, link['branch'], env, expected_base)
                        _verify_ready_registration(workspace, link, repo, task)
                        return {**link, 'state': 'present', 'checkout_state': 'ready',
                                'workspace_path': str(path)}
                    else:
                        raise ValueError('Worktree initialization is not verified; kept it unchanged')
                else:
                    base, default = repositories.worktree_base(workspace, repo, env)
                    if state not in ('queued', 'initializing'):
                        raise ValueError('Missing worktree is not in a resumable initialization state')
                    expected, target = _create_or_resume_checkout(
                        path, workspace, base, default, {**link, 'checkout_state': state},
                        repo, task, env, client)
                    state = 'checkout_ready'
                    setup_pending = True
                    expected_base = _base_identity(_common_dir(base, env))
                    detail.update(checkout_state=state, expected_head=expected,
                                  checkout_target=target, expected_base=expected_base)
                if state == 'checkout_ready' or setup_pending:
                    _run_registered_setup(path, workspace, repo, link['branch'],
                                          {**link, **detail, 'link_id': link['link_id'],
                                           'checkout_state': state, 'expected_base': expected_base},
                                          task, env, client)
                elif state != 'ready':
                    raise ValueError('Worktree is not ready; no initialization or setup completion was recorded')
                return {**link, 'state': 'present', 'checkout_state': 'ready', 'setup_pending': False,
                        'workspace_path': str(path),
                        **({'warning': repo['fetch_warning']} if repo.get('fetch_warning') else {})}
        else:
            raw = Path(value)
            relative = str(raw.relative_to(Path(workspace).resolve())) if raw.is_absolute() else value
            path = safe_path(workspace, relative)
            relative = str(path.relative_to(Path(workspace).resolve()))
            if not (path / '.git').is_file():
                raise ValueError('Attach needs a Git worktree, not a base clone')
            attach_env = safe_git.machine_environment()
            common = Path(git(path, 'rev-parse', '--path-format=absolute', '--git-common-dir', env=attach_env).stdout.strip()).resolve()
            if Path(workspace).resolve() not in common.parents:
                raise ValueError('Worktree base must be inside the team workspace')
            origin = git(path, 'config', '--get', 'remote.origin.url', env=attach_env).stdout.strip()
            repo_name = git_credentials.repository_name(origin) or origin
            repo = metadata(client, repo_name or '')
            if repo.get('machine_git'):
                env = safe_git.machine_environment()
            else:
                env = safe_git.environment()
            branch = checked_branch(git(path, 'symbolic-ref', '--short', 'HEAD', env=env).stdout.strip())
            link = client.post(f'tasks/{task}/worktrees/attach', {'path': relative, 'repo': repo['full_name'], 'branch': branch})
            expected_head, checkout_target, expected_base = _record_attached_checkout(
                workspace, {**link, 'task_id': task}, repo, env)
        client.patch(f'tasks/{task}/links/{link["link_id"]}', {'state': 'present', 'path': link['path'],
                     'setup_pending': False, 'checkout_state': 'ready', 'expected_head': expected_head,
                     'checkout_target': checkout_target, 'expected_base': expected_base})
        return {**link, 'workspace_path': str(path), **({'warning': repo['fetch_warning']} if repo.get('fetch_warning') else {})}
    except (ValueError, OSError, subprocess.SubprocessError):
        if operation == 'add' and 'link' in locals() and ('path' not in locals() or not (path / '.git').is_file()):
            client.patch(f'tasks/{task}/links/{link["link_id"]}', {'state': 'pending'})
        raise
    except APIError as exc:
        if exc.status in (404, 405):
            raise ValueError('The server is too old for task worktrees; update Tico') from exc
        raise


def checked_branch(branch):
    if not isinstance(branch, str) or not branch or len(branch) > 200 or branch.startswith('-'):
        raise ValueError('Invalid or oversized worktree branch')
    if isolation.run([*safe_git.PREFIX, 'check-ref-format', '--branch', branch], cwd='/', env=safe_git.process_environment(), capture_output=True, timeout=15).returncode:
        raise ValueError('Invalid worktree branch')
    return branch


def wip_branch(row):
    return 'wip/' + row['task_id'][:8] + '-' + (row.get('id') or row['link_id'])[:6]


def remote_branch(base, branch, env):
    checked_branch(branch)
    listed = git(base, 'ls-remote', '--exit-code', 'origin', 'refs/heads/' + branch, env=env, check=False)
    if listed.returncode == 2:
        return None
    if listed.returncode:
        error = ValueError('Git fetch failed; cannot determine remote branch, retry later')
        error.git_detail = listed.stderr or f'exit {listed.returncode}'
        raise error
    git(base, 'fetch', '--no-tags', 'origin', f'+refs/heads/{branch}:refs/remotes/origin/{branch}', env=env)
    return 'refs/remotes/origin/' + branch


def fast_forward(path, branch, target, env):
    ref = 'refs/heads/' + checked_branch(branch)
    old = git(path, 'rev-parse', '--verify', ref, env=env, check=False)
    head = git(path, 'rev-parse', target, env=env).stdout.strip()
    if old.returncode == 0:
        previous = old.stdout.strip()
        if git(path, 'merge-base', '--is-ancestor', previous, head, env=env, check=False).returncode:
            raise ValueError('Task branch has separate history; kept worktree')
        if 'branch ' + ref in git(path, 'worktree', 'list', '--porcelain', env=env).stdout.splitlines():
            raise ValueError('Task branch is checked out elsewhere; kept worktree')
    else:
        previous = '0' * len(head)
    # A concurrent branch update must fail rather than discard its new commits.
    git(path, 'update-ref', ref, head, previous, env=env)


def inspect(workspace, row, env=None, cache=None):
    result = {'link_id': str(row['id'])[:100], 'state': 'unknown', 'branch': None}
    env = safe_git.environment(env)
    try:
        path = safe_path(workspace, row['path'])
        detail = _detail(row)
        checkout_state = detail.get('checkout_state')
        not_ready = checkout_state != 'ready' or bool(detail.get('setup_pending'))
        result['checkout_state'] = checkout_state or 'unverified'
        expected_base = detail.get('expected_base')
        expected_head = detail.get('expected_head')
        checkout_target = detail.get('checkout_target')
        if expected_head is not None:
            result['expected_head'] = expected_head
        if checkout_target is not None:
            result['checkout_target'] = checkout_target
        if expected_base is not None:
            result['expected_base'] = expected_base
        if not path.exists():
            result['state'] = 'removed' if row['state'] == 'removed' else ('pending' if not_ready else 'missing')
            return result
        if not (path / '.git').is_file():
            raise ValueError('Tracked path is not a Git worktree; left as it is')
        origin = git(path, 'config', '--get', 'remote.origin.url', env=env).stdout.strip()
        actual_repo = git_credentials.repository_name(origin) or origin
        result['repo'] = row.get('repo') or actual_repo
        if checkout_state == 'attached_pending':
            expected_repo = row.get('repo')
            if expected_repo:
                matches = git_repository.matches(origin, expected_repo) if row.get('machine_git') else git_credentials._same_repository(origin, expected_repo)
                if not matches:
                    raise ValueError('Attached worktree repository does not match its task link')
        if result['repo'] and (len(result['repo']) > 200 or not repositories.valid_repository(result['repo'])):
            result['repo'] = None
            result['error'] = 'Invalid or oversized repository name'
        branch = git(path, 'symbolic-ref', '--short', 'HEAD', env=env).stdout.strip()
        common = _common_dir(path, env)
        if expected_base and _base_identity(common) != expected_base:
            raise ValueError('Worktree is attached to a different managed base clone')
        if checkout_state == 'ready' and (not expected_base or not expected_head or not checkout_target):
            not_ready = True
            result['checkout_state'] = 'unverified'
        elif checkout_state == 'ready':
            _verify_ready_registration(workspace, {**row, **detail},
                                       {'full_name': result['repo']})
        result.update(state='pending' if not_ready else 'present',
                      dirty_files=len(git(path, 'status', '--porcelain', env=env).stdout.splitlines()),
                      last_commit=git(path, 'rev-parse', 'HEAD', env=env).stdout.strip()[:100])
        try:
            result['branch'] = checked_branch(branch)
        except ValueError:
            result['error'] = 'Invalid or oversized worktree branch'
        if checkout_state == 'attached_pending':
            if result.get('error'):
                raise ValueError('Attached worktree metadata is invalid; left as it is')
            if row.get('branch') and row['branch'] != result['branch']:
                raise ValueError('Attached worktree branch does not match its task link')
            expected_head, checkout_target, expected_base = _record_attached_checkout(
                workspace, {**row, 'link_id': row['id'], 'branch': result['branch']},
                {'full_name': result['repo'], 'machine_git': row.get('machine_git')}, env)
            result.update(checkout_state='ready',
                          expected_head=expected_head,
                          checkout_target=checkout_target,
                          expected_base=expected_base)
            checkout_state = 'ready'
            not_ready = False
            result['state'] = 'present'
        task_branch = row.get('branch')
        remote = 'refs/remotes/origin/' + task_branch if task_branch else ''
        if remote and git(path, 'show-ref', '--verify', remote, env=env, check=False).returncode == 0:
            counts = git(path, 'rev-list', '--left-right', '--count', 'HEAD...' + remote, env=env).stdout
            result['ahead'], result['behind'] = map(int, counts.split())
        else:
            result['ahead'] = int(git(path, 'rev-list', '--count', 'HEAD', '--not', '--remotes=origin', env=env).stdout)
            result['behind'] = 0
        # File statistics are expensive on dependency folders; refresh at most every ten minutes.
        cached = (cache or {}).get(row['id'])
        if not cached or time.monotonic() - cached[0] >= 600:
            size, activity = 0, 0
            for root, dirs, files in os.walk(path, followlinks=False):
                dirs[:] = [d for d in dirs if not (Path(root) / d).is_symlink()]
                for filename in files:
                    file = Path(root) / filename
                    if not file.is_symlink():
                        stat = file.stat()
                        size += stat.st_size
                        activity = max(activity, stat.st_mtime)
            cached = (time.monotonic(), round(size / 1024 ** 2, 1), activity)
            if cache is not None:
                cache[row['id']] = cached
        result['size_mb'], result['last_activity'] = cached[1:]
    except (ValueError, OSError, subprocess.SubprocessError):
        result['error'] = 'Could not inspect worktree; check disk space, Git state and workspace permissions'
    return result


_BUILD = {'node_modules', '.venv', 'venv', 'dist', 'build', 'target', '.next', '__pycache__', '.cache', 'coverage', '.pytest_cache', '.mypy_cache', '.ruff_cache', '.tox', '.gradle', '.turbo', '.parcel-cache', '.DS_Store', '.idea', '.vscode', 'out'}
def build_file(name):
    parts = Path(name).parts
    return any(part in _BUILD or part.endswith(('.egg-info', '.pyc')) for part in parts) or any(parts[i:i + 2] in (('.terraform', 'providers'), ('.terraform', 'plugin-cache')) for i in range(len(parts) - 1))


_SECRET = ('.env*', '*.pem', '*.key', 'id_rsa*', 'credentials*', '*.p12')


def _act(workspace, row, action, env, vault_values=(), before_remove=lambda: True):
    env = safe_git.environment(env)
    path = safe_path(workspace, row['path'])
    if action == 'remove':
        if not before_remove():
            raise Deferred('Task reopened or bot is running; retry cleanup later')
        if not path.exists():
            # Prune even when the folder was removed outside Tico.
            if not row.get('repo'):
                return 'removed'
            base = Path(workspace) / 'repos' / repositories.base_folder(row['repo'])
            if (base.parent.is_symlink() or base.is_symlink() or (base / '.git').is_symlink()
                    or base.resolve().parent != base.parent.resolve()):
                raise ValueError('Worktree base points outside repos')
            if base.exists():
                git(base, 'worktree', 'prune', '--expire', 'now', env=env)
            return 'removed'
        if not row.get('repo'):
            raise ValueError('Repository not identified; kept worktree')
        branch = checked_branch(row['branch'])
        if not (path / '.git').is_file():
            raise ValueError('Tracked path is not a worktree; left as it is')
        common = Path(git(path, 'rev-parse', '--path-format=absolute', '--git-common-dir', env=env).stdout.strip()).resolve()
        if Path(workspace).resolve() not in common.parents:
            raise ValueError('Worktree base is outside the team workspace')
        base = common.parent
        default = row.get('default_branch')
        origin_head = git(base, 'symbolic-ref', '--short', 'refs/remotes/origin/HEAD', env=env, check=False).stdout.strip().removeprefix('origin/')
        defaults = {name for name in (default, origin_head) if name}
        if not defaults:
            raise ValueError('Repository default branch is unknown; refresh Repositories before cleanup')
        git(base, 'worktree', 'prune', '--expire', 'now', env=env)
        ignored = git(path, 'ls-files', '--others', '--ignored', '--exclude-standard', '-z', env=env).stdout.split('\0')
        if any(name and not build_file(name) for name in ignored):
            raise ValueError('kept: ignored files')
        dirty = git(path, 'status', '--porcelain', env=env).stdout.strip()
        wip = wip_branch(row)
        if wip in defaults:
            raise ValueError('Snapshot branch matches the default branch; kept worktree')
        current = git(path, 'symbolic-ref', '--short', 'HEAD', env=env, check=False).stdout.strip()
        if dirty:
            names = (git(path, 'diff', 'HEAD', '--name-only', '-z', env=env).stdout
                     + git(path, 'ls-files', '--others', '--exclude-standard', '-z', env=env).stdout).split('\0')
            skipped = [name for name in names if name and (any(fnmatch.fnmatch(Path(name).name.lower(), pat) for pat in _SECRET)
                       or (path / name).is_symlink() or ((path / name).exists() and (path / name).stat().st_size > 20 * 1024 ** 2))]
            if skipped:
                row['skipped_files'] = [name[:1000] for name in skipped[:100]]
                raise ValueError('kept: skipped unsafe or oversized files: ' + ', '.join(skipped)[:150])
            # Divergent HEAD must never replace the task branch, even after a successful snapshot push.
            exists = git(path, 'show-ref', '--verify', 'refs/heads/' + branch, env=env, check=False).returncode == 0
            if exists and git(path, 'merge-base', '--is-ancestor', branch, 'HEAD', env=env, check=False).returncode:
                raise ValueError('Task branch has separate history; kept worktree to preserve unpushed commits')
            from . import redact
            patch = git(path, 'diff', '--no-ext-diff', '--no-textconv', 'HEAD', env=env).stdout
            patch += ''.join((path / name).read_text(errors='replace') for name in names if name and (path / name).is_file())
            known = redact.for_turn(env, vault_values)
            possible_secret = re.search(r'-----BEGIN (?:[A-Z ]+ )?PRIVATE KEY-----|(?:gh[pousr]_|github_pat_)[A-Za-z0-9_]{20,}|AKIA[0-9A-Z]{16}|sk-(?:proj-|ant-)?[A-Za-z0-9_-]{24,}', patch)
            if redact.scrub_log(patch) != patch or known and known.holds(patch.encode()) or possible_secret:
                raise ValueError('Possible secret in unsaved changes; kept worktree')
            if current != wip:
                if git(path, 'show-ref', '--verify', 'refs/heads/' + wip, env=env, check=False).returncode == 0:
                    if git(path, 'merge-base', '--is-ancestor', wip, 'HEAD', env=env, check=False).returncode:
                        raise ValueError('Saved work has separate history; kept worktree')
                git(path, 'switch', '-C', wip, env=env)
            git(path, 'add', '--all', env=env)
            git(path, '-c', 'user.name=Tico', '-c', 'user.email=bot@example.com', 'commit', '-m', 'Save task work before cleanup', env=env)
            git(path, 'push', 'origin', 'HEAD:refs/heads/' + wip, env=env)
            if branch not in defaults:
                try:
                    fast_forward(path, branch, 'HEAD', env)
                finally:
                    git(path, 'switch', branch, env=env)
        else:
            # Retrying a failed snapshot push must still save its history.
            if current == wip:
                git(path, 'push', 'origin', 'HEAD:refs/heads/' + wip, env=env)
                if branch not in defaults:
                    if git(path, 'merge-base', '--is-ancestor', branch, 'HEAD', env=env, check=False).returncode:
                        raise ValueError('Task branch has separate history; kept worktree')
                    try:
                        fast_forward(path, branch, 'HEAD', env)
                    finally:
                        git(path, 'switch', branch, env=env)
            else:
                if int(git(path, 'rev-list', '--count', 'HEAD', '--not', '--remotes=origin', env=env).stdout):
                    if row.get('prs_finished') or not current:
                        git(path, 'push', 'origin', 'HEAD:refs/heads/' + wip, env=env)
                    elif current != branch or branch in defaults:
                        raise ValueError('Unpushed history on another or default branch; kept worktree')
                    else:
                        git(path, 'push', 'origin', branch + ':refs/heads/' + branch, env=env)
        if not before_remove():
            if branch not in defaults and git(path, 'symbolic-ref', '--short', 'HEAD', env=env, check=False).stdout.strip() == wip:
                git(path, 'switch', branch, env=env)
            raise Deferred('Task reopened or bot is running; retry cleanup later')
        git(base, 'worktree', 'remove', str(path), env=env)
        return 'removed'
    branch = checked_branch(row['branch'])
    disk_floor(workspace)
    if path.exists():
        if inspect(workspace, {**row, 'id': row.get('id') or row['link_id']}, env).get('state') != 'present':
            raise ValueError('Restore path already exists and is not a worktree')
        return 'present'
    base, default = repositories.worktree_base(workspace, row, env)
    if row.get('fetch_warning'):
        row['restore_source'] = row['fetch_warning']
    git(base, 'worktree', 'prune', '--expire', 'now', env=env)
    isolation.mkdir(path.parent, mode=0o755)
    local = git(base, 'show-ref', '--verify', 'refs/heads/' + branch, env=env, check=False).returncode == 0
    try:
        task_remote = remote_branch(base, branch, env)
        saved = remote_branch(base, wip_branch(row), env)
        legacy = remote_branch(base, 'wip/' + row['task_id'][:8], env) if not saved else None
        saved = saved or legacy
    except ValueError:
        if not local:
            raise
        task_remote, saved = None, None
        row['restore_source'] = 'local (remote unavailable)'
    start = branch if local else task_remote or 'origin/' + default
    if saved:
        if not (local or task_remote) or git(base, 'merge-base', '--is-ancestor', start, saved, env=env, check=False).returncode == 0:
            start = saved
        else:
            row['snapshot_skipped'] = saved
    if local:
        if start != branch:
            fast_forward(base, branch, start, env)
        git(base, 'worktree', 'add', str(path), branch, env=env)
    else:
        git(base, 'worktree', 'add', '--no-track', '-b', branch, str(path), start, env=env)
    return 'present'


class Deferred(ValueError):
    pass


def act(workspace, row, action, env, vault_values=(), before_remove=lambda: True):
    with locked(workspace, row['path']):
        try:
            return _act(workspace, row, action, env, vault_values, before_remove)
        except ValueError as exc:
            if row.get('machine_git') and hasattr(exc, 'git_detail'):
                raise ValueError(repositories.machine_error(row['repo'], exc.git_detail)) from None
            raise


class Worktrees:
    def __init__(self, workspace, client, idle=lambda: True, environment=lambda bot: safe_git.process_environment(), vault_values=lambda bot: (), retain_vault=lambda owners: None, refresh=None):
        self.workspace, self.client, self.idle = workspace, client, idle
        self.environment = environment
        self.refresh = refresh
        self.vault_values = vault_values
        self.retain_vault = retain_vault
        self.bot_locks = weakref.WeakValueDictionary()
        self.maintaining = set()
        self.retry = {}
        self.stats = {}
        self.pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        self.pending = None
        self.reports = []
        self.actions = {}
        self.errors = {}
        self.lock = threading.Lock()

    def bot_lock(self, bot):
        with self.lock:
            return self.bot_locks.setdefault(bot.removeprefix('bot:'), threading.RLock())

    def claim(self, client, assignments):
        # Coordinate selection with maintenance without adding a server field.
        with self.lock:
            if not self.maintaining:
                return client.post('jobs/claim', {'next_run': True})
            for assignment in assignments:
                bot = assignment['bot']
                if bot in self.maintaining:
                    continue
                result = client.post('jobs/claim', {'next_run': True, 'bot': bot})
                if result.get('attempt') or result.get('paused'):
                    return result
            return {'attempt': None}

    def bot_idle(self, bot):
        try:
            return self.idle(bot)
        except TypeError:
            return self.idle()

    def poll(self, actions=()):
        with self.lock:
            self.actions.update({a['link_id']: a for a in actions})
            if self.pending is None or self.pending.done():
                queued = list(self.actions.values())
                self.actions.clear()
                self.pending = self.pool.submit(self.sync, queued)
            return list(self.reports)

    def sync(self, actions):
        try:
            rows = self.client.get('runners/me/worktrees')['worktrees']
        except Exception:
            return
        self.retain_vault({r['owner'].removeprefix('bot:') for r in rows})
        failed = set()
        errors = {key: error for key, error in self.errors.items() if any(r["id"] == key for r in rows)}
        for action in actions:
            row = next((r for r in rows if r['id'] == action['link_id']), None)
            if not row or time.monotonic() < self.retry.get(row['id'], (0, 0))[0]:
                continue
            closed = row['task_status'] in ('done', 'closed', 'declined') or row['bot_state'] == 'archived' or json.loads(row.get('detail_json') or '{}').get('delete_requested')
            if action['action'] == 'remove' and not closed or action['action'] == 'restore' and closed:
                continue
            bot_lock = self.bot_lock(row['owner'])
            if not bot_lock.acquire(blocking=False):
                continue
            if not self.bot_idle(row['owner']):
                bot_lock.release()
                continue
            with self.lock:
                self.maintaining.add(row['owner'].removeprefix('bot:'))
            action_row = {}
            try:
                env = safe_git.environment(self.environment(row['owner']))
                if row.get('repo'):
                    granted = self.client.post(f'runners/me/worktrees/{row["id"]}/token')
                    if granted.get('configured') is False:
                        env = safe_git.machine_environment(self.environment(row['owner']))
                        repo = {'machine_git': True}
                    elif not granted.get('token'):
                        raise ValueError('No repository credential for this worktree')
                    else:
                        env.update(git_credentials.environment(granted['token']))
                        repo = next((r for r in self.client.get('runners/me/repositories')['repositories'] if r['full_name'].lower() == row['repo'].lower()), {})
                else:
                    repo = {}
                action_row = {**row, **repo, **action, 'full_name': row['repo']}
                if action['action'] == 'restore' and row.get('repo') and not repo.get('machine_git') and self.refresh:
                    try:
                        action_row['_mirror_refresh'] = self.refresh(row['repo'], granted['token'])
                    except (ValueError, OSError, subprocess.SubprocessError):
                        pass  # worktree_base reports the cached mirror's age if refresh is unavailable
                def still_closed():
                    fresh = next((r for r in self.client.get('runners/me/worktrees')['worktrees'] if r['id'] == row['id']), None)
                    return fresh is not None and self.bot_idle(row['owner']) and (fresh['task_status'] in ('done', 'closed', 'declined') or fresh['bot_state'] == 'archived' or json.loads(fresh.get('detail_json') or '{}').get('delete_requested'))
                state = act(self.workspace, action_row, action['action'], env, self.vault_values(row['owner']), still_closed)
                restore_pending = action['action'] == 'restore'
                if restore_pending:
                    expected_head, checkout_target, expected_base = _record_restored_checkout(
                        self.workspace, row, repo, env)
                self.client.patch(f'tasks/{row["task_id"]}/links/{row["id"]}',
                                  {'state': 'pending' if restore_pending else state,
                                   'cleanup': action['action'] == 'remove', 'setup_pending': restore_pending,
                                   **({'checkout_state': 'checkout_ready', 'expected_head': expected_head,
                                       'checkout_target': checkout_target, 'expected_base': expected_base}
                                      if restore_pending else {}),
                                   **{k: action_row[k] for k in ('snapshot_skipped', 'restore_source') if k in action_row}})
                row['state'] = 'pending' if restore_pending else state
                if restore_pending:
                    detail = _detail(row)
                    detail.update(checkout_state='checkout_ready', setup_pending=True,
                                  expected_head=expected_head, checkout_target=checkout_target,
                                  expected_base=expected_base)
                    row['detail_json'] = json.dumps(detail)
                errors.pop(row['id'], None)
                self.retry.pop(row['id'], None)
            except Deferred:
                continue
            except (ValueError, APIError) as exc:
                failed.add(row['id'])
                if action_row.get('skipped_files'):
                    try:
                        self.client.patch(f'tasks/{row["task_id"]}/links/{row["id"]}', {'skipped_files': action_row['skipped_files']})
                    except Exception:
                        pass
                errors[row['id']] = ('Worktree action failed; history kept. ' + str(exc))[:300]
            except Exception:
                failed.add(row['id'])
                errors[row['id']] = 'Worktree action failed; history kept. Check repository access, network, disk space and permissions'
            finally:
                with self.lock:
                    self.maintaining.discard(row['owner'].removeprefix('bot:'))
                bot_lock.release()
        for key in failed:
            failures = self.retry.get(key, (0, 0))[1] + 1
            self.retry[key] = (time.monotonic() + min(300 * 2 ** min(failures - 1, 7), 21600), failures)
        self.stats = {key: value for key, value in self.stats.items() if any(r['id'] == key for r in rows)}
        self.retry = {key: value for key, value in self.retry.items() if any(r['id'] == key for r in rows)}
        reports = [inspect(self.workspace, r, self.environment(r['owner']), self.stats) for r in rows]
        for report in reports:
            if report['link_id'] in errors:
                report['error'] = errors[report['link_id']]
        with self.lock:
            self.errors = errors
            self.reports = reports

    def close(self):
        self.pool.shutdown(wait=False, cancel_futures=True)

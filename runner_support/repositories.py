"""A turn's base clone of a repository (worktrees.command, `hub worktree setup`): cloned or fetched with the bot's
own access, from the computer's mirror when the runner keeps one. The runner keeps the computer's mirrors itself
(runner-rs)."""

import json
from pathlib import Path
import re
import shutil
import subprocess
import time
from clients import git_repository

from . import git_credentials, isolation, safe_git

CLONE_TIMEOUT = 120           # a base clone made inside a turn, when no computer mirror is there yet
GB = 1024 ** 3


def valid_name(name):
    return isinstance(name, str) and re.fullmatch(r"[A-Za-z0-9-]+/[A-Za-z0-9_.-]+", name) and name.split('/')[1] not in ('.', '..')


def valid_repository(name):
    try:
        git_repository.address(name)
        return True
    except ValueError:
        return False


def base_folder(name):
    return git_repository.folder(name.lower() if valid_name(name) else name)


def machine_error(name, detail, env=None):
    from . import redact
    reason = redact.git_failure(detail, redact.env_secrets(env), limit=240) or 'Git command failed'
    return f"git could not reach {name} with this computer's git login: {reason}"


def git_error(what, name, done, env=None):
    """`what` failed for `name`, with git's own reason (credentials masked), so nobody has to guess between
    access, network and disk."""
    from . import redact
    reason = redact.git_failure(done.stderr, redact.env_secrets(env), limit=240) or f'exit {done.returncode}'
    return f'{what} of {name} failed: {reason}'


def timed_out(what, name, seconds):
    return f'{what} of {name} timed out after {int(seconds)} seconds; a large repository or a slow network needs longer'


def mirror_warning(mirror):
    try:
        fetched_at = float((mirror / 'tico-fetched-at').read_text())
        age = f'{max(0, int((time.time() - fetched_at) / 60))} minutes old'
    except (OSError, ValueError):
        age = 'age unknown'
    return 'Could not refresh origin; using the local mirror (' + age + ')'


def worktree_base(workspace, repo, env):
    """Fetch a managed base using this bot's own credentials, also usable inside a turn."""
    source_env = env
    env = safe_git.machine_environment(env) if repo.get('machine_git') else safe_git.environment(env)
    name = repo['full_name']
    if not valid_repository(name) or not repo.get('machine_git') and not valid_name(name):
        raise ValueError('Invalid repository name')
    root = Path(workspace) / 'repos'
    path = root / base_folder(name)
    if root.is_symlink() or path.is_symlink() or path.resolve().parent != root.resolve():
        raise ValueError('Base clone points outside repos')
    isolation.mkdir(root, mode=0o755)
    refreshed = repo.pop('_mirror_refresh', None)
    if path.exists():
        if not (path / '.git').is_dir() or (path / '.git').is_symlink():
            raise ValueError('Base clone folder is not a Git repository')
        current = isolation.run([*safe_git.prefix(path), '-C', str(path), 'config', '--get', 'remote.origin.url'], env=env, capture_output=True, text=True, timeout=15)
        matches = git_repository.matches(current.stdout.strip(), name) if repo.get('machine_git') else git_credentials._same_repository(current.stdout.strip(), name)
        if not matches:
            raise ValueError('Base clone remote does not match this repository')
    else:
        socket_path = source_env.get(git_credentials.credential_socket.SOCKET_ENV)
        if socket_path and not refreshed and not repo.get('machine_git'):
            try:
                refreshed = git_credentials.credential_socket.request_refresh(socket_path, source_env.get('HUB_TOKEN', ''), name)
            except (OSError, ValueError):
                pass
        direct = [*safe_git.PREFIX, '-c', 'protocol.allow=never', '-c', 'protocol.https.allow=always', '-c', 'protocol.ssh.allow=always', '-c', 'protocol.file.allow=always', '-c', 'http.followRedirects=false', '-c', 'http.sslVerify=true', 'clone', '--quiet', '--single-branch']
        command, clone_env, url = list(direct), env, git_repository.address(name)
        if refreshed:
            url = refreshed['mirror']
            from urllib.parse import urlparse, unquote
            position = command.index('clone')
            command[position:position] = ['-c', 'safe.directory=' + unquote(urlparse(url).path)]
            command += ['--origin', 'tico-mirror']
            clone_env = safe_git.trusting(safe_git.clean_environment(env), unquote(urlparse(url).path))
            repo['default_branch'] = refreshed['default_branch']
            if refreshed.get('warning'):
                repo['fetch_warning'] = refreshed['warning']
        branch_args = ['--branch', repo['default_branch']] if repo.get('default_branch') else []
        problem = _clone(name, path, [*command, *branch_args], url, clone_env, repo.get('machine_git'))
        if problem and refreshed and not problem.endswith(_ELSEWHERE):
            # The computer's mirror could not be copied into the workspace while GitHub may well be fine: clone from
            # GitHub with this bot's own access instead of failing the turn on the mirror.
            fallback = _clone(name, path, [*direct, *branch_args], git_repository.address(name), env, False)
            if fallback:
                raise ValueError(f'{problem}; cloning from GitHub instead also failed: '
                                 f'{fallback.removeprefix(f"Git clone of {name} failed: ")}')
            problem, refreshed, clone_env = None, None, env
            repo['fetch_warning'] = "Could not copy this computer's mirror of " + name + '; cloned from GitHub instead'
        if problem:
            raise ValueError(problem)
        if refreshed:
            for key, value in [('remote.origin.url', git_repository.address(name)),
                               ('remote.origin.fetch', '+refs/heads/*:refs/remotes/origin/*')]:
                result = isolation.run([*safe_git.prefix(path), '-C', str(path), 'config', key, value], env=clone_env, capture_output=True, timeout=15)
                if result.returncode:
                    raise ValueError('Could not configure base clone origin')
        mark_managed(path, env, name)
    branch = repo.get('default_branch')
    if not branch:
        result = isolation.run([*safe_git.prefix(path), '-C', str(path), 'symbolic-ref', '--short', 'refs/remotes/origin/HEAD'], env=env, capture_output=True, text=True, timeout=15)
        branch = result.stdout.strip().removeprefix('origin/')
    if not branch or branch.startswith('-') or isolation.run([*safe_git.PREFIX, 'check-ref-format', '--branch', branch],
            env=env, capture_output=True, timeout=15).returncode:
        raise ValueError('Repository default branch is missing; refresh Repositories')
    mirror_url = isolation.run([*safe_git.prefix(path), '-C', str(path), 'config', '--get', 'remote.tico-mirror.url'],
                               env=env, capture_output=True, text=True, timeout=15).stdout.strip()
    if repo.get('machine_git'):
        mirror_url = ''
    if mirror_url:
        from urllib.parse import urlparse, unquote
        from . import credential_socket
        mirror = Path(unquote(urlparse(mirror_url).path))
        if not refreshed:
            repo.pop('fetch_warning', None)
        try:
            socket_path = (source_env or {}).get(credential_socket.SOCKET_ENV)
            if not socket_path:
                raise ValueError('Supervisor mirror refresh unavailable')
            if not refreshed:
                response = credential_socket.request_refresh(socket_path, (source_env or {}).get('HUB_TOKEN', ''), name)
                if response.get('warning'):
                    repo['fetch_warning'] = response['warning']
        except (OSError, ValueError, subprocess.SubprocessError):
            repo['fetch_warning'] = mirror_warning(mirror)
    source = mirror_url or 'origin'
    local_env = safe_git.clean_environment(env) if mirror_url else env
    if mirror_url:
        from urllib.parse import urlparse, unquote
        local_env = safe_git.trusting(local_env, unquote(urlparse(mirror_url).path))
    prefix = [*safe_git.prefix(path), '-c', 'protocol.allow=never', '-c', 'protocol.file.allow=always'] if mirror_url else [*safe_git.prefix(path), '-c', 'http.followRedirects=false']
    if mirror_url:
        from urllib.parse import urlparse, unquote
        prefix += ['-c', 'safe.directory=' + unquote(urlparse(mirror_url).path)]
    try:
        done = isolation.run([*prefix, '-C', str(path), 'fetch', '--quiet', '--no-tags', '--no-write-fetch-head', '--', source,
                             f'+refs/heads/{branch}:refs/remotes/origin/{branch}'], env=local_env, capture_output=True, text=True, timeout=15 if not mirror_url else 30)
        fetched = done.returncode == 0
        fetch_error = done.stderr or f'exit {done.returncode}'
    except (OSError, subprocess.SubprocessError) as exc:
        fetched = False
        fetch_error = 'timed out' if isinstance(exc, subprocess.TimeoutExpired) else type(exc).__name__
    if not fetched:
        available = isolation.run([*safe_git.prefix(path), '-C', str(path), 'show-ref', '--verify', 'refs/remotes/origin/' + branch],
                                 env=local_env, capture_output=True, timeout=15)
        if available.returncode:
            if repo.get('machine_git'):
                raise ValueError(machine_error(name, fetch_error, local_env))
            from . import redact
            reason = redact.git_failure(fetch_error, redact.env_secrets(local_env), limit=240) or 'no reason given'
            raise ValueError(f'Git fetch of {name} failed: {reason}')
        repo.setdefault('fetch_warning', 'Could not refresh origin; using the base clone (age unknown)')
    mark_managed(path, local_env, name)
    # A normal git push then updates the task branch's tracking ref, rather than comparing it to main.
    done = isolation.run([*safe_git.prefix(path), '-C', str(path), 'config', 'remote.origin.fetch',
                          '+refs/heads/*:refs/remotes/origin/*'], env=env, capture_output=True, text=True, timeout=15)
    if done.returncode:
        raise ValueError('Could not configure task branch tracking')
    done = isolation.run([*safe_git.prefix(path), '-C', str(path), 'symbolic-ref', 'refs/remotes/origin/HEAD',
                          'refs/remotes/origin/' + branch], env=local_env, capture_output=True, timeout=15)
    if done.returncode:
        raise ValueError('Could not record base default branch')
    return path, branch


_ELSEWHERE = 'already exists and is not an empty directory.'


def _clone(name, path, command, url, env, machine_git):
    """Clone into a new `path`: None, or why not (git's own reason). What a failed clone left is removed."""
    try:
        done = isolation.run([*command, '--', url, str(path)], env=env, capture_output=True, text=True, timeout=CLONE_TIMEOUT)
    except (OSError, subprocess.SubprocessError) as exc:
        discard_failed_clone(path, fresh=True)
        if isinstance(exc, subprocess.TimeoutExpired):
            return timed_out('Git clone', name, CLONE_TIMEOUT)
        return machine_error(name, type(exc).__name__) if machine_git else f'Git clone of {name} failed: {type(exc).__name__}'
    if not done.returncode:
        return None
    if 'already exists' in (done.stderr or ''):
        # Git refuses a destination something else made meanwhile: that folder is not this clone's to remove.
        return f'Git clone of {name} failed: {path.name} {_ELSEWHERE}'
    discard_failed_clone(path, fresh=True)
    return machine_error(name, done.stderr or f'exit {done.returncode}', env) if machine_git else git_error('Git clone', name, done, env)


def discard_failed_clone(path, fresh=False):
    """Remove what a failed clone left behind. `fresh`: the folder did not exist before this clone, so nothing
    in it is anyone's work unless a task worktree was already registered on it; a clone killed halfway or one
    whose checkout failed is otherwise left half-made, and every retry would refuse it."""
    if not path.exists() or path.is_symlink():
        return
    if fresh and not (path / '.git').is_symlink():
        registered = path / '.git' / 'worktrees'
        if not registered.is_symlink() and not (registered.is_dir() and any(registered.iterdir())):
            shutil.rmtree(path)
            return
    if (path / '.git').exists():
        try:
            if base_kept(path):
                return
        except (ValueError, OSError):
            return
    shutil.rmtree(path)


def was_adopted(path):
    try:
        marker = path / '.git' / 'tico-managed'
        if marker.is_symlink() or not marker.is_file() or marker.stat().st_size > 1024:
            return False
        return json.loads(marker.read_text() or '{}').get('adopted') is True
    except (OSError, ValueError, AttributeError):
        return False


def mark_managed(path, env, name=None, adopted=False):
    # A clone Tico took over stays recorded as one, whoever marks it next (a turn's worktree_base).
    adopted = adopted or was_adopted(path)
    marker = json.dumps({'full_name': name, **({'adopted': True} if adopted else {})}) if name else ''
    done = isolation.run(['/bin/sh', '-c', 'test ! -L .git/tico-managed && printf %s \"$1\" > .git/tico-managed', 'mark', marker],
                         cwd=str(path), env=safe_git.process_environment(env), capture_output=True, timeout=15)
    if done.returncode:
        raise ValueError('Could not mark managed base clone; check permissions')


def base_kept(path):
    if (path / '.git').is_symlink() or (path / '.git' / 'worktrees').is_symlink():
        raise ValueError('Base clone registrations point outside repos')
    env = safe_git.clean_environment()
    prefix = [*safe_git.prefix(path), '-C', str(path)]
    done = isolation.run([*prefix, 'worktree', 'prune', '--expire', 'now'], env=env, capture_output=True, text=True, timeout=120)
    if done.returncode:
        raise ValueError('Could not prune base clone; repair without deleting local work')
    listing = isolation.run([*prefix, 'worktree', 'list', '--porcelain'], env=env, capture_output=True, text=True, timeout=15)
    if listing.returncode:
        raise ValueError('Could not inspect base clone worktrees')
    count = max(0, sum(line.startswith('worktree ') for line in listing.stdout.splitlines()) - 1)
    if count:
        return f'kept: {count} task worktrees'
    status = isolation.run([*prefix, 'status', '--porcelain', '--untracked-files=all'], env=env, capture_output=True, text=True, timeout=30)
    if status.returncode:
        raise ValueError('Could not inspect base clone changes; repair without deleting local work')
    if status.stdout.strip():
        return 'kept: uncommitted or untracked files'
    branches = isolation.run([*prefix, 'rev-list', '--count', '--ignore-missing', '--branches', 'HEAD', 'refs/stash', '--not', '--remotes'], env=env, capture_output=True, text=True, timeout=30)
    if branches.returncode:
        raise ValueError('Could not inspect local branches; repair without deleting local work')
    if int(branches.stdout.strip() or '0'):
        return 'kept: local branches with unpublished commits (including HEAD or stash)'
    return None

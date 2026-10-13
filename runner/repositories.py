"""Base clones for this computer; independent of bot readiness and turn credentials."""

import concurrent.futures
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import shutil
import signal
import stat
import subprocess
import threading
import time
import tempfile
from clients import git_repository

from . import git_credentials, isolation, safe_git

FETCH_INTERVAL = 15 * 60
# A failed base clone is tried again after 5 minutes, then 10, 20, ... and at most every 6 hours. A runner
# restart (Computers > Restart) forgets the wait and tries at once.
RETRY_FIRST, RETRY_MOST = 5 * 60, 6 * 3600
CLONE_TIMEOUT = 120           # a base clone made inside a turn, when no computer mirror is there yet
REMOVE_AFTER = 30 * 86400
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


_MIRROR_BROKEN = re.compile(r'corrupt|bad object|missing (?:blob|tree|commit|object)|unable to read|did not send all|'
                            r'index-pack|unpack-objects|\.lock\b|is not a valid|not a git repository|'
                            r'could not (?:open|read)|permission denied|unable to create temporary', re.I)


def mirror_broken(stderr, mirror):
    """Whether a failed mirror fetch is the mirror's own fault (corrupt, half-written, locked, unreadable) rather
    than GitHub's or the network's, so rebuilding it can help."""
    text = str(stderr or '')
    return bool(_MIRROR_BROKEN.search(text)) or str(mirror) in text


def wait_words(seconds):
    minutes = round(seconds / 60)
    return f'{minutes} minutes' if minutes < 120 else f'{minutes // 60} hours'


def mirror_warning(mirror):
    try:
        fetched_at = float((mirror / 'tico-fetched-at').read_text())
        age = f'{max(0, int((time.time() - fetched_at) / 60))} minutes old'
    except (OSError, ValueError):
        age = 'age unknown'
    return 'Could not refresh origin; using the local mirror (' + age + ')'


class Repositories:
    def __init__(self, workspace, state_file, client):
        self.root = Path(workspace) / 'repos'
        self.state_file = Path(state_file)
        self.mirrors = self.state_file.parent / 'mirrors'
        self.client = client
        self.lock = threading.RLock()
        self.pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        self.pending = None
        self.visible = False
        self.stopping = threading.Event()
        self.process = None
        try:
            saved = json.loads(self.state_file.read_text())
            self.rows = {name: row for name, row in saved.items() if valid_repository(name) and isinstance(row, dict)}
        except (OSError, ValueError, AttributeError):
            self.rows = {}
        # Restarting the runner is how a person says "try again now": a failure's wait does not outlive it.
        for row in self.rows.values():
            if row.get('state') == 'failed' or row.get('failures'):
                row.pop('attempted_at', None)
                row.update(failures=0, retry_delay=FETCH_INTERVAL)

    def close(self):
        self.stopping.set()
        with self.lock:
            process = self.process
        if process and process.poll() is None:
            self.kill_git(process)
        self.pool.shutdown(wait=False, cancel_futures=True)

    @staticmethod
    def kill_git(process):
        try:
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                pass
            # The parent may have exited while a credential helper kept running.
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
            process.wait(timeout=2)
        except (ProcessLookupError, PermissionError, subprocess.TimeoutExpired):
            pass

    def run_git(self, command, *, env, timeout, bot=False):
        with tempfile.TemporaryDirectory(prefix='tico-git-hooks-') as hooks:
            os.chmod(hooks, 0o755)
            command = [command[0], '-c', 'core.hooksPath=' + hooks, *command[1:]]
            with self.lock:
                if self.stopping.is_set():
                    raise ValueError('Repository sync interrupted by computer shutdown')
                launch = isolation.popen if bot else subprocess.Popen
                process = launch(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                           text=True, stdin=subprocess.DEVNULL, env=env, cwd=hooks, start_new_session=True)
                self.process = process
            deadline = time.monotonic() + timeout
            try:
                while True:
                    if self.stopping.is_set():
                        self.kill_git(process)
                        raise ValueError('Repository sync interrupted by computer shutdown')
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        self.kill_git(process)
                        raise subprocess.TimeoutExpired(command, timeout)
                    try:
                        out, err = process.communicate(timeout=min(.2, remaining))
                        return subprocess.CompletedProcess(command, process.returncode, out, err)
                    except subprocess.TimeoutExpired:
                        continue
            finally:
                with self.lock:
                    self.process = None

    def path(self, name):
        path = self.root / base_folder(self.rows.get(name, {}).get('full_name', name))
        if self.root.is_symlink() or path.is_symlink() or path.resolve().parent != self.root.resolve():
            raise ValueError('Repository folder points outside repos; left as it is')
        return path

    def save(self):
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        temp = self.state_file.with_suffix('.tmp')
        temp.write_text(json.dumps(self.rows))
        temp.replace(self.state_file)

    def report(self):
        with self.lock:
            return [self.report_row(row) for row in self.rows.values()] if self.visible else []

    def inspect(self):
        """Doctor reads current repositories and saved status without cloning or cleanup."""
        try:
            repos = self.client.get("runners/me/repositories")["repositories"]
            return [self.report_row(self.rows.get(repo["full_name"].lower(), {
                "full_name": repo["full_name"], "state": "cloning", "last_fetch": None, "size_mb": 0}))
                    for repo in repos if valid_name(repo.get("full_name"))]
        except Exception:
            return []

    @staticmethod
    def report_row(row):
        return {k: row.get(k) for k in ("full_name", "state", "last_fetch", "size_mb", "error") if k in row}

    def poll(self):
        if self.stopping.is_set():
            return self.report()
        # Failed requests are not an empty list: they must never start the removal clock.
        try:
            reply = self.client.get('runners/me/repositories')
            repos = reply['repositories']
            if not isinstance(repos, list) or any(not isinstance(r, dict) or not valid_name(r.get('full_name')) for r in repos):
                return None
        except Exception:
            self.visible = False
            return None
        self.visible = True
        with self.lock:
            if self.pending is not None and not self.pending.done():
                return self.report()
            now = time.time()
            wanted = {r['full_name'].lower(): r for r in repos}
            for marker in self.root.glob('*/.git/tico-managed'):
                name = marker.parent.parent.name.replace('__', '/', 1)
                try:
                    if not stat.S_ISREG(marker.lstat().st_mode):
                        continue
                    fd = os.open(marker, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
                    with os.fdopen(fd, 'rb') as stream:
                        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                            continue
                        data = stream.read(1025)
                    if len(data) > 1024:
                        continue
                    stored = json.loads(data) if data else {}
                    name = stored.get('full_name') or name
                except (OSError, ValueError, AttributeError):
                    continue
                name = name.lower() if valid_name(name) else name
                if valid_repository(name) and not marker.is_symlink() and base_folder(name) == marker.parent.parent.name:
                    self.rows.setdefault(name, {'full_name': name, 'state': 'cloned', 'managed': True, 'left_at': now, 'size_mb': 0,
                                                **({'adopted': True} if stored.get('adopted') is True else {})})
            for name, row in list(self.rows.items()):
                if name in wanted:
                    row.pop('left_at', None)
                elif row.get('state') == 'removed':
                    del self.rows[name]
                else:
                    row.setdefault('left_at', now)
            for name, repo in wanted.items():
                self.rows.setdefault(name, {'full_name': repo['full_name'], 'state': 'cloning', 'last_fetch': None, 'size_mb': 0})
            try:
                self.save()
            except OSError:
                for row in self.rows.values():
                    row.update(state="failed", error="Could not save repository state; free disk space and check permissions")
                return self.report()
            # One job per cycle, chosen fairly so a failed clone cannot hold up the others.
            due = [name for name in wanted if now - self.rows[name].get('attempted_at', 0) >= self.rows[name].get('retry_delay', FETCH_INTERVAL)
                   and now - self.rows[name].get('fetched_at', 0) >= FETCH_INTERVAL]
            due.sort(key=lambda name: self.rows[name].get('attempted_at', 0))
            selected = due[0] if due else None
            if selected:
                self.rows[selected]['attempted_at'] = now
            self.pending = self.pool.submit(self.sync, wanted, selected, now)
            return self.report()

    def sync(self, wanted, selected, now):
        with self.lock:
            stale = [name for name, row in self.rows.items() if name not in wanted
                     and now - row.get('left_at', now) >= REMOVE_AFTER]
        for name in stale:
            try:
                path = self.path(name)
                if self.rows[name].get('adopted') and path.exists():
                    # A clone a person made is theirs: Tico stops looking after it and leaves it where it is.
                    marker = path / '.git' / 'tico-managed'
                    if marker.is_file() and not marker.is_symlink():
                        marker.unlink()
                elif self.rows[name].get('managed') and path.exists():
                    kept = base_kept(path)
                    if kept:
                        self.rows[name].update(error=kept)
                        continue
                    shutil.rmtree(path)
                mirror = self.mirror_path(name) if valid_name(name) else None
                if mirror and mirror.exists():
                    shutil.rmtree(mirror)
                with self.lock:
                    self.rows[name].update(state='removed', size_mb=0)
                    self.rows[name].pop('error', None)
            except (OSError, ValueError):
                with self.lock:
                    self.rows[name].update(state='failed', error='Could not remove base clone safely; check repos folder permissions')
        if selected:
            self.update(selected, wanted[selected], now)
        with self.lock:
            try:
                self.save()
            except OSError:
                # Disk exhaustion must not break the next heartbeat or any bot.
                if selected:
                    self.rows[selected].update(state='failed', error='Could not save repository state; free disk space and check permissions')

    def mirror_path(self, name):
        path = self.mirrors / (name.lower().replace('/', '__') + '.git')
        if self.mirrors.is_symlink() or path.is_symlink():
            raise ValueError('Mirror contains a symlink; repair the computer mirror folder')
        if self.mirrors.exists() and isolation.enabled():
            stats = self.mirrors.stat()
            if stats.st_uid != os.geteuid() or stats.st_mode & 0o022:
                raise ValueError('Mirror folder is writable by another user; repair the computer mirror permissions')
        if path.exists():
            for root, dirs, files in os.walk(path, followlinks=False):
                for entry in (Path(root), *(Path(root) / n for n in (*dirs, *files))):
                    stats = entry.lstat()
                    if entry.is_symlink():
                        raise ValueError('Mirror contains a symlink; repair the computer mirror folder')
                    if isolation.enabled() and (stats.st_uid != os.geteuid() or stats.st_mode & 0o022):
                        raise ValueError('Mirror is writable by another user; repair the computer mirror permissions')
        return path

    def adoption_problem(self, path, full_name):
        """Why a folder already at a base clone's place cannot be taken over, or '' when it can: a real Git checkout
        of this very repository, made by the user the runner runs as, on a computer where bots are not a separate
        user (a Mac). Nothing in it is changed by asking."""
        from . import redact
        git_dir = path / '.git'
        if isolation.enabled():
            return 'bots run as a separate user on this computer, so a folder made by someone else is not taken over'
        if not git_dir.is_dir() or git_dir.is_symlink():
            return 'it is not a Git checkout'
        if any(p.lstat().st_uid != os.geteuid() for p in (path, git_dir)):
            return 'it belongs to another user'
        if (git_dir / 'objects' / 'info' / 'alternates').exists() or (git_dir / 'commondir').exists():
            return 'it shares its objects with another repository'
        env = safe_git.clean_environment()
        git = [*safe_git.prefix(path), '-C', str(path), 'config', '--local', '--get']
        if self.run_git([*git, 'core.worktree'], env=env, timeout=10).returncode == 0:
            return 'its work tree is somewhere else'
        if self.run_git([*git, '--bool', 'core.bare'], env=env, timeout=10).stdout.strip() == 'true':
            return 'it is a bare repository'
        origin = self.run_git([*git, 'remote.origin.url'], env=env, timeout=10).stdout.strip()
        if not github_origin(origin, full_name):
            return f'its origin is {redact.scrub_credentials(origin)[:120] or "not set"}, not {full_name}'
        return ''

    def discard_mirror(self, name):
        """Remove a computer mirror so it is fetched afresh. It holds only what GitHub has, never anyone's work."""
        mirror = self.mirror_path(name)
        if mirror.exists():
            shutil.rmtree(mirror)

    @staticmethod
    def mirror_permissions(path):
        for root, dirs, files in os.walk(path, followlinks=False):
            os.chmod(root, 0o755)
            for name in files:
                os.chmod(Path(root) / name, 0o644)

    def update(self, name, repo, now):
        token = ''
        created = False
        try:
            path = self.path(name)
            exists = path.exists()
            if exists and self.rows[name].get('partial') and not (path / '.git' / 'tico-managed').exists():
                # A clone this runner started and never finished (it was stopped mid-clone): nothing in it is anyone's
                # work, so it is removed and cloned again rather than refused as a broken repository forever.
                discard_failed_clone(path, fresh=True)
                exists = path.exists()
            managed = self.rows[name].get('managed') or (path / '.git' / 'tico-managed').is_file()
            if exists and (not (path / '.git').is_dir() or (path / '.git').is_symlink()):
                raise ValueError('Repository folder already exists and is not a managed base clone; left as it is')
            if exists and not managed:
                # A clone a person made here themselves (on a Mac, with their own git) is taken over when it is
                # plainly this repository's; Tico then fetches into it but never deletes it or rewrites its origin.
                problem = self.adoption_problem(path, repo['full_name'])
                if problem:
                    raise ValueError(f'Repository folder already exists and is not a managed base clone ({problem}); left as it is')
                with self.lock:
                    self.rows[name].update(managed=True, adopted=True)
            volumes = (self.root if self.root.exists() else self.root.parent,
                       self.mirrors if self.mirrors.exists() else self.state_file.parent)
            volume, usage = min(((p, shutil.disk_usage(p)) for p in volumes), key=lambda item: item[1].free)
            size_kb = int(repo.get('size_kb') or self.rows[name].get('size_mb', 0) * 1024)
            floor = 5 * GB + 2 * size_kb * 1024
            if usage.free < floor:
                with self.lock:
                    self.rows[name].update(state='disk_low', error=f'Not enough disk to {"fetch" if exists else "clone"} {repo["full_name"]}: {usage.free / GB:.1f} GB free, needs {floor / GB:g} GB. Free space on {volume}')
                return
            from .service import Runner
            clean = Runner.credential_environment(None, None)
            clean.update(GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_SYSTEM=os.devnull,
                         GIT_TERMINAL_PROMPT='0')
            mirror = self.mirror_path(name)
            self.mirrors.mkdir(mode=0o755, parents=True, exist_ok=True)
            os.chmod(self.mirrors, 0o755)
            try:
                granted = self.client.post('runners/me/repositories/token')
            except Exception as exc:
                raise ValueError(git_credentials.token_problem(exc, repo['full_name'])) from None
            token = granted.get('token') or ''
            if not token:
                raise ValueError(f'Tico could not get a GitHub read token for {repo["full_name"]}: the GitHub App gave '
                                 'none; check Settings > Tools > GitHub')
            if name not in {n.lower() for n in granted.get('repositories', [])}:
                raise ValueError(f'Tico could not get a GitHub read token for {repo["full_name"]}: the GitHub App is not '
                                 "installed on it or cannot reach it; add it to the App's repository access on GitHub")
            env = {**clean, **git_credentials.environment(token)}
            url = f'https://github.com/{repo["full_name"]}.git'
            prefix = ['git', '-c', 'core.fsmonitor=false', '-c', 'gc.auto=0', '-c', 'maintenance.auto=false',
                      '-c', 'http.followRedirects=false', '-c', 'http.sslVerify=true',
                      '-c', 'protocol.allow=never', '-c', 'protocol.https.allow=always', '-c', 'protocol.file.allow=never']
            timeout = max(900, min(7200, size_kb // 1024))
            branch = repo.get('default_branch')
            if not branch:
                result = self.run_git([*prefix, 'ls-remote', '--symref', url, 'HEAD'], env=env, timeout=timeout)
                match = re.search(r'^ref: refs/heads/(.+)\tHEAD$', result.stdout, re.M)
                if result.returncode or not match:
                    raise ValueError(git_error('Finding the default branch', repo['full_name'], result, env)
                                     if result.returncode else 'Could not find the default branch; check GitHub access')
                branch = match.group(1)
            base_prefix = list(prefix)

            def fill_mirror(rebuild=True):
                """Create the mirror if it is missing and fetch the default branch into it from GitHub."""
                prefix = list(base_prefix)
                if not mirror.exists():
                    done = self.run_git([*prefix, 'init', '--bare', '--quiet', '--initial-branch=' + branch, str(mirror)], env=clean, timeout=10)
                    if done.returncode:
                        raise ValueError('Could not create mirror; check disk space and permissions')
                # Only supervisor-owned Git config is read with an installation token.
                config = self.run_git([*prefix, '-C', str(mirror), 'config', '--no-includes', '--name-only',
                                       '--get-regexp', r'^(http|include|includeif)\.'], env=clean, timeout=10)
                if config.returncode not in (0, 1):
                    raise ValueError('Could not inspect mirror Git config')
                for key in config.stdout.splitlines():
                    if key.lower().startswith(('include.', 'includeif.')):
                        raise ValueError('Mirror Git config includes another file; remove the include before fetching')
                    setting = key.rsplit('.', 1)[-1].lower()
                    value = 'true' if setting == 'sslverify' else (clean.get('GIT_SSL_CAINFO', '') if setting == 'sslcainfo' else '')
                    prefix += ['-c', key + '=' + value]
                done = self.run_git([*prefix, '-C', str(mirror), 'fetch', '--quiet', '--prune', '--no-tags',
                                     '--no-write-fetch-head', '--', url, f'+refs/heads/{branch}:refs/heads/{branch}'],
                                    env=env, timeout=timeout)
                if done.returncode and rebuild and mirror_broken(done.stderr, mirror):
                    # The mirror, not GitHub, failed (corrupt or missing objects, a stale lock): start it again.
                    self.discard_mirror(name)
                    return fill_mirror(rebuild=False)
                if done.returncode:
                    raise ValueError(git_error('Git mirror fetch', repo['full_name'], done, env))
                done = self.run_git([*prefix, '-C', str(mirror), 'symbolic-ref', 'HEAD', 'refs/heads/' + branch], env=clean, timeout=10)
                if done.returncode:
                    raise ValueError('Could not set mirror default branch')
                self.mirror_permissions(mirror)
                (mirror / 'tico-fetched-at').write_text(str(now))

            fill_mirror()
            # Bot-owned directories are touched only by unprivileged Git, without a token.
            # Both spellings of the mirror's path: older Git compares safe.directory without resolving symlinks.
            local = [*safe_git.PREFIX, '-c', 'safe.directory=' + str(mirror.resolve()), '-c', 'safe.directory=' + str(mirror),
                     '-c', 'safe.directory=' + str(path.resolve()), '-c', 'core.fsmonitor=false', '-c', 'gc.auto=0', '-c', 'maintenance.auto=false',
                     '-c', 'protocol.allow=never', '-c', 'protocol.file.allow=always']
            if not exists:
                isolation.mkdir(self.root, mode=0o755)
                created = True
                with self.lock:
                    self.rows[name].update(state='cloning', managed=True, partial=True)
                    self.save()
                def clone_mirror():
                    return self.run_git([*local, 'clone', '--quiet', '--single-branch', '--no-hardlinks',
                                         '--origin', 'tico-mirror', '--branch', branch, '--', mirror.as_uri(), str(path)],
                                        env=clean, timeout=timeout, bot=True)
                done = clone_mirror()
                if done.returncode and 'already exists' not in (done.stderr or ''):
                    # The mirror itself can be what is broken while GitHub is fine: a fetch killed halfway, missing
                    # objects, stale lock files, files the bot user cannot read. Rebuild it from GitHub once and
                    # clone again, rather than leaving every later try to fail the same way.
                    first = git_error('Cloning the local mirror', repo['full_name'], done, clean)
                    discard_failed_clone(path, fresh=True)
                    self.discard_mirror(name)
                    fill_mirror(rebuild=False)
                    done = clone_mirror()
                    if done.returncode:
                        from . import redact
                        again = redact.git_failure(done.stderr, redact.env_secrets(clean), limit=200) or f'exit {done.returncode}'
                        created = 'already exists' not in (done.stderr or '')
                        raise ValueError(f'{first}; rebuilt the mirror from GitHub and cloning it failed again: {again}')
                elif done.returncode:
                    # A turn's own clone made the folder meanwhile: that one is not this clone's to remove.
                    created = False
                    raise ValueError(git_error('Cloning the local mirror', repo['full_name'], done, clean))
            else:
                done = self.run_git([*local, '-C', str(path), 'rev-parse', '--git-dir'], env=clean, timeout=10, bot=True)
                if done.returncode:
                    raise ValueError('Base clone is not a working Git repository; repair it without deleting local work')
            settings = [('remote.tico-mirror.url', mirror.as_uri()),
                        ('remote.tico-mirror.fetch', f'+refs/heads/{branch}:refs/remotes/tico-mirror/{branch}')]
            if not self.rows[name].get('adopted'):
                # An adopted clone keeps the origin and branch settings its owner chose (an SSH remote, say).
                settings += [('remote.origin.url', url), ('remote.origin.pushurl', url),
                             ('remote.origin.fetch', '+refs/heads/*:refs/remotes/origin/*'),
                             ('branch.' + branch + '.remote', 'origin'), ('branch.' + branch + '.merge', 'refs/heads/' + branch)]
            for key, value in settings:
                done = self.run_git([*local, '-C', str(path), 'config', '--local', '--replace-all', key, value], env=clean, timeout=10, bot=True)
                if done.returncode:
                    raise ValueError('Could not configure base clone mirror; check its Git config and permissions')
            def fetch_mirror():
                return self.run_git([*local, '-C', str(path), 'fetch', '--quiet', '--no-tags', '--no-write-fetch-head',
                                     '--', mirror.as_uri(), f'+refs/heads/{branch}:refs/remotes/origin/{branch}',
                                     f'+refs/heads/{branch}:refs/remotes/tico-mirror/{branch}'],
                                    env=clean, timeout=timeout, bot=True)
            done = fetch_mirror()
            if done.returncode:
                # git_error masks credentials and local secrets in git's stderr before it reaches a report.
                first = git_error(f'Git {"fetch" if exists else "clone"} from the local mirror', repo['full_name'], done, clean)
                # As for a clone: a broken mirror is rebuilt from GitHub once before this attempt gives up.
                self.discard_mirror(name)
                fill_mirror(rebuild=False)
                done = fetch_mirror()
                if done.returncode:
                    from . import redact
                    again = redact.git_failure(done.stderr, redact.env_secrets(clean), limit=200) or f'exit {done.returncode}'
                    raise ValueError(f'{first}; rebuilt the mirror from GitHub and fetching it failed again: {again}')
            done = self.run_git([*local, '-C', str(path), 'symbolic-ref', 'refs/remotes/origin/HEAD', 'refs/remotes/origin/' + branch], env=clean, timeout=10, bot=True)
            if done.returncode:
                raise ValueError('Could not record base default branch; check permissions')
            mark_managed(path, clean, repo['full_name'] if self.rows[name].get('adopted') else None,
                         adopted=bool(self.rows[name].get('adopted')))
            size = sum(p.stat().st_size for root, dirs, files in os.walk(path, followlinks=False)
                       for p in (Path(root) / f for f in files) if not p.is_symlink()) / (1024 ** 2)
            with self.lock:
                self.rows[name].update(state='cloned', managed=True, fetched_at=now,
                                       last_fetch=datetime.fromtimestamp(now, timezone.utc).isoformat(), size_mb=round(size, 1))
                self.rows[name].pop('error', None)
                self.rows[name].pop('partial', None)
                self.rows[name].update(failures=0, retry_delay=FETCH_INTERVAL)
        except Exception as exc:
            if created:
                try:
                    discard_failed_clone(self.path(name), fresh=True)
                    with self.lock:
                        self.rows[name].pop('partial', None)
                except (OSError, ValueError):
                    pass
            if isinstance(exc, subprocess.TimeoutExpired):
                error = f'Git timed out after {int(exc.timeout)} seconds syncing {repo["full_name"]}; a large repository or a slow network needs longer'
            elif isinstance(exc, ValueError):
                from . import redact
                error = redact.scrub_credentials(str(exc), [token] if token else [])
            elif isinstance(exc, OSError) and exc.errno == 28:
                error = 'Not enough disk space; free space on the workspace volume'
            else:
                error = 'Could not sync base clone; check GitHub connection, network, disk space and folder permissions'
            with self.lock:
                failures = self.rows[name].get('failures', 0) + 1
                delay = min(RETRY_FIRST * 2 ** min(failures - 1, 10), RETRY_MOST)
                self.rows[name].update(state='failed', failures=failures, retry_delay=delay, attempted_at=time.time(),
                                       error=f'{error.rstrip(".")}. Retrying in {wait_words(delay)}, or restart the '
                                             "computer's runner to retry now")

    def refresh_mirror(self, name, token):
        """A bounded refresh requested by an authorized turn; tokens never enter the base."""
        if not valid_name(name):
            raise ValueError('Invalid mirror repository')
        mirror = self.mirror_path(name)
        self.mirrors.mkdir(mode=0o755, parents=True, exist_ok=True)
        size = self.rows.get(name.lower(), {}).get('size_mb', 0) * 1024 ** 2
        if shutil.disk_usage(self.mirrors).free < 5 * GB + 2 * size:
            raise ValueError('Not enough disk to refresh the mirror; free space on the computer state volume')
        clean = safe_git.clean_environment()
        env = {**clean, **git_credentials.environment(token)}
        prefix = [*safe_git.prefix(mirror), '-c', 'gc.auto=0', '-c', 'maintenance.auto=false',
                  '-c', 'http.followRedirects=false', '-c', 'http.sslVerify=true',
                  '-c', 'protocol.allow=never', '-c', 'protocol.https.allow=always', '-c', 'protocol.file.allow=never']
        if not mirror.exists():
            result = self.run_git([*prefix, 'ls-remote', '--symref', '--', git_repository.address(name), 'HEAD'], env=env, timeout=15)
            match = re.search(r'^ref: refs/heads/(.+)\tHEAD$', result.stdout, re.M)
            if result.returncode or not match:
                raise ValueError('Could not find the default branch; check GitHub access')
            initialized = self.run_git([*prefix, 'init', '--bare', '--quiet', '--initial-branch=' + match.group(1), str(mirror)], env=clean, timeout=10)
            if initialized.returncode:
                raise ValueError('Could not create mirror; check disk space and permissions')
        config = self.run_git([*prefix, '-C', str(mirror), 'config', '--no-includes', '--name-only',
                               '--get-regexp', r'^(http|include|includeif)\.'], env=clean, timeout=10)
        if config.returncode not in (0, 1):
            raise ValueError('Could not inspect mirror Git config')
        for key in config.stdout.splitlines():
            if key.lower().startswith(('include.', 'includeif.')):
                raise ValueError('Mirror Git config includes another file')
            setting = key.rsplit('.', 1)[-1].lower()
            value = 'true' if setting == 'sslverify' else clean.get('GIT_SSL_CAINFO', '') if setting == 'sslcainfo' else ''
            prefix += ['-c', key + '=' + value]
        head = self.run_git([*prefix, '-C', str(mirror), 'symbolic-ref', 'HEAD'], env=clean, timeout=10)
        branch = head.stdout.strip()
        if head.returncode or not branch.startswith('refs/heads/'):
            raise ValueError('Mirror default branch is missing')
        try:
            result = self.run_git([*prefix, '-C', str(mirror), 'fetch', '--quiet', '--no-tags', '--no-write-fetch-head',
                                   '--', git_repository.address(name), '+' + branch + ':' + branch], env=env, timeout=15)
            fetched = result.returncode == 0
        except (OSError, subprocess.SubprocessError):
            fetched = False
        if not fetched:
            available = self.run_git([*prefix, '-C', str(mirror), 'show-ref', '--verify', branch], env=clean, timeout=10)
            if available.returncode == 0:
                return {'cached': True, 'mirror': mirror.as_uri(), 'default_branch': branch.removeprefix('refs/heads/'),
                        'warning': mirror_warning(mirror)}
            raise ValueError('Mirror fetch failed; check repository access and network')
        (mirror / 'tico-fetched-at').write_text(str(time.time()))
        self.mirror_permissions(mirror)
        return {'refreshed': True, 'mirror': mirror.as_uri(), 'default_branch': branch.removeprefix('refs/heads/')}


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
            clone_env = safe_git.clean_environment(env)
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


def github_origin(url, full_name):
    """Whether `url` is GitHub's address for `full_name`, in its HTTPS or SSH spelling (no user, no other host)."""
    pattern = r'(?:https://github\.com/|git@github\.com:|ssh://git@github\.com/)' + re.escape(full_name) + r'(?:\.git)?/?'
    return re.fullmatch(pattern, str(url or '').strip(), re.I) is not None


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

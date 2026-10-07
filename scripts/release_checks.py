#!/usr/bin/env python3
"""Release checks.

    python scripts/release_checks.py            # the default Python and core browser suites (the per-PR gate), under 300 s
    python scripts/release_checks.py --release  # reuse matching default evidence or run it, then opt-in and whole-product checks

`--release` reuses the default suites only when this trusted local worktree has a valid receipt for the exact clean
candidate and environment. Otherwise it runs the default suites once on the candidate. It then runs what the default
leaves out (`pytest -m slow` and `node scripts/ui-tests.cjs --non-core`) while it builds the candidate images once,
then runs the whole-product checks against them at the same time: docker/smoke.sh,
docker/side-jobs-smoke.sh and `scripts/journey-test.sh --release` (install the previous release, upgrade to the
candidate, roll back a migrating update). The journey starts installing the previous release while the images build. Each check gets its own Docker names and smoke a free host port, so they run
side by side; two release checks must not run at once, since the journey's candidate tags are fixed.

Use the test environment's Python to invoke this script. Existing pytest and TICO_UI_JOBS settings still
select concurrency for the default suites.
"""
import argparse
import hashlib
import hmac
import json
import os
from pathlib import Path
import platform
import secrets
import shutil
import stat
import socket
import subprocess
import sys
import tempfile
import threading
import time


ROOT = Path(__file__).resolve().parents[1]
BUDGET_SECONDS = 300
DEFAULT_RECEIPT_MAX_AGE_SECONDS = 24 * 60 * 60
CANDIDATE = 'v9.9.9'   # what scripts/journey-test.sh calls a local build
DEPENDENCY_FILES = ('pytest.ini', 'backend/requirements.txt', 'backend/requirements-dev.txt',
                    'package.json', 'package-lock.json', 'ui/package.json', 'ui/package-lock.json')
RECEIPT_ENV = ('PYTEST_ADDOPTS', 'PYTEST_PLUGINS', 'PYTEST_DISABLE_PLUGIN_AUTOLOAD', 'PYTHONPATH',
               'PYTHONHASHSEED', 'PYTHONWARNINGS', 'NODE_OPTIONS', 'NODE_PATH', 'CI', 'HOME', 'LANG', 'LC_ALL', 'TZ',
               'TICO_PYTHON', 'TICO_UI_JOBS', 'TICO_UI_BUNDLE', 'TICO_BROWSER', 'TICO_BROWSER_CHANNEL',
               'TICO_TAG_TEST_ONLY', 'TASKS_ONLY', 'PLAYWRIGHT_BROWSERS_PATH')
DEFAULT_SELECTION = {'python': 'not slow', 'browser': 'core'}
IMAGES = (('server', 'ghcr.io/ticoteam/tico', 'tico-rc'),
          ('runner', 'ghcr.io/ticoteam/tico-runner', 'tico-rc-runner'),
          ('updater', 'ghcr.io/ticoteam/tico-updater', 'tico-rc-updater'))


def default_commands():
    return [[sys.executable, '-m', 'pytest', '-q', '-m', 'not slow'],
            ['node', 'scripts/ui-tests.cjs', '--core']]


def git_snapshot():
    def git(*args):
        return subprocess.run(['git', *args], cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()
    return {'commit': git('rev-parse', 'HEAD'), 'tree': git('rev-parse', 'HEAD^{tree}'),
            'clean': not bool(git('status', '--porcelain=v1', '--untracked-files=all'))}


def _run_identity_command(command, timeout=10, cwd=ROOT):
    result = subprocess.run(command, cwd=cwd, capture_output=True, text=True, timeout=timeout, check=True)
    return result.stdout.strip()


def environment_identity():
    """Hash the test dependencies and runtime inputs without storing environment values."""
    manifests = {}
    for relative in DEPENDENCY_FILES:
        path = ROOT / relative
        manifests[relative] = hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None
    python_packages = _run_identity_command([sys.executable, '-m', 'pip', 'freeze', '--all'], timeout=20)
    node = shutil.which('node')
    node_version = _run_identity_command([node, '--version']) if node else None
    browser_runtime = None
    if node:
        browser_runtime = _run_identity_command([
            node, '-e',
            "const fs=require('fs'),path=require('path'); const p=require('playwright'); "
            "const name=process.env.TICO_BROWSER==='webkit'?'webkit':'chromium'; const b=p[name]; const f=b.executablePath(); "
            "let s=null; try { const x=fs.statSync(f); s={size:x.size,mtime:x.mtimeMs}; } catch (_) {} "
            "const meta=require(path.join(path.dirname(require.resolve('playwright')),'package.json')); "
            "console.log(JSON.stringify({playwright:meta.version,browser:name,executable:f,stat:s}));"
        ], timeout=10, cwd=ROOT / 'ui')
    env = {name: os.environ.get(name) for name in RECEIPT_ENV}
    material = {
        'schema': 1,
        'dependencies': manifests,
        'python': {'path': str(Path(sys.executable).resolve()), 'version': sys.version,
                   'packages_sha256': hashlib.sha256(python_packages.encode()).hexdigest()},
        'node': {'path': str(Path(node).resolve()) if node else None, 'version': node_version,
                 'browser_runtime': browser_runtime},
        'platform': {'system': platform.system(), 'release': platform.release(), 'machine': platform.machine()},
        'path_sha256': hashlib.sha256(os.environ.get('PATH', '').encode()).hexdigest(),
        'environment': env,
    }
    encoded = json.dumps(material, sort_keys=True, separators=(',', ':')).encode()
    return hashlib.sha256(encoded).hexdigest()


def receipt_paths():
    raw = subprocess.run(['git', 'rev-parse', '--absolute-git-dir'], cwd=ROOT,
                         capture_output=True, text=True, check=True).stdout.strip()
    directory = Path(raw) / 'tico-validation'
    return directory, directory / 'default-core.json', directory / 'default-core.key'


def invalidate_default_receipt():
    try:
        _, receipt, _ = receipt_paths()
        receipt.unlink(missing_ok=True)
        return True
    except OSError:
        return False


def _receipt_key(path, create=False):
    try:
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(path.parent, 0o700)
        key = path.read_bytes()
    except FileNotFoundError:
        if not create:
            return None
        key = secrets.token_bytes(32)
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            return _receipt_key(path, create=False)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(key)
        return key
    except OSError:
        return None
    try:
        info = path.lstat()
    except OSError:
        return None
    if not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) & 0o077 or len(key) != 32:
        if create:
            try:
                path.unlink(missing_ok=True)
                return _receipt_key(path, create=True)
            except OSError:
                pass
        return None
    return key


def _canonical_json(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':')).encode()


def _write_signed_receipt(payload):
    directory, receipt, key_path = receipt_paths()
    key = _receipt_key(key_path, create=True)
    if key is None:
        return False
    envelope = {'payload': payload, 'signature': hmac.new(key, _canonical_json(payload), hashlib.sha256).hexdigest()}
    temp = receipt.with_name(receipt.name + f'.{os.getpid()}.tmp')
    try:
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(directory, 0o700)
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(envelope, stream, sort_keys=True)
            stream.write('\n')
        os.replace(temp, receipt)
        os.chmod(receipt, 0o600)
        return True
    except OSError:
        try:
            temp.unlink(missing_ok=True)
        except OSError:
            pass
        return False


def valid_default_receipt(snapshot=None, identity=None, now=None):
    """Read only the signed receipt in this worktree's Git metadata; never accept a caller path."""
    try:
        snapshot = snapshot or git_snapshot()
        if not snapshot.get('clean'):
            return None
        identity = identity or environment_identity()
        _, receipt, key_path = receipt_paths()
        envelope = json.loads(receipt.read_text(encoding='utf-8'))
        if not isinstance(envelope, dict) or set(envelope) != {'payload', 'signature'}:
            return None
        payload, signature = envelope['payload'], envelope['signature']
        required = {'schema', 'kind', 'status', 'commit', 'tree', 'environment', 'commands', 'selection',
                    'started_at', 'finished_at', 'elapsed_seconds', 'load_start', 'load_end', 'checks'}
        if not isinstance(payload, dict) or set(payload) != required:
            return None
        if not isinstance(signature, str) or len(signature) != 64:
            return None
        key = _receipt_key(key_path)
        if key is None:
            return None
        expected = hmac.new(key, _canonical_json(payload), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, expected):
            return None
        if (payload['schema'] != 1 or payload['kind'] != 'tico-default-validation' or payload['status'] != 'passed'
                or payload['commit'] != snapshot['commit'] or payload['tree'] != snapshot['tree']
                or payload['environment'] != identity or payload['commands'] != default_commands()
                or payload['selection'] != DEFAULT_SELECTION):
            return None
        if (not isinstance(payload['elapsed_seconds'], (int, float))
                or payload['elapsed_seconds'] >= BUDGET_SECONDS or payload['elapsed_seconds'] < 0):
            return None
        now = time.time() if now is None else now
        if not isinstance(payload['finished_at'], (int, float)) or not isinstance(payload['started_at'], (int, float)):
            return None
        if payload['finished_at'] > now + 60 or now - payload['finished_at'] > DEFAULT_RECEIPT_MAX_AGE_SECONDS:
            return None
        if payload['finished_at'] < payload['started_at'] or abs(
                payload['finished_at'] - payload['started_at'] - payload['elapsed_seconds']) > 0.02:
            return None
        checks = payload['checks']
        if (not isinstance(checks, list) or len(checks) != len(default_commands())
                or any(not isinstance(check, dict) or check.get('command') != command
                       or check.get('exit_code') != 0 or not isinstance(check.get('seconds'), (int, float))
                       or check['seconds'] < 0 for check, command in zip(checks, default_commands()))):
            return None
        return payload
    except (OSError, ValueError, TypeError, KeyError, subprocess.SubprocessError):
        return None


def ensure_default_validation():
    receipt = valid_default_receipt()
    if receipt:
        print(f"Reusing signed default validation receipt for {receipt['commit'][:7]} / {receipt['tree'][:7]}", flush=True)
        return 0
    print('No valid current default receipt; running the default gate once on this candidate', flush=True)
    return full()


def load():
    return ','.join(f'{value:.2f}' for value in os.getloadavg()) if hasattr(os, 'getloadavg') else 'unavailable'


def full():
    started, started_at, initial_load = time.monotonic(), time.time(), load()
    if not invalidate_default_receipt():
        print('Could not invalidate the previous default receipt; refusing to run with stale evidence', flush=True)
        return 1
    try:
        snapshot_before = git_snapshot()
        identity_before = environment_identity()
    except (OSError, ValueError, subprocess.SubprocessError):
        snapshot_before = identity_before = None
    env = {**os.environ, 'TICO_PYTHON': sys.executable}
    commands = default_commands()
    runs = []
    for name, command in (('Python', commands[0]), ('Browser', commands[1])):
        before = time.monotonic()
        result = subprocess.run(command, cwd=ROOT, env=env)
        seconds = time.monotonic() - before
        runs.append({'command': command, 'exit_code': result.returncode, 'seconds': round(seconds, 2)})
        print(f'{name} checks: exit {result.returncode}, {seconds:.2f}s', flush=True)
        if result.returncode:
            print(f'Release checks failed after {time.monotonic() - started:.2f}s; load {initial_load} -> {load()}', flush=True)
            return 1
    elapsed = time.monotonic() - started
    within_budget = elapsed < BUDGET_SECONDS
    print(f'Release checks {"passed" if within_budget else "exceeded budget"}: {elapsed:.2f}s / '
          f'{BUDGET_SECONDS}s; load {initial_load} -> {load()}', flush=True)
    if within_budget and snapshot_before and identity_before:
        try:
            snapshot_after = git_snapshot()
            identity_after = environment_identity()
        except (OSError, ValueError, subprocess.SubprocessError):
            snapshot_after = identity_after = None
        if (snapshot_before.get('clean') and snapshot_after == snapshot_before and identity_after == identity_before):
            finished_at = time.time()
            payload = {'schema': 1, 'kind': 'tico-default-validation', 'status': 'passed',
                       'commit': snapshot_before['commit'], 'tree': snapshot_before['tree'],
                       'environment': identity_before, 'commands': commands, 'selection': DEFAULT_SELECTION,
                       'started_at': started_at, 'finished_at': finished_at, 'elapsed_seconds': round(elapsed, 2),
                       'load_start': initial_load, 'load_end': load(), 'checks': runs}
            if _write_signed_receipt(payload):
                print('Wrote a signed per-worktree default validation receipt in Git metadata', flush=True)
            else:
                print('Default checks passed, but the local receipt could not be written', flush=True)
        else:
            print('Default checks passed, but no reusable receipt was written: candidate or environment changed, or tree is dirty', flush=True)
    return 0 if within_budget else 1


def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


class Check:
    """One command in the background, its output in a log file, its wall time recorded."""

    def __init__(self, name, command, log_dir, env):
        self.name, self.log = name, Path(log_dir) / f'{name}.log'
        self.started = time.monotonic()
        self.stream = open(self.log, 'wb')
        self.process = subprocess.Popen(command, cwd=ROOT, env=env, stdout=self.stream, stderr=subprocess.STDOUT)
        self.code = self.seconds = None
        self.thread = threading.Thread(target=self.wait, daemon=True)
        self.thread.start()

    def wait(self):
        self.code = self.process.wait()
        self.seconds = time.monotonic() - self.started
        self.stream.close()
        print(f'  {self.name}: {"ok" if self.code == 0 else f"FAILED (exit {self.code})"} in {self.seconds:.0f}s', flush=True)

    def join(self):
        self.thread.join()
        return self.code

    def tail(self, lines=40):
        return '\n'.join(self.log.read_text(errors='replace').splitlines()[-lines:])


def release(args):
    started, initial_load = time.monotonic(), load()
    candidate = git_snapshot()
    if not candidate['clean']:
        print('Release checks require a clean candidate worktree', flush=True)
        return 1
    try:
        identity_before = environment_identity()
    except (OSError, ValueError, subprocess.SubprocessError):
        identity_before = None
        print('Could not identify the environment for receipt reuse; the default gate will run', flush=True)
    commit = candidate['commit']
    if ensure_default_validation():
        print('Release checks stopped because the default candidate gate failed', flush=True)
        return 1
    try:
        candidate_after = git_snapshot()
        identity_after = environment_identity()
    except (OSError, ValueError, subprocess.SubprocessError):
        print('Could not verify the candidate and environment after the default gate', flush=True)
        return 1
    if candidate_after != candidate or identity_before is None or identity_after != identity_before:
        print('Release candidate or validation environment changed after the default gate', flush=True)
        return 1
    logs = Path(tempfile.mkdtemp(prefix='tico-release-check-'))
    ready = logs / 'images-ready'
    base_env = {**os.environ, 'DOCKER_BUILDKIT': '1'}
    print(f'Release check: candidate {CANDIDATE} from {commit[:7]}; logs in {logs}', flush=True)

    journey_env = {**base_env, 'TICO_JOURNEY_IMAGES_READY': str(ready)}
    journey_cmd = ['bash', 'scripts/journey-test.sh', '--release'] + (['--previous', args.previous] if args.previous else [])
    checks = [Check('journey', journey_cmd, logs, journey_env),   # installs the previous release while the images build
              Check('slow-python', [sys.executable, '-m', 'pytest', '-q', '-m', 'slow'], logs,
                    {**base_env, 'TICO_PYTHON': sys.executable}),
              Check('non-core-browser', ['node', 'scripts/ui-tests.cjs', '--non-core'], logs,
                    {**base_env, 'TICO_PYTHON': sys.executable})]

    builds = [Check(f'build-{target}', ['docker', 'build', '-q', '--target', target,
                                        '--build-arg', f'TICO_VERSION={CANDIDATE}', '--build-arg', f'TICO_COMMIT={commit}',
                                        '--build-arg', f'TICO_REPOSITORY={os.environ.get("TICO_JOURNEY_REPOSITORY", "ticoteam/tico")}',
                                        '-t', f'{published}:{CANDIDATE}', '-t', f'{local}:local', '.'], logs, base_env)
              for target, published, local in IMAGES]
    built = all(build.join() == 0 for build in builds)
    ready.write_text('ok' if built else 'failed')
    if built:
        local_env = {**base_env, 'TICO_IMAGE': 'tico-rc', 'TICO_TAG': 'local', 'TICO_RUNNER_IMAGE': 'tico-rc-runner',
                     'TICO_UPDATER_IMAGE': 'tico-rc-updater'}
        checks.append(Check('smoke', ['bash', 'docker/smoke.sh'], logs,
                            {**local_env, 'TICO_SMOKE_PROJECT': 'tico-rc-smoke', 'TICO_SMOKE_PORT': str(free_port())}))
        checks.append(Check('side-jobs', ['bash', 'docker/side-jobs-smoke.sh'], logs,
                            {**local_env, 'TICO_SIDEJOBS_NAME': 'tico-rc-sidejobs'}))
    failed = [check for check in builds + checks if check.join() != 0]
    for check in failed:
        print(f'\n--- {check.name} (last lines of {check.log}) ---\n{check.tail()}', flush=True)
    elapsed = time.monotonic() - started
    verdict = 'failed' if failed else 'passed' if elapsed < BUDGET_SECONDS else 'passed, over budget'
    print(f'Release check {verdict}: {elapsed:.0f}s / {BUDGET_SECONDS}s; load {initial_load} -> {load()}', flush=True)
    return 1 if failed else 0


def main():
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('--release', action='store_true',
                        help='release gate (reuse or run default checks, then whole-product checks)')
    parser.add_argument('--previous', help='with --release: the release the journey upgrades from (default: newest tag)')
    args = parser.parse_args()
    return release(args) if args.release else full()


if __name__ == '__main__':
    raise SystemExit(main())

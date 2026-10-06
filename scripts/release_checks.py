#!/usr/bin/env python3
"""Release checks.

    python scripts/release_checks.py            # the default Python and core browser suites (the per-PR gate), under 300 s
    python scripts/release_checks.py --release  # the release gate: the opt-in tests and the whole product, under 300 s

Every PR runs the default suites against main before it merges, so a release does not run them again. `--release`
runs what the default leaves out (`pytest -m slow` and `node scripts/ui-tests.cjs --all`) while it builds the candidate
images once, then runs the whole-product checks against them at the same time: docker/smoke.sh,
docker/side-jobs-smoke.sh and `scripts/journey-test.sh --release` (install the previous release, upgrade to the
candidate, roll back a migrating update). The journey starts installing the previous release while the images build. Each check gets its own Docker names and smoke a free host port, so they run
side by side; two release checks must not run at once, since the journey's candidate tags are fixed.

Use the test environment's Python to invoke this script. Existing pytest and TICO_UI_JOBS settings still
select concurrency for the default suites.
"""
import argparse
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import time


ROOT = Path(__file__).resolve().parents[1]
BUDGET_SECONDS = 300
CANDIDATE = 'v9.9.9'   # what scripts/journey-test.sh calls a local build
IMAGES = (('server', 'ghcr.io/ticoteam/tico', 'tico-rc'),
          ('runner', 'ghcr.io/ticoteam/tico-runner', 'tico-rc-runner'),
          ('updater', 'ghcr.io/ticoteam/tico-updater', 'tico-rc-updater'))


def load():
    return ','.join(f'{value:.2f}' for value in os.getloadavg()) if hasattr(os, 'getloadavg') else 'unavailable'


def full():
    started, initial_load = time.monotonic(), load()
    env = {**os.environ, 'TICO_PYTHON': sys.executable}
    for name, command in (
            ('Python', [sys.executable, '-m', 'pytest', '-q']),
            ('Browser', ['node', 'scripts/ui-tests.cjs'])):
        before = time.monotonic()
        result = subprocess.run(command, cwd=ROOT, env=env)
        print(f'{name} checks: exit {result.returncode}, {time.monotonic() - before:.2f}s', flush=True)
        if result.returncode:
            print(f'Release checks failed after {time.monotonic() - started:.2f}s; load {initial_load} -> {load()}', flush=True)
            return 1
    elapsed = time.monotonic() - started
    within_budget = elapsed < BUDGET_SECONDS
    print(f'Release checks {"passed" if within_budget else "exceeded budget"}: {elapsed:.2f}s / '
          f'{BUDGET_SECONDS}s; load {initial_load} -> {load()}', flush=True)
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
    commit = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()
    logs = Path(tempfile.mkdtemp(prefix='tico-release-check-'))
    ready = logs / 'images-ready'
    base_env = {**os.environ, 'DOCKER_BUILDKIT': '1'}
    print(f'Release check: candidate {CANDIDATE} from {commit[:7]}; logs in {logs}', flush=True)

    journey_env = {**base_env, 'TICO_JOURNEY_IMAGES_READY': str(ready)}
    journey_cmd = ['bash', 'scripts/journey-test.sh', '--release'] + (['--previous', args.previous] if args.previous else [])
    checks = [Check('journey', journey_cmd, logs, journey_env),   # installs the previous release while the images build
              Check('slow-python', [sys.executable, '-m', 'pytest', '-q', '-m', 'slow'], logs,
                    {**base_env, 'TICO_PYTHON': sys.executable}),
              Check('all-browser', ['node', 'scripts/ui-tests.cjs', '--all'], logs, {**base_env, 'TICO_PYTHON': sys.executable})]

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
    parser.add_argument('--release', action='store_true', help='whole-product checks only (the release gate)')
    parser.add_argument('--previous', help='with --release: the release the journey upgrades from (default: newest tag)')
    args = parser.parse_args()
    return release(args) if args.release else full()


if __name__ == '__main__':
    raise SystemExit(main())

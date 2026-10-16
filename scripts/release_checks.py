#!/usr/bin/env python3
"""Release checks.

    python scripts/release_checks.py            # the default Python and core browser suites, under 300 s
    python scripts/release_checks.py --release  # the release gate: every test and the whole product, under 480 s

A PR runs only the tests for what it changed, so the full suite runs here, once, right before a release. `--release`
runs the journey's previous-release install while it builds the candidate images once. After all builds finish,
it runs Python tests except Docker isolation (`pytest -m "slow or not slow"`) and every browser test
(`node scripts/ui-tests.cjs --all`), alongside the Docker isolation tests
and whole-product checks against the successfully built images: docker/smoke.sh,
docker/side-jobs-smoke.sh and `scripts/journey-test.sh --release` (install the previous release, upgrade to the
candidate, roll back a migrating update). The journey starts installing the previous release while the images build. Each check gets its own Docker names and smoke a free host port, so they run
side by side; two release checks must not run at once, since the journey's candidate tags are fixed.

`--part docker|python|browser` runs one part of the same gate, so a hosted validation can spread it over parallel
VMs: `docker` is everything except the two suites, `browser` the browser suite, and `python --shard I/N` every I-th of
the collected Python tests (the same selection as the full gate). Together the parts run exactly the full gate.

Use the test environment's Python to invoke this script. The release gate defaults to half the CPU count in
Python workers (at least one); the browser runner selects free-core concurrency and load-scaled timeouts.
TICO_PYTHON_WORKERS, TICO_UI_JOBS and TICO_UI_SLOWDOWN override those defaults.
Existing pytest and TICO_UI_JOBS settings still select concurrency for the default suites.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import socket
import re
import subprocess
import sys
import tempfile
import threading
import time


ROOT = Path(__file__).resolve().parents[1]
BUDGET_SECONDS = 480
DEFAULT_BUDGET_SECONDS = 300
CANDIDATE = 'v9.9.9'   # what scripts/journey-test.sh calls a local build
PYTHON_SELECTION = ['-m', 'slow or not slow', '--ignore=runner/tests/test_isolation_docker.py']
PARTS = ('docker', 'python', 'browser')
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
    within_budget = elapsed < DEFAULT_BUDGET_SECONDS
    print(f'Release checks {"passed" if within_budget else "exceeded budget"}: {elapsed:.2f}s / '
          f'{DEFAULT_BUDGET_SECONDS}s; load {initial_load} -> {load()}', flush=True)
    return 0 if within_budget else 1


def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


class Check:
    """One command in the background, its output in a log file, its wall time recorded."""

    def __init__(self, name, command, log_dir, env, tests=False):
        self.name, self.log, self.tests = name, Path(log_dir) / f'{name}.log', tests
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
        # A test step that ran nothing (every test filtered out or skipped) proves nothing, so it fails.
        summary = re.sub(r'\x1b\[[0-9;]*m', '', self.tail(5))   # a coloured summary reads the same
        if self.tests and self.code == 0 and not re.search(r'(?<!\d)[1-9]\d* passed\b', summary):
            print(f'  {self.name}: no test passed; a release test step must run tests', flush=True)
            self.code = 1
        print(f'  {self.name}: {"ok" if self.code == 0 else f"FAILED (exit {self.code})"} in {self.seconds:.0f}s', flush=True)

    def join(self):
        self.thread.join()
        return self.code

    def tail(self, lines=40):
        return '\n'.join(self.log.read_text(errors='replace').splitlines()[-lines:])


def phase_table(checks, started, not_run=()):
    print('\n| Phase | Start offset (s) | Duration (s) | Exit |\n'
          '| --- | ---: | ---: | --- |', flush=True)
    for check in sorted(checks, key=lambda check: check.started):
        print(f'| {check.name} | {check.started - started:.1f} | {check.seconds:.1f} | {check.code} |', flush=True)
    for name in not_run:
        print(f'| {name} | - | - | not run |', flush=True)


def python_workers():
    return str(max(1, int(os.environ.get('TICO_PYTHON_WORKERS', max(1, (os.cpu_count() or 2) // 2)))))


def finish(checks, started, initial_load, not_run=()):
    failed = [check for check in checks if check.join() != 0]
    for check in failed:
        print(f'\n--- {check.name} (last lines of {check.log}) ---\n{check.tail()}', flush=True)
    phase_table(checks, started, not_run)
    elapsed = time.monotonic() - started
    verdict = 'failed' if failed else 'passed' if elapsed < BUDGET_SECONDS else 'passed, over budget'
    print(f'Release check {verdict}: {elapsed:.0f}s / {BUDGET_SECONDS}s; load {initial_load} -> {load()}', flush=True)
    return 1 if failed else 0


def shard(text):
    index, _, count = text.partition('/')
    if not (index.isdigit() and count.isdigit() and 1 <= int(index) <= int(count)):
        raise argparse.ArgumentTypeError('expected I/N with 1 <= I <= N')
    return int(index), int(count)


def python_shard(args):
    """Every N-th collected test, so slow parametrized cases spread over the shards; the union is the full selection."""
    started, initial_load = time.monotonic(), load()
    index, count = args.shard
    env = {**os.environ, 'TICO_PYTHON': sys.executable}
    collected = subprocess.run([sys.executable, '-m', 'pytest', '-q', '-n', '0', '--collect-only', *PYTHON_SELECTION],
                               cwd=ROOT, env=env, capture_output=True, text=True)
    tests = [line for line in collected.stdout.splitlines() if '::' in line and not line.startswith(' ')]
    if collected.returncode or not tests:
        print(collected.stdout[-4000:] + collected.stderr[-4000:], flush=True)
        print(f'Python shard {index}/{count}: collection failed (exit {collected.returncode})', flush=True)
        return 1
    selected = tests[index - 1::count]
    receipt = {'index': index, 'count': count, 'collected': len(tests), 'selected': len(selected),
               'collection_sha256': hashlib.sha256('\n'.join(tests).encode()).hexdigest()}
    print('PYTHON_SHARD ' + json.dumps(receipt, sort_keys=True), flush=True)
    logs = Path(tempfile.mkdtemp(prefix='tico-release-check-'))
    check = Check(f'python-shard-{index}', [sys.executable, '-m', 'pytest', '-q', '-n', python_workers(), *PYTHON_SELECTION,
                                            *selected], logs, env, tests=True)
    return finish([check], started, initial_load)


def release(args):
    if args.part == 'python':
        return python_shard(args)
    started, initial_load = time.monotonic(), load()
    if args.part == 'browser':
        logs = Path(tempfile.mkdtemp(prefix='tico-release-check-'))
        test_env = {**os.environ, 'TICO_PYTHON': sys.executable}
        return finish([Check('all-browser', ['node', 'scripts/ui-tests.cjs', '--all'], logs, test_env)], started, initial_load)
    commit = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()
    logs = Path(tempfile.mkdtemp(prefix='tico-release-check-'))
    ready = logs / 'images-ready'
    base_env = {**os.environ, 'DOCKER_BUILDKIT': '1'}
    workers = python_workers()
    test_env = {**base_env, 'TICO_PYTHON': sys.executable}
    print(f'Release check: candidate {CANDIDATE} from {commit[:7]}; logs in {logs}', flush=True)

    journey_env = {**base_env, 'TICO_JOURNEY_IMAGES_READY': str(ready)}
    journey_cmd = ['bash', 'scripts/journey-test.sh', '--release'] + (['--previous', args.previous] if args.previous else [])
    checks = [Check('journey', journey_cmd, logs, journey_env)]   # installs the previous release while the images build

    builds = [Check(f'build-{target}', ['docker', 'build', '-q', '--target', target,
                                        '--build-arg', f'TICO_VERSION={CANDIDATE}', '--build-arg', f'TICO_COMMIT={commit}',
                                        '--build-arg', f'TICO_REPOSITORY={os.environ.get("TICO_JOURNEY_REPOSITORY", "ticoteam/tico")}',
                                        '-t', f'{published}:{CANDIDATE}', '-t', f'{local}:local', '.'], logs, base_env)
              for target, published, local in IMAGES]
    build_codes = [build.join() for build in builds]  # wait for every build, including after a failed one
    built = all(code == 0 for code in build_codes)
    ready.write_text('ok' if built else 'failed')
    if args.part is None:   # --part docker leaves the two suites to their own parts
        checks.append(Check('all-python', [sys.executable, '-m', 'pytest', '-q', '-n', workers, *PYTHON_SELECTION],
                            logs, test_env, tests=True))
        checks.append(Check('all-browser', ['node', 'scripts/ui-tests.cjs', '--all'], logs, test_env))
    if built:
        local_env = {**base_env, 'TICO_IMAGE': 'tico-rc', 'TICO_TAG': 'local', 'TICO_RUNNER_IMAGE': 'tico-rc-runner',
                     'TICO_UPDATER_IMAGE': 'tico-rc-updater'}
        checks.append(Check('smoke', ['bash', 'docker/smoke.sh'], logs,
                            {**local_env, 'TICO_SMOKE_PROJECT': 'tico-rc-smoke', 'TICO_SMOKE_PORT': str(free_port())}))
        checks.append(Check('side-jobs', ['bash', 'docker/side-jobs-smoke.sh'], logs,
                            {**local_env, 'TICO_SIDEJOBS_NAME': 'tico-rc-sidejobs'}))
        checks.append(Check('isolation', [sys.executable, '-m', 'pytest', '-q', '-n', workers, '-m', 'slow or not slow',
                                         'runner/tests/test_isolation_docker.py'], logs,
                            {**test_env,
                             'TICO_RUNNER_TEST_IMAGE': f"{local_env['TICO_RUNNER_IMAGE']}:{local_env['TICO_TAG']}"},
                            tests=True))
    else:
        print('  isolation: not run (candidate image builds failed)', flush=True)
    if args.part == 'docker':
        print('  all-python, all-browser: run as their own parts of this gate', flush=True)
    return finish(builds + checks, started, initial_load, () if built else ('smoke', 'side-jobs', 'isolation'))


def main():
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('--release', action='store_true', help='every test and the whole-product checks (the release gate)')
    parser.add_argument('--previous', help='with --release: the release the journey upgrades from (default: newest tag)')
    parser.add_argument('--part', choices=PARTS, help='with --release: run only this part of the gate')
    parser.add_argument('--shard', type=shard, help='with --part python: run shard I of N (I/N)')
    args = parser.parse_args()
    if (args.part and not args.release) or ((args.part == 'python') != bool(args.shard)):
        parser.error('--part needs --release, and --part python needs --shard I/N (only it)')
    return release(args) if args.release else full()


if __name__ == '__main__':
    raise SystemExit(main())

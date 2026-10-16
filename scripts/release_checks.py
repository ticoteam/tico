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

`--part docker|python|browser` runs one part of that same gate, so separate machines can share it: `docker` is the
builds, Docker isolation and whole-product checks, `python` the Python tests (`--shard 2/3` runs the second of three
balanced groups of test files) and `browser` every browser test. Run on separate machines, the parts start at once
rather than after the image builds. With no `--part` the whole gate runs here.

Use the test environment's Python to invoke this script. The release gate defaults to half the CPU count in
Python workers (at least one); the browser runner selects free-core concurrency and load-scaled timeouts.
TICO_PYTHON_WORKERS, TICO_UI_JOBS and TICO_UI_SLOWDOWN override those defaults.
Existing pytest and TICO_UI_JOBS settings still select concurrency for the default suites.
"""
import argparse
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
IMAGES = (('server', 'ghcr.io/ticoteam/tico', 'tico-rc'),
          ('runner', 'ghcr.io/ticoteam/tico-runner', 'tico-rc-runner'),
          ('updater', 'ghcr.io/ticoteam/tico-updater', 'tico-rc-updater'))
PARTS = ('all', 'docker', 'python', 'browser')
ISOLATION = 'runner/tests/test_isolation_docker.py'
SLOW_WEIGHT = 20   # a slow test (real git, Docker, servers) costs about this many ordinary ones when balancing shards


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


def shard_files(index, count):
    """The Python test files for shard `index` of `count`: whole files, balanced by test count with slow tests weighed
    heavier, the same split on every machine for the same commit."""
    def collected(marker):
        listing = subprocess.run([sys.executable, '-m', 'pytest', '--collect-only', '-q', '-n', '0', '-m', marker,
                                  f'--ignore={ISOLATION}'], cwd=ROOT, capture_output=True, text=True, check=True).stdout
        counts = {}
        for line in listing.splitlines():
            if '::' in line:
                path = line.split('::', 1)[0]
                counts[path] = counts.get(path, 0) + 1
        return counts
    slow = collected('slow')
    weights = {path: tests + (SLOW_WEIGHT - 1) * slow.get(path, 0)
               for path, tests in collected('slow or not slow').items()}
    loads, files = [0] * count, [[] for _ in range(count)]
    for path in sorted(weights, key=lambda path: (-weights[path], path)):
        lightest = loads.index(min(loads))
        loads[lightest] += weights[path]
        files[lightest].append(path)
    return sorted(files[index - 1])


def phase_table(checks, started, not_run=()):
    print('\n| Phase | Start offset (s) | Duration (s) | Exit |\n'
          '| --- | ---: | ---: | --- |', flush=True)
    for check in sorted(checks, key=lambda check: check.started):
        print(f'| {check.name} | {check.started - started:.1f} | {check.seconds:.1f} | {check.code} |', flush=True)
    for name in not_run:
        print(f'| {name} | - | - | not run |', flush=True)


def release(args):
    part, shard = args.part, args.shard
    docker, python, browser = part in ('all', 'docker'), part in ('all', 'python'), part in ('all', 'browser')
    started, initial_load = time.monotonic(), load()
    commit = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()
    logs = Path(tempfile.mkdtemp(prefix='tico-release-check-'))
    ready = logs / 'images-ready'
    base_env = {**os.environ, 'DOCKER_BUILDKIT': '1'}
    workers = str(max(1, int(os.environ.get('TICO_PYTHON_WORKERS', max(1, (os.cpu_count() or 2) // 2)))))
    test_env = {**base_env, 'TICO_PYTHON': sys.executable}
    scope = '' if part == 'all' else f'; part {part}' + (f' {shard[0]}/{shard[1]}' if shard else '')
    print(f'Release check: candidate {CANDIDATE} from {commit[:7]}{scope}; logs in {logs}', flush=True)

    checks, builds, built = [], [], True
    if docker:
        journey_env = {**base_env, 'TICO_JOURNEY_IMAGES_READY': str(ready)}
        journey_cmd = ['bash', 'scripts/journey-test.sh', '--release'] + (['--previous', args.previous] if args.previous else [])
        checks.append(Check('journey', journey_cmd, logs, journey_env))   # installs the previous release while the images build

        builds = [Check(f'build-{target}', ['docker', 'build', '-q', '--target', target,
                                            '--build-arg', f'TICO_VERSION={CANDIDATE}', '--build-arg', f'TICO_COMMIT={commit}',
                                            '--build-arg', f'TICO_REPOSITORY={os.environ.get("TICO_JOURNEY_REPOSITORY", "ticoteam/tico")}',
                                            '-t', f'{published}:{CANDIDATE}', '-t', f'{local}:local', '.'], logs, base_env)
                  for target, published, local in IMAGES]
        build_codes = [build.join() for build in builds]  # wait for every build, including after a failed one
        built = all(code == 0 for code in build_codes)
        ready.write_text('ok' if built else 'failed')
    if python:
        files = shard_files(*shard) if shard else []
        if shard:
            print(f'  all-python: shard {shard[0]}/{shard[1]}, {len(files)} test files', flush=True)
        checks.append(Check('all-python', [sys.executable, '-m', 'pytest', '-q', '-n', workers, '-m', 'slow or not slow',
                                          f'--ignore={ISOLATION}', *files], logs, test_env, tests=True))
    if browser:
        checks.append(Check('all-browser', ['node', 'scripts/ui-tests.cjs', '--all'], logs, test_env))
    if docker and built:
        local_env = {**base_env, 'TICO_IMAGE': 'tico-rc', 'TICO_TAG': 'local', 'TICO_RUNNER_IMAGE': 'tico-rc-runner',
                     'TICO_UPDATER_IMAGE': 'tico-rc-updater'}
        checks.append(Check('smoke', ['bash', 'docker/smoke.sh'], logs,
                            {**local_env, 'TICO_SMOKE_PROJECT': 'tico-rc-smoke', 'TICO_SMOKE_PORT': str(free_port())}))
        checks.append(Check('side-jobs', ['bash', 'docker/side-jobs-smoke.sh'], logs,
                            {**local_env, 'TICO_SIDEJOBS_NAME': 'tico-rc-sidejobs'}))
        checks.append(Check('isolation', [sys.executable, '-m', 'pytest', '-q', '-n', workers, '-m', 'slow or not slow',
                                         ISOLATION], logs,
                            {**test_env,
                             'TICO_RUNNER_TEST_IMAGE': f"{local_env['TICO_RUNNER_IMAGE']}:{local_env['TICO_TAG']}"},
                            tests=True))
    elif docker:
        print('  isolation: not run (candidate image builds failed)', flush=True)
    failed = [check for check in builds + checks if check.join() != 0]
    for check in failed:
        print(f'\n--- {check.name} (last lines of {check.log}) ---\n{check.tail()}', flush=True)
    phase_table(builds + checks, started, () if built else ('smoke', 'side-jobs', 'isolation'))
    elapsed = time.monotonic() - started
    verdict = 'failed' if failed else 'passed' if elapsed < BUDGET_SECONDS else 'passed, over budget'
    print(f'Release check {verdict}: {elapsed:.0f}s / {BUDGET_SECONDS}s; load {initial_load} -> {load()}', flush=True)
    return 1 if failed else 0


def shard_spec(value):
    match = re.fullmatch(r'([1-9][0-9]*)/([1-9][0-9]*)', value)
    if not match or int(match[1]) > int(match[2]):
        raise argparse.ArgumentTypeError('a shard is K/N with 1 <= K <= N')
    return int(match[1]), int(match[2])


def main():
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('--release', action='store_true', help='every test and the whole-product checks (the release gate)')
    parser.add_argument('--previous', help='with --release: the release the journey upgrades from (default: newest tag)')
    parser.add_argument('--part', choices=PARTS, default='all', help='with --release: run only this part of the gate')
    parser.add_argument('--shard', type=shard_spec, help='with --part python: run group K of N test files, as K/N')
    args = parser.parse_args()
    if (args.part != 'all' or args.shard) and not args.release:
        parser.error('--part and --shard select part of the release gate; use them with --release')
    if args.shard and args.part != 'python':
        parser.error('--shard splits the Python tests; use it with --part python')
    return release(args) if args.release else full()


if __name__ == '__main__':
    raise SystemExit(main())

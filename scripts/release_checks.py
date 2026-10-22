#!/usr/bin/env python3
"""Release checks.

    python scripts/release_checks.py            # the default Python and core browser suites, under 300 s
    python scripts/release_checks.py --release  # the release gate: every test and the whole product, under 480 s
    python scripts/release_checks.py --shard 2/3  # file group 2 of 3 of the default Python suite (a PR check job)

A PR runs only the tests for what it changed, so the full suite runs here, once, right before a release. `--release`
runs the journey's previous-release install while it builds the candidate images once. After all builds finish,
it runs Python tests except Docker isolation (`pytest -m "slow or not slow"`) and every browser test
(`node scripts/ui-tests.cjs --all`), alongside the Docker isolation tests
and whole-product checks against the successfully built images: docker/smoke.sh,
docker/side-jobs-smoke.sh and `scripts/journey-test.sh --release` (install the previous release, upgrade to the
candidate, roll back a migrating update). The journey starts installing the previous release while the images build. Each check gets its own Docker names, smoke a free host port, and each compose stack its own edge network range
(EDGE, checked to be creatable side by side before any stack starts), so they run
side by side; two release checks must not run at once, since the journey's candidate tags are fixed.

`--part product|python|browser` runs one slice of the same gate so the hosted Release validation workflow can
spread it over several VMs: `product` is the journey, image builds, smoke, side-jobs and Docker isolation; `python`
is the Python tests (with `--shard K/N`, the Kth of N file groups balanced by test count); `browser` is every browser
test. Without `--part` the whole gate runs on this machine, as before.

`--shard K/N` without `--release` runs the Kth of N file groups of the default Python suite (plain `pytest`, what PR
checks run), balanced the same way, so the PR-check Python job can spread over several VMs. The N groups together run
every file the single default run collects, each once.

Use the test environment's Python to invoke this script. The release gate defaults to half the CPU count in
Python workers (at least one); the browser runner selects free-core concurrency and load-scaled timeouts.
TICO_PYTHON_WORKERS, TICO_UI_JOBS and TICO_UI_SLOWDOWN override those defaults.
Existing pytest and TICO_UI_JOBS settings still select concurrency for the default suites.

Run locally, it first takes one of the computer's heavy-job slots (scripts/heavy.sh): at most TICO_HEAVY_SLOTS heavy
runs at once on a box, the others waiting their turn. Hosted CI goes straight on.
"""
import argparse
import ipaddress
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
# The edge network range (A.B.C.0/28, see docker/edge-env.sh) of each compose stack the gate runs side by side. A
# separate project and host port do not separate networks: Docker refuses a second network on a range in use.
EDGE = {'journey': '172.30.252', 'smoke': '172.30.253', 'backup-test': '172.30.251'}   # the journey runs backup-test
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


def edge_settings(prefix):
    """The edge settings docker/edge-env.sh writes for one range, as the stacks append them to their env files."""
    output = subprocess.run(['bash', 'docker/edge-env.sh', prefix], cwd=ROOT, capture_output=True, text=True,
                            check=True).stdout
    return dict(line.split('=', 1) for line in output.splitlines())


def edge_networks(ranges=EDGE):
    """On this Docker host, before any stack starts: every gate stack's edge network can be created at the same time,
    and Docker refuses a second network on a range already held, so a shared range would fail here, not in a stack.
    Returns (ok, report); a failure names the existing networks on a clashing range."""
    settings = {name: edge_settings(prefix) for name, prefix in ranges.items()}
    probes = {name: f'tico-rc-edgecheck-{name}' for name in settings}
    overlap = 'tico-rc-edgecheck-overlap'

    def create(network, edge):
        return subprocess.Popen(['docker', 'network', 'create', '--subnet', edge['TICO_EDGE_SUBNET'],
                                 '--ip-range', edge['TICO_EDGE_DYNAMIC_RANGE'], network],
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)

    def remove():
        subprocess.run(['docker', 'network', 'rm', *probes.values(), overlap], capture_output=True, text=True)

    def holders():
        ids = subprocess.run(['docker', 'network', 'ls', '-q'], capture_output=True, text=True).stdout.split()
        listing = subprocess.run(['docker', 'network', 'inspect', '--format',
                                  '{{.Name}} {{range .IPAM.Config}}{{.Subnet}} {{end}}', *ids],
                                 capture_output=True, text=True).stdout if ids else ''
        wanted = [ipaddress.ip_network(edge['TICO_EDGE_SUBNET']) for edge in settings.values()]
        clashing = []
        for line in listing.splitlines():
            name, *subnets = line.split()
            for subnet in subnets:
                try:
                    network = ipaddress.ip_network(subnet, strict=False)
                except ValueError:
                    continue
                if any(network.version == want.version and network.overlaps(want) for want in wanted):
                    clashing.append(f'{name} {subnet}')
        return '; '.join(clashing)

    remove()   # leftovers of an interrupted check
    try:
        started = {name: create(probes[name], edge) for name, edge in settings.items()}   # all at once, as in the gate
        outputs = {name: process.communicate()[0].strip() for name, process in started.items()}
        failed = [name for name, process in started.items() if process.returncode]
        if failed:
            return False, (f'edge networks: could not create {", ".join(failed)} beside the others: '
                           + ' | '.join(outputs[name] for name in failed)
                           + f'; networks on these ranges: {holders() or "none listed"}')
        first = next(iter(settings))
        second = create(overlap, settings[first])
        refusal = second.communicate()[0].strip()
        if second.returncode == 0 or 'overlap' not in refusal.lower():
            return False, (f'edge networks: Docker did not refuse a second network on {first}\'s '
                           f'{settings[first]["TICO_EDGE_SUBNET"]} ({refusal or "created"}), so this check proves nothing')
        return True, ('edge networks: ' + ', '.join(f'{name} {edge["TICO_EDGE_SUBNET"]}' for name, edge in settings.items())
                      + f' created at once; a second network on {first}\'s range refused as expected')
    finally:
        remove()


ISOLATION_FILE = 'runner/tests/test_isolation_docker.py'
PARTS = ('product', 'python', 'browser')


def parse_shard(text):
    """'K/N' with 1 <= K <= N, as two integers."""
    match = re.fullmatch(r'([1-9]\d*)/([1-9]\d*)', text or '')
    if not match or int(match.group(1)) > int(match.group(2)):
        raise argparse.ArgumentTypeError('--shard must be K/N with 1 <= K <= N')
    return int(match.group(1)), int(match.group(2))


# What the release Python step selects; the default suite (PR checks) selects with pytest.ini alone.
RELEASE_SELECTION = ('-m', 'slow or not slow', f'--ignore={ISOLATION_FILE}')


def shard_files(index, total, env, selection=RELEASE_SELECTION):
    """The test files of shard `index` of `total`: every file pytest collects with `selection` (by default the
    release Python step's), grouped so each shard holds about the same number of tests (largest file first, into the
    lightest shard). Deterministic, so the N shards together cover each collected file exactly once."""
    result = subprocess.run([sys.executable, '-m', 'pytest', '--collect-only', '-q', '-n', '0', *selection],
                            cwd=ROOT, env=env, capture_output=True, text=True)
    if result.returncode:
        print(result.stdout[-4000:] + result.stderr[-4000:], flush=True)
        raise RuntimeError(f'test collection failed (exit {result.returncode})')
    counts = {}
    for line in result.stdout.splitlines():
        if '::' in line and not line.startswith(' '):
            name = line.split('::', 1)[0]
            counts[name] = counts.get(name, 0) + 1
    if len(counts) < total:
        raise RuntimeError(f'{len(counts)} test files cannot fill {total} shards')
    loads, groups = [0] * total, [[] for _ in range(total)]
    for name in sorted(counts, key=lambda name: (-counts[name], name)):
        lightest = min(range(total), key=lambda i: (loads[i], i))
        loads[lightest] += counts[name]
        groups[lightest].append(name)
    print(f'Python shard {index}/{total}: {len(groups[index - 1])} files, {loads[index - 1]} of '
          f'{sum(loads)} tests', flush=True)
    return sorted(groups[index - 1])


def python_shard(index, total):
    """File group `index` of `total` of the default Python suite: the PR-check Python job, split over VMs."""
    started, initial_load = time.monotonic(), load()
    env = {**os.environ, 'TICO_PYTHON': sys.executable}
    # Alone on its VM, a shard uses every core rather than pytest.ini's shared-box cap.
    workers = str(os.cpu_count() or 2)
    result = subprocess.run([sys.executable, '-m', 'pytest', '-q', '-n', workers] + shard_files(index, total, env, selection=()),
                            cwd=ROOT, env=env)
    print(f'Python shard {index}/{total}: exit {result.returncode}, {time.monotonic() - started:.2f}s; '
          f'load {initial_load} -> {load()}', flush=True)
    return result.returncode


def phase_table(checks, started, not_run=()):
    print('\n| Phase | Start offset (s) | Duration (s) | Exit |\n'
          '| --- | ---: | ---: | --- |', flush=True)
    for check in sorted(checks, key=lambda check: check.started):
        print(f'| {check.name} | {check.started - started:.1f} | {check.seconds:.1f} | {check.code} |', flush=True)
    for name in not_run:
        print(f'| {name} | - | - | not run |', flush=True)


def release(args):
    part, shard = getattr(args, 'part', None), getattr(args, 'shard', None)
    runs = lambda name: part in (None, name)
    started, initial_load = time.monotonic(), load()
    commit = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()
    logs = Path(tempfile.mkdtemp(prefix='tico-release-check-'))
    ready = logs / 'images-ready'
    base_env = {**os.environ, 'DOCKER_BUILDKIT': '1'}
    # Alone on its VM the Python slice can use every core; beside builds and browsers, half of them.
    default_workers = (os.cpu_count() or 2) if part == 'python' else max(1, (os.cpu_count() or 2) // 2)
    workers = str(max(1, int(os.environ.get('TICO_PYTHON_WORKERS', default_workers))))
    test_env = {**base_env, 'TICO_PYTHON': sys.executable}
    scope = '' if part is None else f'; part {part}' + (f' {shard[0]}/{shard[1]}' if shard else '')
    print(f'Release check: candidate {CANDIDATE} from {commit[:7]}{scope}; logs in {logs}', flush=True)

    builds, checks, built, not_run = [], [], True, ()
    if runs('product'):
        edge_ok, edge_report = edge_networks()
        print(f'  {edge_report}', flush=True)
        if not edge_ok:
            print('Release check failed: the gate stacks cannot hold their edge networks side by side', flush=True)
            return 1
        journey_env = {**base_env, 'TICO_JOURNEY_IMAGES_READY': str(ready), 'TICO_JOURNEY_EDGE': EDGE['journey'],
                       'TICO_BACKUP_EDGE': EDGE['backup-test']}
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
    if runs('python'):
        python_cmd = [sys.executable, '-m', 'pytest', '-q', '-n', workers, '-m', 'slow or not slow',
                      f'--ignore={ISOLATION_FILE}']
        name = 'all-python'
        if shard:
            python_cmd += shard_files(shard[0], shard[1], test_env)
            name = f'python-{shard[0]}-of-{shard[1]}'
        checks.append(Check(name, python_cmd, logs, test_env, tests=True))
    if runs('browser'):
        checks.append(Check('all-browser', ['node', 'scripts/ui-tests.cjs', '--all'], logs, test_env))
    if runs('product') and built:
        local_env = {**base_env, 'TICO_IMAGE': 'tico-rc', 'TICO_TAG': 'local', 'TICO_RUNNER_IMAGE': 'tico-rc-runner',
                     'TICO_UPDATER_IMAGE': 'tico-rc-updater'}
        checks.append(Check('smoke', ['bash', 'docker/smoke.sh'], logs,
                            {**local_env, 'TICO_SMOKE_PROJECT': 'tico-rc-smoke', 'TICO_SMOKE_PORT': str(free_port()),
                             'TICO_SMOKE_EDGE': EDGE['smoke']}))
        checks.append(Check('side-jobs', ['bash', 'docker/side-jobs-smoke.sh'], logs,
                            {**local_env, 'TICO_SIDEJOBS_NAME': 'tico-rc-sidejobs'}))
        checks.append(Check('isolation', [sys.executable, '-m', 'pytest', '-q', '-n', workers, '-m', 'slow or not slow',
                                         ISOLATION_FILE], logs,
                            {**test_env,
                             'TICO_RUNNER_TEST_IMAGE': f"{local_env['TICO_RUNNER_IMAGE']}:{local_env['TICO_TAG']}"},
                            tests=True))
    elif runs('product'):
        print('  isolation: not run (candidate image builds failed)', flush=True)
        not_run = ('smoke', 'side-jobs', 'isolation')
    failed = [check for check in builds + checks if check.join() != 0]
    for check in failed:
        print(f'\n--- {check.name} (last lines of {check.log}) ---\n{check.tail()}', flush=True)
    phase_table(builds + checks, started, not_run)
    elapsed = time.monotonic() - started
    verdict = 'failed' if failed else 'passed' if elapsed < BUDGET_SECONDS else 'passed, over budget'
    print(f'Release check {verdict}: {elapsed:.0f}s / {BUDGET_SECONDS}s; load {initial_load} -> {load()}', flush=True)
    return 1 if failed else 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('--release', action='store_true', help='every test and the whole-product checks (the release gate)')
    parser.add_argument('--previous', help='with --release: the release the journey upgrades from (default: newest tag)')
    parser.add_argument('--part', choices=PARTS, help='with --release: run only this slice of the gate')
    parser.add_argument('--shard', type=parse_shard,
                        help='run file group K of N (K/N) of the Python tests: alone, of the default suite (PR checks); '
                             'with --release --part python, of the release set')
    args = parser.parse_args(argv)
    if args.part and not args.release:
        parser.error('--part needs --release')
    if args.shard and args.release and args.part != 'python':
        parser.error('--shard with --release needs --part python')
    if args.release:
        return release(args)
    return python_shard(*args.shard) if args.shard else full()


def in_slot(env=os.environ):
    """Whether this run goes straight on: hosted CI, or a run already in one of the box's heavy-job slots."""
    return bool(env.get('CI') or env.get('TICO_HEAVY_HELD')) or os.name == 'nt'


if __name__ == '__main__':
    if not in_slot():
        # A local run first takes one of the box's heavy-job slots, at low priority (scripts/heavy.sh).
        os.execvp('bash', ['bash', str(ROOT / 'scripts/heavy.sh'), sys.executable, __file__, *sys.argv[1:]])
    raise SystemExit(main())

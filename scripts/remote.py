#!/usr/bin/env python3
"""Run one heavy command on a fresh RunsOn VM (.github/workflows/remote-run.yml) instead of on this computer.

    python3 scripts/remote.py [--setup python|browser|rust|all] [--cpus 4|8|16] [--minutes N] [--ref SHA] -- CMD...

It runs the pushed commit (HEAD by default), never this checkout's uncommitted files, waits for the result, prints the
command's last lines and keeps the whole log under .remote-runs/. The exit code is the command's own. `gh` must be
signed in with permission to dispatch workflows on the repository. scripts/heavy.sh calls it when TICO_HEAVY_REMOTE=1.
At most TICO_REMOTE_MAX (4 by default; 0 means no cap) of the repository's remote runs go at once: a run past that waits
up to 20 minutes for a slot, then exits without dispatching. A failed exit is never 0: see EXIT_CODES (75 when the run
was never dispatched, 76 when it was dispatched but ended without the command's result).

A builder's run (one started by a Tico bot, which has HUB_TOKEN and HUB_TASK_ID) first asks Tico for a permit
(backend/ci_runs.py): when its ticket has used its build-time minutes, or today's CI share is used up, the run is not
dispatched, one line says why, and the exit code is 3. A run that Tico cannot be asked about is refused too.
"""

import argparse
import json
import os
from pathlib import Path
import random
import shlex
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import uuid

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = 'remote-run.yml'
TAIL = 200
CAP = 4  # remote runs of this repository at once; TICO_REMOTE_MAX overrides it
WAIT_LIMIT = 20 * 60  # seconds a dispatch waits for a free slot
CHECK_LIMIT = 2 * 60  # seconds the count may keep failing before a dispatch goes ahead without it
BUSY = ('queued', 'in_progress', 'waiting', 'pending')
EX_NOT_DISPATCHED = 75  # EX_TEMPFAIL: no run started, so the command never ran; trying again later can work
EX_NO_RESULT = 76  # EX_PROTOCOL: a run started, but it ended without the command's exit code
RUN_LIMIT = 2 * 60 * 60  # seconds a dispatched run may take to finish, queue time included
EXIT_CODES = """exit codes:
  the command's own code   it ran to the end (0 on success; 124 when --minutes ran out)
  75                       never dispatched: the run cap held it past 20 minutes, GitHub refused the dispatch,
                           the commit is not pushed, or TICO_REMOTE_MAX is not a whole number
  76                       dispatched, but no result: cancelled, the VM was lost or never ran the command, the log
                           is missing or unreadable, or the run did not finish within 2 hours
  3                        refused by Tico's CI cap: a builder's ticket or the company's daily CI share is used up
                           (not dispatched; one line says why)
  2                        a bad command line
"""


def fail(code, message):
    """Say why remote.py stopped on stderr, and exit with `code` (see EXIT_CODES)."""
    print(f'remote: {message}', file=sys.stderr, flush=True)
    raise SystemExit(code)


REFUSED = 3  # the exit code when Tico's CI cap refuses a builder's run
PERMIT_TIMEOUT = 30  # seconds a permit request may take


def rate_limit_wait():
    """Seconds until GitHub's core API limit resets: at most 15 minutes, and 60 when the reset is unknown or past (a
    secondary limit has no reset time of its own)."""
    reset = subprocess.run(
        ['gh', 'api', 'rate_limit', '--jq', '.resources.core.reset'], cwd=ROOT, capture_output=True, text=True
    ).stdout.strip()
    seconds = int(reset) - int(time.time()) if reset.isdigit() else 0
    return min(seconds, 900) if seconds > 0 else 60


def gh(*args, capture=True, poll=False, failure=EX_NO_RESULT):
    """`gh`. With `poll` (a poll loop's call), a rate-limit failure waits for the reset, then retries. Any other failure
    exits with `failure`."""
    while True:
        result = subprocess.run(['gh', *args], cwd=ROOT, capture_output=capture, text=True)
        if result.returncode == 0:
            return result.stdout
        error = (result.stderr or '').strip()
        if not poll or not ('rate limit' in error.lower() or 'http 429' in error.lower()):
            fail(failure, f'gh {" ".join(args[:2])} failed: {error}')
        seconds = rate_limit_wait()
        print(f'remote: GitHub API rate limit hit; retrying in {seconds} s', file=sys.stderr, flush=True)
        time.sleep(seconds)


def git(*args):
    return subprocess.run(['git', *args], cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()


def in_checkout(arg):
    """A path inside this checkout, alone or as a flag's value (--out=PATH), relative to it; any other word as it is."""
    flag, value = arg.split('=', 1) if arg.startswith('-') and '=' in arg else ('', arg)
    if not os.path.isabs(value):
        return arg
    for path in (os.path.normpath(value), os.path.realpath(value)):
        if Path(path).is_relative_to(ROOT):
            relative = str(Path(path).relative_to(ROOT))
            return f'{flag}={relative}' if flag else relative
    return arg


def portable(command):
    """A local interpreter path (a venv's python, a node install) becomes the VM's own interpreter of that name, and a
    path inside this checkout becomes relative to it: the VM runs the command from its own checkout, somewhere else.
    ui-tests.cjs and release_checks.py re-run themselves through heavy.sh by their absolute path."""
    if not command:
        return []
    first = command[0]
    if '/' in first and Path(first).name.startswith('python'):
        first = 'python'
    elif '/' in first and Path(first).name == 'node':
        first = 'node'
    return [in_checkout(first), *map(in_checkout, command[1:])]


def setup_for(command):
    """The least setup that runs this command: browsers for the UI scripts, Rust for cargo, both for a release check
    (the default release check is pytest and then the UI scripts; --release adds cargo)."""
    text = ' '.join(command)
    browser = any(word in text for word in ('ui-tests', 'playwright', 'npm ', 'npx ', '.cjs'))
    rust = 'cargo' in text
    if 'release_checks.py' in text:
        if '--release' in text:
            browser = rust = True
        elif '--shard' not in text and '--record-timings' not in text:
            browser = True
    return 'all' if browser and rust else 'browser' if browser else 'rust' if rust else 'python'


def find_run(run_id, deadline):
    title = f'Remote run {run_id}'
    time.sleep(5)  # the dispatched run takes a few seconds to reach the list
    while time.monotonic() < deadline:
        runs = json.loads(
            gh(
                'run',
                'list',
                '--workflow',
                WORKFLOW,
                '--event',
                'workflow_dispatch',
                '--limit',
                '30',
                '--json',
                'databaseId,displayTitle,url',
                poll=True,
            )
        )
        for run in runs:
            if run['displayTitle'] == title:
                return run
        time.sleep(10)
    fail(EX_NO_RESULT, f'dispatched, but "{title}" did not appear; see the Remote run workflow in Actions')


def wait(run):
    last = None
    deadline = time.monotonic() + RUN_LIMIT
    while True:
        view = json.loads(gh('run', 'view', str(run['databaseId']), '--json', 'status,conclusion', poll=True))
        if view['status'] != last:
            print(f'remote: {view["status"]}', file=sys.stderr, flush=True)
            last = view['status']
        if view['status'] == 'completed':
            return view['conclusion']
        if time.monotonic() >= deadline:
            fail(
                EX_NO_RESULT,
                f'run {run["databaseId"]} was still {view["status"]} after {RUN_LIMIT // 3600} hours; giving up on it',
            )
        time.sleep(30)


def remote_cap():
    """TICO_REMOTE_MAX, or CAP when it is unset; 0 means no cap."""
    value = os.environ.get('TICO_REMOTE_MAX', '').strip()
    if not value:
        return CAP
    if not value.isdigit():
        fail(EX_NOT_DISPATCHED, f'TICO_REMOTE_MAX must be a whole number (0 means no cap), not {value!r}')
    return int(value)


def busy_remote_runs():
    """How many of the newest 100 runs of the workflow are queued, running, waiting or pending. One API call: GitHub
    filters a run list by a single status, so the four states are counted from one list."""
    result = subprocess.run(
        ['gh', 'api', f'repos/{{owner}}/{{repo}}/actions/workflows/{WORKFLOW}/runs?per_page=100'],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode:
        raise RuntimeError(' '.join((result.stderr or 'gh api failed').split()))
    try:
        return sum(run['status'] in BUSY for run in json.loads(result.stdout)['workflow_runs'])
    except (ValueError, KeyError, TypeError) as error:
        raise RuntimeError(f'unreadable run list ({error})') from None


def wait_for_capacity(cap):
    """Return True once fewer than `cap` remote runs are busy. While the count fails, retry with backoff; after two
    minutes of failures, warn and return False, so the caller dispatches anyway and CI still works. A wait past 20
    minutes exits without dispatching."""
    if not cap:
        return True
    started = time.monotonic()
    failing_since, retry = None, 5
    while True:
        try:
            busy = busy_remote_runs()
        except RuntimeError as error:
            now = time.monotonic()
            failing_since = now if failing_since is None else failing_since
            left = CHECK_LIMIT - (now - failing_since)
            if left <= 0:
                print(
                    f'remote: warning: could not count busy remote runs for 2 min ({error}); dispatching without the cap',
                    file=sys.stderr,
                    flush=True,
                )
                return False
            print(
                f'remote: could not count busy remote runs ({error}); retrying in {min(retry, left):g} s',
                file=sys.stderr,
                flush=True,
            )
            time.sleep(min(retry, left))
            retry = min(retry * 2, 60)
            continue
        failing_since, retry = None, 5
        if busy < cap:
            return True
        waited = time.monotonic() - started
        if waited >= WAIT_LIMIT:
            fail(
                EX_NOT_DISPATCHED,
                f'{busy} remote runs still busy (cap {cap}) after 20 min; not dispatching. '
                'Wait for them to finish, or raise TICO_REMOTE_MAX.',
            )
        print(f'waiting: {busy} remote run{"" if busy == 1 else "s"} busy (cap {cap})', file=sys.stderr, flush=True)
        # Jittered, so agents that started together don't all poll and dispatch together.
        time.sleep(min(random.uniform(30, 60), WAIT_LIMIT - waited))


def permit(run_id, cpus, minutes, environ=os.environ):
    """Tico's answer for a builder's run: None to go ahead, or the one line that refuses it. A run with no hub credential
    (one a person starts from a terminal) needs no permit. A builder whose Tico cannot be asked is refused, so its
    minutes are never spent unrecorded. A persistent harness's `tico-file:<path>` token is read from that file, as
    clients/tico.py does; the run id is the write's Idempotency-Key, so a retried ask is the same permit."""
    url, token, task = (environ.get(name, '').strip() for name in ('HUB_API_URL', 'HUB_TOKEN', 'HUB_TASK_ID'))
    if not (url and token and task):
        return None
    if token.startswith('tico-file:'):
        try:
            token = Path(token[len('tico-file:') :]).read_text().strip()
        except OSError:
            token = ''
        if not token:
            return 'no active Tico turn to ask for the CI cap; not starting the run'
    body = json.dumps({'task_id': task, 'remote_id': run_id, 'cpus': int(cpus), 'minutes': int(minutes)}).encode()
    request = urllib.request.Request(
        url.rstrip('/') + '/api/v2/ci/permits',
        data=body,
        method='POST',
        headers={'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json', 'Idempotency-Key': run_id},
    )
    try:
        with urllib.request.urlopen(request, timeout=PERMIT_TIMEOUT) as response:
            answer = json.loads(response.read() or b'{}')
    except urllib.error.HTTPError as error:
        return f'Tico refused the run (HTTP {error.code}); not starting it'
    except (urllib.error.URLError, OSError, ValueError) as error:
        return f'could not reach Tico for the CI cap ({type(error).__name__}); not starting the run'
    if answer.get('allowed') is True:
        return None
    return ' '.join(str(answer.get('message') or 'Remote run refused').split())


def run(command, *, setup=None, cpus='4', minutes=50, ref=None, echo=True):
    """Run `command` on a fresh VM against the pushed `ref` (HEAD) and wait for it: (exit code, the run's log folder).
    The code is None when the VM never ran the command to the end (setup failed, canceled, timed out, or the Spot
    VM was reclaimed). `echo` prints the command's last lines here, as `remote.py` itself does. A stop before the
    dispatch exits with EX_NOT_DISPATCHED instead of returning."""
    cap = remote_cap()
    try:
        ref = git('rev-parse', ref or 'HEAD')
    except subprocess.CalledProcessError:
        fail(EX_NOT_DISPATCHED, f'cannot resolve {ref or "HEAD"!r} to a commit')
    if not git('branch', '-r', '--contains', ref):
        fail(EX_NOT_DISPATCHED, f'{ref[:7]} is not pushed; push it first (the VM runs the pushed commit)')
    command = portable(command)
    run_id = uuid.uuid4().hex[:12]
    setup = setup or setup_for(command)
    print(
        f'remote: {shlex.join(command)} at {ref[:7]} on {cpus} vCPUs ({setup} setup), id {run_id}',
        file=sys.stderr,
        flush=True,
    )
    refusal = permit(run_id, cpus, minutes)
    if refusal:
        print(f'remote: {refusal}', file=sys.stderr, flush=True)
        raise SystemExit(REFUSED)
    wait_for_capacity(cap)
    # The workflow comes from main, so a branch can't change what a remote run is allowed to do.
    gh(
        'workflow',
        'run',
        WORKFLOW,
        '--ref',
        'main',
        '-f',
        f'id={run_id}',
        '-f',
        f'ref={ref}',
        '-f',
        f'command={shlex.join(command)}',
        '-f',
        f'setup={setup}',
        '-f',
        f'cpus={cpus}',
        '-f',
        f'minutes={minutes}',
        failure=EX_NOT_DISPATCHED,
    )
    found = find_run(run_id, time.monotonic() + 120)
    print(f'remote: {found["url"]}', file=sys.stderr, flush=True)
    conclusion = wait(found)
    logs = ROOT / '.remote-runs' / run_id
    logs.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as scratch:
        fetched = (
            subprocess.run(
                [
                    'bash',
                    'scripts/ci/retry.sh',
                    '--clear',
                    scratch,
                    '--permanent',
                    'HTTP (404|410)|no (valid )?artifacts? (match|found)',
                    '--',
                    'gh',
                    'run',
                    'download',
                    str(found['databaseId']),
                    '-n',
                    f'remote-run-{run_id}',
                    '-D',
                    scratch,
                ],
                cwd=ROOT,
            ).returncode
            == 0
        )
        for name in ('command', 'output.log', 'exit-code'):
            if (Path(scratch) / name).is_file():
                (logs / name).write_bytes((Path(scratch) / name).read_bytes())
    output = logs / 'output.log'
    if echo and output.is_file():
        print('\n'.join(output.read_text(errors='replace').splitlines()[-TAIL:]))
    code_file = logs / 'exit-code'
    if not fetched or not code_file.is_file():
        print(f'remote: no result ({conclusion}); see {found["url"]}', file=sys.stderr, flush=True)
        return None, logs
    text = code_file.read_text().strip()
    if not text.isdigit():
        print(f'remote: no exit code in the log ({text[:40]!r}); see {found["url"]}', file=sys.stderr, flush=True)
        return None, logs
    code = int(text)
    print(f'remote: exit code {code}; full log {output.relative_to(ROOT)}; {found["url"]}', file=sys.stderr)
    return code, logs


def main(argv=None):
    parser = argparse.ArgumentParser(
        description=__doc__.split('\n')[0], epilog=EXIT_CODES, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        '--setup',
        choices=('python', 'browser', 'rust', 'all'),
        help='what the VM installs first (default: guessed from the command)',
    )
    parser.add_argument('--cpus', choices=('4', '8', '16'), default=os.environ.get('TICO_REMOTE_CPUS', '4'))
    # The whole default Python suite takes 25-28 minutes on 4 vCPUs; a 30-minute limit cut it off near the end.
    parser.add_argument('--minutes', type=int, default=50, help='time limit for the command (1 to 60)')
    parser.add_argument('--ref', help='commit to run (default: HEAD, which must be pushed)')
    parser.add_argument('command', nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    command = args.command[1:] if args.command[:1] == ['--'] else args.command
    if not command:
        parser.error('a command is required, after --')
    if git('status', '--porcelain', '--untracked-files=no'):
        print('remote: uncommitted changes are not included; running the pushed commit', file=sys.stderr)
    code, _ = run(command, setup=args.setup, cpus=args.cpus, minutes=args.minutes, ref=args.ref)
    return EX_NO_RESULT if code is None else code


if __name__ == '__main__':
    raise SystemExit(main())

#!/usr/bin/env python3
"""Run one heavy command on a fresh RunsOn VM (.github/workflows/remote-run.yml) instead of on this computer.

    python3 scripts/remote.py [--setup python|browser|rust|all] [--cpus 4|8|16] [--minutes N] [--ref SHA] -- CMD...

It runs the pushed commit (HEAD by default), never this checkout's uncommitted files, waits for the result, prints the
command's last lines and keeps the whole log under .remote-runs/. The exit code is the command's own. `gh` must be
signed in with permission to dispatch workflows on the repository. scripts/heavy.sh calls it when TICO_HEAVY_REMOTE=1.
"""
import argparse
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = 'remote-run.yml'
TAIL = 200
# Waits between tries when GitHub rate-limits gh: five tries over about ten minutes.
BACKOFF = (60, 120, 180, 240)
RATE_LIMITS = ('rate limit', 'too quickly', 'http 429', 'abuse detection')


def gh(*args, capture=True):
    """Run gh and return its output; a rate-limit error is retried with backoff, any other failure exits 1 at once."""
    name = ' '.join(args[:2])
    for wait in (*BACKOFF, None):
        result = subprocess.run(['gh', *args], cwd=ROOT, capture_output=capture, text=True)
        if result.returncode == 0:
            return result.stdout
        error = (result.stderr or '').strip()
        if wait is None or not any(limit in error.lower() for limit in RATE_LIMITS):
            raise SystemExit(f'remote: gh {name} failed: {error}')
        print(f'remote: gh {name} hit a rate limit; retrying in {wait}s', file=sys.stderr, flush=True)
        time.sleep(wait)


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
    while time.monotonic() < deadline:
        runs = json.loads(gh('run', 'list', '--workflow', WORKFLOW, '--event', 'workflow_dispatch', '--limit', '30',
                             '--json', 'databaseId,displayTitle,url'))
        for run in runs:
            if run['displayTitle'] == title:
                return run
        time.sleep(3)
    raise SystemExit(f'remote: dispatched, but "{title}" did not appear; see the Remote run workflow in Actions')


def wait(run):
    last = None
    while True:
        view = json.loads(gh('run', 'view', str(run['databaseId']), '--json', 'status,conclusion'))
        if view['status'] != last:
            print(f'remote: {view["status"]}', file=sys.stderr, flush=True)
            last = view['status']
        if view['status'] == 'completed':
            return view['conclusion']
        time.sleep(10)


def run(command, *, setup=None, cpus='4', minutes=50, ref=None, echo=True):
    """Run `command` on a fresh VM against the pushed `ref` (HEAD) and wait for it: (exit code, the run's log folder).
    The code is None when the VM never ran the command to the end (setup failed, cancelled, timed out, or the Spot
    VM was reclaimed). `echo` prints the command's last lines here, as `remote.py` itself does."""
    ref = git('rev-parse', ref or 'HEAD')
    if not git('branch', '-r', '--contains', ref):
        raise SystemExit(f'remote: {ref[:7]} is not pushed; push it first (the VM runs the pushed commit)')
    command = portable(command)
    run_id = uuid.uuid4().hex[:12]
    setup = setup or setup_for(command)
    print(f'remote: {shlex.join(command)} at {ref[:7]} on {cpus} vCPUs ({setup} setup), id {run_id}',
          file=sys.stderr, flush=True)
    # The workflow comes from main, so a branch can't change what a remote run is allowed to do.
    gh('workflow', 'run', WORKFLOW, '--ref', 'main', '-f', f'id={run_id}', '-f', f'ref={ref}',
       '-f', f'command={shlex.join(command)}', '-f', f'setup={setup}', '-f', f'cpus={cpus}',
       '-f', f'minutes={minutes}')
    found = find_run(run_id, time.monotonic() + 120)
    print(f'remote: {found["url"]}', file=sys.stderr, flush=True)
    conclusion = wait(found)
    logs = ROOT / '.remote-runs' / run_id
    logs.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as scratch:
        fetched = subprocess.run(['gh', 'run', 'download', str(found['databaseId']), '-n', f'remote-run-{run_id}',
                                  '-D', scratch], cwd=ROOT, capture_output=True, text=True).returncode == 0
        for name in ('command', 'output.log', 'exit-code'):
            if (Path(scratch) / name).is_file():
                (logs / name).write_bytes((Path(scratch) / name).read_bytes())
    output = logs / 'output.log'
    if echo and output.is_file():
        print('\n'.join(output.read_text(errors='replace').splitlines()[-TAIL:]))
    code_file = logs / 'exit-code'
    if not fetched or not code_file.is_file():
        print(f'remote: no result ({conclusion}); see {found["url"]}', file=sys.stderr)
        return None, logs
    code = int(code_file.read_text().strip() or 1)
    print(f'remote: exit code {code}; full log {output.relative_to(ROOT)}; {found["url"]}', file=sys.stderr)
    return code, logs


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('--setup', choices=('python', 'browser', 'rust', 'all'),
                        help='what the VM installs first (default: guessed from the command)')
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
    return 1 if code is None else code


if __name__ == '__main__':
    raise SystemExit(main())

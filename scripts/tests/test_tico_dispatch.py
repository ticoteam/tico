"""scripts/tico is the teamwork CLI (R1). It refuses this computer's commands with a pointer to tico-computer, and
scripts/hub forwards to it with one notice. An update from R0 (its bash scripts/tico, embedded below) still ends with
tico-computer restart through the new checkout, and the next update is tico-computer's by name.
"""

import json
import os
import runpy
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
TICO = ROOT / 'scripts' / 'tico'
HUB = ROOT / 'scripts' / 'hub'
COMPUTER = ROOT / 'scripts' / 'tico-computer'
NOTICE = 'hub: "hub" is now "tico"; the old name stops working in the next release'

# R0's scripts/tico, the bash dispatcher that shipped before R1 (git show of that release).
R0_TICO = '''#!/usr/bin/env bash
# `tico`: this computer's commands go to scripts/tico-computer, everything else to scripts/hub (the teamwork CLI).
# Usage: tico [-e ENV] install|uninstall|restart|status|doctor|logs|update|open|env|enroll|profile|screen|server|help ...
#        tico [-e ENV] bot create ...      the same as tico-computer ... (see scripts/tico-computer help)
#        tico update list|show|create|reply ...   the bots' updates: hub update ... (a bare tico update updates Tico)
#        tico <anything else>              the same as hub <anything else>: tico task list = hub task list
# A scripts/tico from before tico-computer ends `update` with `scripts/tico [-e ENV] restart all`: that lands here.
set -euo pipefail

SELF="$(readlink -f "$0" 2>/dev/null || echo "$0")"   # ~/.local/bin/tico is a symlink here
DIR="$(dirname "$SELF")"

case "${1:-help}" in
  # Only the computer commands take an environment.
  -e|--env|-e=*|--env=*|install|uninstall|restart|status|doctor|logs|open|env|enroll|profile|screen|server|help|-h|--help)
    exec "$DIR/tico-computer" "$@" ;;
  bot) [ "${2:-}" != create ] || exec "$DIR/tico-computer" "$@" ;;
  update) [ $# -gt 1 ] || exec "$DIR/tico-computer" "$@" ;;
esac
exec "$DIR/hub" "$@"
'''


def stub(path, name):
    path.write_text(f'#!/bin/sh\necho "{name}: $*"\n')
    path.chmod(0o755)


def command_home(tmp_path, name, target):
    """A ~/.local/bin with `name` linked to `target`, as the installer links it."""
    bin_dir = tmp_path / 'home' / '.local' / 'bin'
    bin_dir.mkdir(parents=True, exist_ok=True)
    (bin_dir / name).symlink_to(target)
    return bin_dir / name


def run(cmd, *args, env=None):
    return subprocess.run(
        [str(cmd), *args],
        capture_output=True,
        text=True,
        timeout=60,
        env=env or {'PATH': os.environ.get('PATH', os.defpath), 'HOME': os.environ.get('HOME', '')},
    )


@pytest.fixture
def tico(tmp_path):
    return command_home(tmp_path, 'tico', TICO)


@pytest.mark.parametrize(
    'args',
    [
        ['install'],
        ['restart', 'all'],
        ['-e', 'acme', 'restart', 'all'],
        ['--env=acme', 'status'],
        ['-e', 'acme', 'task', 'list'],  # an environment is only ever the computer's
        ['update'],
        ['-e', 'acme', 'update'],
        ['doctor'],
        ['env', 'list'],
        ['logs', 'bot'],
        ['server', 'start'],
    ],
)
def test_a_computer_command_is_refused_with_its_tico_computer_name(tico, args):
    done = run(tico, *args)
    assert done.returncode == 2, done.stdout + done.stderr
    assert done.stderr.strip() == f'tico: moved to tico-computer {" ".join(args)}'
    assert done.stdout == ''


def test_the_teamwork_commands_are_not_refused():
    moved = runpy.run_path(str(TICO), run_name='tico_module')['moved']
    for argv in (
        ['task', 'list'],
        ['bot', 'create', 'ops'],  # a bot is registered with the company, as it always was
        ['update', 'list'],  # the bots' updates stay teamwork
        ['update', 'show', '7'],
        ['update', 'reply', '7'],
        ['message', 'send', 'ops', 'hi'],
        [],
    ):
        assert moved(argv) is None, argv


def test_hub_forwards_to_tico_with_one_notice(tmp_path):
    hub = command_home(tmp_path, 'hub', HUB)
    done = run(hub, '--version')
    assert done.returncode == 0, done.stderr
    assert done.stderr == NOTICE + '\n'
    assert done.stdout.strip()


def test_hub_forwards_a_refused_computer_command_with_the_notice_too(tmp_path):
    hub = command_home(tmp_path, 'hub', HUB)
    done = run(hub, 'install')
    assert done.returncode == 2
    assert done.stderr == NOTICE + '\ntico: moved to tico-computer install\n'


def git(cwd, *args):
    subprocess.run(
        ['git', '-c', 'user.name=Test', '-c', 'user.email=test@example.invalid', *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
        timeout=60,
    )


def test_an_update_from_r0_ends_with_tico_computer_restart_and_the_next_update_is_tico_computer(tmp_path):
    source, checkout, home = tmp_path / 'source', tmp_path / 'checkout', tmp_path / 'home'
    (source / 'scripts').mkdir(parents=True)
    (source / 'scripts' / 'tico').write_text(R0_TICO)
    (source / 'scripts' / 'tico').chmod(0o755)
    shutil.copy2(COMPUTER, source / 'scripts' / 'tico-computer')
    stub(source / 'scripts' / 'hub', 'hub')
    git(source, 'init', '-q', '-b', 'main')
    git(source, 'add', '-A')
    git(source, 'commit', '-q', '-m', 'R0')
    git(tmp_path, 'clone', '-q', str(source), str(checkout))

    # R1 lands on the remote; the installed checkout is still R0 until the update pulls it.
    for name in ('tico', 'hub', 'tico-computer'):
        shutil.copy2(ROOT / 'scripts' / name, source / 'scripts' / name)
    git(source, 'add', '-A')
    git(source, 'commit', '-q', '-m', 'R1')

    tico_cmd = command_home(tmp_path, 'tico', checkout / 'scripts' / 'tico')  # R0's dispatcher is the installed `tico`
    envs = tmp_path / 'environments'
    (envs / 'acme').mkdir(parents=True)
    (envs / 'acme' / 'environment.json').write_text(json.dumps({'url': 'https://acme.example', 'id': 'env_acme'}))
    fakes = tmp_path / 'bin'  # a systemd user session that answers, with nothing installed
    fakes.mkdir()
    (fakes / 'systemctl').write_text('#!/bin/sh\nexit 0\n')
    (fakes / 'systemctl').chmod(0o755)
    env = {
        'PATH': f"{fakes}:{os.environ.get('PATH', os.defpath)}",
        'HOME': str(home),
        'TICO_RUNNER_PYTHON': sys.executable,
        'TICO_ENVIRONMENTS_DIR': str(envs),
        'TICO_OS': 'Linux',
        'TICO_SYSTEMD_USER_DIR': str(tmp_path / 'units'),
        'GIT_CONFIG_NOSYSTEM': '1',
    }

    done = run(tico_cmd, '-e', 'acme', 'update', env=env)
    assert done.returncode == 0, done.stdout + done.stderr
    out = done.stdout
    assert 'updated ' in out
    # No job is installed here, so tico-computer's restart says so for each: it ran, with the environment.
    for kind in ('bot', 'connectors', 'close-calls', 'importers'):
        assert f'{kind}: not installed' in out
    assert 'linked ~/.local/bin/tico-computer' in out
    link = home / '.local' / 'bin' / 'tico-computer'
    assert link.is_symlink() and link.resolve() == (checkout / 'scripts' / 'tico-computer').resolve()

    # The new `tico` is the teamwork CLI, so its bare `update` is refused with the name that does it now.
    refused = run(tico_cmd, 'update', env=env)
    assert refused.returncode == 2 and refused.stderr.strip() == 'tico: moved to tico-computer update'

    # The next update runs by tico-computer's name, and restarts through the checkout's script.
    (source / 'NOTES').write_text('R1.1\n')
    git(source, 'add', '-A')
    git(source, 'commit', '-q', '-m', 'R1.1')
    done = run(link, 'update', env=env)
    assert done.returncode == 0, done.stdout + done.stderr
    assert 'updated ' in done.stdout and 'importers: not installed' in done.stdout and 'linked' not in done.stdout

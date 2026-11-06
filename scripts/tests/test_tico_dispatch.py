"""scripts/tico hands this computer's commands to scripts/tico-computer and everything else to scripts/hub.

Customers' ~/.local/bin/tico is a symlink to a checkout's scripts/tico, and a scripts/tico from before tico-computer
ends `update` with `exec "$SELF" [-e ENV] restart all`, where $SELF is the path that now holds the dispatcher. These
run the real scripts from a temporary checkout, through that symlink.
"""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
DISPATCH = ROOT / 'scripts' / 'tico'
COMPUTER = ROOT / 'scripts' / 'tico-computer'
OLD_EXEC = 'if [ -n "$ENV_SLUG" ]; then exec "$SELF" -e "$ENV_SLUG" restart all; else exec "$SELF" restart all; fi\n'


def stub(path, name):
    path.write_text(f'#!/bin/sh\necho "{name}: $*"\n')
    path.chmod(0o755)


@pytest.fixture
def linked(tmp_path):
    """A checkout's scripts/ with the real dispatcher and stand-ins for both commands, and ~/.local/bin/tico to it."""
    scripts = tmp_path / 'checkout' / 'scripts'
    scripts.mkdir(parents=True)
    shutil.copy2(DISPATCH, scripts / 'tico')
    stub(scripts / 'tico-computer', 'computer')
    stub(scripts / 'hub', 'hub')
    bin_dir = tmp_path / 'home' / '.local' / 'bin'
    bin_dir.mkdir(parents=True)
    (bin_dir / 'tico').symlink_to(scripts / 'tico')

    def run(*args):
        done = subprocess.run([str(bin_dir / 'tico'), *args], capture_output=True, text=True, timeout=30,
                              env={'PATH': os.environ.get('PATH', os.defpath), 'HOME': str(tmp_path / 'home')})
        assert done.returncode == 0, done.stderr
        return done.stdout.strip()
    return run


@pytest.mark.parametrize('args, ran', [
    (['install'], 'computer: install'),
    (['restart', 'all'], 'computer: restart all'),
    (['-e', 'acme', 'restart', 'all'], 'computer: -e acme restart all'),
    (['--env=acme', 'status'], 'computer: --env=acme status'),
    (['-e', 'acme', 'task', 'list'], 'computer: -e acme task list'),   # an environment is only ever the computer's
    (['update'], 'computer: update'),
    (['-e', 'acme', 'update'], 'computer: -e acme update'),
    (['update', 'list'], 'hub: update list'),          # the bots' daily updates
    (['doctor'], 'computer: doctor'),
    (['env', 'list'], 'computer: env list'),
    (['bot', 'create', 'ops'], 'computer: bot create ops'),
    ([], 'computer:'),
    (['help'], 'computer: help'),
    (['task', 'list'], 'hub: task list'),
    (['bot', 'list'], 'hub: bot list'),
    (['thread', 'post', '--', '-e'], 'hub: thread post -- -e'),
])
def test_computer_commands_go_to_tico_computer_and_the_rest_to_hub(linked, args, ran):
    assert linked(*args) == ran


def git(cwd, *args):
    subprocess.run(['git', '-c', 'user.name=Test', '-c', 'user.email=test@example.invalid', *args], cwd=cwd,
                   check=True, capture_output=True, text=True, timeout=60)


def test_an_update_by_the_old_scripts_tico_restarts_through_the_dispatcher_and_links_tico_computer(tmp_path):
    # The old script is today's tico-computer as it was before the move: `update` re-runs "$SELF" for the restart and
    # links nothing.
    old = COMPUTER.read_text()
    new_exec = old[old.index('    if [ -n "$ENV_SLUG" ]; then exec "$HUB/scripts/tico-computer"'):]
    new_exec = new_exec[:new_exec.index('    ;;\n')]
    assert old.count(new_exec) == 1 and old.count('    link_commands tico-computer\n    # exec:') == 1
    old = old.replace(new_exec, '    ' + OLD_EXEC).replace('    link_commands tico-computer\n    # exec:', '    # exec:')

    source, checkout, home = tmp_path / 'source', tmp_path / 'checkout', tmp_path / 'home'
    (source / 'scripts').mkdir(parents=True)
    (source / 'scripts' / 'tico').write_text(old)
    (source / 'scripts' / 'tico').chmod(0o755)
    git(source, 'init', '-q', '-b', 'main')
    git(source, 'add', '-A')
    git(source, 'commit', '-q', '-m', 'R-1')
    git(tmp_path, 'clone', '-q', str(source), str(checkout))
    for name in ('tico', 'tico-computer'):
        shutil.copy2(ROOT / 'scripts' / name, source / 'scripts' / name)
    stub(source / 'scripts' / 'hub', 'hub')
    git(source, 'add', '-A')
    git(source, 'commit', '-q', '-m', 'R0')

    (home / '.local' / 'bin').mkdir(parents=True)
    (home / '.local' / 'bin' / 'tico').symlink_to(checkout / 'scripts' / 'tico')
    envs = tmp_path / 'environments'
    (envs / 'acme').mkdir(parents=True)
    (envs / 'acme' / 'environment.json').write_text(json.dumps({'url': 'https://acme.example', 'id': 'env_acme'}))
    fakes = tmp_path / 'bin'                          # a systemd user session that answers, with nothing installed
    fakes.mkdir()
    (fakes / 'systemctl').write_text('#!/bin/sh\nexit 0\n')
    (fakes / 'systemctl').chmod(0o755)
    env = {'PATH': f"{fakes}:{os.environ.get('PATH', os.defpath)}", 'HOME': str(home), 'TICO_RUNNER_PYTHON': sys.executable,
           'TICO_ENVIRONMENTS_DIR': str(envs), 'TICO_OS': 'Linux', 'TICO_SYSTEMD_USER_DIR': str(tmp_path / 'units'),
           'GIT_CONFIG_NOSYSTEM': '1'}

    def update(*args):
        done = subprocess.run([str(home / '.local' / 'bin' / 'tico'), *args, 'update'], capture_output=True, text=True,
                              env=env, timeout=120)
        assert done.returncode == 0, done.stdout + done.stderr
        return done.stdout

    out = update('-e', 'acme')
    assert 'updated ' in out
    # No job is installed here, so tico-computer's restart says so for each: it ran, with the environment.
    for kind in ('bot', 'connectors', 'close-calls', 'importers'):
        assert f'{kind}: not installed' in out
    assert (checkout / 'scripts' / 'tico-computer').is_file()
    link = home / '.local' / 'bin' / 'tico-computer'
    assert link.is_symlink() and link.resolve() == (checkout / 'scripts' / 'tico-computer').resolve()
    assert 'linked ~/.local/bin/tico-computer' in out

    # The next update runs the new script, which restarts with tico-computer by name.
    (source / 'NOTES').write_text('R0.1\n')
    git(source, 'add', '-A')
    git(source, 'commit', '-q', '-m', 'R0.1')
    out = update()
    assert 'updated ' in out and 'importers: not installed' in out and 'linked' not in out

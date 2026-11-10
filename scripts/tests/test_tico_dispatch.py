"""scripts/tico is the teamwork CLI; this computer's own commands reach scripts/tico-computer only from a person.

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
    """A checkout's scripts/ with the real dispatcher and stand-ins for both commands, and ~/.local/bin/tico to it.
    `run(*args, bot=...)` runs it from a person's terminal, or with the environment the runner gives a bot turn
    (runner-rs/src/env.rs sets HUB_BOT and HUB_EMPLOYEE on every turn)."""
    scripts = tmp_path / 'checkout' / 'scripts'
    scripts.mkdir(parents=True)
    shutil.copy2(DISPATCH, scripts / 'tico')
    stub(scripts / 'tico-computer', 'computer')
    stub(scripts / 'hub', 'teamwork')
    bin_dir = tmp_path / 'home' / '.local' / 'bin'
    bin_dir.mkdir(parents=True)
    (bin_dir / 'tico').symlink_to(scripts / 'tico')

    def run(*args, bot=None):
        env = {'PATH': os.environ.get('PATH', os.defpath), 'HOME': str(tmp_path / 'home')}
        if isinstance(bot, dict):  # one variable alone
            env.update(bot)
        elif bot:
            env.update({'HUB_BOT': bot, 'HUB_EMPLOYEE': bot, 'HUB_TOKEN': 't', 'HUB_API_URL': 'https://x.invalid'})
        return subprocess.run([str(bin_dir / 'tico'), *args], capture_output=True, text=True, timeout=30, env=env)

    return run


TEAMWORK = [
    [],
    ['--help'],
    ['-h'],
    ['help'],
    ['--version'],
    ['status'],
    ['update'],
    ['update', 'list'],  # the bots' daily updates
    ['bot', 'create', 'ops', '--template', 'support'],
    ['bot', 'list'],
    ['task', 'list'],
    ['computer', 'list'],
    ['setup', 'show'],
    ['thread', 'post', '--', '-e'],
]
COMPUTER_WORDS = [
    ['install'],
    ['uninstall'],
    ['restart', 'all'],
    ['-e', 'acme', 'restart', 'all'],
    ['--env=acme', 'status'],
    ['-e', 'acme', 'task', 'list'],  # an environment is only ever the computer's
    ['-e', 'acme', 'update'],
    ['doctor'],
    ['logs', 'bot'],
    ['open'],
    ['env', 'list'],
    ['enroll', '--code-file', 'f'],
    ['profile', 'list'],
    ['screen', 'on'],
    ['server', 'status'],
]


@pytest.mark.parametrize('args', TEAMWORK)
@pytest.mark.parametrize('bot', [None, 'ops'])
def test_every_teamwork_word_goes_to_the_teamwork_cli_for_people_and_bots(linked, args, bot):
    done = linked(*args, bot=bot)
    assert (done.returncode, done.stdout.strip(), done.stderr) == (0, ' '.join(['teamwork:', *args]).strip(), '')


@pytest.mark.parametrize('args', COMPUTER_WORDS)
def test_a_computer_word_from_a_persons_terminal_reaches_tico_computer_with_a_note(linked, args):
    done = linked(*args)
    assert (done.returncode, done.stdout.strip()) == (0, ' '.join(['computer:', *args]))
    if 'restart' in args:  # the update chain of a scripts/tico from before tico-computer: silent
        assert done.stderr == ''
    else:
        assert done.stderr == f"tico: this computer's commands are tico-computer now: running tico-computer {' '.join(args)}\n"


@pytest.mark.parametrize('args', COMPUTER_WORDS)
def test_a_computer_word_in_a_bot_turn_is_refused_and_never_reaches_tico_computer(linked, args):
    done = linked(*args, bot='ops')
    assert done.returncode == 2 and done.stdout == ''
    assert 'tico-computer' in done.stderr and 'a bot turn does not run it' in done.stderr


@pytest.mark.parametrize('name', ['HUB_BOT', 'HUB_EMPLOYEE'])
def test_either_name_for_the_bot_marks_a_bot_turn(linked, name):
    done = linked('update', bot='ops')
    assert done.stdout.strip() == 'teamwork: update'
    done = linked('restart', 'all', bot={name: 'ops'})
    assert done.returncode == 2 and done.stdout == ''


def git(cwd, *args):
    subprocess.run(
        ['git', '-c', 'user.name=Test', '-c', 'user.email=test@example.invalid', *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
        timeout=60,
    )


def test_an_update_by_the_old_scripts_tico_restarts_through_the_dispatcher_and_links_tico_computer(tmp_path):
    # The old script is today's tico-computer as it was before the move: `update` re-runs "$SELF" for the restart and
    # links nothing.
    old = COMPUTER.read_text()
    new_exec = old[old.index('    if [ -n "$ENV_SLUG" ]; then exec "$HUB/scripts/tico-computer"') :]
    new_exec = new_exec[: new_exec.index('    ;;\n')]
    assert old.count(new_exec) == 1 and old.count('    link_commands tico-computer\n    # exec:') == 1
    old = old.replace(new_exec, '    ' + OLD_EXEC).replace(
        '    link_commands tico-computer\n    # exec:', '    # exec:'
    )

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

    def update(*args, command='tico'):
        done = subprocess.run(
            [str(home / '.local' / 'bin' / command), *args, 'update'],
            capture_output=True,
            text=True,
            env=env,
            timeout=120,
        )
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

    # Now `tico update` is the teamwork CLI's (the bots' updates); the computer updates with tico-computer, whose
    # new script restarts with tico-computer by name.
    assert update() == 'hub: update\n'
    (source / 'NOTES').write_text('R0.1\n')
    git(source, 'add', '-A')
    git(source, 'commit', '-q', '-m', 'R0.1')
    out = update(command='tico-computer')
    assert 'updated ' in out and 'importers: not installed' in out and 'linked' not in out

"""A bot's own pytest run takes one of the box's heavy-job slots (scripts/heavy.sh), as release_checks.py and
ui-tests.cjs do. Bots on one shared computer each ran the suite directly, with xdist workers, until the load held
every new turn on that computer; heavy.sh lets a few run at once, at low priority, and the rest wait.

The root conftest.py calls `reexec` from pytest_configure, before collection and before xdist starts workers."""
import os
import sys
from pathlib import Path

HEAVY = Path(__file__).resolve().parent / 'heavy.sh'


def slotted_command(env, argv, orig_argv, executable, os_name=os.name):
    """The command that re-runs this pytest inside a heavy-job slot, or None when it runs as it is.

    Only in a bot's turn (the runner sets HUB_BOT): a person's own run is theirs to schedule. Never on hosted CI,
    inside a slot already held (heavy.sh exports TICO_HEAVY_HELD, so full-suite.sh, release_checks.py and the re-run
    itself go straight on), in an xdist worker, or for pytest started by something other than its own command line
    (pytest.main in-process, where orig_argv is another program's). Listing tests runs nothing, so it never waits."""
    if not env.get('HUB_BOT') or env.get('CI') or env.get('TICO_HEAVY_HELD') or env.get('PYTEST_XDIST_WORKER'):
        return None
    if os_name == 'nt' or len(orig_argv) < 2:
        return None
    program = Path(argv[0]) if argv else Path()
    if program.name not in ('pytest', 'py.test') and program.parent.name != 'pytest':
        return None
    if {'--collect-only', '--co'} & set(orig_argv):
        return None
    return ['bash', str(HEAVY), executable, *orig_argv[1:]]


def reexec():
    command = slotted_command(os.environ, sys.argv, getattr(sys, 'orig_argv', []), sys.executable)
    if command:
        sys.stdout.flush()
        sys.stderr.flush()
        os.execvp(command[0], command)

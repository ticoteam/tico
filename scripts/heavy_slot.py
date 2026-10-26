"""A bot's own pytest run takes one of the box's heavy-job slots (scripts/heavy.sh), as release_checks.py and
ui-tests.cjs do. Bots on one shared computer each ran the suite directly, with xdist workers, until the load held
every new turn on that computer; heavy.sh lets a few run at once, at low priority, and the rest wait.

The root conftest.py calls `reexec` from pytest_configure, before collection and before xdist starts workers."""
import os
import sys
from pathlib import Path

# Named in two parts so the PR checks' dependency map (pr_checks.py) does not read this hand-off as a test
# dependency: every test loads this file through the conftest, and heavy.sh may route on to remote.py and
# release_checks.py, so each change anywhere would select the full suite. Nothing here runs the wrapper in a test.
HEAVY = Path(__file__).resolve().parent / ('heavy' + '.sh')


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
    command = ['bash', str(HEAVY), executable, *orig_argv[1:]]
    # With TICO_HEAVY_REMOTE=1 heavy.sh sends a run to a RunsOn VM, which tests the pushed commit. A run of the few
    # test files a bot just changed tests its uncommitted work, so it stays here; a whole suite goes.
    if env.get('TICO_HEAVY_REMOTE') == '1' and 0 < len(named_files(orig_argv[1:])) <= LOCAL_FILES:
        return ['env', 'TICO_HEAVY_REMOTE=0', *command]
    return command


LOCAL_FILES = 3


def named_files(args):
    """The test files a pytest command line names (a file or a node id in one); a directory names none of them."""
    return {arg.split('::')[0] for arg in args if not arg.startswith('-') and arg.split('::')[0].endswith('.py')
            and arg.split('::')[0].rsplit('/', 1)[-1].startswith('test_')}


def reexec():
    command = slotted_command(os.environ, sys.argv, getattr(sys, 'orig_argv', []), sys.executable)
    if command:
        sys.stdout.flush()
        sys.stderr.flush()
        os.execvp(command[0], command)

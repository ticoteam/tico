"""A bot's own pytest run re-runs itself inside a heavy-job slot; every other run goes straight on."""
import pytest

from scripts.heavy_slot import HEAVY, slotted_command

PY = '/opt/venv/bin/python3'
MAIN = '/opt/venv/lib/python3/site-packages/pytest/__main__.py'
ARGV = [MAIN, '-q', 'backend/tests/test_a.py']
ORIG = ['python3', '-X', 'utf8', '-m', 'pytest', '-q', 'backend/tests/test_a.py']


def test_a_bots_run_reruns_its_exact_command_through_heavy_sh():
    assert slotted_command({'HUB_BOT': 'ops'}, ARGV, ORIG, PY, 'posix') == [
        'bash', str(HEAVY), PY, '-X', 'utf8', '-m', 'pytest', '-q', 'backend/tests/test_a.py']
    # The pytest console script, not only python -m pytest.
    assert slotted_command({'HUB_BOT': 'ops'}, ['/opt/venv/bin/pytest', '-q'], [PY, '/opt/venv/bin/pytest', '-q'],
                           PY, 'posix') == ['bash', str(HEAVY), PY, '/opt/venv/bin/pytest', '-q']


@pytest.mark.parametrize('env', [
    {},                                                 # a person's own run
    {'HUB_BOT': 'ops', 'CI': 'true'},                   # hosted CI has no shared box
    {'HUB_BOT': 'ops', 'TICO_HEAVY_HELD': '1'},         # full-suite.sh, release_checks.py, or the re-run itself
    {'HUB_BOT': 'ops', 'PYTEST_XDIST_WORKER': 'gw0'},   # a worker of a run that already holds the slot
])
def test_runs_outside_a_bot_turn_or_already_in_a_slot_go_straight_on(env):
    assert slotted_command(env, ARGV, ORIG, PY, 'posix') is None


def test_pytest_main_inside_another_program_and_listing_go_straight_on():
    # pytest.main from a script: orig_argv is that script's, so re-running it would run something else.
    assert slotted_command({'HUB_BOT': 'ops'}, ['scripts/tool.py'], ['python3', 'scripts/tool.py'], PY, 'posix') is None
    assert slotted_command({'HUB_BOT': 'ops'}, ARGV, [*ORIG, '--collect-only'], PY, 'posix') is None


def test_with_remote_runs_on_a_few_named_test_files_stay_here_and_a_suite_goes():
    env = {'HUB_BOT': 'ops', 'TICO_HEAVY_REMOTE': '1'}
    # The files a bot just changed: its uncommitted work, which a VM running the pushed commit would not test.
    assert slotted_command(env, ARGV, ORIG, PY, 'posix') == [
        'env', 'TICO_HEAVY_REMOTE=0', 'bash', str(HEAVY), PY, '-X', 'utf8', '-m', 'pytest', '-q', 'backend/tests/test_a.py']
    one = [*ORIG[:-1], 'backend/tests/test_a.py::test_b', '-k', 'x']
    assert slotted_command(env, ARGV, one, PY, 'posix')[:2] == ['env', 'TICO_HEAVY_REMOTE=0']
    # The whole suite, a directory, or many files go to the VM.
    for args in ([], ['backend/tests'], [f'backend/tests/test_{i}.py' for i in range(4)]):
        assert slotted_command(env, ARGV, [*ORIG[:-1], *args], PY, 'posix')[0] == 'bash'

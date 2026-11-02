"""scripts/remote.py sends a command to a RunsOn VM, which runs it from its own checkout of the repository."""
import pytest

from scripts import remote

ROOT = remote.ROOT


@pytest.mark.parametrize('local, sent', [
    # ui-tests.cjs and release_checks.py re-run themselves through heavy.sh by their absolute path (__filename,
    # __file__); the VM's checkout is somewhere else, so a path inside this checkout goes as a relative one.
    (['/usr/local/bin/node', f'{ROOT}/scripts/ui-tests.cjs', 'chat'], ['node', 'scripts/ui-tests.cjs', 'chat']),
    (['/opt/venv/bin/python3', f'{ROOT}/scripts/release_checks.py', '--release', '--part', 'python'],
     ['python', 'scripts/release_checks.py', '--release', '--part', 'python']),
    (['python', '-m', 'pytest', f'{ROOT}/backend/tests/test_a.py::test_b', f'--junitxml={ROOT}/out/j.xml', '-q'],
     ['python', '-m', 'pytest', 'backend/tests/test_a.py::test_b', '--junitxml=out/j.xml', '-q']),
    ([f'{ROOT}/scripts/heavy.sh', 'cargo', 'test'], ['scripts/heavy.sh', 'cargo', 'test']),
    ([f'{ROOT}/.venv/bin/python', '-c', 'print(1)'], ['python', '-c', 'print(1)']),
    (['bash', '-c', f'cd {ROOT}/backend && pytest'], ['bash', '-c', f'cd {ROOT}/backend && pytest']),   # not a path
    # Paths outside the checkout, relative paths, other flags and values stay as they are.
    (['node', '/tmp/x.cjs', 'scripts/a.cjs', '--grep=/chat/', f'{ROOT}-other/a.py'],
     ['node', '/tmp/x.cjs', 'scripts/a.cjs', '--grep=/chat/', f'{ROOT}-other/a.py']),
    (['cargo', 'test', '--manifest-path', f'{ROOT}/runner-rs/Cargo.toml'],
     ['cargo', 'test', '--manifest-path', 'runner-rs/Cargo.toml']),
])
def test_paths_inside_the_checkout_go_relative_to_it(local, sent):
    assert remote.portable(local) == sent


def test_a_path_through_a_symlinked_checkout_goes_relative_too(tmp_path):
    link = tmp_path / 'checkout'
    link.symlink_to(ROOT)
    assert remote.portable(['node', f'{link}/scripts/ui-tests.cjs']) == ['node', 'scripts/ui-tests.cjs']


@pytest.mark.parametrize('command, setup', [
    # The default release check runs pytest and then node scripts/ui-tests.cjs: it needs the browsers.
    (['python', 'scripts/release_checks.py'], 'browser'),
    (['python', 'scripts/release_checks.py', '--release'], 'all'),
    (['python', 'scripts/release_checks.py', '--shard', '2', '7'], 'python'),
    (['python', 'scripts/release_checks.py', '--record-timings', 'a', 'b'], 'python'),
    (['node', 'scripts/ui-tests.cjs', 'chat'], 'browser'),
    (['cargo', 'test'], 'rust'),
    (['python', '-m', 'pytest', '-q'], 'python'),
])
def test_setup_covers_what_the_command_runs(command, setup):
    assert remote.setup_for(command) == setup


def test_a_patient_call_waits_out_the_rate_limit_and_any_other_failure_stops_at_once(monkeypatch):
    import subprocess
    replies = [(1, 'HTTP 403: API rate limit exceeded for user ID 1.'), (0, '')]
    calls, sleeps = [], []

    def run(args, **_):
        calls.append(args)
        code, err = replies.pop(0)
        return subprocess.CompletedProcess(args, code, stdout='{"status":"completed"}' if code == 0 else '', stderr=err)

    monkeypatch.setattr(remote.subprocess, 'run', run)
    monkeypatch.setattr(remote.time, 'sleep', sleeps.append)
    assert remote.gh('run', 'view', '1', patient=True) == '{"status":"completed"}'
    assert len(calls) == 2 and sleeps == [60]
    replies[:] = [(1, 'HTTP 403: API rate limit exceeded for user ID 1.')]
    with pytest.raises(SystemExit, match='rate limit'):
        remote.gh('run', 'view', '1')                       # not patient: one try
    replies[:] = [(1, 'HTTP 404: Not Found')]
    with pytest.raises(SystemExit, match='Not Found'):
        remote.gh('run', 'view', '1', patient=True)         # not a rate limit: no retry
    assert sleeps == [60]

"""Release isolation checks must use the runner image built from the candidate."""
from types import SimpleNamespace

from scripts import release_checks


def test_isolation_uses_the_candidate_image_only_after_successful_builds(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(release_checks, 'os', SimpleNamespace(
        environ={}, getloadavg=lambda: (0, 0, 0), cpu_count=lambda: 12))
    monkeypatch.setattr(release_checks, 'subprocess', SimpleNamespace(
        run=lambda *args, **kwargs: SimpleNamespace(stdout='a' * 40)))
    monkeypatch.setattr(release_checks, 'tempfile', SimpleNamespace(mkdtemp=lambda **kwargs: str(tmp_path)))
    monkeypatch.setattr(release_checks, 'free_port', lambda: 12345)

    for builds_ok, overrides, workers, browser_jobs in (
            (True, {}, '6', None), (False, {}, '6', None),
            (True, {'TICO_PYTHON_WORKERS': '2', 'TICO_UI_JOBS': '1'}, '2', '1')):
        release_checks.os.environ = {'TICO_RUNNER_TEST_IMAGE': 'stale-runner:local', **overrides}
        monkeypatch.setattr(release_checks, 'time', SimpleNamespace(monotonic=iter(range(100, 200)).__next__))
        calls, completed = {}, set()

        class Check:
            def __init__(self, name, command, log_dir, env, tests=False):
                calls[name] = (command, env, completed.copy())
                assert tests == (name in ('all-python', 'isolation'))   # the test steps must run tests to pass
                self.name, self.log = name, tmp_path / f'{name}.log'
                self.started = release_checks.time.monotonic()

            def join(self):
                completed.add(self.name)
                self.code = int(self.name == 'build-runner' and not builds_ok)
                self.seconds = 1.0
                return self.code

            def tail(self):
                return 'synthetic build failure'

        monkeypatch.setattr(release_checks, 'Check', Check)
        assert release_checks.release(SimpleNamespace(previous=None, part=None)) == (0 if builds_ok else 1)
        output = capsys.readouterr().out
        python_command = calls['all-python'][0]
        isolation_file = 'runner/tests/test_isolation_docker.py'
        assert f'--ignore={isolation_file}' in python_command
        assert python_command[python_command.index('-n') + 1] == workers
        assert calls['all-browser'][1].get('TICO_UI_JOBS') == browser_jobs
        builds = {'build-server', 'build-runner', 'build-updater'}
        assert builds <= calls['all-python'][2] and builds <= calls['all-browser'][2]
        assert not calls['journey'][2]
        for offset, name in enumerate(calls, 1):
            code = 1 if name == 'build-runner' and not builds_ok else 0
            assert f'| {name} | {offset:.1f} | 1.0 | {code} |' in output
        if builds_ok:
            command, env, finished = calls['isolation']
            assert command[:4] == [release_checks.sys.executable, '-m', 'pytest', '-q']
            assert isolation_file in command and command[command.index('-m', 2) + 1] == 'slow or not slow'
            assert builds <= finished
            assert command[command.index('-n') + 1] == workers
            runner_build = calls['build-runner'][0]
            assert env['TICO_RUNNER_TEST_IMAGE'] == 'tico-rc-runner:local'
            assert env['TICO_RUNNER_TEST_IMAGE'] in runner_build
            assert {'smoke', 'side-jobs'} <= calls.keys()
            assert (tmp_path / 'images-ready').read_text() == 'ok'
        else:
            assert 'isolation' not in calls
            assert 'isolation: not run' in output
            assert '| isolation | - | - | not run |' in output
            assert (tmp_path / 'images-ready').read_text() == 'failed'
    assert release_checks.BUDGET_SECONDS == 480 and release_checks.DEFAULT_BUDGET_SECONDS == 300


def test_a_test_step_that_passes_nothing_fails(tmp_path):
    import sys
    def run(output, tests=True):
        check = release_checks.Check('step', [sys.executable, '-c', f'print({output!r})'], tmp_path, {}, tests=tests)
        return check.join()
    assert run('3 passed, 1 skipped in 1.0s') == 0
    assert run('\x1b[32m\x1b[1m5 passed\x1b[0m in 1.0s') == 0   # FORCE_COLOR / PY_COLORS output
    assert run('4 skipped in 0.5s') == 1                # all skipped: the step proved nothing
    assert run('no tests ran in 0.4s') == 1
    assert run('4 skipped in 0.5s', tests=False) == 0   # a non-test step is judged by its exit code alone


def test_the_parts_together_run_exactly_the_full_gate(tmp_path, monkeypatch, capsys):
    tests = [f'backend/tests/test_{n % 3}.py::test_{n}' for n in range(10)]
    collect = SimpleNamespace(returncode=0, stdout='\n'.join(tests) + '\n\n10 tests collected in 0.1s\n', stderr='')
    monkeypatch.setattr(release_checks, 'os', SimpleNamespace(
        environ={'TICO_PYTHON_WORKERS': '4'}, getloadavg=lambda: (0, 0, 0), cpu_count=lambda: 4))
    monkeypatch.setattr(release_checks, 'subprocess', SimpleNamespace(
        run=lambda command, **kwargs: collect if '--collect-only' in command else SimpleNamespace(stdout='a' * 40)))
    monkeypatch.setattr(release_checks, 'tempfile', SimpleNamespace(mkdtemp=lambda **kwargs: str(tmp_path)))
    monkeypatch.setattr(release_checks, 'free_port', lambda: 12345)
    monkeypatch.setattr(release_checks, 'time', SimpleNamespace(monotonic=iter(range(100, 10000)).__next__))
    calls = {}

    class Check:
        def __init__(self, name, command, log_dir, env, tests=False):
            calls[name] = (command, tests)
            self.name, self.log, self.started, self.code, self.seconds = name, tmp_path / name, 1, 0, 1.0

        def join(self):
            return 0

    monkeypatch.setattr(release_checks, 'Check', Check)
    run = lambda part, shard=None: release_checks.release(SimpleNamespace(previous=None, part=part, shard=shard))

    assert run('docker') == 0
    assert 'all-python' not in calls and 'all-browser' not in calls
    assert {'journey', 'build-runner', 'smoke', 'side-jobs', 'isolation'} <= calls.keys()
    calls.clear()
    assert run('browser') == 0 and list(calls) == ['all-browser']
    assert calls['all-browser'] == (['node', 'scripts/ui-tests.cjs', '--all'], False)
    selected, receipts = [], []
    for index in range(1, 4):
        calls.clear()
        assert run('python', (index, 3)) == 0
        command, test_step = calls[f'python-shard-{index}']
        assert test_step and command[command.index('-n') + 1] == '4'
        assert command[6:9] == release_checks.PYTHON_SELECTION
        selected += command[9:]
        receipts.append(next(line for line in capsys.readouterr().out.splitlines() if line.startswith('PYTHON_SHARD ')))
    assert sorted(selected) == sorted(tests) and len(selected) == len(tests)   # disjoint and complete
    assert all('"collected": 10' in line for line in receipts)
    collect.returncode = 2
    assert run('python', (1, 3)) == 1   # a collection error fails the shard rather than running nothing


def test_shard_arguments_are_checked():
    import argparse
    import pytest
    assert release_checks.shard('2/4') == (2, 4)
    for text in ('0/4', '5/4', '4', 'a/b'):
        with pytest.raises(argparse.ArgumentTypeError):
            release_checks.shard(text)

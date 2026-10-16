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
        assert release_checks.release(SimpleNamespace(previous=None, part='all', shard=None)) == (0 if builds_ok else 1)
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


def test_parts_and_shards_together_run_the_whole_gate(tmp_path, monkeypatch):
    listing = {'slow': 'b.py::s1\nb.py::s2\n', 'slow or not slow': 'a.py::t1\na.py::t2\nb.py::s1\nb.py::s2\nc.py::t\n'}
    monkeypatch.setattr(release_checks, 'subprocess', SimpleNamespace(run=lambda command, **kwargs: SimpleNamespace(
        stdout=listing[command[command.index('-m', 3) + 1]] if '--collect-only' in command else 'a' * 40)))
    shards = [release_checks.shard_files(index, 2) for index in (1, 2)]
    assert sorted(shards[0] + shards[1]) == ['a.py', 'b.py', 'c.py'] and shards[0] == ['b.py']   # every file, once

    monkeypatch.setattr(release_checks, 'tempfile', SimpleNamespace(mkdtemp=lambda **kwargs: str(tmp_path)))
    for part, expected in (('docker', {'journey', 'build-server', 'build-runner', 'build-updater', 'smoke', 'side-jobs',
                                       'isolation'}), ('python', {'all-python'}), ('browser', {'all-browser'})):
        calls = {}

        class Check:
            def __init__(self, name, command, log_dir, env, tests=False):
                calls[name], self.name, self.code, self.seconds, self.started = command, name, 0, 1.0, 0.0

            def join(self):
                return 0

        monkeypatch.setattr(release_checks, 'Check', Check)
        shard = (2, 2) if part == 'python' else None
        assert release_checks.release(SimpleNamespace(previous=None, part=part, shard=shard)) == 0
        assert set(calls) == expected
    assert calls == {'all-browser': ['node', 'scripts/ui-tests.cjs', '--all']}

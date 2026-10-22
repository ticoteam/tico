"""Release isolation checks must use the runner image built from the candidate."""
from types import SimpleNamespace

import pytest

from scripts import release_checks


def test_isolation_uses_the_candidate_image_only_after_successful_builds(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(release_checks, 'os', SimpleNamespace(
        environ={}, getloadavg=lambda: (0, 0, 0), cpu_count=lambda: 12))
    monkeypatch.setattr(release_checks, 'subprocess', SimpleNamespace(
        run=lambda *args, **kwargs: SimpleNamespace(stdout='a' * 40)))
    monkeypatch.setattr(release_checks, 'tempfile', SimpleNamespace(mkdtemp=lambda **kwargs: str(tmp_path)))
    monkeypatch.setattr(release_checks, 'free_port', lambda: 12345)
    monkeypatch.setattr(release_checks, 'edge_networks', lambda: (True, 'edge networks: ok'))

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
        assert release_checks.release(SimpleNamespace(previous=None)) == (0 if builds_ok else 1)
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
            assert calls['smoke'][1]['TICO_SMOKE_EDGE'] == release_checks.EDGE['smoke']
            assert calls['journey'][1]['TICO_JOURNEY_EDGE'] == release_checks.EDGE['journey']
            assert calls['journey'][1]['TICO_BACKUP_EDGE'] == release_checks.EDGE['backup-test']
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


def test_each_part_runs_only_its_own_slice_of_the_gate(tmp_path, monkeypatch):
    monkeypatch.setattr(release_checks, 'os', SimpleNamespace(
        environ={}, getloadavg=lambda: (0, 0, 0), cpu_count=lambda: 4))
    monkeypatch.setattr(release_checks, 'subprocess', SimpleNamespace(
        run=lambda *args, **kwargs: SimpleNamespace(stdout='a' * 40)))
    monkeypatch.setattr(release_checks, 'tempfile', SimpleNamespace(mkdtemp=lambda **kwargs: str(tmp_path)))
    monkeypatch.setattr(release_checks, 'free_port', lambda: 12345)
    monkeypatch.setattr(release_checks, 'shard_files', lambda index, total, env: [f'group{index}of{total}.py'])
    calls, edge_checks = {}, []
    monkeypatch.setattr(release_checks, 'edge_networks', lambda: edge_checks.append(len(calls)) or (True, 'ok'))

    class Check:
        def __init__(self, name, command, log_dir, env, tests=False):
            calls[name] = command
            self.name, self.log, self.code, self.seconds = name, tmp_path / f'{name}.log', 0, 1.0
            self.started = release_checks.time.monotonic()

        def join(self):
            return 0

    monkeypatch.setattr(release_checks, 'Check', Check)
    product = {'journey', 'build-server', 'build-runner', 'build-updater', 'smoke', 'side-jobs', 'isolation'}
    for part, shard, expected in (('product', None, product), ('browser', None, {'all-browser'}),
                                  ('python', None, {'all-python'}), ('python', (2, 4), {'python-2-of-4'})):
        calls.clear()
        monkeypatch.setattr(release_checks, 'time', SimpleNamespace(monotonic=iter(range(100, 200)).__next__))
        assert release_checks.release(SimpleNamespace(previous=None, part=part, shard=shard)) == 0
        assert set(calls) == expected
        assert edge_checks == ([0] if part == 'product' else [])   # before any stack starts, product only
        edge_checks.clear()
        if part == 'python':   # alone on its VM, the Python slice uses every core
            command = calls[next(iter(expected))]
            assert command[command.index('-n') + 1] == '4'
            assert ('group2of4.py' in command) == bool(shard)
    calls.clear()
    monkeypatch.setattr(release_checks, 'time', SimpleNamespace(monotonic=iter(range(100, 200)).__next__))
    assert release_checks.release(SimpleNamespace(previous=None)) == 0
    assert set(calls) == product | {'all-python', 'all-browser'}
    assert calls['all-python'][calls['all-python'].index('-n') + 1] == '2'
    calls.clear()
    monkeypatch.setattr(release_checks, 'edge_networks', lambda: (False, 'edge networks: could not create smoke'))
    assert release_checks.release(SimpleNamespace(previous=None, part='product', shard=None)) == 1
    assert not calls   # no stack starts when the edge networks cannot be held side by side


def test_python_shards_cover_every_collected_file_once_balanced_by_tests(monkeypatch):
    import pytest
    listing = '\n'.join(['a.py::t%d' % i for i in range(6)] + ['b.py::t%d' % i for i in range(3)]
                        + ['c.py::T::t[x::y]', 'c.py::T::u', 'd.py::t', '', '12 tests collected in 0.1s'])
    monkeypatch.setattr(release_checks, 'subprocess', SimpleNamespace(
        run=lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout=listing, stderr='')))
    shards = [release_checks.shard_files(index, 3, {}) for index in (1, 2, 3)]
    assert sorted(sum(shards, [])) == ['a.py', 'b.py', 'c.py', 'd.py']
    assert shards == [['a.py'], ['b.py'], ['c.py', 'd.py']]
    with pytest.raises(RuntimeError, match='cannot fill'):
        release_checks.shard_files(1, 5, {})
    monkeypatch.setattr(release_checks, 'subprocess', SimpleNamespace(
        run=lambda *args, **kwargs: SimpleNamespace(returncode=2, stdout='ImportError', stderr='')))
    with pytest.raises(RuntimeError, match='collection failed'):
        release_checks.shard_files(1, 2, {})
    for bad in ('0/4', '5/4', '1-4', ''):
        with pytest.raises(Exception):
            release_checks.parse_shard(bad)
    assert release_checks.parse_shard('3/4') == (3, 4)


def test_shard_alone_runs_a_file_group_of_the_default_suite_pr_checks_run(monkeypatch, capsys):
    listing = '\n'.join(['a.py::t%d' % i for i in range(4)] + ['b.py::t0', 'b.py::t1', 'c.py::t', 'd.py::t',
                                                                 '', '8/9 tests collected (1 deselected) in 0.1s'])
    commands = []

    def run(command, **kwargs):
        commands.append(command)
        return SimpleNamespace(returncode=0, stdout=listing, stderr='')
    monkeypatch.setattr(release_checks, 'subprocess', SimpleNamespace(run=run))
    monkeypatch.setattr(release_checks, 'release', lambda args: pytest.fail('--shard alone is not the release gate'))
    monkeypatch.setattr(release_checks, 'full', lambda: pytest.fail('--shard alone runs one Python file group'))
    runs = {}
    for index in (1, 2, 3):
        commands.clear()
        assert release_checks.main(['--shard', f'{index}/3']) == 0
        collect, test = commands
        assert collect[collect.index('pytest') + 1:] == ['--collect-only', '-q', '-n', '0']   # pytest.ini selects
        assert test[test.index('pytest') + 1:test.index('pytest') + 3] == ['-q', '-n']   # every core, not the ini cap
        runs[index] = test[test.index('pytest') + 4:]   # then only files: no release marker or ignore
    files = sum(runs.values(), [])
    assert sorted(files) == ['a.py', 'b.py', 'c.py', 'd.py']   # every collected file, none dropped or doubled
    assert runs == {1: ['a.py'], 2: ['b.py'], 3: ['c.py', 'd.py']}   # 4, 2 and 2 of the 8 tests

    for argv, message in ((['--part', 'python'], '--part needs --release'),
                          (['--release', '--shard', '1/3'], '--shard with --release needs --part python'),
                          (['--shard', '4/3'], 'K/N')):
        with pytest.raises(SystemExit):
            release_checks.main(argv)
        assert message in capsys.readouterr().err
    monkeypatch.setattr(release_checks, 'release', lambda args: 7 if args.shard == (1, 3) else 0)
    assert release_checks.main(['--release', '--part', 'python', '--shard', '1/3']) == 7


@pytest.mark.slow
def test_three_default_shards_collect_exactly_the_single_default_run(monkeypatch):
    """Real collection: the three PR-check shards hold every test of today's single `pytest` job, each once."""
    import os
    import subprocess
    import sys
    from concurrent.futures import ThreadPoolExecutor

    def collect(*files):
        result = subprocess.run([sys.executable, '-m', 'pytest', '--collect-only', '-q', '-n', '0', *files],
                                cwd=release_checks.ROOT, env=dict(os.environ), capture_output=True, text=True)
        assert result.returncode == 0, result.stdout[-2000:] + result.stderr[-2000:]
        return result.stdout
    listing = collect()   # the single job's collection, the same command shard_files runs for the default suite
    single = [line for line in listing.splitlines() if '::' in line and not line.startswith(' ')]
    with monkeypatch.context() as patch:
        patch.setattr(release_checks, 'subprocess', SimpleNamespace(
            run=lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout=listing, stderr='')))
        shards = [release_checks.shard_files(index, 3, {}, selection=()) for index in (1, 2, 3)]
    files = sum(shards, [])
    assert len(files) == len(set(files)) == len({test.split('::', 1)[0] for test in single})
    with ThreadPoolExecutor(3) as pool:
        sharded = [line for output in pool.map(lambda group: collect(*group), shards)
                   for line in output.splitlines() if '::' in line and not line.startswith(' ')]
    assert len(sharded) == len(single) and sorted(sharded) == sorted(single)

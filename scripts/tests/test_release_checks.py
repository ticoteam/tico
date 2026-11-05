"""Release isolation checks must use the runner image built from the candidate."""
from types import SimpleNamespace

import pytest

from scripts import release_checks


@pytest.fixture
def shard_tree(tmp_path, monkeypatch):
    """Tiny offline suites exercise discovery and estimates without importing their test modules."""
    monkeypatch.setattr(release_checks, 'ROOT', tmp_path)
    monkeypatch.setattr(release_checks, 'TIMINGS', tmp_path / 'test-timings.json')
    (tmp_path / 'pytest.ini').write_text('[pytest]\npython_files = *.py\n')

    def write(counts):
        for name, count in counts.items():
            path = tmp_path / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(''.join(f'def test_{i}(): pass\n' for i in range(count)))
    return write


def test_isolation_uses_the_candidate_image_only_after_successful_builds(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(release_checks, 'os', SimpleNamespace(
        environ={}, getloadavg=lambda: (0, 0, 0), cpu_count=lambda: 12))
    monkeypatch.setattr(release_checks, 'subprocess', SimpleNamespace(
        run=lambda *args, **kwargs: SimpleNamespace(stdout='a' * 40)))
    monkeypatch.setattr(release_checks, 'tempfile', SimpleNamespace(mkdtemp=lambda **kwargs: str(tmp_path)))
    monkeypatch.setattr(release_checks, 'free_port', lambda: 12345)
    monkeypatch.setattr(release_checks, 'edge_networks', lambda: (True, 'edge networks: ok'))
    monkeypatch.setattr(release_checks, 'desktop_buildable', lambda: False)

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
        wrapper = calls['all-python'][0]
        assert wrapper == [release_checks.sys.executable, str(release_checks.ROOT / 'scripts/release_checks.py'),
                           '--python-tests', workers]
        python_command, alone_command = release_checks.python_test_commands(wrapper[3], wrapper[4:])
        isolation_file = 'runner_support/tests/test_isolation_docker.py'
        assert all(f'--ignore={isolation_file}' in command for command in (python_command, alone_command))
        assert python_command[python_command.index('-n') + 1] == workers
        assert '--maxschedchunk=2' in python_command
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


@pytest.mark.parametrize('codes, parallel_summary, expected', [
    ((0, 0), '3 passed in 1.0s', 0),
    ((1,), '1 failed, 2 passed in 1.0s', 1),
    ((0, 5), '3 passed in 1.0s', 0),
    ((0, 1), '3 passed in 1.0s', 1),
    ((0, 0), '3 skipped in 1.0s', 1),
])
def test_release_python_passes_run_sequentially_and_keep_the_no_pass_guard(
        tmp_path, monkeypatch, capsys, codes, parallel_summary, expected):
    from io import StringIO

    calls, completed = [], []

    def popen(command, **kwargs):
        index = len(calls)
        assert completed == list(range(index))   # the next pass starts only after wait()
        calls.append(command)
        summary = parallel_summary if index == 0 else '3 skipped in 1.0s'
        # Empty collections can print enough lines to push the parallel summary out of Check.tail(5).
        output = '\n'.join(['collection detail'] * 8 + [summary]) + '\n'

        def wait():
            completed.append(index)
            return codes[index]
        return SimpleNamespace(stdout=StringIO(output), wait=wait)

    with monkeypatch.context() as patch:
        patch.delenv('PYTEST_ADDOPTS', raising=False)
        patch.setattr(release_checks, 'subprocess', SimpleNamespace(Popen=popen, PIPE=-1, STDOUT=-2))
        code = release_checks.main(['--python-tests', '4', 'a.py', 'b.py'])
    assert len(calls) == len(codes)
    assert calls[0] == [release_checks.sys.executable, '-m', 'pytest', '-q', '-n', '4', '--maxschedchunk=2',
                        '-m', '(slow or not slow) and not alone',
                        f'--ignore={release_checks.ISOLATION_FILE}', '--api-database-snapshots', 'a.py', 'b.py']
    if len(codes) == 2:
        assert calls[1] == [release_checks.sys.executable, '-m', 'pytest', '-q', '-n', '0', '-m', 'alone',
                           f'--ignore={release_checks.ISOLATION_FILE}', '--api-database-snapshots', 'a.py', 'b.py']
    output = capsys.readouterr().out
    check = release_checks.Check('python', [release_checks.sys.executable, '-c',
                                         f'import sys; print({output!r}); sys.exit({code})'], tmp_path, {}, tests=True)
    assert check.join() == expected


def test_release_python_junit_and_timings_include_both_passes(tmp_path, monkeypatch):
    from io import StringIO
    from xml.etree import ElementTree
    import json

    report = tmp_path / 'report with spaces.xml'
    monkeypatch.setenv('PYTEST_ADDOPTS', f'-o junit_family=xunit1 --junitxml="{report}"')
    calls = []

    def popen(command, **kwargs):
        calls.append(command)
        # Explicit command options override PYTEST_ADDOPTS, as pytest does.
        path = next((arg.split('=', 1)[1] for arg in command if arg.startswith('--junitxml=')), str(report))
        seconds = 1.5 if len(calls) == 1 else 4
        release_checks.Path(path).write_text('<testsuites><testsuite><testcase file="a.py" name="test" '
                                             f'time="{seconds}"/></testsuite></testsuites>')
        return SimpleNamespace(stdout=StringIO('1 passed in 1.0s\n'), wait=lambda: 0)

    monkeypatch.setattr(release_checks, 'subprocess', SimpleNamespace(Popen=popen, PIPE=-1, STDOUT=-2))
    assert release_checks.python_tests('4', []) == 0   # unsharded: both commands cover the whole suite
    assert len(calls) == 2
    assert release_checks.junit_seconds(report) == {'a.py': 5.5}
    assert len(list(ElementTree.parse(report).getroot().iter('testcase'))) == 2
    assert list(tmp_path.glob('*.xml')) == [report]   # the temporary alone report cannot be counted twice
    monkeypatch.setattr(release_checks, 'ROOT', tmp_path)
    monkeypatch.setattr(release_checks, 'TIMINGS', tmp_path / 'test-timings.json')
    (tmp_path / 'a.py').touch()
    assert release_checks.record_shard_timings(tmp_path) == 0
    assert json.loads(release_checks.TIMINGS.read_text())['release'] == {'a.py': 5.5}


def test_each_part_runs_only_its_own_slice_of_the_gate(tmp_path, monkeypatch):
    monkeypatch.setattr(release_checks, 'os', SimpleNamespace(
        environ={}, getloadavg=lambda: (0, 0, 0), cpu_count=lambda: 4))
    monkeypatch.setattr(release_checks, 'subprocess', SimpleNamespace(
        run=lambda *args, **kwargs: SimpleNamespace(stdout='a' * 40)))
    monkeypatch.setattr(release_checks, 'tempfile', SimpleNamespace(mkdtemp=lambda **kwargs: str(tmp_path)))
    monkeypatch.setattr(release_checks, 'free_port', lambda: 12345)
    monkeypatch.setattr(release_checks, 'shard_files', lambda index, total, env, native_first=False: [f'group{index}of{total}.py'])
    monkeypatch.setattr(release_checks, 'browser_shard', lambda index, total: [f'page{index}of{total}'])
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
    monkeypatch.setattr(release_checks, 'desktop_buildable', lambda: False)
    for part, shard, expected in (('product', None, product), ('browser', None, {'all-browser'}),
                                  ('browser', (1, 2), {'browser-1-of-2'}),
                                  ('python', None, {'all-python'}), ('python', (2, 4), {'python-2-of-4'}),
                                  ('desktop', None, {'desktop-update'})):
        calls.clear()
        monkeypatch.setattr(release_checks, 'time', SimpleNamespace(monotonic=iter(range(100, 200)).__next__))
        assert release_checks.release(SimpleNamespace(previous=None, part=part, shard=shard)) == 0
        assert set(calls) == expected
        assert edge_checks == ([0] if part == 'product' else [])   # before any stack starts, product only
        edge_checks.clear()
        if part == 'python':   # alone on its VM, the Python slice uses every core
            wrapper = calls[next(iter(expected))]
            assert wrapper[2:4] == ['--python-tests', '4']
            command, alone_command = release_checks.python_test_commands(wrapper[3], wrapper[4:])
            assert '--maxschedchunk=2' in command
            assert ('group2of4.py' in command) == bool(shard)
            assert ('group2of4.py' in alone_command) == bool(shard)
            assert alone_command[alone_command.index('-n') + 1] == '0'
        if shard and part == 'browser':   # only its own scripts, not --all
            assert calls['browser-1-of-2'][2:] == ['page1of2']
    calls.clear()
    monkeypatch.setattr(release_checks, 'time', SimpleNamespace(monotonic=iter(range(100, 200)).__next__))
    assert release_checks.release(SimpleNamespace(previous=None)) == 0
    assert set(calls) == product | {'all-python', 'all-browser'}
    assert calls['all-python'][2:] == ['--python-tests', '2']
    # Given the images Release validation built, the product part tests those, pulled by digest, as their own version.
    release_checks.os.environ.update(TICO_RELEASE_VERSION='v1.2.3', TICO_RELEASE_IMAGES=','.join(
        f'{target}=ghcr.io/acme/{target}@sha256:{"b" * 64}' for target in ('server', 'runner', 'updater')))
    calls.clear()
    monkeypatch.setattr(release_checks, 'time', SimpleNamespace(monotonic=iter(range(100, 200)).__next__))
    assert release_checks.release(SimpleNamespace(previous=None, part='product', shard=None)) == 0
    assert set(calls) == product - {'build-server', 'build-runner', 'build-updater'} | {'pull-server', 'pull-runner', 'pull-updater'}
    assert calls['pull-runner'][-3:] == [f'ghcr.io/acme/runner@sha256:{"b" * 64}', 'ghcr.io/ticoteam/tico-runner:v1.2.3',
                                         'tico-rc-runner:local']
    release_checks.os.environ.clear()
    calls.clear()
    monkeypatch.setattr(release_checks, 'edge_networks', lambda: (False, 'edge networks: could not create smoke'))
    assert release_checks.release(SimpleNamespace(previous=None, part='product', shard=None)) == 1
    assert not calls   # no stack starts when the edge networks cannot be held side by side


def test_python_shards_cover_every_candidate_once_and_are_deterministic(shard_tree, monkeypatch):
    # Deliberately create files out of order; neither traversal order nor previous shard calls affects the plan.
    shard_tree({'d.py': 1, 'c.py': 2, 'b.py': 3, 'a.py': 6})
    monkeypatch.setattr(release_checks, 'subprocess', SimpleNamespace(
        run=lambda *args, **kwargs: pytest.fail('sharding must not launch pytest')))
    shards = [release_checks.shard_files(index, 3, {}) for index in (1, 2, 3)]
    assert sorted(sum(shards, [])) == ['a.py', 'b.py', 'c.py', 'd.py']
    assert shards == [['a.py'], ['b.py'], ['c.py', 'd.py']]
    assert [release_checks.shard_files(i, 3, {}) for i in (3, 2, 1)] == shards[::-1]
    with pytest.raises(RuntimeError, match='cannot fill'):
        release_checks.shard_files(1, 5, {})
    for bad in ('0/4', '5/4', '1-4', ''):
        with pytest.raises(Exception):
            release_checks.parse_shard(bad)
    assert release_checks.parse_shard('3/4') == (3, 4)


def test_filesystem_discovery_honors_pytest_config_and_ignores_without_imports(tmp_path, monkeypatch):
    monkeypatch.setattr(release_checks, 'ROOT', tmp_path)
    (tmp_path / 'pytest.ini').write_text('[pytest]\ntestpaths = cases\npython_files = check_*.py\n'
                                        'norecursedirs = excluded\naddopts = --ignore=cases/ini\n')
    (tmp_path / 'conftest.py').write_text("collect_ignore = ['cases/root_ignored']\n")
    cases = tmp_path / 'cases'
    cases.mkdir()
    (cases / 'conftest.py').write_text("collect_ignore_glob = ['check_skip*.py']\n")
    names = ['check_sync.py', 'check_skip_this.py', 'root_ignored/check_no.py', 'ini/check_no.py',
             'cli/check_no.py', 'env/check_no.py', 'excluded/check_no.py', 'virtual/check_no.py',
             'test_wrong_pattern.py']
    for name in names:
        path = cases / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('raise AssertionError("discovery must not import tests")\ndef test_one(): pass\n')
    (cases / 'virtual/pyvenv.cfg').touch()
    (tmp_path / 'check_outside.py').touch()
    assert release_checks.test_file_counts(('--ignore', 'cases/cli'), {'PYTEST_ADDOPTS': '--ignore=cases/env'}) == {
        'cases/check_sync.py': 1}


def test_default_discovery_keeps_dynamic_and_slow_only_files_and_excludes_release_isolation(tmp_path, monkeypatch):
    monkeypatch.setattr(release_checks, 'ROOT', tmp_path)
    names = ['test_dynamic.py', 'slow_test.py', '.hidden/test_no.py', 'build/test_no.py',
             'node_modules/test_no.py', 'helper.py', release_checks.ISOLATION_FILE]
    for name in names:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('# Tests may be generated at import time or selected only with -m slow.\n')
    assert release_checks.test_file_counts(env={}) == {'slow_test.py': 1, 'test_dynamic.py': 1}
    assert set(release_checks.test_file_counts((), {})) == {
        'slow_test.py', 'test_dynamic.py', release_checks.ISOLATION_FILE}
    assert release_checks.test_file_counts(('--ignore-glob=*_test.py',), {}) == {
        release_checks.ISOLATION_FILE: 1, 'test_dynamic.py': 1}


def test_coverage_guard_fails_on_unlisted_collection_and_allows_empty_candidates(shard_tree, monkeypatch, capsys):
    shard_tree({'a.py': 1, 'empty.py': 0})
    commands = []

    def collect(command, **kwargs):
        commands.append((command, kwargs))
        return SimpleNamespace(returncode=0, stdout='a.py::Test::test[x::y]\n1 test collected\n', stderr='')

    monkeypatch.setattr(release_checks, 'subprocess', SimpleNamespace(run=collect))
    assert release_checks.main(['--check-shard-coverage', '--release']) == 0
    assert commands[-1][0][2:] == ['pytest', '--collect-only', '-q', '-n', '0', *release_checks.RELEASE_SELECTION]
    assert '1 additional candidates' in capsys.readouterr().out
    assert release_checks.main(['--check-shard-coverage']) == 0
    assert commands[-1][0][2:] == ['pytest', '--collect-only', '-q', '-n', '0']
    with pytest.raises(RuntimeError, match='absent.*custom.py'):
        release_checks.validate_shard_coverage({'a.py', 'custom.py'}, env={})
    monkeypatch.setattr(release_checks, 'subprocess', SimpleNamespace(
        run=lambda *args, **kwargs: SimpleNamespace(returncode=2, stdout='ImportError', stderr='')))
    with pytest.raises(RuntimeError, match='collection failed'):
        release_checks.check_shard_coverage({})
    monkeypatch.setattr(release_checks, 'subprocess', SimpleNamespace(
        run=lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout='', stderr='')))
    with pytest.raises(RuntimeError, match='no test files'):
        release_checks.check_shard_coverage({})


def test_pr_guard_reuses_the_existing_default_collection(tmp_path, monkeypatch):
    from scripts import pr_checks

    (tmp_path / 'test_listed.py').write_text('def test_one(): pass\n')
    commands = []

    def collect(command, **kwargs):
        commands.append(command)
        return SimpleNamespace(returncode=0, stdout='test_listed.py::test_one\ncustom.py::test_lost\n', stderr='')

    monkeypatch.setattr(pr_checks, 'subprocess', SimpleNamespace(run=collect))
    with pytest.raises(RuntimeError, match='absent.*custom.py'):
        pr_checks.collect(root=tmp_path)
    assert len(commands) == 1   # the plan already pays for this collection; the guard never launches another


def test_python_shards_balance_by_recorded_seconds_and_still_run_unrecorded_files(tmp_path, monkeypatch, shard_tree):
    import json
    timings = tmp_path / 'test-timings.json'
    timings.write_text(json.dumps({'default': {'a.py': 1, 'b.py': 30, 'c.py': 10, 'gone.py': 99},
                                   'release': {'a.py': 50}}))
    monkeypatch.setattr(release_checks, 'TIMINGS', timings)
    shard_tree({'a.py': 6, 'b.py': 3, 'c.py': 2, 'd.py': 1})
    # By seconds, not count: a.py's 6 quick tests share a shard with d.py, which has no record and weighs its one
    # test at the recorded 41 s / 11 tests; a recorded file that is no longer collected (gone.py) is ignored.
    default = [release_checks.shard_files(index, 3, {}, selection=()) for index in (1, 2, 3)]
    assert default == [['b.py'], ['c.py'], ['a.py', 'd.py']]
    # The release step reads its own record: only a.py is recorded, the rest weigh 50/6 s per test.
    release = [release_checks.shard_files(index, 3, {}) for index in (1, 2, 3)]
    assert release == [['a.py'], ['b.py'], ['c.py', 'd.py']]
    for shards in (default, release):   # every collected file, the unrecorded one included, exactly once
        assert sorted(sum(shards, [])) == ['a.py', 'b.py', 'c.py', 'd.py']
    timings.unlink()   # no record at all: balanced by test count, as before
    assert [release_checks.shard_files(index, 3, {}) for index in (1, 2, 3)] == [['a.py'], ['b.py'], ['c.py', 'd.py']]


def test_an_unrecorded_file_weighs_its_source_estimate_at_the_recorded_seconds_per_test(tmp_path, monkeypatch, shard_tree):
    import json
    timings = tmp_path / 'test-timings.json'
    timings.write_text(json.dumps({'default': {'a.py': 10, 'b.py': 10, 'z.py': 0.0}}))
    monkeypatch.setattr(release_checks, 'TIMINGS', timings)
    shard_tree({'a.py': 1, 'b.py': 1, 'z.py': 1})
    (tmp_path / 'new.py').write_text('def test_sync(): pass\nasync def test_async(): pass\n'
                                     'class TestCases:\n    def test_method(self): pass\n')
    assert release_checks.test_file_counts((), {})['new.py'] == 3
    # new.py's 3 tests at 20 s / 3 recorded tests weigh 20 s, the heaviest, so it goes first; at 1 s a test it would
    # go last. z.py's recorded 0.0 is a record, not a missing one.
    assert [release_checks.shard_files(index, 3, {}, selection=()) for index in (1, 2, 3)] == [
        ['new.py'], ['a.py', 'z.py'], ['b.py']]
    assert release_checks.file_weights({'z.py': 1, 'new.py': 3}, {'z.py': 0.0, 'a.py': 1})[1] == ['new.py']


def test_recorded_timings_split_into_three_shards_within_ten_percent_of_the_mean(monkeypatch):
    """The checked-in record: on its own seconds, the slowest of the three shards is within 10% of the mean."""
    import json
    recorded = json.loads(release_checks.TIMINGS.read_text())
    for kind, selection in (('default', ()), ('release', release_checks.RELEASE_SELECTION)):
        timings = recorded[kind]
        monkeypatch.setattr(release_checks, 'test_file_counts', lambda *args: dict.fromkeys(timings, 1))
        shards = [release_checks.shard_files(index, 3, {}, selection=selection) for index in (1, 2, 3)]
        assert sorted(sum(shards, [])) == sorted(timings)
        loads = [sum(timings[name] for name in shard) for shard in shards]
        assert max(loads) <= 1.1 * sum(loads) / 3, (kind, loads)


def test_record_timings_writes_each_files_seconds_from_junit_reports(tmp_path, monkeypatch):
    import json
    import pytest

    def report(name, cases):
        path = tmp_path / name
        path.write_text('<testsuites><testsuite>' + ''.join(
            '<testcase ' + (f'file="{file}" ' if file else '') + f'name="t" time="{seconds}"/>' for file, seconds in cases)
            + '</testsuite></testsuites>')
        return str(path)
    monkeypatch.setattr(release_checks, 'TIMINGS', tmp_path / 'test-timings.json')
    default = report('default.xml', [('a.py', 1.5), ('a.py', 2), ('b.py', 0.25)])
    slow = report('slow.xml', [('a.py', 10), ('s.py', 4)])
    assert release_checks.main(['--record-timings', default, slow]) == 0
    assert json.loads(release_checks.TIMINGS.read_text()) == {
        'default': {'a.py': 3.5, 'b.py': 0.25}, 'release': {'a.py': 13.5, 'b.py': 0.25, 's.py': 4.0}}
    with pytest.raises(RuntimeError, match='xunit1'):
        release_checks.record_timings(report('xunit2.xml', [(None, 1)]), slow)
    log = tmp_path / 'browser.log'
    log.write_text('UI tests: jobs=3\nok    a.cjs (2.5s)\nFAIL  b.cjs (60.0s)\n      TimeoutError\nok    c.cjs (0.4s)\n')
    assert release_checks.main(['--record-browser-timings', str(log)]) == 0
    release_checks.record_timings(default, slow)   # the junit refresh keeps the browser record
    assert json.loads(release_checks.TIMINGS.read_text())['browser'] == {'a': 2.5, 'c': 0.4}


def test_record_shard_timings_refreshes_release_from_shard_junit_and_keeps_unrun_files_that_exist(tmp_path, monkeypatch):
    import json
    monkeypatch.setattr(release_checks, 'ROOT', tmp_path)
    for name in ('a.py', 'b.py', 'kept.py'):
        (tmp_path / name).write_text('')
    monkeypatch.setattr(release_checks, 'TIMINGS', tmp_path / 'test-timings.json')
    release_checks.TIMINGS.write_text(json.dumps({
        'default': {'a.py': 1, 'gone.py': 5}, 'release': {'a.py': 9, 'kept.py': 3, 'gone.py': 7}, 'browser': {'x': 1.0}}))
    reports = tmp_path / 'junit'
    for shard, cases in ((1, [('a.py', 1.5), ('a.py', 2)]), (2, [('b.py', 4)])):
        (reports / f'release-junit-1-python-{shard}').mkdir(parents=True)
        (reports / f'release-junit-1-python-{shard}' / f'python-{shard}-of-2.xml').write_text(
            '<testsuites><testsuite>' + ''.join(f'<testcase file="{file}" name="t" time="{seconds}"/>'
                                                for file, seconds in cases) + '</testsuite></testsuites>')
    assert release_checks.main(['--record-shard-timings', str(reports)]) == 0
    # a.py's fresh seconds replace its record; kept.py did not run but exists, so it keeps its 3 s; gone.py is dropped.
    assert json.loads(release_checks.TIMINGS.read_text()) == {
        'default': {'a.py': 1}, 'release': {'a.py': 3.5, 'b.py': 4.0, 'kept.py': 3}, 'browser': {'x': 1.0}}
    with pytest.raises(RuntimeError, match='no junit reports'):
        release_checks.record_shard_timings(str(tmp_path / 'empty'))


def test_shard_alone_runs_a_file_group_of_the_default_suite_pr_checks_run(monkeypatch, capsys, shard_tree):
    shard_tree({'a.py': 4, 'b.py': 2, 'c.py': 1, 'd.py': 1})
    commands = []

    def run(command, **kwargs):
        commands.append(command)
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(release_checks, 'subprocess', SimpleNamespace(run=run))
    monkeypatch.setattr(release_checks, 'release', lambda args: pytest.fail('--shard alone is not the release gate'))
    monkeypatch.setattr(release_checks, 'full', lambda: pytest.fail('--shard alone runs one Python file group'))
    runs = {}
    for index in (1, 2, 3):
        commands.clear()
        assert release_checks.main(['--shard', f'{index}/3']) == 0
        test, = commands   # no whole-suite collection before running the shard
        assert test[test.index('pytest') + 1:test.index('pytest') + 3] == ['-q', '-n']   # every core, not the ini cap
        assert test[test.index('pytest') + 4] == '--api-database-snapshots'   # one schema build per worker
        runs[index] = test[test.index('pytest') + 5:]   # then only files: no release marker or ignore
    files = sum(runs.values(), [])
    assert sorted(files) == ['a.py', 'b.py', 'c.py', 'd.py']   # every collected file, none dropped or doubled
    assert runs == {1: ['a.py'], 2: ['b.py'], 3: ['c.py', 'd.py']}   # 4, 2 and 2 of the 8 tests

    for argv, message in ((['--part', 'python'], '--part needs --release'),
                          (['--release', '--shard', '1/3'], '--shard with --release needs --part python or browser'),
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
    listing = collect()   # the single job's collection, compared with filesystem discovery
    single = [line for line in listing.splitlines() if '::' in line and not line.startswith(' ')]
    shards = [release_checks.shard_files(index, 3, {}, selection=()) for index in (1, 2, 3)]
    files = sum(shards, [])
    assert len(files) == len(set(files))
    assert {test.split('::', 1)[0] for test in single} <= set(files)   # slow-only and empty files are allowed
    with ThreadPoolExecutor(3) as pool:
        sharded = [line for output in pool.map(lambda group: collect(*group), shards)
                   for line in output.splitlines() if '::' in line and not line.startswith(' ')]
    assert len(sharded) == len(single) and sorted(sharded) == sorted(single)


def test_native_test_files_take_the_first_two_shards_and_the_rest_balance_over_the_other_shards(tmp_path, monkeypatch, shard_tree):
    shard_tree({'n1.py': 1, 'n2.py': 1, 'a.py': 3, 'b.py': 2, 'c.py': 1})
    for name, text in (('n1.py', 'def test_native(rust_binary): ...'), ('n2.py', 'env = {"TICO_SCREEN_RELAY_BIN": x}')):
        (tmp_path / name).write_text(text)
    monkeypatch.setattr(release_checks, 'ROOT', tmp_path)
    monkeypatch.setattr(release_checks, 'TIMINGS', tmp_path / 'none.json')
    shards = [release_checks.shard_files(index, 4, {}, native_first=True) for index in (1, 2, 3, 4)]
    assert shards == [['n1.py'], ['n2.py'], ['a.py'], ['b.py', 'c.py']]
    assert sorted(sum(shards, [])) == ['a.py', 'b.py', 'c.py', 'n1.py', 'n2.py']
    # Without native_first (PR checks), the same files balance over all shards as before.
    assert sorted(sum((release_checks.shard_files(i, 4, {}) for i in (1, 2, 3, 4)), [])) == sorted(sum(shards, []))
    with pytest.raises(RuntimeError, match='more than 2 shards'):
        release_checks.shard_files(1, 2, {}, native_first=True)


def test_native_test_files_include_tests_that_borrow_a_native_test_modules_helpers(tmp_path, monkeypatch):
    tests = tmp_path / 'backend/tests'
    tests.mkdir(parents=True)
    (tests / 'test_relay.py').write_text('def relay(): return os.environ["TICO_SCREEN_RELAY_BIN"]\n')
    (tests / 'test_uses.py').write_text('from backend.tests.test_relay import relay\n')
    (tests / 'test_uses_too.py').write_text('from backend.tests import test_uses\n')
    (tests / 'test_plain.py').write_text('from backend.tests.test_api import client\n')
    monkeypatch.setattr(release_checks, 'ROOT', tmp_path)
    names = [f'backend/tests/{n}.py' for n in ('test_uses', 'test_uses_too', 'test_plain')]
    assert release_checks.native_test_files(names) == {'backend/tests/test_uses.py', 'backend/tests/test_uses_too.py'}


def test_native_test_files_follow_the_runner_support_package(tmp_path, monkeypatch):
    tests = tmp_path / 'runner_support/tests'
    tests.mkdir(parents=True)
    (tests / 'test_engine.py').write_text('from runner_support import engine\n')
    (tests / 'test_prepare.py').write_text('run("-m", "runner_support.engine", "prepare")\n')
    (tests / 'test_borrows.py').write_text('from runner_support.tests.test_engine import fake\n')
    (tests / 'test_plain.py').write_text('from runner_support import redact\n')
    monkeypatch.setattr(release_checks, 'ROOT', tmp_path)
    names = [f'runner_support/tests/{n}.py' for n in ('test_engine', 'test_prepare', 'test_borrows', 'test_plain')]
    assert release_checks.native_test_files(names) == set(names[:3])


def test_the_screen_relay_borrowers_and_scripts_tico_runs_are_native():
    # v0.5.59's first validation: test_systemd_units runs scripts/tico install, which needs the runner binary.
    names = ['backend/tests/test_screen.py', 'backend/tests/test_browser_exit_relay.py',
             'runner_support/tests/test_systemd_units.py', 'backend/tests/test_api.py', 'backend/tests/test_selected_claim.py']
    assert release_checks.native_test_files(names) == set(names[:3])

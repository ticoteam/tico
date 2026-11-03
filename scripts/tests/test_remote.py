"""scripts/remote.py sends a command to a RunsOn VM, which runs it from its own checkout of the repository."""
from types import SimpleNamespace

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


class Clock:
    """Stands in for the time module: sleeping only moves the clock on."""

    def __init__(self):
        self.now = 1000.0
        self.sleeps = []

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


@pytest.fixture
def clock(monkeypatch):
    fake = Clock()
    monkeypatch.setattr(remote, 'time', fake)
    return fake


def scripted_gh(monkeypatch, *replies):
    """gh() answers each call with the next reply, or raises it when it is an exception."""
    calls = []

    def fake_gh(*args, capture=True):
        calls.append(args)
        reply = replies[len(calls) - 1]
        if isinstance(reply, BaseException):
            raise reply
        return reply

    monkeypatch.setattr(remote, 'gh', fake_gh)
    return calls


RUN_LISTED = '[{"databaseId": 9, "displayTitle": "Remote run abc", "url": "https://example.test/runs/9"}]'
LIMITED = remote.RateLimited('remote: gh run view failed: HTTP 403: API rate limit exceeded for user ID 1')


def test_wait_polls_every_30_seconds_while_queued_and_60_while_in_progress(monkeypatch, clock):
    calls = scripted_gh(monkeypatch,
                        '{"status": "queued", "conclusion": null}',
                        '{"status": "in_progress", "conclusion": null}',
                        '{"status": "in_progress", "conclusion": null}',
                        '{"status": "completed", "conclusion": "success"}')
    assert remote.wait({'databaseId': 7}) == 'success'
    assert clock.sleeps == [30, 60, 60]
    assert calls == [('run', 'view', '7', '--json', 'status,conclusion')] * 4


def test_find_run_polls_every_10_seconds_until_the_run_is_listed(monkeypatch, clock):
    scripted_gh(monkeypatch, '[]', '[]', RUN_LISTED)
    assert remote.find_run('abc', clock.now + 180)['databaseId'] == 9
    assert clock.sleeps == [10, 10]


def test_a_rate_limit_in_wait_backs_off_60_120_240_then_300_and_keeps_waiting(monkeypatch, clock, capsys):
    scripted_gh(monkeypatch, LIMITED, LIMITED, LIMITED, LIMITED, LIMITED,
                '{"status": "completed", "conclusion": "failure"}')
    assert remote.wait({'databaseId': 7}) == 'failure'
    assert clock.sleeps == [60, 120, 240, 300, 300]
    assert 'remote: GitHub API rate limited; waiting 60s' in capsys.readouterr().err


def test_a_rate_limit_in_find_run_does_not_use_up_its_deadline(monkeypatch, clock):
    # The three waits add up to 420 s, past the 180 s deadline; the run is still looked for after them.
    scripted_gh(monkeypatch, LIMITED, LIMITED, LIMITED, '[]', RUN_LISTED)
    assert remote.find_run('abc', clock.now + 180)['databaseId'] == 9
    assert clock.sleeps == [60, 120, 240, 10]


def test_other_gh_errors_still_stop_the_wait(monkeypatch, clock):
    scripted_gh(monkeypatch, SystemExit('remote: gh run view failed: HTTP 404: Not Found'))
    with pytest.raises(SystemExit, match='HTTP 404'):
        remote.wait({'databaseId': 7})
    assert clock.sleeps == []


@pytest.mark.parametrize('stderr, limited', [
    ('HTTP 403: API rate limit exceeded for user ID 1', True),
    ('You have exceeded a secondary rate limit', True),
    ('HTTP 429: Too Many Requests', True),
    ('HTTP 403: Resource not accessible by integration', True),
    ('HTTP 404: Not Found', False),
    ('', False),
])
def test_gh_marks_a_rate_limit_apart_from_other_failures(monkeypatch, stderr, limited):
    failed = SimpleNamespace(returncode=1, stdout='', stderr=stderr)
    monkeypatch.setattr(remote, 'subprocess', SimpleNamespace(run=lambda *args, **kwargs: failed))
    with pytest.raises(SystemExit) as caught:
        remote.gh('run', 'view', '7')
    assert isinstance(caught.value, remote.RateLimited) == limited
    assert caught.value.code == 'remote: gh run view failed: ' + stderr

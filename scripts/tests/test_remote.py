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


def fake_gh(monkeypatch, replies):
    """subprocess.run for `gh`: each call takes the next (returncode, stdout, stderr) from `replies`."""
    calls = []

    def run(argv, **kwargs):
        calls.append(argv)
        code, out, err = replies.pop(0)
        return SimpleNamespace(returncode=code, stdout=out, stderr=err)

    monkeypatch.setattr(remote.subprocess, 'run', run)
    return calls


def fake_clock(monkeypatch):
    """Time stands still at 1,000,000 s, and sleep() only records how long it was asked to wait."""
    sleeps = []
    monkeypatch.setattr(remote.time, 'time', lambda: 1_000_000)
    monkeypatch.setattr(remote.time, 'sleep', sleeps.append)
    return sleeps


RATE_LIMIT_ERRORS = [
    'HTTP 403: API rate limit exceeded for user ID 1.',
    'You have exceeded a secondary rate limit. Please wait a few minutes before you try again.',
    'HTTP 429: Too Many Requests',
]


@pytest.mark.parametrize('error', RATE_LIMIT_ERRORS)
def test_a_rate_limited_poll_waits_for_the_reset_then_makes_the_same_call(monkeypatch, capsys, error):
    sleeps = fake_clock(monkeypatch)
    calls = fake_gh(monkeypatch, [(1, '', error), (0, '1000120\n', ''), (0, '{"status": "queued"}', '')])
    assert remote.gh('run', 'view', '7', poll=True) == '{"status": "queued"}'
    assert sleeps == [120]
    assert calls == [['gh', 'run', 'view', '7'], ['gh', 'api', 'rate_limit', '--jq', '.resources.core.reset'],
                     ['gh', 'run', 'view', '7']]
    assert capsys.readouterr().err.count('rate limit hit') == 1


@pytest.mark.parametrize('reset, seconds', [
    ('1000120\n', 120),  # the reset is two minutes away
    ('1007200\n', 900),  # two hours away: wait 15 minutes at most
    ('999000\n', 60),    # already past (a secondary limit has no reset of its own): a minute
    ('', 60),            # unknown: a minute
])
def test_the_rate_limit_wait_is_capped_and_falls_back_to_a_minute(monkeypatch, reset, seconds):
    fake_clock(monkeypatch)
    fake_gh(monkeypatch, [(0, reset, '')])
    assert remote.rate_limit_wait() == seconds


def test_wait_keeps_polling_through_a_rate_limit(monkeypatch):
    sleeps = fake_clock(monkeypatch)
    fake_gh(monkeypatch, [
        (0, '{"status": "in_progress", "conclusion": ""}', ''),
        (1, '', 'HTTP 403: API rate limit exceeded'),
        (0, '1000120\n', ''),
        (0, '{"status": "completed", "conclusion": "success"}', ''),
    ])
    assert remote.wait({'databaseId': 7}) == 'success'
    assert sleeps == [30, 120]


@pytest.mark.parametrize('poll, error', [
    (True, 'HTTP 404: Not Found'),       # a poll that fails for another reason still stops
    (False, 'API rate limit exceeded'),  # a one-off call does not wait for the limit
])
def test_other_failures_still_exit(monkeypatch, poll, error):
    sleeps = fake_clock(monkeypatch)
    fake_gh(monkeypatch, [(1, '', error)])
    with pytest.raises(SystemExit, match='gh run view failed'):
        remote.gh('run', 'view', '7', poll=poll)
    assert sleeps == []

"""scripts/remote.py sends a command to a RunsOn VM, which runs it from its own checkout of the repository."""

import json
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest

from scripts import remote

ROOT = remote.ROOT


@pytest.mark.parametrize(
    'local, sent',
    [
        # ui-tests.cts and release_checks.py re-run themselves through heavy.sh by their absolute path (__filename,
        # __file__); the VM's checkout is somewhere else, so a path inside this checkout goes as a relative one.
        (['/usr/local/bin/node', f'{ROOT}/scripts/ui-tests.cts', 'chat'], ['node', 'scripts/ui-tests.cts', 'chat']),
        (
            ['/opt/venv/bin/python3', f'{ROOT}/scripts/release_checks.py', '--release', '--part', 'python'],
            ['python', 'scripts/release_checks.py', '--release', '--part', 'python'],
        ),
        (
            ['python', '-m', 'pytest', f'{ROOT}/backend/tests/test_a.py::test_b', f'--junitxml={ROOT}/out/j.xml', '-q'],
            ['python', '-m', 'pytest', 'backend/tests/test_a.py::test_b', '--junitxml=out/j.xml', '-q'],
        ),
        ([f'{ROOT}/scripts/heavy.sh', 'cargo', 'test'], ['scripts/heavy.sh', 'cargo', 'test']),
        ([f'{ROOT}/.venv/bin/python', '-c', 'print(1)'], ['python', '-c', 'print(1)']),
        (['bash', '-c', f'cd {ROOT}/backend && pytest'], ['bash', '-c', f'cd {ROOT}/backend && pytest']),  # not a path
        # Paths outside the checkout, relative paths, other flags and values stay as they are.
        (
            ['node', '/tmp/x.cjs', 'scripts/a.cjs', '--grep=/chat/', f'{ROOT}-other/a.py'],
            ['node', '/tmp/x.cjs', 'scripts/a.cjs', '--grep=/chat/', f'{ROOT}-other/a.py'],
        ),
        (
            ['cargo', 'test', '--manifest-path', f'{ROOT}/runner-rs/Cargo.toml'],
            ['cargo', 'test', '--manifest-path', 'runner-rs/Cargo.toml'],
        ),
    ],
)
def test_paths_inside_the_checkout_go_relative_to_it(local, sent):
    assert remote.portable(local) == sent


def test_a_path_through_a_symlinked_checkout_goes_relative_too(tmp_path):
    link = tmp_path / 'checkout'
    link.symlink_to(ROOT)
    assert remote.portable(['node', f'{link}/scripts/ui-tests.cts']) == ['node', 'scripts/ui-tests.cts']


@pytest.mark.parametrize(
    'command, setup',
    [
        # The default release check runs pytest and then node scripts/ui-tests.cts: it needs the browsers.
        (['python', 'scripts/release_checks.py'], 'browser'),
        (['python', 'scripts/release_checks.py', '--release'], 'all'),
        (['python', 'scripts/release_checks.py', '--shard', '2', '7'], 'python'),
        (['python', 'scripts/release_checks.py', '--record-timings', 'a', 'b'], 'python'),
        (['node', 'scripts/ui-tests.cts', 'chat'], 'browser'),
        (['node', 'ui/tests/chat.cts'], 'browser'),
        (['cargo', 'test'], 'rust'),
        (['python', '-m', 'pytest', '-q'], 'python'),
    ],
)
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
    assert calls == [
        ['gh', 'run', 'view', '7'],
        ['gh', 'api', 'rate_limit', '--jq', '.resources.core.reset'],
        ['gh', 'run', 'view', '7'],
    ]
    assert capsys.readouterr().err.count('rate limit hit') == 1


@pytest.mark.parametrize(
    'reset, seconds',
    [
        ('1007200\n', 900),  # two hours away: wait 15 minutes at most
        ('999000\n', 60),  # already past (a secondary limit has no reset of its own): a minute
        ('', 60),  # unknown: a minute
    ],
)
def test_the_rate_limit_wait_is_capped_and_falls_back_to_a_minute(monkeypatch, reset, seconds):
    fake_clock(monkeypatch)
    fake_gh(monkeypatch, [(0, reset, '')])
    assert remote.rate_limit_wait() == seconds


def test_wait_keeps_polling_through_a_rate_limit(monkeypatch):
    sleeps = fake_clock(monkeypatch)
    fake_gh(
        monkeypatch,
        [
            (0, '{"status": "in_progress", "conclusion": ""}', ''),
            (1, '', 'HTTP 403: API rate limit exceeded'),
            (0, '1000120\n', ''),
            (0, '{"status": "completed", "conclusion": "success"}', ''),
        ],
    )
    assert remote.wait({'databaseId': 7}) == 'success'
    assert sleeps == [30, 120]


@pytest.mark.parametrize(
    'poll, error',
    [
        (True, 'HTTP 404: Not Found'),  # a poll that fails for another reason still stops
        (False, 'API rate limit exceeded'),  # a one-off call does not wait for the limit
    ],
)
def test_other_failures_still_exit(monkeypatch, capsys, poll, error):
    sleeps = fake_clock(monkeypatch)
    fake_gh(monkeypatch, [(1, '', error)])
    with pytest.raises(SystemExit) as exit:
        remote.gh('run', 'view', '7', poll=poll)
    assert exit.value.code == remote.EX_NO_RESULT
    assert 'remote: gh run view failed' in capsys.readouterr().err
    assert sleeps == []


def runs(*statuses):
    """The reply to the workflow's run list: one run per status."""
    return json.dumps({'workflow_runs': [{'status': status} for status in statuses]})


def fake_timeline(monkeypatch):
    """A clock that moves only when sleep() is called: sleep records how long it was asked to wait and advances time."""
    now = [0.0]
    sleeps = []

    def sleep(seconds):
        sleeps.append(seconds)
        now[0] += seconds

    monkeypatch.setattr(remote.time, 'monotonic', lambda: now[0])
    monkeypatch.setattr(remote.time, 'sleep', sleep)
    return sleeps


def test_a_dispatch_under_the_cap_goes_at_once(monkeypatch, capsys):
    sleeps = fake_timeline(monkeypatch)
    calls = fake_gh(monkeypatch, [(0, runs('in_progress', 'queued', 'completed', 'success'), '')])
    assert remote.wait_for_capacity(4)
    assert sleeps == []
    assert capsys.readouterr().err == ''
    assert len(calls) == 1 and '/actions/workflows/remote-run.yml/runs' in calls[0][2]


def test_a_dispatch_at_the_cap_waits_for_a_slot_then_goes(monkeypatch, capsys):
    sleeps = fake_timeline(monkeypatch)
    fake_gh(
        monkeypatch,
        [
            (0, runs('in_progress', 'queued', 'waiting', 'pending', 'completed'), ''),  # four busy: the cap
            (0, runs('in_progress', 'queued', 'waiting', 'completed'), ''),  # one has finished
        ],
    )
    assert remote.wait_for_capacity(4)
    assert len(sleeps) == 1 and 30 <= sleeps[0] <= 60
    assert capsys.readouterr().err == 'waiting: 4 remote runs busy (cap 4)\n'


def test_a_wait_past_twenty_minutes_exits_without_dispatching(monkeypatch, capsys):
    sleeps = fake_timeline(monkeypatch)
    fake_gh(
        monkeypatch, [(0, runs('in_progress', 'in_progress', 'queued', 'queued'), '')] * 50
    )  # 20 min at 30 s a poll
    with pytest.raises(SystemExit) as exit:
        remote.wait_for_capacity(4)
    assert exit.value.code == remote.EX_NOT_DISPATCHED
    assert 'after 20 min; not dispatching' in capsys.readouterr().err
    assert sum(sleeps) == pytest.approx(20 * 60)  # the last wait is clamped to the limit, in floats


def test_a_count_that_keeps_failing_for_two_minutes_warns_and_lets_the_dispatch_go(monkeypatch, capsys):
    sleeps = fake_timeline(monkeypatch)
    fake_gh(monkeypatch, [(1, '', 'HTTP 502: Bad Gateway')] * 20)
    assert remote.wait_for_capacity(4) is False
    assert sum(sleeps) == pytest.approx(2 * 60)
    assert 'dispatching without the cap' in capsys.readouterr().err


def test_a_cap_of_zero_means_no_cap_and_no_count(monkeypatch):
    fake_gh(monkeypatch, [])  # any count would pop an empty list and fail
    assert remote.wait_for_capacity(0)


def test_remote_max_must_be_a_whole_number(monkeypatch, capsys):
    monkeypatch.setenv('TICO_REMOTE_MAX', 'many')
    with pytest.raises(SystemExit) as exit:
        remote.remote_cap()
    assert exit.value.code == remote.EX_NOT_DISPATCHED
    assert 'TICO_REMOTE_MAX must be a whole number' in capsys.readouterr().err


SHA = 'a' * 40
COMMAND = ['--', 'python', '-m', 'pytest', '-q']


def reply(out, code=0):
    return SimpleNamespace(returncode=code, stdout=out, stderr='')


def stub_remote(
    monkeypatch,
    tmp_path,
    *,
    pushed=True,
    commit=True,
    dispatch=0,
    appears=True,
    busy=0,
    status='completed',
    conclusion='failure',
    artifact=None,
):
    """Stands in for git, gh and the log download for one remote.main(). The run list and the run view answer for the
    run that was dispatched. `artifact` is the exit-code file the VM left in the log, or None when the log never came
    back. `busy` is how many remote runs the count reports as busy."""
    monkeypatch.delenv('TICO_REMOTE_MAX', raising=False)
    monkeypatch.setattr(remote, 'ROOT', tmp_path)
    fake_timeline(monkeypatch)
    dispatched = {}

    def git(*args):
        if args[0] == 'rev-parse':
            if not commit:
                raise subprocess.CalledProcessError(128, ['git', *args])
            return SHA
        if args[0] == 'branch':
            return 'origin/chris/topic' if pushed else ''
        return ''  # git status: nothing uncommitted

    def run(argv, **kwargs):
        if argv[:3] == ['gh', 'workflow', 'run']:
            dispatched['id'] = next(arg for arg in argv if arg.startswith('id=')).removeprefix('id=')
            return SimpleNamespace(returncode=dispatch, stdout='', stderr='refused' if dispatch else '')
        if argv[:3] == ['gh', 'run', 'list']:
            found = [{'databaseId': 99, 'displayTitle': f'Remote run {dispatched["id"]}', 'url': 'https://x/99'}]
            return reply(json.dumps(found if appears else []))
        if argv[:3] == ['gh', 'run', 'view']:
            return reply(json.dumps({'status': status, 'conclusion': conclusion}))
        if argv[:2] == ['gh', 'api']:
            return reply(json.dumps({'workflow_runs': [{'status': 'in_progress'}] * busy}))
        if argv[:2] == ['bash', 'scripts/ci/retry.sh']:
            if artifact is None:
                return SimpleNamespace(returncode=1, stdout='', stderr='HTTP 404: Not Found')
            folder = Path(argv[argv.index('-D') + 1])
            (folder / 'exit-code').write_text(artifact)
            (folder / 'output.log').write_text('last line\n')
            return reply('')
        raise AssertionError(f'unexpected call: {argv}')

    monkeypatch.setattr(remote, 'git', git)
    monkeypatch.setattr(remote.subprocess, 'run', run)


@pytest.mark.parametrize(
    'options, message',
    [
        ({'busy': 4}, 'not dispatching'),  # the cap held it for 20 minutes
        ({'dispatch': 1}, 'gh workflow run failed: refused'),  # GitHub refused the dispatch
        ({'pushed': False}, 'is not pushed'),
        ({'commit': False}, 'cannot resolve'),
    ],
)
def test_a_run_that_is_never_dispatched_exits_75(monkeypatch, tmp_path, capsys, options, message):
    stub_remote(monkeypatch, tmp_path, **options)
    with pytest.raises(SystemExit) as exit:
        remote.main(COMMAND)
    assert exit.value.code == remote.EX_NOT_DISPATCHED
    assert message in capsys.readouterr().err


@pytest.mark.parametrize('artifact, code', [('0', 0), ('124', 124)])
def test_the_commands_own_exit_code_is_the_exit_code(monkeypatch, tmp_path, artifact, code):
    stub_remote(monkeypatch, tmp_path, artifact=artifact)
    assert remote.main(COMMAND) == code


@pytest.mark.parametrize(
    'options, message',
    [
        ({'conclusion': 'cancelled'}, 'no result (cancelled)'),  # cancelled before the command ended, so no log
        ({'conclusion': 'failure'}, 'no result (failure)'),  # the VM was lost before it wrote a log
        ({'artifact': 'oops'}, 'no exit code in the log'),  # a log without a number is no result
    ],
)
def test_a_run_without_a_result_exits_76(monkeypatch, tmp_path, capsys, options, message):
    stub_remote(monkeypatch, tmp_path, **options)
    assert remote.main(COMMAND) == remote.EX_NO_RESULT
    assert message in capsys.readouterr().err


def test_a_run_that_never_shows_up_exits_76(monkeypatch, tmp_path, capsys):
    stub_remote(monkeypatch, tmp_path, appears=False)
    with pytest.raises(SystemExit) as exit:
        remote.main(COMMAND)
    assert exit.value.code == remote.EX_NO_RESULT
    assert 'did not appear' in capsys.readouterr().err


def test_a_run_still_going_after_two_hours_exits_76(monkeypatch, tmp_path, capsys):
    stub_remote(monkeypatch, tmp_path, status='in_progress')
    with pytest.raises(SystemExit) as exit:
        remote.main(COMMAND)
    assert exit.value.code == remote.EX_NO_RESULT
    assert 'still in_progress after 2 hours' in capsys.readouterr().err


def test_a_bad_command_line_exits_2():
    with pytest.raises(SystemExit) as exit:
        remote.main(['--'])
    assert exit.value.code == 2


class _Answer:
    def __init__(self, body):
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self):
        return self.body


def test_a_permit_is_one_keyed_write_with_the_turn_token_from_its_file(monkeypatch, tmp_path):
    """The permit route is an idempotent write (an Idempotency-Key is required), and a persistent harness's HUB_TOKEN is
    a `tico-file:` reference to the live turn's token."""
    (tmp_path / 'token').write_text('live-token\n')
    sent = []

    def urlopen(request, timeout):
        sent.append(request)
        return _Answer(b'{"allowed": true}')

    monkeypatch.setattr(remote.urllib.request, 'urlopen', urlopen)
    environ = {'HUB_API_URL': 'http://hub/', 'HUB_TOKEN': f'tico-file:{tmp_path / "token"}', 'HUB_TASK_ID': 't1'}
    assert remote.permit('0123456789ab', '4', 50, environ) is None
    (request,) = sent
    assert request.full_url == 'http://hub/api/v2/ci/permits'
    assert request.get_header('Authorization') == 'Bearer live-token'
    assert request.get_header('Idempotency-key') == '0123456789ab'
    assert json.loads(request.data) == {'task_id': 't1', 'remote_id': '0123456789ab', 'cpus': 4, 'minutes': 50}


def test_a_permit_with_no_live_turn_token_refuses_without_asking(monkeypatch, tmp_path):
    monkeypatch.setattr(remote.urllib.request, 'urlopen', lambda *a, **k: pytest.fail('asked Tico'))
    environ = {'HUB_API_URL': 'http://hub', 'HUB_TOKEN': f'tico-file:{tmp_path / "gone"}', 'HUB_TASK_ID': 't1'}
    assert 'no active Tico turn' in remote.permit('0123456789ab', '4', 50, environ)

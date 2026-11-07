"""scripts/remote.py on GitHub-hosted runners: chosen by --hosted or TICO_REMOTE_HOSTED=1, counted under their own cap,
found by their own run title, and dispatched from main like every other remote run."""

import json
from types import SimpleNamespace

import pytest

from scripts import remote


def fake_gh(monkeypatch, replies):
    """subprocess.run for `gh`: each call takes the next (returncode, stdout, stderr) from `replies`."""
    calls = []

    def run(argv, **kwargs):
        calls.append(argv)
        code, out, err = replies.pop(0)
        return SimpleNamespace(returncode=code, stdout=out, stderr=err)

    monkeypatch.setattr(remote.subprocess, 'run', run)
    return calls


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


def runs(*pairs):
    """The reply to the workflow's run list: one (status, display title) per run."""
    return json.dumps({'workflow_runs': [{'status': status, 'display_title': title} for status, title in pairs]})


RUNSON = 'Remote run 0123456789ab'
HOSTED = 'Remote run 0123456789ab (hosted)'


@pytest.mark.parametrize(
    'flag, env, hosted',
    [(False, None, False), (True, None, True), (False, '1', True), (False, '0', False)],
)
def test_the_flag_or_the_environment_picks_a_hosted_run(monkeypatch, flag, env, hosted):
    monkeypatch.delenv('TICO_REMOTE_HOSTED', raising=False)
    if env is not None:
        monkeypatch.setenv('TICO_REMOTE_HOSTED', env)
    assert remote.hosted_requested(flag) is hosted


def test_busy_runs_are_counted_apart_for_runson_and_hosted(monkeypatch):
    reply = (0, runs(('in_progress', RUNSON), ('completed', RUNSON), ('queued', HOSTED), ('waiting', HOSTED)), '')
    fake_gh(monkeypatch, [reply, reply])
    assert remote.busy_remote_runs() == 1
    assert remote.busy_remote_runs(hosted=True) == 2


def test_hosted_runs_have_their_own_cap_and_runson_runs_do_not_use_it(monkeypatch):
    sleeps = fake_timeline(monkeypatch)
    busy_runson = [('in_progress', RUNSON)] * 4
    busy_hosted = [('in_progress', HOSTED)] * 5
    fake_gh(monkeypatch, [(0, runs(*busy_runson, *busy_hosted), '')])
    assert remote.wait_for_capacity(remote.HOSTED_CAP, hosted=True)
    assert sleeps == []


def test_the_sixth_hosted_run_waits_for_a_slot(monkeypatch, capsys):
    sleeps = fake_timeline(monkeypatch)
    six = [('queued', HOSTED)] * 6
    fake_gh(monkeypatch, [(0, runs(*six), ''), (0, runs(*six[:5]), '')])
    assert remote.wait_for_capacity(remote.HOSTED_CAP, hosted=True)
    assert len(sleeps) == 1 and 30 <= sleeps[0] <= 60
    assert capsys.readouterr().err == 'waiting: 6 hosted remote runs busy (cap 6)\n'


@pytest.mark.parametrize('title', [RUNSON, HOSTED])
def test_find_run_finds_the_run_under_either_title(monkeypatch, title):
    fake_timeline(monkeypatch)
    listing = json.dumps([{'databaseId': 9, 'displayTitle': title, 'url': 'https://example.test/runs/9'}])
    fake_gh(monkeypatch, [(0, listing, '')])
    assert remote.find_run('0123456789ab', deadline=1000)['databaseId'] == 9


@pytest.mark.parametrize('hosted, value, cap', [(True, 'true', remote.HOSTED_CAP), (False, 'false', 4)])
def test_a_hosted_dispatch_is_named_as_such_and_still_comes_from_main(monkeypatch, tmp_path, hosted, value, cap):
    sha = 'a' * 40
    monkeypatch.delenv('TICO_REMOTE_MAX', raising=False)
    monkeypatch.setattr(remote, 'ROOT', tmp_path)
    monkeypatch.setattr(remote, 'git', lambda *args: sha)
    counted = []
    monkeypatch.setattr(remote, 'wait_for_capacity', lambda cap_, hosted_: counted.append((cap_, hosted_)))
    dispatched = []
    monkeypatch.setattr(remote, 'gh', lambda *args: dispatched.append(args) or '')
    url = 'https://example.test/runs/1'
    monkeypatch.setattr(remote, 'find_run', lambda run_id, deadline: {'databaseId': 1, 'url': url})
    monkeypatch.setattr(remote, 'wait', lambda found: 'success')
    # No artifact comes back, so run() reports no result without fetching anything.
    monkeypatch.setattr(remote.subprocess, 'run', lambda *args, **kwargs: SimpleNamespace(returncode=1))

    remote.run(['python3', '-m', 'pytest', '-q'], ref=sha, echo=False, hosted=hosted)

    assert counted == [(cap, hosted)]
    (argv,) = dispatched
    assert argv[:5] == ('workflow', 'run', 'remote-run.yml', '--ref', 'main')
    assert f'hosted={value}' in argv


def test_the_workflow_titles_hosted_runs_the_way_remote_py_counts_them():
    workflow = (remote.ROOT / '.github' / 'workflows' / 'remote-run.yml').read_text()
    assert f"'{remote.HOSTED_TITLE}'" in workflow

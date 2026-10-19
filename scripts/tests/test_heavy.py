"""scripts/heavy.sh runs heavy local commands in a few box-wide slots, at low priority.

Each job here is a short shell command that writes when it started and ended, so the overlap of jobs is read from
their own timestamps rather than guessed from wall time.
"""
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
HEAVY = str(ROOT / 'scripts/heavy.sh')

pytestmark = pytest.mark.skipif(not shutil.which('flock'), reason='flock not installed')


def env(tmp_path, **extra):
    out = {k: v for k, v in os.environ.items() if k not in ('CI', 'TICO_HEAVY_HELD')}
    return {**out, 'TICO_HEAVY_LOCK_DIR': str(tmp_path / 'locks'), 'TICO_HEAVY_SLOTS': '2', **extra}


def job(tmp_path, name, seconds, code=0, **extra):
    log = tmp_path / f'{name}.log'
    script = (f'echo "pid $$" > {log}; echo "start $(date +%s.%N)" >> {log}; sleep {seconds}; '
              f'echo "end $(date +%s.%N)" >> {log}; exit {code}')
    return subprocess.Popen([HEAVY, 'bash', '-c', script], env=env(tmp_path, **extra), stderr=subprocess.PIPE,
                            text=True)


def span(tmp_path, name):
    times = dict(line.split() for line in (tmp_path / f'{name}.log').read_text().splitlines())
    return float(times['start']), float(times['end'])


def stat(pid):
    try:
        return Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()
    except (FileNotFoundError, ProcessLookupError):
        return ['Z', 0, 0, 0]


def alive(pid):
    return stat(pid)[0] != 'Z'


def in_session(sid):
    """Live processes of a session: the wrapper gives its command a process group of its own inside it."""
    return [int(d.name) for d in Path('/proc').iterdir() if d.name.isdigit() and stat(d.name)[3] == str(sid)
            and alive(d.name)]


def gone(test, seconds=5):
    deadline = time.time() + seconds
    while test() and time.time() < deadline:
        time.sleep(0.1)
    return not test()


def stop(*jobs):
    for p in jobs:
        p.terminate()
    for p in jobs:
        p.wait(timeout=15)


def test_three_jobs_on_two_slots_run_two_at_a_time_and_the_third_starts_when_one_ends(tmp_path):
    first, second = job(tmp_path, 'a', 1.5, code=7), job(tmp_path, 'b', 1.5)
    time.sleep(0.5)
    third = job(tmp_path, 'c', 0.2, code=3)
    codes = [p.wait(timeout=20) for p in (first, second, third)]
    assert codes == [7, 0, 3]                           # each command's own exit code
    assert 'waiting for heavy-job slot (2 holders)' in third.stderr.read()
    (a0, a1), (b0, b1), (c0, c1) = (span(tmp_path, n) for n in 'abc')
    assert a0 < b1 and b0 < a1                          # the first two ran side by side
    assert c0 >= min(a1, b1) - 0.05                     # the third only once one of them had ended


def test_a_killed_job_lets_its_slot_go(tmp_path):
    holders = [job(tmp_path, f'h{n}', 30) for n in range(2)]
    time.sleep(0.5)
    waiting = job(tmp_path, 'w', 0)
    time.sleep(1)
    assert waiting.poll() is None                       # both slots held
    holders[0].send_signal(signal.SIGKILL)            # the wrapper only: its command is left running, unlocked
    assert waiting.wait(timeout=10) == 0
    os.killpg(int(dict(line.split() for line in (tmp_path / 'h0.log').read_text().splitlines())['pid']), signal.SIGKILL)
    stop(holders[1])
    timed = subprocess.run(['timeout', '1', HEAVY, 'sleep', '30'], env=env(tmp_path))
    assert timed.returncode == 124
    assert subprocess.run([HEAVY, 'true'], env=env(tmp_path, TICO_HEAVY_SLOTS='1'), timeout=10).returncode == 0


def test_a_stopped_job_ends_its_workers_before_its_slot_goes(tmp_path):
    workers = tmp_path / 'workers'
    coordinator = f'sleep 60 & echo $! >> {workers}; sleep 60 & echo $! >> {workers}; wait'
    held = subprocess.Popen([HEAVY, 'bash', '-c', coordinator], env=env(tmp_path, TICO_HEAVY_SLOTS='1'))
    assert gone(lambda: len(workers.read_text().split()) < 2 if workers.exists() else True)
    waiting = job(tmp_path, 'w', 0, TICO_HEAVY_SLOTS='1')
    time.sleep(1)
    assert waiting.poll() is None
    held.terminate()
    assert held.wait(timeout=15) == 128 + signal.SIGTERM
    assert not any(alive(int(pid)) for pid in workers.read_text().split())     # the workers, not just the coordinator
    assert waiting.wait(timeout=10) == 0


def test_the_command_runs_at_niceness_ten(tmp_path):
    before = os.nice(0)
    out = subprocess.run([HEAVY, 'sh', '-c', 'ps -o ni= -p $$'], env=env(tmp_path), capture_output=True, text=True)
    assert int(out.stdout) == min(before + 10, 19)


def test_without_a_working_lock_it_runs_unlocked_with_a_warning(tmp_path):
    tools = tmp_path / 'bin'
    tools.mkdir()
    for tool in ('bash', 'nice', 'sh', 'mkdir', 'chmod', 'sleep', 'echo'):
        if shutil.which(tool):
            (tools / tool).symlink_to(shutil.which(tool))
    out = subprocess.run(['/bin/bash', HEAVY, 'sh', '-c', 'echo ran; exit 5'], env=env(tmp_path, PATH=str(tools)),
                         capture_output=True, text=True)
    assert (out.returncode, out.stdout) == (5, 'ran\n')
    assert 'flock not found; running without a slot' in out.stderr
    # a flock that cannot lock this folder (exit 71, not 1 for a held lock) is not a holder: no endless wait
    (tools / 'flock').write_text('#!/bin/sh\necho "flock: 9: No locks available" >&2\nexit 71\n')
    (tools / 'flock').chmod(0o755)
    broken = env(tmp_path, PATH=f'{tools}{os.pathsep}{os.environ["PATH"]}')
    out = subprocess.run([HEAVY, 'sh', '-c', 'exit 5'], env=broken, capture_output=True, text=True, timeout=10)
    assert out.returncode == 5 and 'running without a slot' in out.stderr, out
    assert 'waiting for heavy-job slot' not in out.stderr
    (tmp_path / 'file').write_text('')
    out = subprocess.run([HEAVY, 'sh', '-c', 'exit 6'], env=env(tmp_path, TICO_HEAVY_LOCK_DIR=str(tmp_path / 'file/x')),
                         capture_output=True, text=True, timeout=10)
    assert out.returncode == 6 and 'running without a slot' in out.stderr, out


def test_hosted_ci_and_a_job_already_in_a_slot_run_as_they_are(tmp_path):
    holders = [job(tmp_path, f'h{n}', 30) for n in range(2)]
    time.sleep(0.5)
    try:
        for extra in ({'CI': 'true'}, {'TICO_HEAVY_HELD': '1'}):
            out = subprocess.run([HEAVY, 'sh', '-c', 'exit 4'], env=env(tmp_path, **extra), timeout=5)
            assert out.returncode == 4, extra           # did not wait for a slot
    finally:
        stop(*holders)


def entry(command, env, timeout=60):
    """Runs an entry point in its own session, so a guard that re-runs it forever is ended whole on timeout."""
    p = subprocess.Popen(command, cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                         start_new_session=True)
    try:
        out, err = p.communicate(timeout=timeout)
        return p.returncode, out, err
    except subprocess.TimeoutExpired:
        for pid in in_session(p.pid):
            os.kill(pid, signal.SIGKILL)
        return None, *p.communicate()


@pytest.mark.slow
@pytest.mark.parametrize('entry_point,code,says', [
    (['python', 'scripts/release_checks.py', '--help'], 0, 'usage:'),   # the release checks and release validation
    (['node', 'scripts/ui-tests.cjs', 'no-such-script'], 1, 'no UI test scripts matched'),     # the browser runner
    (['scripts/full-suite.sh', '--co', '-q', 'scripts/tests/test_heavy.py'], 0, 'test_heavy.py'),
], ids=['release_checks', 'ui-tests', 'full-suite'])
def test_the_heavy_entry_points_take_one_slot_wait_for_it_and_stop_whole(tmp_path, entry_point, code, says):
    command = [sys.executable if entry_point[0] == 'python' else shutil.which(entry_point[0]) or entry_point[0],
               *entry_point[1:]]
    if entry_point[0] == 'node' and not shutil.which('node'):
        pytest.skip('node not installed')
    local = env(tmp_path, TICO_PYTHON=sys.executable)
    for how, run_env in (('local', local), ('inside an outer one-slot job', {**local, 'TICO_HEAVY_SLOTS': '1'}),
                         ('hosted', {**local, 'CI': 'true'})):
        wrapped = [HEAVY, *command] if how.startswith('inside') else command
        got, out, err = entry(wrapped, run_env, timeout=30)
        assert (got, says in out + err, 'waiting for heavy-job slot' in err) == (code, True, False), (how, out, err)

    holders = [job(tmp_path, f'h{n}', 30) for n in range(2)]
    time.sleep(0.5)
    try:
        waiting = subprocess.Popen(command, cwd=ROOT, env=local, stderr=subprocess.PIPE, text=True,
                                   start_new_session=True)
        time.sleep(2)
        assert waiting.poll() is None                   # waiting for a slot
        waiting.terminate()                             # the entry point only, as a person's stop or a run's end does
        waiting.wait(timeout=15)
        assert gone(lambda: in_session(waiting.pid)), 'a stopped entry point left its wrapper waiting for a slot'
        assert 'waiting for heavy-job slot (2 holders)' in waiting.stderr.read()
    finally:
        stop(*holders)

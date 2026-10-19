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
    script = f'echo "start $(date +%s.%N)" > {log}; sleep {seconds}; echo "end $(date +%s.%N)" >> {log}; exit {code}'
    return subprocess.Popen([HEAVY, 'bash', '-c', script], env=env(tmp_path, **extra), stderr=subprocess.PIPE,
                            text=True)


def span(tmp_path, name):
    times = dict(line.split() for line in (tmp_path / f'{name}.log').read_text().splitlines())
    return float(times['start']), float(times['end'])


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
    holders[0].send_signal(signal.SIGKILL)
    assert waiting.wait(timeout=10) == 0
    holders[1].kill()
    timed = subprocess.run(['timeout', '1', HEAVY, 'sleep', '30'], env=env(tmp_path))
    assert timed.returncode == 124
    assert subprocess.run([HEAVY, 'true'], env=env(tmp_path, TICO_HEAVY_SLOTS='1'), timeout=10).returncode == 0


def test_the_command_runs_at_niceness_ten(tmp_path):
    before = os.nice(0)
    out = subprocess.run([HEAVY, 'sh', '-c', 'ps -o ni= -p $$'], env=env(tmp_path), capture_output=True, text=True)
    assert int(out.stdout) == min(before + 10, 19)


def test_without_flock_it_runs_unlocked_with_a_warning(tmp_path):
    tools = tmp_path / 'bin'
    tools.mkdir()
    for tool in ('bash', 'nice', 'sh', 'mkdir', 'chmod', 'sleep', 'echo'):
        if shutil.which(tool):
            (tools / tool).symlink_to(shutil.which(tool))
    out = subprocess.run(['/bin/bash', HEAVY, 'sh', '-c', 'echo ran; exit 5'], env=env(tmp_path, PATH=str(tools)),
                         capture_output=True, text=True)
    assert (out.returncode, out.stdout) == (5, 'ran\n')
    assert 'flock not found; running without a slot' in out.stderr


def test_hosted_ci_and_a_job_already_in_a_slot_run_as_they_are(tmp_path):
    holders = [job(tmp_path, f'h{n}', 30) for n in range(2)]
    time.sleep(0.5)
    try:
        for extra in ({'CI': 'true'}, {'TICO_HEAVY_HELD': '1'}):
            out = subprocess.run([HEAVY, 'sh', '-c', 'exit 4'], env=env(tmp_path, **extra), timeout=5)
            assert out.returncode == 4, extra           # did not wait for a slot
    finally:
        for p in holders:
            p.kill()


@pytest.mark.slow
@pytest.mark.parametrize('entry,code', [
    (['python', 'scripts/release_checks.py', '--help'], 0),     # the release checks and release validation
    (['node', 'scripts/ui-tests.cjs', 'no-such-script'], 1),    # the browser runner: "no UI test scripts matched"
    (['scripts/full-suite.sh', '--co', '-q', 'scripts/tests/test_heavy.py'], 0),
], ids=['release_checks', 'ui-tests', 'full-suite'])
def test_the_heavy_entry_points_wait_for_a_slot_and_hosted_ci_goes_straight_on(tmp_path, entry, code):
    command = [sys.executable if entry[0] == 'python' else shutil.which(entry[0]) or entry[0], *entry[1:]]
    if entry[0] == 'node' and not shutil.which('node'):
        pytest.skip('node not installed')
    holders = [job(tmp_path, f'h{n}', 30) for n in range(2)]
    time.sleep(0.5)
    try:
        held = subprocess.run(['timeout', '3', *command], cwd=ROOT, env=env(tmp_path, TICO_PYTHON=sys.executable),
                              capture_output=True, text=True)
        assert held.returncode == 124 and 'waiting for heavy-job slot (2 holders)' in held.stderr, held
        hosted = subprocess.run(command, cwd=ROOT, env=env(tmp_path, CI='true', TICO_PYTHON=sys.executable),
                                capture_output=True, text=True, timeout=60)
        assert (hosted.returncode, 'waiting for heavy-job slot' in hosted.stderr) == (code, False), hosted
    finally:
        for p in holders:
            p.kill()

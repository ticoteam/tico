"""scripts/journey_plan.py picks the journey for a release pair, and scripts/journey-test.sh runs the steps it names.

0.6 starts fresh: a 0.6 server refuses a 0.5 database with tasks, so the release gate's journey from a 0.5 release to a
0.6 candidate proves that refusal instead of an in-place upgrade (v0.6.0-rc.1 could not publish otherwise), and a pair
on one side of the line keeps the ordinary upgrade.
"""

import os
import subprocess
from pathlib import Path

import pytest

from scripts import journey_plan

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize(
    'previous, candidate, want',
    [
        ('v0.5.216', 'v0.6.0-rc.1', 'fresh-start'),
        ('v0.5.216', 'v0.6.0', 'fresh-start'),
        ('v0.5.216', 'v9.9.9', 'fresh-start'),  # a local build of a 0.6 checkout
        ('v0.5.215', 'v0.5.216', 'upgrade'),
        ('v0.6.0', 'v0.6.1', 'upgrade'),
        ('v0.6.0-rc.1', 'v0.6.0-rc.2', 'upgrade'),
    ],
)
def test_the_journey_for_a_release_pair(previous, candidate, want):
    assert journey_plan.journey(previous, candidate) == want


@pytest.mark.parametrize('previous, candidate', [('main', 'v0.6.0'), ('v0.5.216', ''), ('0.5.216', 'v0.6.0')])
def test_a_pair_that_is_not_two_release_tags_is_refused(previous, candidate):
    with pytest.raises(ValueError):
        journey_plan.journey(previous, candidate)
    assert journey_plan.main([previous, candidate]) == 2


def run_journey(tmp_path, previous):
    """The journey against a stand-in `docker` that fails every step: its plan and its step table, nothing run."""
    bin_dir = tmp_path / 'bin'
    bin_dir.mkdir()
    for name, body in (('docker', '[ "$1" = info ]'), ('sleep', 'true')):
        (bin_dir / name).write_text(f'#!/bin/sh\n{body}\n')
        (bin_dir / name).chmod(0o755)
    env = {
        **os.environ,
        'PATH': f'{bin_dir}:{os.environ["PATH"]}',
        'TICO_JOURNEY_RELEASES_URL': (tmp_path / 'no-releases').as_uri(),
    }
    env.pop('TICO_JOURNEY_CANDIDATE', None)
    result = subprocess.run(
        ['bash', 'scripts/journey-test.sh', '--release', '--previous', previous],
        cwd=ROOT,
        env={**env, 'TICO_JOURNEY_ROLLBACK': 'always'},
        capture_output=True,
        text=True,
        timeout=60,
    )
    table = result.stdout.split('\nSTEP')[-1].splitlines()[1:]
    return result, [line.rsplit(None, 2)[0].strip() for line in table if line.strip()]


def test_a_failed_step_says_why_above_the_step_table(tmp_path):
    """The release gate shows only the journey's last 40 lines, and the container logs printed after a failure push
    the step's own reason out of them (run 45032835765: "seed ... FAIL" with no reason). The summary repeats it."""
    result, steps = run_journey(tmp_path, 'v0.6.208')
    assert result.returncode == 1
    assert steps[0] == 'install v0.6.208 (bundle, auth none)'
    why = result.stdout.split('\nWhy it failed')[1].split('\nSTEP')[0]
    assert 'install v0.6.208 (bundle, auth none): could not download the v0.6.208 bundle' in why
    assert why in '\n'.join(result.stdout.splitlines()[-40:])


def test_a_reason_only_on_stderr_reaches_the_summary(tmp_path):
    """A step can fail on what Docker, curl or Python printed to stderr without a line of its own (a runner container
    that did not start, a response that was not JSON): the summary carries the last of the step's stderr too."""
    result, _ = run_journey(tmp_path, 'v0.6.208')
    why = result.stdout.split('\nWhy it failed')[1].split('\nSTEP')[0]
    assert "stderr: curl: (37) Couldn't open file" in why
    assert why.count('curl: (37)') == 1  # repeated lines once
    assert why in '\n'.join(result.stdout.splitlines()[-40:])

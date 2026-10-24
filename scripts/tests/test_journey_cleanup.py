"""scripts/journey-test.sh cleans up only the Docker containers and volumes its own run made.

A maintainer runs the journey on their own laptop, where a container or volume may already carry a name the journey
once used as a fixed name (journey-runner). The run here fails at its first step against a stand-in `docker` that
records what it is asked to do; the cleanup that follows must name only this run's resources.
"""
import os
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FIXED = {'tico-journey', 'journey-runner', 'journey-registry', 'journey-releases', 'tico-updater-swap-tico-journey'}


def test_cleanup_leaves_resources_this_run_did_not_make(tmp_path):
    bin_dir, log = tmp_path / 'bin', tmp_path / 'docker.log'
    bin_dir.mkdir()
    fake = bin_dir / 'docker'
    fake.write_text('#!/bin/sh\nprintf "%s\\n" "$*" >> "$DOCKER_LOG"\n[ "$1" = info ]\n')
    fake.chmod(0o755)
    env = {**os.environ, 'PATH': f'{bin_dir}:{os.environ["PATH"]}', 'DOCKER_LOG': str(log),
           'TICO_JOURNEY_RELEASES_URL': (tmp_path / 'no-releases').as_uri()}
    result = subprocess.run(['bash', 'scripts/journey-test.sh', '--previous', 'v0.0.1'], cwd=ROOT, env=env,
                            capture_output=True, text=True, timeout=60)
    assert result.returncode == 1 and 'FAIL' in result.stdout, result.stdout + result.stderr

    removed = []
    for line in log.read_text().splitlines():
        words = line.split()
        if words[:2] == ['rm', '-f']:
            removed += words[2:]
        elif words[:2] == ['volume', 'rm']:
            removed += words[2:]
        elif words[:2] == ['compose', '-p'] and 'down' in words:
            removed.append(words[2])
    assert removed and not FIXED & set(removed), removed
    suffixes = {re.search(r'-([0-9a-f]{8})$', name).group(1) for name in removed}
    assert len(suffixes) == 1, removed

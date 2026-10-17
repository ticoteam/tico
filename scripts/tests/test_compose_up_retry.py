"""docker/compose-up.sh: the release smoke retries a Docker network create that fails at once, and nothing else.

On 2026-10-17 the v0.3.53 candidate's Release validation failed only because `docker compose up` printed "Network
tico-rc-smoke_edge Error" after 1 s; the next run passed. That cost a whole validation run.
"""
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
NETWORK_FLAKE = (' Network tico-rc-smoke_edge  Creating\n Network tico-rc-smoke_edge  Error\n'
                 'failed to create network tico-rc-smoke_edge: Error response from daemon: context deadline exceeded')
OVERLAP = ('failed to create network tico-rc-smoke_edge: Error response from daemon: invalid pool request: '
           'Pool overlaps with other one on this address space')


def run(failures, output, tmp_path):
    """compose_up on a fake compose that fails `failures` times with `output`, then succeeds. Returns
    (exit code, the fake's calls, stderr). compose_up runs the fake in a subshell, so it counts in files."""
    script = '''
        set -euo pipefail
        . docker/compose-up.sh
        echo "$FAKE_FAILURES" > "$FAKE_DIR/left"
        fake() {
          echo "$*" >> "$FAKE_DIR/calls"
          left=$(cat "$FAKE_DIR/left")
          if [ "$1" = up ] && [ "$left" -gt 0 ]; then echo $((left - 1)) > "$FAKE_DIR/left"; echo "$FAKE_OUTPUT"; return 1; fi
          echo started
        }
        compose_up fake up -d > /dev/null
    '''
    tmp_path.mkdir()
    result = subprocess.run(['bash', '-c', script], cwd=ROOT, capture_output=True, text=True,
                            env={'PATH': '/usr/bin:/bin', 'FAKE_OUTPUT': output, 'FAKE_FAILURES': str(failures),
                                 'FAKE_DIR': str(tmp_path), 'TICO_COMPOSE_RETRY_SECONDS': '0'})
    return result.returncode, (tmp_path / 'calls').read_text().splitlines(), result.stderr


def test_a_failed_network_create_is_retried_after_a_down_at_most_twice(tmp_path):
    code, calls, stderr = run(1, NETWORK_FLAKE, tmp_path / 'once')
    assert code == 0, stderr
    assert calls == ['up -d', 'down', 'up -d']
    assert 'could not create a network (attempt 1 of 3)' in stderr
    code, calls, _ = run(5, NETWORK_FLAKE, tmp_path / 'always')
    assert code == 1 and calls == ['up -d', 'down', 'up -d', 'down', 'up -d']


def test_a_range_clash_or_any_other_failure_fails_at_once(tmp_path):
    for case, output in (('overlap', OVERLAP), ('pull', 'Error response from daemon: pull access denied for tico-rc')):
        code, calls, stderr = run(1, output, tmp_path / case)
        assert code == 1 and calls == ['up -d'] and output.splitlines()[-1] in stderr, case


def test_a_permanent_network_create_error_stops_after_the_two_retries(tmp_path):
    # Not a range clash, and no retry fixes it: the address pools are used up, or the daemon refuses. compose_up
    # cannot tell it from the 2026-10-17 flake, so it spends its two retries (seconds), then fails with the daemon's
    # own message last on stderr, never hanging and never passing.
    for case, error in (('pools', 'could not find an available, non-overlapping IPv4 address pool among the '
                                  'defaults to assign to the network'),
                        ('denied', 'permission denied while trying to connect to the Docker daemon socket')):
        output = f' Network tico-rc-smoke_edge  Error\nfailed to create network tico-rc-smoke_edge: {error}'
        code, calls, stderr = run(99, output, tmp_path / case)
        assert code == 1 and calls == ['up -d', 'down', 'up -d', 'down', 'up -d'], case
        assert stderr.rstrip().endswith(error) and 'attempt 2 of 3' in stderr and 'attempt 3' not in stderr, case


def test_smoke_starts_every_stack_through_it():
    smoke = (ROOT / 'docker' / 'smoke.sh').read_text().splitlines()
    assert '. docker/compose-up.sh' in [line.split('#')[0].strip() for line in smoke]
    # A plain `up` that creates networks would skip the retry; `--no-deps server` reuses the stack's networks.
    ups = [line for line in smoke if re.match(r'\s*(\S+=\S+ )*(compose_up )?(dc|rc)\b.* up\b', line)]
    assert ups, 'smoke.sh starts no stack'
    for line in ups:
        assert line.lstrip().startswith('compose_up ') or '--no-deps' in line, line

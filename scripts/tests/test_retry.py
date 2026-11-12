"""Missing release assets are an answer; storage throttling and network failures deserve another attempt."""

import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest
import yaml

from scripts import remote

ROOT = Path(__file__).resolve().parents[2]
BACKOFF = ['30', '60', '90', '120', '120', '120', '120']


@pytest.fixture
def downloads(tmp_path, monkeypatch):
    """Run the real retry wrapper with stand-in downloads and a sleep that only records its argument."""
    bin_dir = tmp_path / 'bin'
    bin_dir.mkdir()
    monkeypatch.setenv('PATH', f'{bin_dir}:{os.environ["PATH"]}')
    monkeypatch.setenv('RETRY_LOG', str(tmp_path / 'calls'))
    monkeypatch.setenv('SLEEP_LOG', str(tmp_path / 'sleeps'))
    monkeypatch.setenv('FAILURES', '100')
    monkeypatch.setenv('ERROR_STATUS', '1')
    monkeypatch.setenv('ERROR', 'HTTP 503: Egress is over the account limit')
    download = bin_dir / 'download'
    download.write_text(
        f'#!{sys.executable}\n'
        + '''
import os, sys
from pathlib import Path
args = sys.argv[1:]
if args[:2] == ['release', 'view']:
    print('\\n'.join(f'{name}-{platform}.gz' for name in ('tico-runner', 'tico-screen-relay')
                    for platform in ('macos-arm64', 'macos-x86_64')))
    sys.exit(0)
if args[:2] == ['run', 'list']:
    print('7')
    sys.exit(0)
log = Path(os.environ['RETRY_LOG'])
calls = log.read_text().splitlines() if log.exists() else []
with log.open('a') as out:
    out.write(' '.join(args) + '\\n')
print(os.environ.get('DOWNLOAD_STDOUT', ''))
into = None
if '-o' not in args:
    flag = '-D' if '-D' in args else '--dir'
    into = Path(args[args.index(flag) + 1])
    if (into / 'partial').exists():
        print('refusing to overwrite a partial download', file=sys.stderr)
        sys.exit(9)
if len(calls) < int(os.environ['FAILURES']):
    if into is not None:
        into.mkdir(parents=True, exist_ok=True)
        (into / 'partial').touch()
    print(os.environ['ERROR'], file=sys.stderr)
    sys.exit(int(os.environ['ERROR_STATUS']))
if '-o' in args:
    Path(args[args.index('-o') + 1]).write_text('downloaded')
else:
    into.mkdir(parents=True, exist_ok=True)
    for name in ('tico-runner', 'tico-screen-relay'):
        (into / f'{name}-SHA256SUMS').write_text('\\n'.join(
            'a' * 64 + f'  {name}-{platform}.gz' for platform in ('macos-arm64', 'macos-x86_64')) + '\\n')
    (into / 'exit-code').write_text('0')
    (into / 'output.log').write_text('remote output')
'''
    )
    download.chmod(0o755)
    for name in ('gh', 'curl'):
        (bin_dir / name).symlink_to(download)
    sleep = bin_dir / 'sleep'
    sleep.write_text('#!/bin/sh\nprintf "%s\\n" "$*" >> "$SLEEP_LOG"\n')
    sleep.chmod(0o755)
    return tmp_path


def sleeps(downloads):
    path = downloads / 'sleeps'
    return path.read_text().splitlines() if path.exists() else []


def shell(script):
    return subprocess.run(['bash', '-c', script], cwd=ROOT, capture_output=True, text=True, timeout=10)


@pytest.mark.parametrize(
    'error, failures, code, waits',
    [
        ('HTTP 404: Not Found', '100', 1, []),
        ('no assets match the file pattern', '100', 1, []),
        ('HTTP 503: Egress is over the account limit', '1', 0, ['30']),
    ],
)
def test_mac_probe_stops_on_missing_assets_and_retries_transient_errors(
    downloads, monkeypatch, error, failures, code, waits
):
    monkeypatch.setenv('ERROR', error)
    monkeypatch.setenv('FAILURES', failures)
    monkeypatch.setenv('GITHUB_REPOSITORY', 'acme/example')
    steps = yaml.safe_load((ROOT / '.github/workflows/release.yml').read_text())['jobs']['runner-changes']['steps']
    script = next(step['run'] for step in steps if step.get('id') == 'diff')
    probe = script[script.index('has_mac() {') : script.index('if [ "$PUBLISHED" = true ]')]
    result = shell(probe + '\nhas_mac v0.0.1')
    assert result.returncode == code and result.stderr == error + '\n', result
    assert len((downloads / 'calls').read_text().splitlines()) == len(waits) + 1
    assert sleeps(downloads) == waits


@pytest.mark.parametrize(
    'error, waits',
    [
        ('no artifact matches any of the names or patterns provided', []),
        ('HTTP 410: Gone', []),
        ('HTTP 503: Egress is over the account limit', ['30', '60']),
    ],
)
def test_optional_timings_fail_fast_or_use_a_short_retry_budget(downloads, monkeypatch, error, waits):
    monkeypatch.setenv('ERROR', error)
    plan = yaml.safe_load((ROOT / '.github/workflows/pr-checks.yml').read_text())['jobs']['plan']
    step = next(step for step in plan['steps'] if step.get('name', '').startswith('Restore measured timings'))
    # Also bound slow/hung commands, so optional timing restoration cannot consume an eleven-minute retry window.
    assert plan['timeout-minutes'] == 12 and step['timeout-minutes'] <= 2 and step['continue-on-error']
    result = shell(
        f'cd "{downloads}"\n' + step['run'].replace('bash scripts/ci/retry.sh', f'bash "{ROOT}/scripts/ci/retry.sh"')
    )
    assert result.returncode == 0 and 'No measured timings yet' in result.stdout, result
    assert sleeps(downloads) == waits
    assert result.stderr == (error + '\n') * (len(waits) + 1)


@pytest.mark.parametrize(
    'error, failures, expected',
    [
        ('no artifact matches any of the names or patterns provided', '100', None),
        ('HTTP 503: Egress is over the account limit', '1', 0),
    ],
)
def test_remote_reports_missing_results_and_recovers_from_transient_downloads(
    downloads, monkeypatch, capsys, error, failures, expected
):
    monkeypatch.setenv('ERROR', error)
    monkeypatch.setenv('FAILURES', failures)
    wrapper = downloads / 'scripts/ci/retry.sh'
    wrapper.parent.mkdir(parents=True)
    shutil.copyfile(ROOT / 'scripts/ci/retry.sh', wrapper)
    monkeypatch.setattr(remote, 'ROOT', downloads)
    monkeypatch.setattr(remote, 'git', lambda *args: 'pushed')
    monkeypatch.setattr(remote, 'gh', lambda *args, **kwargs: '')
    monkeypatch.setattr(remote, 'wait_for_capacity', lambda *args: True)  # the cap has its own tests
    monkeypatch.setattr(remote, 'find_run', lambda *args: {'databaseId': 7, 'url': 'https://acme.example/run/7'})
    monkeypatch.setattr(remote, 'wait', lambda *args: 'cancelled' if expected is None else 'success')
    code, logs = remote.run(['python', '-V'])
    assert code == expected
    assert sleeps(downloads) == ([] if expected is None else ['30'])
    if expected is None:
        assert 'no result (cancelled)' in capsys.readouterr().err
    else:
        assert (logs / 'output.log').read_text() == 'remote output'


@pytest.mark.parametrize(
    'error, waits',
    [
        ('curl: (22) The requested URL returned error: 404', []),
        ('curl: (22) The requested URL returned error: 503', BACKOFF),
    ],
)
def test_candidate_downloads_stop_on_404_but_keep_full_transient_retries(downloads, monkeypatch, error, waits):
    monkeypatch.setenv('ERROR', error)
    monkeypatch.setenv('ERROR_STATUS', '22')
    monkeypatch.setenv('WORK', str(downloads))
    monkeypatch.setenv('CAND', 'v0.0.2')
    monkeypatch.setenv('LOCAL', '0')
    monkeypatch.setenv('RELEASES', 'https://acme.example')
    source = (ROOT / 'scripts/journey-test.sh').read_text()
    start = source.index('release_bundle() {')
    result = shell(source[start : source.index('\n}', start) + 2] + '\nrelease_bundle v0.0.2')
    assert result.returncode == 1, result
    assert sleeps(downloads) == waits
    assert result.stderr == (error + '\n') * (len(waits) + 1)


def test_stderr_pattern_does_not_match_stdout_or_change_success(downloads, monkeypatch):
    monkeypatch.setenv('DOWNLOAD_STDOUT', 'HTTP 404: only text, not an error')
    monkeypatch.setenv('FAILURES', '1')
    # Clear a directory with spaces on each attempt, including leftover files from a failed download.
    target = downloads / 'partial download'
    target.mkdir()
    (target / 'stale').touch()
    result = shell(
        f'bash scripts/ci/retry.sh --clear "{target}" --permanent "HTTP 404" -- gh run download 7 -D "{target}"'
    )
    assert result.returncode == 0 and not (target / 'stale').exists(), result
    assert sleeps(downloads) == ['30']
    assert result.stderr == 'HTTP 503: Egress is over the account limit\n'

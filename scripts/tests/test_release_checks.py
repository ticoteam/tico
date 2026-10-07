"""Fast contract tests for default/release validation selection and receipts."""
import json
import os
from pathlib import Path
import shutil
import subprocess
from types import SimpleNamespace

import pytest

from scripts import release_checks


def git_repo(path: Path):
    path.mkdir()
    subprocess.run(['git', 'init', '-q', str(path)], check=True)
    (path / 'tracked.txt').write_text('candidate\n')
    subprocess.run(['git', '-C', str(path), 'add', 'tracked.txt'], check=True)
    subprocess.run(['git', '-C', str(path), '-c', 'user.name=Test', '-c', 'user.email=test@example.invalid',
                    'commit', '-qm', 'candidate'], check=True)
    return path


def receipt_payload(snapshot, identity, now=None, **changes):
    now = 1_800_000_000 if now is None else now
    commands = release_checks.default_commands()
    payload = {
        'schema': 1, 'kind': 'tico-default-validation', 'status': 'passed',
        'commit': snapshot['commit'], 'tree': snapshot['tree'], 'environment': identity,
        'commands': commands, 'selection': release_checks.DEFAULT_SELECTION,
        'started_at': now - 12, 'finished_at': now - 2, 'elapsed_seconds': 10.0,
        'load_start': '1.00,1.10,1.20', 'load_end': '1.10,1.20,1.30',
        'checks': [{'command': command, 'exit_code': 0, 'seconds': 4.0} for command in commands],
    }
    payload.update(changes)
    return payload


def test_default_receipt_is_signed_bound_recent_and_exact(tmp_path, monkeypatch):
    root = git_repo(tmp_path / 'candidate')
    monkeypatch.setattr(release_checks, 'ROOT', root)
    snapshot = release_checks.git_snapshot()
    identity = 'env-digest'
    now = 1_800_000_000

    payload = receipt_payload(snapshot, identity, now)
    assert release_checks._write_signed_receipt(payload)
    assert release_checks.valid_default_receipt(snapshot, identity, now=now) == payload

    assert release_checks.valid_default_receipt({**snapshot, 'commit': 'f' * 40}, identity, now=now) is None
    assert release_checks.valid_default_receipt({**snapshot, 'tree': 'e' * 40}, identity, now=now) is None
    assert release_checks.valid_default_receipt({**snapshot, 'clean': False}, identity, now=now) is None
    assert release_checks.valid_default_receipt(snapshot, 'different-environment', now=now) is None

    invalid_receipts = (
        {'status': 'failed'},
        {'selection': {'python': 'all'}},
        {'commands': [['pytest', '-q']]},
        {'checks': [{'command': command, 'exit_code': 1, 'seconds': 4.0}
                    for command in release_checks.default_commands()]},
    )
    for change in invalid_receipts:
        assert release_checks._write_signed_receipt(receipt_payload(snapshot, identity, now, **change))
        assert release_checks.valid_default_receipt(snapshot, identity, now=now) is None
    assert release_checks._write_signed_receipt(receipt_payload(
        snapshot, identity, now, finished_at=now - release_checks.DEFAULT_RECEIPT_MAX_AGE_SECONDS - 1,
        started_at=now - release_checks.DEFAULT_RECEIPT_MAX_AGE_SECONDS - 11,
    ))
    assert release_checks.valid_default_receipt(snapshot, identity, now=now) is None


def test_unrelated_json_and_unsigned_or_failed_runs_cannot_authorize_reuse(tmp_path, monkeypatch):
    root = git_repo(tmp_path / 'candidate')
    monkeypatch.setattr(release_checks, 'ROOT', root)
    snapshot = release_checks.git_snapshot()
    identity = 'env-digest'
    now = 1_800_000_000
    outside = tmp_path / 'passed.json'
    outside.write_text(json.dumps(receipt_payload(snapshot, identity, now)))
    assert release_checks.valid_default_receipt(snapshot, identity, now=now) is None

    directory, receipt, key = release_checks.receipt_paths()
    directory.mkdir(parents=True)
    receipt.write_text(outside.read_text())
    assert not key.exists()
    assert release_checks.valid_default_receipt(snapshot, identity, now=now) is None


def test_failed_default_run_invalidates_an_older_pass(tmp_path, monkeypatch):
    root = git_repo(tmp_path / 'candidate')
    monkeypatch.setattr(release_checks, 'ROOT', root)
    snapshot = {'commit': 'a' * 40, 'tree': 'b' * 40, 'clean': True}
    monkeypatch.setattr(release_checks, 'git_snapshot', lambda: snapshot)
    monkeypatch.setattr(release_checks, 'environment_identity', lambda: 'test-env')
    monkeypatch.setattr(release_checks, 'load', lambda: '0.00,0.00,0.00')
    _, receipt, _ = release_checks.receipt_paths()
    assert release_checks._write_signed_receipt(receipt_payload(snapshot, 'test-env'))

    real_run = subprocess.run
    calls = []

    def run(command, **kwargs):
        if command[:3] == ['git', 'rev-parse', '--absolute-git-dir']:
            return real_run(command, **kwargs)
        calls.append(command)
        return subprocess.CompletedProcess(command, 1, '', 'synthetic failure')

    monkeypatch.setattr(release_checks.subprocess, 'run', run)
    assert release_checks.full() == 1
    assert len(calls) == 1
    assert not receipt.exists()


def test_release_falls_back_to_one_default_run_without_a_valid_receipt(monkeypatch):
    calls = []
    monkeypatch.setattr(release_checks, 'valid_default_receipt', lambda: None)
    monkeypatch.setattr(release_checks, 'full', lambda: calls.append('full') or 0)
    assert release_checks.ensure_default_validation() == 0
    assert calls == ['full']


def test_environment_digest_binds_test_selection_inputs(monkeypatch):
    monkeypatch.setattr(release_checks.shutil, 'which', lambda name: None)
    monkeypatch.setattr(release_checks, '_run_identity_command', lambda *args, **kwargs: 'fixed-runtime')
    monkeypatch.delenv('TASKS_ONLY', raising=False)
    monkeypatch.delenv('TICO_TAG_TEST_ONLY', raising=False)
    complete = release_checks.environment_identity()
    monkeypatch.setenv('TASKS_ONLY', 'privacy')
    partial = release_checks.environment_identity()
    assert partial != complete


def test_release_refuses_dirty_candidate_before_validation(monkeypatch):
    monkeypatch.setattr(release_checks, 'load', lambda: '0.00,0.00,0.00')
    monkeypatch.setattr(release_checks, 'git_snapshot', lambda: {'commit': 'a' * 40, 'tree': 'b' * 40, 'clean': False})
    monkeypatch.setattr(release_checks, 'ensure_default_validation',
                        lambda: pytest.fail('dirty candidate must not reuse or run validation'))
    assert release_checks.release(SimpleNamespace(previous=None)) == 1


def test_release_aborts_if_candidate_changes_after_default_gate(monkeypatch):
    snapshots = iter(({'commit': 'a' * 40, 'tree': 'b' * 40, 'clean': True},
                      {'commit': 'c' * 40, 'tree': 'd' * 40, 'clean': True}))
    monkeypatch.setattr(release_checks, 'load', lambda: '0.00,0.00,0.00')
    monkeypatch.setattr(release_checks, 'git_snapshot', lambda: next(snapshots))
    monkeypatch.setattr(release_checks, 'environment_identity', lambda: 'synthetic-env')
    calls = []
    monkeypatch.setattr(release_checks, 'ensure_default_validation', lambda: calls.append('validated') or 0)
    assert release_checks.release(SimpleNamespace(previous=None)) == 1
    assert calls == ['validated']


def test_ui_core_and_non_core_are_a_disjoint_partition_of_all_scripts():
    node = shutil.which('node')
    if not node:
        pytest.skip('Node.js is unavailable')
    root = release_checks.ROOT

    def selected(*args):
        result = subprocess.run([node, 'scripts/ui-tests.cjs', '--list', *args], cwd=root,
                                capture_output=True, text=True, check=True)
        return json.loads(result.stdout)

    default = selected()
    core = selected('--core')
    non_core = selected('--non-core')
    all_scripts = selected('--all')
    assert default == core
    assert len(core) == 11
    assert set(core).isdisjoint(non_core)
    assert len(all_scripts) == len(core) + len(non_core)
    assert set(all_scripts) == set(core) | set(non_core)
    assert len(all_scripts) == len(set(all_scripts))
    conflict = subprocess.run([node, 'scripts/ui-tests.cjs', '--list', '--core', '--all'], cwd=root,
                              capture_output=True, text=True)
    assert conflict.returncode == 2

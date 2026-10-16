"""Hosted validation must not reclaim local data or accept a stale native binary."""
from pathlib import Path

import pytest

from scripts import hosted_release_check as check


def test_inputs_and_host_are_checked_before_any_reclamation(tmp_path, monkeypatch):
    assert check.inputs({'CANDIDATE_SHA': 'a' * 40, 'PREVIOUS_RELEASE': 'v0.3.48'}) == ('a' * 40, 'v0.3.48')
    for sha, previous in [('main', 'v0.3.48'), ('a' * 40, 'v0.3.48;touch unwanted')]:
        with pytest.raises(RuntimeError):
            check.inputs({'CANDIDATE_SHA': sha, 'PREVIOUS_RELEASE': previous})
    monkeypatch.setattr(check.subprocess, 'run', lambda *a, **kw: pytest.fail('must not remove anything on a local host'))
    with pytest.raises(RuntimeError, match='never run on a user or bot host'):
        check.capacity(tmp_path, 30 * check.GIB, {'GITHUB_ACTIONS': 'true', 'TICO_HOSTED_ENVIRONMENT': 'self-hosted'})


def test_source_bytes_must_match_the_native_build_id(tmp_path, monkeypatch):
    for kind in ('runner-rs', 'relay-rs'):
        (tmp_path / kind / 'src').mkdir(parents=True)
        for name in ('Cargo.toml', 'Cargo.lock', 'build.rs', 'src/main.rs'):
            (tmp_path / kind / name).write_text(name)
        binary = tmp_path / kind / 'target/release' / ('tico-runner' if kind == 'runner-rs' else 'tico-screen-relay')
        binary.parent.mkdir(parents=True)
        binary.write_bytes(b'fictional binary')
    (tmp_path / 'runner/harnesses').mkdir(parents=True)
    ids = {kind: check.fingerprint(tmp_path, kind) for kind in ('runner', 'relay')}
    def ask(args, cwd=None):
        kind = 'runner' if Path(args[0]).name == 'tico-runner' else 'relay'
        return ids[kind] if args[-1] == '--build-id' else 'tico-runner 0.3.49'
    monkeypatch.setattr(check, 'output', ask)
    assert check.native_binaries(tmp_path)['relay']['build_id'] == ids['relay']
    (tmp_path / 'relay-rs/src/main.rs').write_text('changed source')
    with pytest.raises(RuntimeError, match='relay native binary does not match'):
        check.native_binaries(tmp_path)


def test_artifacts_keep_only_bounded_owned_log_tails(tmp_path):
    report = tmp_path / 'report'
    report.mkdir()
    logs = tmp_path / 'tico-release-gate/tico-release-check-owned'
    logs.mkdir(parents=True)
    (logs / 'phase.log').write_bytes(b'a' * (check.LOG_LIMIT + 123))
    private = tmp_path / 'private.log'
    private.write_text('must not collect')
    (logs / 'linked.log').symlink_to(private)
    (logs.parent / 'tico-release-check-linked').symlink_to(tmp_path, target_is_directory=True)
    check.collect(tmp_path, report)
    assert [p.name for p in report.iterdir()] == ['tico-release-check-owned-phase.log']
    assert next(report.iterdir()).stat().st_size == check.LOG_LIMIT
    gate = report / 'gate.log'
    with gate.open('w+b') as stream:
        check.log_line(stream, 'b' * (check.LOG_LIMIT + 123))
    assert gate.stat().st_size <= check.LOG_LIMIT

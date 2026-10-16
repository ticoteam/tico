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


def receipts(split=True, changes=()):
    parts = []
    for part, shard in check.PARTS:
        receipt = {'part': part, 'shard': shard, 'success': True, 'candidate_sha': 'a' * 40, 'source_tree': 't',
                   'gate_exit': 0, 'plan': {'split': split}}
        if split and part == 'python':
            index = int(shard.split('/')[0])
            receipt['python_shard'] = {'index': index, 'count': check.PYTHON_SHARDS, 'collected': 10,
                                       'selected': 3 if index < 3 else 2, 'collection_sha256': 'h'}
        parts.append(receipt)
    for (part, shard), change in changes:
        next(r for r in parts if (r['part'], r['shard']) == (part, shard)).update(change)
    return parts


def test_verify_needs_every_part_green_and_the_shards_to_cover_one_collection():
    assert check.verify(receipts()) == {'split': True, 'python_tests': 10}
    assert check.verify(receipts(split=False)) == {'split': False}
    bad = [receipts()[:-1], receipts() + receipts()[-1:],
           receipts(changes=[(('browser', None), {'success': False})]),
           receipts(changes=[(('docker', None), {'source_tree': 'other'})])]
    for broken in ({'collection_sha256': 'other'}, {'selected': 1}, {'index': 1}):
        parts = receipts()
        parts[-1]['python_shard'].update(broken)
        bad.append(parts)
    parts = receipts()
    del parts[-1]['python_shard']
    bad.append(parts)
    parts = receipts(split=False)
    parts[0]['gate_exit'] = None
    bad.append(parts)
    for parts in bad:
        with pytest.raises(RuntimeError):
            check.verify(parts)


def test_only_a_splitting_candidate_runs_parts(tmp_path):
    (tmp_path / 'scripts').mkdir()
    gate = tmp_path / 'scripts/release_checks.py'
    gate.write_text("parser.add_argument('--part', choices=PARTS)")
    whole = {'split': False, 'gate': True, 'natives': True, 'browsers': True}
    assert check.plan(tmp_path, None, None) == whole   # no part named: the whole gate, as before the split
    gate.write_text("parser.add_argument('--release')")
    assert check.plan(tmp_path, 'docker', None) == {'split': False, 'gate': True, 'natives': True, 'browsers': True}
    assert check.plan(tmp_path, 'python', '2/4')['gate'] is False
    gate.write_text("parser.add_argument('--part', choices=PARTS)")
    assert check.plan(tmp_path, 'docker', None) == {'split': True, 'gate': True, 'natives': False, 'browsers': False}
    assert check.plan(tmp_path, 'python', '2/4') == {'split': True, 'gate': True, 'natives': True, 'browsers': True}
    with pytest.raises(RuntimeError):
        check.plan(tmp_path, 'python', '5/4')

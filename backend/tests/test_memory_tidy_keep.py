"""Disposable Git repositories and synthetic offers only; no model or live state."""
import json
import subprocess
from pathlib import Path

import pytest
from backend import memory_tidy as C
from backend.tests.test_memory_tidy import snapshot, proposal, run, TODAY
from runner.memory_tidy_keep import apply, candidate, Refused, Git


def raw(snap):
    result = run(snap, proposal(snap))
    assert result.status == 'offer'
    return result.edits_json


def test_candidate_preserves_day_logs_and_stale_targets():
    base = snapshot()
    current = {**base, C.LEARNINGS: base[C.LEARNINGS] + '\n## Log\n2026-11-01 A day’s new fact.\n'}
    files, skipped = candidate(base, current, raw(base), TODAY)
    assert not skipped
    assert 'A day’s new fact.' in files[C.LEARNINGS]
    assert not C.preservation_flags(current, {**current, **files})
    old = C.parse(base[C.LEARNINGS]).rules[0].text
    # A changed target becomes an explicit skipped edit. It is never reconstructed.
    current[C.LEARNINGS] = current[C.LEARNINGS].replace(old, '')
    files, skipped = candidate(base, current, raw(base), TODAY)
    assert skipped == [1] and files == {}


def test_duplicate_anchor_skips_and_refuses_unchecked_rule_changes():
    base = snapshot()
    old = C.parse(base[C.LEARNINGS]).rules[0].text
    current = {**base, C.LEARNINGS: base[C.LEARNINGS] + old}
    files, skipped = candidate(base, current, raw(base), TODAY)
    assert files == {} and skipped == [1]
    current = {**base, C.LEARNINGS: base[C.LEARNINGS] + '- New instruction never semantically checked.\n'}
    with pytest.raises(Refused, match='9:'):
        candidate(base, current, raw(base), TODAY)
    with pytest.raises(Refused, match='Instructions changed'):
        candidate(base, {**base, 'AGENT.md': 'Changed instructions'}, raw(base), TODAY)


def test_partial_skip_keeps_original_numbers_in_skip_list():
    base = snapshot()
    edits = []
    for n, entry in enumerate(C.parse(base[C.LEARNINGS]).rules[:2], 1):
        edits.append({'number': n, 'operation': 'retire', 'targets': [{'entry_id': entry.entry_id, 'old_text': entry.text}],
                      'replacement': '', 'sources': [entry.entry_id]})
    current = {**base, C.LEARNINGS: base[C.LEARNINGS].replace(edits[0]['targets'][0]['old_text'], '')}
    files, skipped = candidate(base, current, json.dumps({'edits': edits, 'diary': 'Two edits'}), TODAY)
    assert skipped == [1]
    assert edits[1]['targets'][0]['old_text'] in files[C.ARCHIVE]
    assert not C.preservation_flags(current, {**current, **files})


def git(path, *args):
    return Git(path).text(*args)


@pytest.fixture
def repos(tmp_path):
    remote, local, writer = (tmp_path / n for n in ('remote.git', 'bot', 'writer'))
    remote.mkdir()
    git(remote, 'init', '--bare', '--quiet')
    subprocess.run(['git', 'clone', '-q', str(remote), str(local)], check=True, capture_output=True)
    git(local, 'checkout', '-b', 'main')
    snap = snapshot()
    for p, content in snap.items():
        file = local / p
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(content)
    git(local, 'add', '.')
    git(local, 'commit', '-qm', 'Fixture')
    git(local, 'push', '-q', 'origin', 'main')
    git(remote, 'symbolic-ref', 'HEAD', 'refs/heads/main')
    subprocess.run(['git', 'clone', '-q', str(remote), str(writer)], check=True, capture_output=True)
    request = {'id': 'operation-one', 'card_id': 'card-one', 'memory_commit': git(local, 'rev-parse', 'HEAD'),
               'date': TODAY.isoformat(), 'diary': 'Checked diary.', 'edits_json': raw(snap)}
    return remote, local, writer, request


def write_day(writer, n):
    path = writer / C.LEARNINGS
    path.write_text(path.read_text() + f'\n## Log\n2026-11-01 Day write {n}.\n')
    git(writer, 'add', '.')
    git(writer, 'commit', '-qm', f'Day {n}')
    git(writer, 'push', '-q', 'origin', 'main')


def test_keep_one_commit_checkout_untouched_replay_and_next_rules(repos):
    remote, local, writer, request = repos
    before = git(local, 'rev-parse', 'HEAD'), (local / C.LEARNINGS).read_bytes(), git(local, 'status', '--porcelain')
    write_day(writer, 1)
    result = apply(local, request)
    assert result['status'] == 'kept'
    assert git(remote, 'rev-list', '--count', request['memory_commit'] + '..main') == '2'
    assert git(remote, 'show', '-s', '--format=%s', 'main') == 'Dreamed 2026-11-01'
    body = git(remote, 'show', '-s', '--format=%b', 'main')
    assert 'Checked diary.' in body and 'Skipped edits: none.' in body
    text = git(remote, 'show', 'main:' + C.LEARNINGS)
    assert 'Day write 1.' in text and 'General rule.' in text
    assert before == (git(local, 'rev-parse', 'HEAD'), (local / C.LEARNINGS).read_bytes(), git(local, 'status', '--porcelain'))
    (local / 'state.md').write_text('New uncommitted day write')
    assert apply(local, request) == result  # recovery precedes new dirty-checkout refusal
    git(local, 'checkout', '--', 'state.md')
    git(local, 'pull', '--ff-only', 'origin', 'main')
    assert 'General rule.' in (local / C.LEARNINGS).read_text()  # ordinary next-turn refresh


def test_rejected_push_reapplies_latest_and_preserves_day_write(repos):
    remote, local, writer, request = repos
    calls = []
    def racing(attempt):
        calls.append(attempt)
        if attempt == 1:
            write_day(writer, 1)
    result = apply(local, request, before_push=racing)
    assert result['status'] == 'kept' and calls == [1, 2]
    assert 'Day write 1.' in git(remote, 'show', 'main:' + C.LEARNINGS)


def test_exhausted_pushes_never_force_and_restart_cannot_retry_budget(repos, tmp_path):
    remote, local, writer, request = repos
    journal = tmp_path / 'attempts'
    with pytest.raises(Refused, match='three push attempts'):
        apply(local, request, before_push=lambda n: write_day(writer, n), journal=journal)
    assert journal.read_text() == '3'
    assert 'General rule.' not in git(remote, 'show', 'main:' + C.LEARNINGS)
    with pytest.raises(Refused, match='already used'):
        apply(local, request, journal=journal)
    assert git(remote, 'rev-list', '--count', request['memory_commit'] + '..main') == '3'


def test_moved_instructions_refuse_and_dirty_checkout_is_preserved(repos):
    remote, local, writer, request = repos
    (writer / 'AGENT.md').write_text('Changed instructions.\n')
    git(writer, 'add', '.')
    git(writer, 'commit', '-qm', 'New instructions')
    git(writer, 'push', '-q', 'origin', 'main')
    head = git(remote, 'rev-parse', 'main')
    with pytest.raises(Refused, match='Instructions changed'):
        apply(local, request)
    assert git(remote, 'rev-parse', 'main') == head
    (local / C.LEARNINGS).write_text('Uncommitted day write')
    with pytest.raises(Refused, match='uncommitted'):
        apply(local, request)
    assert (local / C.LEARNINGS).read_text() == 'Uncommitted day write'

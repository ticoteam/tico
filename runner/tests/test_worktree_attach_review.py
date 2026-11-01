"""Regressions for inspecting API-attached worktrees without losing readiness proof."""
import json

from runner import worktrees as W
from runner.tests.test_worktrees import git, trees  # noqa: F401


def _api_attached(row):
    row.update(state='pending', detail_json=json.dumps({
        'checkout_state': 'attached_pending', 'setup_pending': False,
    }))


def test_api_attachment_stays_ready_across_consecutive_heartbeats(trees):
    workspace, base, remote, row, client = trees
    path = workspace / row['path']
    path.parent.mkdir(parents=True)
    git(base, 'worktree', 'add', '--no-track', '-b', row['branch'], str(path), 'origin/main')
    _api_attached(row)

    first = W.inspect(workspace, row)
    assert first['state'] == 'present', first
    detail = json.loads(row['detail_json'])
    detail.update({key: value for key, value in first.items()
                   if key not in ('state', 'link_id', 'branch')})
    row.update(state=first['state'], detail_json=json.dumps(detail))

    second = W.inspect(workspace, row)
    assert second['state'] == 'present', second
    assert W._read_marker(workspace, row['id'])['phase'] == 'ready'
    assert (path / 'file').read_text() == 'initial'


def test_api_attachment_with_missing_tracked_file_stays_unverified(trees):
    workspace, base, remote, row, client = trees
    path = workspace / row['path']
    path.parent.mkdir(parents=True)
    git(base, 'worktree', 'add', '--no-track', '-b', row['branch'], str(path), 'origin/main')
    (path / 'file').unlink()
    _api_attached(row)

    report = W.inspect(workspace, row)

    assert report['state'] == 'pending', report
    assert W._read_marker(workspace, row['id']) is None
    assert not (path / 'file').exists()


def test_api_attachment_records_proof_without_discarding_dirty_work(trees):
    workspace, base, remote, row, client = trees
    path = workspace / row['path']
    path.parent.mkdir(parents=True)
    git(base, 'worktree', 'add', '--no-track', '-b', row['branch'], str(path), 'origin/main')
    (path / 'file').write_text('kept user edit')
    (path / 'notes.txt').write_text('kept untracked file')
    _api_attached(row)

    first = W.inspect(workspace, row)
    assert first['state'] == 'present', first
    assert first['dirty_files'] == 2
    assert (path / 'file').read_text() == 'kept user edit'
    assert (path / 'notes.txt').read_text() == 'kept untracked file'

    detail = json.loads(row['detail_json'])
    detail.update({key: value for key, value in first.items()
                   if key not in ('state', 'link_id', 'branch')})
    row.update(state=first['state'], detail_json=json.dumps(detail))
    second = W.inspect(workspace, row)
    assert second['state'] == 'present', second
    assert (path / 'file').read_text() == 'kept user edit'
    assert (path / 'notes.txt').read_text() == 'kept untracked file'

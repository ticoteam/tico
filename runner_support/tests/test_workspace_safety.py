"""Every bot workspace write checks its final destination before any setup or Git."""
from contextlib import nullcontext
from unittest import mock

import pytest

from clients import repo_safety
from runner_support import isolation, repositories, worktrees


@pytest.mark.parametrize('existing', [False, True], ids=['linked-parent', 'linked-destination'])
@pytest.mark.parametrize('operation', ['mkdir', 'base', 'prepare', 'restore', 'resume', 'locks', 'marker', 'git', 'scratch'])
def test_workspace_destinations_refuse_the_install_before_writing(tmp_path, monkeypatch, operation, existing):
    install = tmp_path / 'install'
    target = install / 'templates'
    target.mkdir(parents=True)
    (target / 'keep').write_text('keep')
    workspace = tmp_path / 'workspace'
    workspace.mkdir()
    monkeypatch.setattr(repo_safety, 'ROOT', install)
    relative = 'tasks/task/product'
    if operation == 'base':
        link = workspace / ('repos/acme__product' if existing else 'repos')
    elif operation in ('prepare', 'restore', 'resume'):
        link = workspace / (relative if existing else 'tasks')
    elif operation == 'locks':
        link = workspace / ('tasks/.locks' if existing else 'tasks')
    elif operation == 'marker':
        link = workspace / ('tasks/.checkout-state' if existing else 'tasks')
    else:
        link = workspace / 'scratch'
    link.parent.mkdir(parents=True, exist_ok=True)
    link.symlink_to(target, target_is_directory=True)
    path = link if existing else link / 'new'
    row = {'id': 'link', 'task_id': 'task', 'path': relative, 'branch': 'tico/task', 'repo': 'acme/product'}
    with mock.patch.object(isolation, 'run', side_effect=AssertionError('Git must not start')):
        with pytest.raises(ValueError, match='^Repository path is inside the Tico install:'):
            context = nullcontext()
            if operation == 'mkdir':
                isolation.mkdir(path)
            elif operation == 'base':
                repositories.worktree_base(workspace, {'full_name': 'acme/product'}, {})
            elif operation == 'prepare':
                worktrees.prepare(workspace, row, {}, {}, mock.Mock())
            elif operation == 'restore':
                worktrees.act(workspace, row, 'restore', {})
            elif operation == 'resume':
                worktrees._create_or_resume_checkout(workspace / relative, workspace, workspace / 'repos/base',
                                                     'main', {}, {}, 'task', {}, mock.Mock())
            elif operation == 'locks':
                context = worktrees.locked(workspace, 'task')
            elif operation == 'marker':
                worktrees._marker_path(workspace, 'link', create=True)
            elif operation == 'git':
                worktrees.git(path, 'status')
            elif operation == 'scratch':
                context = worktrees._merge_scratch(path)
            with context:
                pytest.fail('Unsafe workspace accepted')
    assert sorted(p.name for p in target.iterdir()) == ['keep']
    assert (target / 'keep').read_text() == 'keep'
    if operation in ('prepare', 'restore', 'resume'):
        assert not (workspace / 'tasks/.locks').exists()


def test_safe_scratch_is_created_and_removed(tmp_path):
    with worktrees._merge_scratch(tmp_path) as scratch:
        (scratch / 'file').write_text('scratch')
        assert scratch.is_dir()
    assert not scratch.exists()

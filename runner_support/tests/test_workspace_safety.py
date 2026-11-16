"""Every bot workspace write checks its final destination before any setup or Git."""

from contextlib import nullcontext
from unittest import mock

import pytest

from clients import repo_safety
from runner_support import isolation, repositories, worktrees


@pytest.mark.parametrize('existing', [False, True], ids=['linked-parent', 'linked-destination'])
@pytest.mark.parametrize('operation', ['mkdir', 'base', 'resume', 'locks', 'marker', 'git'])
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
    elif operation == 'resume':
        link = workspace / (relative if existing else 'tasks')
    elif operation == 'locks':
        link = workspace / ('tasks/.locks' if existing else 'tasks')
    else:
        link = workspace / ('tasks/.checkout-state' if existing else 'tasks')
    link.parent.mkdir(parents=True, exist_ok=True)
    link.symlink_to(target, target_is_directory=True)
    path = link if existing else link / 'new'
    with mock.patch.object(isolation, 'run', side_effect=AssertionError('Git must not start')):
        with pytest.raises(ValueError, match='^Repository path is inside the Tico install:'):
            context = nullcontext()
            if operation == 'mkdir':
                isolation.mkdir(path)
            elif operation == 'base':
                repositories.worktree_base(workspace, {'full_name': 'acme/product'}, {})
            elif operation == 'resume':
                worktrees._create_or_resume_checkout(
                    workspace / relative, workspace, workspace / 'repos/base', 'main', {}, {}, 'task', {}, mock.Mock()
                )
            elif operation == 'locks':
                context = worktrees.locked(workspace, 'task')
            elif operation == 'marker':
                worktrees._marker_path(workspace, 'link', create=True)
            elif operation == 'git':
                worktrees.git(path, 'status')
            with context:
                pytest.fail('Unsafe workspace accepted')
    assert sorted(p.name for p in target.iterdir()) == ['keep']
    assert (target / 'keep').read_text() == 'keep'
    if operation == 'resume':
        assert not (workspace / 'tasks/.locks').exists()

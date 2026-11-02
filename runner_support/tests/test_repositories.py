"""A turn's base clone (repositories.worktree_base), without a GitHub account or running server."""
import os
from pathlib import Path
import subprocess
from unittest import mock

import pytest


def test_isolated_state_exposes_only_mirrors(tmp_path, monkeypatch):
    import os
    from runner_support.state import State
    directory = tmp_path / 'state'
    directory.mkdir()
    private = directory / 'previous.json'
    private.write_text('private state')
    monkeypatch.setattr('runner_support.isolation.enabled', lambda: True)
    old_umask = os.umask(0o022)
    try:
        state = State(directory)
        assert directory.stat().st_mode & 0o777 == 0o711
        assert private.stat().st_mode & 0o777 == 0o600
        assert state.path.stat().st_mode & 0o777 == 0o600
    finally:
        os.umask(old_umask)


# ---------------------------------------------------------------- a base clone made in a turn
TURN_TOKEN = 'ghs_' + 'T' * 36


def failing_clone(stderr='', returncode=128, raise_timeout=False, leave='partial'):
    """isolation.run for worktree_base: the clone leaves a half-made folder and fails as GitHub would."""
    from runner_support import repositories as R
    def run(args, **kwargs):
        if 'clone' in args:
            path = Path(args[-1])
            if leave == 'partial':
                (path / '.git' / 'objects').mkdir(parents=True)
                (path / '.git' / 'objects' / 'pack-partial').write_text('half a pack')
            if raise_timeout:
                raise subprocess.TimeoutExpired(args, R.CLONE_TIMEOUT)
            return subprocess.CompletedProcess(args, returncode, '', stderr)
        return subprocess.CompletedProcess(args, 0, '', '')
    return run


@pytest.mark.parametrize('stderr,wanted', [
    (f"Cloning into 'x'...\nremote: Repository not found.\nfatal: repository 'https://x-access-token:{TURN_TOKEN}@github.com/acme/web.git/' not found\n",
     'remote: Repository not found.'),
    ("error: unable to create file app/" + 'a' * 30 + ".tsx: Filename too long\nfatal: unable to checkout working tree\n"
     "warning: Clone succeeded, but checkout failed.\n", 'fatal: unable to checkout working tree'),
    ('error: RPC failed; HTTP 500 curl 22 The requested URL returned error: 500\nfatal: expected flush after ref listing\n',
     'RPC failed; HTTP 500'),
])
def test_turn_clone_failure_names_gits_reason_and_removes_the_partial_clone(tmp_path, stderr, wanted):
    from runner_support import repositories as R
    with mock.patch('runner_support.repositories.isolation.run', side_effect=failing_clone(stderr)):
        with pytest.raises(ValueError) as caught:
            R.worktree_base(tmp_path, {'full_name': 'acme/web'}, {'GH_TOKEN': TURN_TOKEN, 'PATH': os.environ['PATH']})
    message = str(caught.value)
    assert message.startswith('Git clone of acme/web failed: ') and wanted in message
    assert TURN_TOKEN not in message and 'check repository access, network and disk space' not in message
    assert not (tmp_path / 'repos' / R.base_folder('acme/web')).exists()     # the next try starts clean


def test_turn_clone_timeout_says_so_and_removes_the_partial_clone(tmp_path):
    from runner_support import repositories as R
    with mock.patch('runner_support.repositories.isolation.run', side_effect=failing_clone(raise_timeout=True)):
        with pytest.raises(ValueError, match=r'Git clone of acme/web timed out after 120 seconds'):
            R.worktree_base(tmp_path, {'full_name': 'acme/web'}, {'PATH': os.environ['PATH']})
    assert not (tmp_path / 'repos' / R.base_folder('acme/web')).exists()


def test_turn_clone_never_removes_a_folder_it_did_not_make(tmp_path):
    from runner_support import repositories as R
    target = tmp_path / 'repos' / R.base_folder('acme/web')
    def appeared(args, **kwargs):
        if 'clone' in args:
            target.mkdir(parents=True)
            (target / 'someone-elses-work').write_text('keep')
            return subprocess.CompletedProcess(args, 128, '', f"fatal: destination path '{target}' already exists and is not an empty directory.\n")
        return subprocess.CompletedProcess(args, 0, '', '')
    with mock.patch('runner_support.repositories.isolation.run', side_effect=appeared):
        with pytest.raises(ValueError, match='already exists'):
            R.worktree_base(tmp_path, {'full_name': 'acme/web'}, {'PATH': os.environ['PATH']})
    assert (target / 'someone-elses-work').read_text() == 'keep'


def test_partial_clone_with_a_registered_worktree_is_kept(tmp_path):
    from runner_support.repositories import discard_failed_clone
    path = tmp_path / 'base'
    (path / '.git' / 'worktrees' / 'task').mkdir(parents=True)
    with mock.patch('runner_support.repositories.base_kept', side_effect=ValueError('cannot prune')):
        discard_failed_clone(path, fresh=True)
    assert path.exists()


# ---------------------------------------------------------------- a computer base clone that fails


def test_turn_clone_falls_back_to_github_when_the_mirror_cannot_be_copied(tmp_path):
    from runner_support import repositories as R
    clones = []
    def run(args, **kwargs):
        if 'clone' in args:
            clones.append(args)
            path = Path(args[-1])
            (path / '.git').mkdir(parents=True)
            if 'tico-mirror' in args:
                return subprocess.CompletedProcess(args, 128, '', 'error: unable to read 0123abcd\nfatal: remote did not send all necessary objects\n')
            assert args[-2] == 'https://github.com/acme/web.git'
        return subprocess.CompletedProcess(args, 0, '', '')
    repo = {'full_name': 'acme/web', '_mirror_refresh': {'mirror': (tmp_path / 'mirror.git').as_uri(), 'default_branch': 'main'}}
    with mock.patch('runner_support.repositories.isolation.run', side_effect=run):
        path, branch = R.worktree_base(tmp_path, repo, {'PATH': os.environ['PATH']})
    assert branch == 'main' and len(clones) == 2 and path.exists()
    assert "Could not copy this computer's mirror of acme/web; cloned from GitHub instead" == repo['fetch_warning']


def test_turn_clone_reports_both_reasons_when_mirror_and_github_fail(tmp_path):
    from runner_support import repositories as R
    def run(args, **kwargs):
        if 'clone' in args:
            if 'tico-mirror' in args:
                return subprocess.CompletedProcess(args, 128, '', 'fatal: remote did not send all necessary objects\n')
            return subprocess.CompletedProcess(args, 128, '', 'remote: Repository not found.\nfatal: repository not found\n')
        return subprocess.CompletedProcess(args, 0, '', '')
    repo = {'full_name': 'acme/web', '_mirror_refresh': {'mirror': (tmp_path / 'mirror.git').as_uri(), 'default_branch': 'main'}}
    with mock.patch('runner_support.repositories.isolation.run', side_effect=run):
        with pytest.raises(ValueError) as caught:
            R.worktree_base(tmp_path, repo, {'PATH': os.environ['PATH']})
    assert str(caught.value) == ('Git clone of acme/web failed: fatal: remote did not send all necessary objects; cloning from '
                                 'GitHub instead also failed: remote: Repository not found. / fatal: repository not found')


# ---------------------------------------------------------------- a clone a person already made (a Mac)



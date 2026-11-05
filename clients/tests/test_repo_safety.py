"""Repository identity checks use real Git metadata, including an installation that is itself a worktree."""
import subprocess

import pytest

from clients import repo_safety


def git(root, *args):
    return subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True, text=True).stdout.strip()


@pytest.mark.slow
@pytest.mark.parametrize("linked_install", [False, True])
def test_git_metadata_aliases_of_the_install_are_refused(tmp_path, monkeypatch, linked_install):
    source = tmp_path / "source"
    source.mkdir()
    git(source, "init", "-q", "-b", "main")
    git(source, "-c", "user.name=Fixture", "-c", "user.email=fixture@acme.example", "commit", "--allow-empty", "-qm", "init")
    install = tmp_path / "install" if linked_install else source
    if linked_install:
        git(source, "worktree", "add", "--detach", str(install))
    sibling = tmp_path / "task"
    git(source, "worktree", "add", "--detach", str(sibling))
    alias = tmp_path / "alias"
    alias.mkdir()
    (alias / ".git").write_text("gitdir: " + git(install, "rev-parse", "--absolute-git-dir") + "\n")
    common = tmp_path / "common"
    common.mkdir()
    git(common, "init", "-q")
    (common / ".git/commondir").write_text(str(source / ".git") + "\n")
    monkeypatch.setattr(repo_safety, "ROOT", install)
    # A turn's overrides do not change which repository this destination actually uses.
    monkeypatch.setenv("GIT_DIR", str(tmp_path / "fake"))
    for path in (sibling, sibling / "new/deep", alias, common):
        with pytest.raises(ValueError, match="shares Git metadata"):
            repo_safety.outside_installation(path)
    separate = tmp_path / "separate"
    monkeypatch.delenv("GIT_DIR")
    git(tmp_path, "clone", "-q", str(source), str(separate))
    assert repo_safety.outside_installation(separate) == separate
    assert repo_safety.outside_installation(tmp_path / "new") == tmp_path / "new"

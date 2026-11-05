"""Repository identity checks use real Git metadata, including an installation that is itself a worktree."""
import os
import shlex
import shutil
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
    separate = tmp_path / "separate"
    git(tmp_path, "clone", "-q", str(source), str(separate))
    separate_link = tmp_path / "separate-link"
    git(separate, "worktree", "add", "--detach", str(separate_link))
    for repo in (install, sibling, alias, common, separate, separate_link):
        (repo / "playbooks").mkdir()
        (repo / ".state").mkdir()

    # Inject the ownership mode at Git's entry point, after the probe clears inherited Git overrides.
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    wrapper = bin_dir / "git"
    wrapper.write_text(f'#!/bin/sh\nGIT_TEST_ASSUME_DIFFERENT_OWNER=1 exec {shlex.quote(shutil.which("git"))} "$@"\n')
    wrapper.chmod(0o755)
    monkeypatch.setenv("PATH", str(bin_dir) + os.pathsep + os.environ["PATH"])
    refused = subprocess.run(["git", "-c", "safe.directory=", "-C", str(separate / "playbooks"),
                              "rev-parse", "--git-dir"], capture_output=True, text=True)
    assert refused.returncode != 0 and "dubious ownership" in refused.stderr
    monkeypatch.setattr(repo_safety, "ROOT", install)
    # A turn's overrides do not change which repository this destination actually uses.
    monkeypatch.setenv("GIT_DIR", str(tmp_path / "fake"))
    for repo in (sibling, alias, common):
        for path in (repo, repo / "playbooks", repo / ".state", repo / "new/deep"):
            with pytest.raises(ValueError, match="^Repository shares Git metadata"):
                repo_safety.outside_installation(path)
    for repo in (separate, separate_link):
        for path in (repo, repo / "playbooks", repo / ".state", repo / "new/deep"):
            assert repo_safety.outside_installation(path) == path
    assert repo_safety.outside_installation(tmp_path / "new") == tmp_path / "new"


def test_probe_failure_is_reported_without_claiming_install_metadata(tmp_path, monkeypatch):
    install = tmp_path / "install"
    install.mkdir()
    repo = tmp_path / "bot"
    (repo / ".git").mkdir(parents=True)
    (repo / "playbooks").mkdir()
    monkeypatch.setattr(repo_safety, "ROOT", install)
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a[0], 128, "", "probe failed"))
    with pytest.raises(ValueError, match="^Cannot check repository identity:"):
        repo_safety.outside_installation(repo / "playbooks")

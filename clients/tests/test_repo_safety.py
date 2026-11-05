"""Repository identity checks use real Git metadata, including an installation that is itself a worktree."""
import os
import shlex
import shutil
import subprocess
from pathlib import Path

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
    plain = tmp_path / "plain"
    plain.mkdir()
    assert repo_safety.outside_installation(plain) == plain
    broken = tmp_path / "broken"
    (broken / "playbooks").mkdir(parents=True)
    (broken / ".git").write_text("invalid gitdir\n")
    for path in (broken, broken / "playbooks", broken / "new/deep"):
        with pytest.raises(ValueError, match="^Cannot check repository identity"):
            repo_safety.outside_installation(path)
    (broken / ".git").write_text("gitdir: " + str(tmp_path / "missing") + "\n")
    for path in (broken, broken / "playbooks", broken / "new/deep"):
        assert repo_safety.outside_installation(path) == path


@pytest.mark.slow
@pytest.mark.parametrize("linked_install", [False, True])
def test_config_error_on_either_side_refuses_a_sibling(tmp_path, monkeypatch, linked_install):
    source = tmp_path / "source"
    source.mkdir()
    git(source, "init", "-q", "-b", "main")
    git(source, "-c", "user.name=Fixture", "-c", "user.email=fixture@acme.example", "commit", "--allow-empty", "-qm", "init")
    install = tmp_path / "install" if linked_install else source
    if linked_install:
        git(source, "worktree", "add", "--detach", str(install))
    sibling = tmp_path / "task"
    git(source, "worktree", "add", "--detach", str(sibling))
    git(source, "config", "extensions.worktreeConfig", "true")
    monkeypatch.setattr(repo_safety, "ROOT", install)
    for broken, healthy in ((install, sibling), (sibling, install)):
        config = Path(git(broken, "rev-parse", "--absolute-git-dir")) / "config.worktree"
        config.write_text("[invalid config\n")
        try:
            failed = subprocess.run(["git", "-C", str(broken), "rev-parse", "--git-dir"], capture_output=True, check=False)
            assert failed.returncode == 128
            assert git(healthy, "rev-parse", "--git-dir")
            with pytest.raises(ValueError, match="^Cannot check repository identity") as error:
                repo_safety.outside_installation(sibling)
            assert str(error.value).endswith(str(broken))
            if os.geteuid() != 0:
                config.write_text("")
                config.chmod(0o000)
                with pytest.raises(ValueError, match="^Cannot check repository identity") as error:
                    repo_safety.outside_installation(sibling)
                assert str(error.value).endswith(str(broken))
        finally:
            config.chmod(0o600)
            config.unlink()


@pytest.mark.parametrize("failure", ["exit", "permission", "timeout"])
def test_probe_failures_on_either_side_are_refused(tmp_path, monkeypatch, failure):
    install = tmp_path / "install"
    (install / ".git").mkdir(parents=True)
    repo = tmp_path / "bot"
    (repo / ".git").mkdir(parents=True)
    (repo / "playbooks").mkdir()
    monkeypatch.setattr(repo_safety, "ROOT", install)
    for broken in (install, repo):
        def probe(args, **kwargs):
            root = Path(args[args.index("-C") + 1])
            if root == broken:
                if failure == "permission":
                    raise PermissionError("config is unreadable")
                if failure == "timeout":
                    raise subprocess.TimeoutExpired(args, 10)
                return subprocess.CompletedProcess(args, 128, "", "bad config")
            return subprocess.CompletedProcess(args, 0, f"{root / '.git'}\n{root / '.git'}\n", "")
        monkeypatch.setattr(subprocess, "run", probe)
        with pytest.raises(ValueError, match="^Cannot check repository identity") as error:
            repo_safety.outside_installation(repo / "playbooks")
        assert str(error.value).endswith(str(broken / "playbooks" if broken == repo else install))

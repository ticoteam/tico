"""The early conflict warning (scripts/check_conflicts.sh) and the pre-push hook that runs it (.githooks/pre-push).

A branch that would not merge into origin/main fails the push, and the files that conflict are named. The tests use real
git in temporary repositories, so they are marked slow (pytest -m slow), as the migration-order tests are.
"""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.slow

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "check_conflicts.sh"
HOOK = ROOT / ".githooks" / "pre-push"
# No global or system git config is read, so the result does not depend on the machine; commits need an identity.
GIT_ENV = {
    "GIT_AUTHOR_NAME": "Test",
    "GIT_AUTHOR_EMAIL": "test@example.com",
    "GIT_COMMITTER_NAME": "Test",
    "GIT_COMMITTER_EMAIL": "test@example.com",
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_TERMINAL_PROMPT": "0",
}


def git(cwd, *args, env=None, check=True):
    inherited = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        env={**inherited, **GIT_ENV, **(env or {})},
        capture_output=True,
        text=True,
        check=check,
    )


def commit(repo, name, text, message):
    (repo / name).write_text(text)
    git(repo, "add", name)
    git(repo, "commit", "-q", "-m", message)


@pytest.fixture
def remotes(tmp_path):
    """A bare origin with main (a.txt, b.txt), and a developer clone of it with the check and the hook copied in."""
    origin = tmp_path / "origin.git"
    seed = tmp_path / "seed"  # the other developer: pushes to main
    dev = tmp_path / "dev"
    git(tmp_path, "init", "-q", "--bare", "-b", "main", str(origin))
    git(tmp_path, "init", "-q", "-b", "main", str(seed))
    commit(seed, "a.txt", "one\ntwo\nthree\n", "base")
    commit(seed, "b.txt", "x\n", "base b")
    git(seed, "push", "-q", str(origin), "main")
    git(tmp_path, "clone", "-q", str(origin), str(dev))
    (dev / "scripts").mkdir()
    shutil.copy2(SCRIPT, dev / "scripts" / "check_conflicts.sh")
    return origin, seed, dev


def install_hook(dev):
    """The repository's pre-push hook, with a stub for scripts/check.sh that fails when STUB_CHECK_RC is 1."""
    (dev / ".githooks").mkdir()
    shutil.copy2(HOOK, dev / ".githooks" / "pre-push")
    stub = dev / "scripts" / "check.sh"
    stub.write_text('#!/usr/bin/env bash\nexit "${STUB_CHECK_RC:-0}"\n')
    stub.chmod(0o755)
    git(dev, "config", "core.hooksPath", ".githooks")


def diverge(seed, origin, dev):
    """A feature branch that edits a.txt, and main (pushed by the other developer) that edits the same line."""
    git(dev, "checkout", "-q", "-b", "feature")
    commit(dev, "a.txt", "one\nFEATURE\nthree\n", "feature edits a")
    commit(seed, "a.txt", "one\nMAIN\nthree\n", "main edits a")
    git(seed, "push", "-q", str(origin), "main")


def check(dev, *args):
    return subprocess.run(
        ["bash", "scripts/check_conflicts.sh", *args],
        cwd=dev,
        env={**os.environ, **GIT_ENV},
        capture_output=True,
        text=True,
        check=False,
    )


def test_the_local_json_merge_driver_does_not_hide_a_text_conflict(remotes):
    # GitHub ignores local merge drivers, so a file .gitattributes gives the tico-json driver still conflicts here.
    origin, seed, dev = remotes
    (dev / ".gitattributes").write_text("a.txt merge=tico-json\n")
    git(dev, "config", "merge.tico-json.driver", "cp %B %A")  # a driver that would always merge cleanly
    diverge(seed, origin, dev)
    git(dev, "fetch", "-q", "origin", "main")
    done = check(dev, "--base", "origin/main", "--head", "feature")
    assert done.returncode == 1, done.stdout + done.stderr
    assert "  a.txt" in done.stderr


def test_a_branch_that_merges_cleanly_passes(remotes):
    origin, seed, dev = remotes
    git(dev, "checkout", "-q", "-b", "feature")
    commit(dev, "b.txt", "x\nfeature\n", "feature edits b")
    commit(seed, "a.txt", "ONE\ntwo\nthree\n", "main edits a")
    git(seed, "push", "-q", str(origin), "main")
    git(dev, "fetch", "-q", "origin", "main")
    done = check(dev, "--base", "origin/main", "--head", "feature")
    assert done.returncode == 0, done.stdout + done.stderr


def test_a_missing_base_is_skipped_not_failed(remotes):
    _, _, dev = remotes
    done = check(dev, "--base", "origin/no-such-branch", "--head", "HEAD")
    assert done.returncode == 0, done.stdout + done.stderr
    assert "conflict check skipped" in done.stdout


def push(dev, *args, stub_rc=0):
    return git(
        dev,
        "push",
        *args,
        env={"STUB_CHECK_RC": str(stub_rc)},
        check=False,
    )


def test_the_hook_refuses_a_push_that_conflicts_and_fetches_main_first(remotes):
    origin, seed, dev = remotes
    install_hook(dev)
    git(dev, "checkout", "-q", "-b", "feature")
    commit(dev, "a.txt", "one\nFEATURE\nthree\n", "feature edits a")
    # The developer has not fetched: main moved on the remote after the clone. The hook's own fetch must see it.
    commit(seed, "a.txt", "one\nMAIN\nthree\n", "main edits a")
    git(seed, "push", "-q", str(origin), "main")
    done = push(dev, "origin", "feature")
    assert done.returncode != 0, done.stdout + done.stderr
    assert "conflicts with origin/main" in done.stderr
    assert "push stopped" in done.stderr
    assert git(origin, "ls-remote", ".", "refs/heads/feature").stdout == ""


def test_a_tag_pushed_to_a_branch_is_checked_like_a_branch(remotes):
    origin, seed, dev = remotes
    install_hook(dev)
    diverge(seed, origin, dev)
    git(dev, "tag", "lightweight")
    done = push(dev, "origin", "lightweight:refs/heads/feature")
    assert done.returncode != 0, done.stdout + done.stderr
    assert "conflicts with origin/main" in done.stderr


def test_the_hook_pushes_a_clean_branch(remotes):
    origin, seed, dev = remotes
    install_hook(dev)
    git(dev, "checkout", "-q", "-b", "feature")
    commit(dev, "b.txt", "x\nfeature\n", "feature edits b")
    done = push(dev, "origin", "feature")
    assert done.returncode == 0, done.stdout + done.stderr
    assert "refs/heads/feature" in git(origin, "ls-remote", ".", "refs/heads/feature").stdout


def test_failed_checks_stop_the_push_before_the_conflict_check(remotes):
    origin, seed, dev = remotes
    install_hook(dev)
    git(dev, "checkout", "-q", "-b", "feature")
    commit(dev, "b.txt", "x\nfeature\n", "feature edits b")
    done = push(dev, "origin", "feature", stub_rc=1)
    assert done.returncode != 0, done.stdout + done.stderr
    assert "scripts/check.sh failed" in done.stderr
    assert git(origin, "ls-remote", ".", "refs/heads/feature").stdout == ""

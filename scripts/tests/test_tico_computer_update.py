"""Updating a computer across the release that renamed its command (`scripts/tico` -> `scripts/tico-computer`).

The pull that brings the rename replaces `scripts/tico` with the bot command. The restart that follows the pull has to
go through `tico-computer`. A fake systemctl records what gets restarted; the computer is a clone of a local origin
that moves on.

Two starting points. A computer already on a release that has `tico-computer` (its `update` execs
`$HUB/scripts/tico-computer`, a path that exists before and after the pull) must update through the rename and again
after it. A computer still on the pre-rename release runs that release's `update`, which execs `$SELF`; after the pull
that is the bot command, so its restart cannot work by content alone (the strict xfail below).
"""
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
OLD_COMMAND = ROOT / "scripts" / "tests" / "fixtures" / "tico-before-rename.sh"
GIT = ["git", "-c", "user.name=fixture", "-c", "user.email=fixture@example.invalid", "-c", "init.defaultBranch=main"]

pytestmark = pytest.mark.skipif(not shutil.which("git") or not shutil.which("bash") or sys.platform == "win32",
                                reason="needs git and bash")


def git(cwd, *args):
    return subprocess.run([*GIT, *args], cwd=cwd, capture_output=True, text=True, check=True).stdout


def put(repo, path, source):
    target = repo / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(Path(source).read_bytes())
    target.chmod(0o755)


def publish(origin, message, files):
    for path, source in files:
        put(origin, path, source)
    git(origin, "add", "-A")
    git(origin, "commit", "-q", "-m", message)


def restarts(calls):
    return [line for line in calls.read_text().splitlines() if "restart tico-bot.service" in line]


def computer(tmp_path, origin_files, message):
    """An origin with one release, a computer cloned from it, and a fake systemctl that logs to calls.log."""
    origin = tmp_path / "origin"
    origin.mkdir()
    git(origin, "init", "-q", "-b", "main")
    publish(origin, message, origin_files)
    checkout = tmp_path / "tico"
    git(tmp_path, "clone", "-q", str(origin), str(checkout))

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    calls = tmp_path / "calls.log"
    systemctl = bin_dir / "systemctl"
    systemctl.write_text(
        f'#!/bin/sh\necho "systemctl $*" >> {calls}\n'
        'case "$*" in *"show-environment"*) echo HOME=/x;; esac\n'
        'case "$*" in *"show tico-bot.service"*) printf "ActiveState=active\\nSubState=running\\nMainPID=77\\nExecMainStatus=0\\n";; esac\n'
        'exit 0\n')
    systemctl.chmod(0o755)
    config = tmp_path / "runner.json"
    config.write_text('{"runner_id": "r1", "url": "https://hub.example"}')
    config.chmod(0o600)
    units = tmp_path / "units"
    units.mkdir()
    (units / "tico-bot.service").write_text("[Unit]\n")      # the bot job is installed on this computer
    home = tmp_path / "home"
    home.mkdir()
    env = {"TICO_OS": "Linux", "PATH": f"{bin_dir}:{os.environ.get('PATH', os.defpath)}", "HOME": str(home),
           "TICO_RUNNER_PYTHON": sys.executable, "TICO_RUNNER_CONFIG": str(config), "TICO_SYSTEMD_USER_DIR": str(units),
           "XDG_CONFIG_HOME": str(tmp_path / "xdg"), "USER": "ana"}
    return {"origin": origin, "checkout": checkout, "env": env, "calls": calls}


RENAME = [("scripts/tico-computer", ROOT / "scripts" / "tico-computer"), ("scripts/tico", ROOT / "scripts" / "tico"),
          ("scripts/hub", ROOT / "scripts" / "hub")]


def test_a_computer_on_the_new_command_updates_through_the_rename_and_again_after_it(tmp_path):
    # Starting point: a release with tico-computer (the old command still at scripts/tico, as the bridge leaves it).
    world = computer(tmp_path, [("scripts/tico", OLD_COMMAND), ("scripts/tico-computer", ROOT / "scripts" / "tico-computer")],
                     "release with tico-computer")
    origin, checkout, env, calls = world["origin"], world["checkout"], world["env"], world["calls"]
    publish(origin, "rename the computer command to tico-computer", RENAME)

    done = subprocess.run([str(checkout / "scripts" / "tico-computer"), "update"], cwd=checkout, env=env,
                          capture_output=True, text=True, timeout=120)
    assert done.returncode == 0, done.stdout + done.stderr
    assert "restarted tico-bot.service" in done.stdout and len(restarts(calls)) == 1
    assert "hubcli" in (checkout / "scripts" / "tico").read_text()           # the pull put the bot command in place

    ordinary = tmp_path / "tico-computer-next"
    ordinary.write_bytes((ROOT / "scripts" / "tico-computer").read_bytes() + b"# a later release changes this file\n")
    publish(origin, "an ordinary release after the rename", [("scripts/tico-computer", ordinary)])
    again = subprocess.run([str(checkout / "scripts" / "tico-computer"), "update"], cwd=checkout, env=env,
                           capture_output=True, text=True, timeout=120)
    assert again.returncode == 0, again.stdout + again.stderr
    assert len(restarts(calls)) == 2


@pytest.mark.xfail(strict=True, reason="a pre-rename computer runs its own update, which execs $SELF: after the pull "
                   "that is the bot command, which restarts nothing (see the report that added this test)")
def test_a_pre_rename_computer_restarts_its_bot_after_the_rename_pull(tmp_path):
    world = computer(tmp_path, [("scripts/tico", OLD_COMMAND)], "old release")
    origin, checkout, env, calls = world["origin"], world["checkout"], world["env"], world["calls"]
    publish(origin, "rename the computer command to tico-computer", RENAME)
    done = subprocess.run(["bash", str(checkout / "scripts" / "tico"), "update"], cwd=checkout, env=env,
                          capture_output=True, text=True, timeout=120)
    assert done.returncode == 0 and len(restarts(calls)) == 1, done.stdout + done.stderr

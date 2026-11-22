"""A re-run on an existing AWS server refreshes its .env from the stored parameter (pull-env.sh). It must never replace
that .env with an empty or partial one: the server then restart-loops. Run here with a stand-in `aws` that, like the
snap aws-cli on Ubuntu, writes nothing when its stdout is a file."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from setup import contract, remote, runner_setup

GOOD = "TICO_DOMAIN=tico.example.com\nTICO_AUTH_PROXY=owner-link\nTICO_TAG=v1.4.0\n"
OLD = "TICO_DOMAIN=tico.example.com\nTICO_AUTH_PROXY=owner-link\nTICO_TAG=v1.3.0\n"


def server(tmp_path, monkeypatch, param: str, aws_exit: int = 0):
    root, bin_dir = tmp_path / "opt", tmp_path / "bin"
    monkeypatch.setattr(contract, "REMOTE_DIR", str(root))
    monkeypatch.setattr(remote, "BOOT_LOG", str(tmp_path / "tico-setup.log"))
    root.mkdir()
    bin_dir.mkdir()
    (tmp_path / "param").write_text(param)
    stubs = {
        # Prints the parameter only into a pipe, as the snap aws-cli does.
        "aws": f'[ -p /dev/stdout ] || exit 0\ncat {tmp_path}/param\nexit {aws_exit}',
        "docker": f'echo "$@" >> {tmp_path}/docker.log',
    }
    for name, body in stubs.items():
        (bin_dir / name).write_text(f"#!/bin/sh\n{body}\n")
        (bin_dir / name).chmod(0o755)
    (root / ".env").write_text(OLD)
    (root / ".bootstrapped").write_text("")
    # The first boot's pull-env.sh, from before the check: it moves whatever the redirect wrote over .env.
    (root / "pull-env.sh").write_text(
        f"#!/bin/sh\nset -eu\naws ssm get-parameter --output text > {root}/.env.new && mv {root}/.env.new {root}/.env\n"
    )
    (root / "pull-env.sh").chmod(0o755)
    script = remote.rerun_script(
        compose=None,
        compose_url="",
        plan=False,
        env_param=("/tico/example/env", "us-west-2"),
        env_keys=["TICO_DOMAIN", "TICO_AUTH_PROXY", "TICO_TAG"],
    )
    env = {k: v for k, v in os.environ.items() if not k.startswith("TICO_")}
    run = subprocess.run(
        ["sh", "-c", script], capture_output=True, text=True, env={**env, "PATH": f"{bin_dir}:{os.environ['PATH']}"}
    )
    docker = (tmp_path / "docker.log").read_text() if (tmp_path / "docker.log").exists() else ""
    return run, root, docker


def test_a_rerun_refreshes_the_env_through_a_pipe_and_restarts(tmp_path, monkeypatch):
    run, root, docker = server(tmp_path, monkeypatch, GOOD)
    assert run.returncode == 0, run.stderr
    assert (root / ".env").read_text() == GOOD and not (root / ".env.new").exists()
    assert docker.splitlines() == ["compose up -d"]


@pytest.mark.parametrize(
    ("param", "aws_exit"),
    [("", 0), ("\n", 0), ("TICO_TAG=v1.4.0\n", 0), (GOOD.replace("TICO_TAG=v1.4.0\n", ""), 0), (GOOD, 255)],
    ids=["empty", "blank", "partial", "a-key-missing", "aws-failed-midway"],
)
def test_a_failed_or_partial_read_keeps_the_old_env_and_fails_loudly(tmp_path, monkeypatch, param, aws_exit):
    run, root, docker = server(tmp_path, monkeypatch, param, aws_exit)
    assert run.returncode == 7 and "kept the old .env" in run.stderr and "/tico/example/env" in run.stderr
    assert (root / ".env").read_text() == OLD
    assert sorted(p.name for p in root.iterdir()) == [".bootstrapped", ".env", "pull-env.sh"]  # no temp files left
    assert docker == ""  # never restarted on a bad .env


@pytest.mark.parametrize(
    "aws",
    [
        "echo TICO_JOIN_URL=https://t.example.com; exit 255",
        "printf 'TICO_JOIN_URL=https://t.example.com\\nTICO_JOIN_CODE=\\nTICO_JOIN_LABEL=build-1\\n'",
        "printf 'TICO_JOIN_URL=https://t.example.com\\nTICO_JOIN_CODE=a b\\nTICO_JOIN_LABEL=build-1\\n'",
        "printf 'TICO_JOIN_URL=t.example.com\\nTICO_JOIN_CODE=abc\\nTICO_JOIN_LABEL=build-1\\n'",
        "printf 'TICO_JOIN_URL=https://t.example.com/path\\nTICO_JOIN_CODE=abc\\nTICO_JOIN_LABEL=build-1\\n'",
        "printf 'TICO_JOIN_URL=https://t.example.com\\nTICO_JOIN_CODE=abc.def\\nTICO_JOIN_LABEL=build-1\\n'",
    ],
    ids=["aws-failed", "empty-code", "malformed-assignment", "not-a-url", "url-with-a-path", "code-charset"],
)
def test_a_runner_rejoin_whose_join_settings_cannot_be_read_keeps_the_running_runner(tmp_path, monkeypatch, aws):
    root, bin_dir = tmp_path / "runner", tmp_path / "bin"
    monkeypatch.setattr(contract, "RUNNER_DIR", str(root))
    root.mkdir()
    bin_dir.mkdir()
    (root / ".env").write_text("TICO_RUNNER_TOKEN=keep\n")
    for name, body in {
        "aws": aws,
        "docker": f'echo "$@" >> {tmp_path}/docker.log',
    }.items():
        (bin_dir / name).write_text(f"#!/bin/sh\n{body}\n")
        (bin_dir / name).chmod(0o755)
    script = runner_setup.bootstrap_script(
        os_family="al2023", tag="latest", env_param=("/tico/r/join", "us-west-2"), rejoin=True
    )
    script = "set -eu\n" + script[script.index(f"mkdir -p {root}") :]  # past the log redirect and package install
    run = subprocess.run(
        ["bash", "-c", script],
        capture_output=True,
        text=True,
        env={**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}"},
    )
    assert run.returncode == 1 and "could not read the join settings in /tico/r/join" in run.stderr
    assert (root / ".env").read_text() == "TICO_RUNNER_TOKEN=keep\n" and not (tmp_path / "docker.log").exists()
    assert not (root / "join.env").exists()


def test_join_values_the_installer_takes_pass_the_rejoin_guard(tmp_path, monkeypatch):
    root, bin_dir = tmp_path / "runner", tmp_path / "bin"
    monkeypatch.setattr(contract, "RUNNER_DIR", str(root))
    root.mkdir()
    bin_dir.mkdir()
    join = runner_setup.join_env("https://t.example.com:8443", "AbC_12-x", "Ana's build 1")
    (tmp_path / "join").write_text(join)
    stubs = {"aws": f"cat {tmp_path}/join", "docker": f'echo "$@" >> {tmp_path}/docker.log', "curl": "exit 22"}
    for name, body in stubs.items():
        (bin_dir / name).write_text(f"#!/bin/sh\n{body}\n")
        (bin_dir / name).chmod(0o755)
    script = runner_setup.bootstrap_script(
        os_family="al2023", tag="latest", env_param=("/tico/r/join", "us-west-2"), rejoin=True
    )
    script = "set -eu\n" + script[script.index(f"mkdir -p {root}") :]
    run = subprocess.run(
        ["bash", "-c", script],
        capture_output=True,
        text=True,
        env={**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}"},
    )
    # Past the guard: the old runner is replaced, and only the stand-in download fails.
    assert "could not read the join settings" not in run.stderr and "could not download" in run.stderr
    assert (tmp_path / "docker.log").read_text().startswith("rm -f tico-runner")


def test_the_rejoin_guard_holds_to_the_installers_own_checks():
    script = (Path(runner_setup.__file__).resolve().parent.parent / "scripts" / "install.sh").read_text()
    for key, flag in (
        ("TICO_JOIN_URL", "RUNNER_URL"),
        ("TICO_JOIN_CODE", "RUNNER_CODE"),
        ("TICO_JOIN_LABEL", "RUNNER_LABEL"),
    ):
        assert f"printf '%s' \"${flag}\" | grep -Eq '{runner_setup.INSTALLER_JOIN_CHECKS[key]}'" in script, key

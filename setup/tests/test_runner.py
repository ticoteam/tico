import io as _io
import json
import shutil
import subprocess

import pytest

from setup import cli, runner_setup as rs, state, verify
from setup import remote
from setup.remote import Host, Result
from setup.tests import fakes
from setup.tests.fakes import CaptureIO, FakeShell

CODE = "one-time-code-abcdefghijklmnopqrstuvwxyz0123456789"


def go(argv, deps=None):
    io = CaptureIO()
    return cli.main(argv, deps=deps or fakes.deps(), io=io), io.text


def test_join_script_is_idempotent_and_removes_the_code_file():
    text = "\n".join(rs.join_lines("v1", "/x/join.env"))
    assert "grep -qx tico-runner" in text and "docker run" not in text
    assert "releases/download/v1/install.sh -o" in text and 'sh "$f" --runner' in text and "| sh" not in text
    assert '--url "$TICO_JOIN_URL" --code "$TICO_JOIN_CODE" --label "$TICO_JOIN_LABEL"' in text
    assert "releases/latest/download/install.sh" in "\n".join(rs.join_lines("latest", "/x/join.env"))
    assert text.rstrip().endswith("rm -f /x/join.env")
    rejoin = "\n".join(rs.join_lines("v1", "/x/join.env", rejoin=True))
    assert "docker rm -f" in rejoin and "rm -f /opt/tico-runner/.env" in rejoin  # a new code needs a new .env
    assert "screen" not in text                                  # the installer offers the screen by default
    assert '"$TICO_JOIN_LABEL" --no-screen' in "\n".join(rs.join_lines("v1", "/x/join.env", screen=False))


def test_aws_bootstrap_takes_the_code_from_ssm_not_from_user_data():
    s = rs.bootstrap_script(os_family="ubuntu", tag="latest", env_param=("/tico/runner-a/env", "us-east-1"))
    assert "ssm get-parameter" in s and CODE not in s


def test_ssh_target_mints_code_late_and_passes_it_by_stdin(monkeypatch):
    monkeypatch.setenv("TICO_OWNER_TOKEN", "owner-token-xyz")
    monkeypatch.setenv("TICO_INBOUND_KEY", "tin_private-0123456789abcdefghijklmnopqrstuv")
    monkeypatch.setenv("TICO_INBOUND_URL", "https://inbox.private.example")
    monkeypatch.setattr(rs, "mint_code", lambda url, tok: CODE)
    shell = FakeShell({"docker ps": Result(0, "Up 3 seconds\n")})
    d = fakes.deps()
    d.ssh_shell = lambda *a: shell
    monkeypatch.setattr(verify, "check_computer_online", lambda *a: None)
    code, out = go(["runner", "--non-interactive", "--yes", "--server-url", "https://tico.example.com", "--target", "ssh",
                    "--ssh", "root@10.0.0.5", "--label", "build-1"], deps=d)
    assert code == 0, out
    assert CODE not in out and "owner-token-xyz" not in out
    assert any(CODE.encode() in (i or b"") for _, i in shell.calls) and not any(CODE in c for c in shell.cmds())
    assert state.load_runners("https://tico.example.com")[0]["label"] == "build-1"
    assert all(b"TICO_INBOUND_" not in (data or b"") and "TICO_INBOUND_" not in cmd for cmd, data in shell.calls)
    assert "tin_private-" not in out and "inbox.private.example" not in out


def test_mint_code_uses_bearer_and_maps_errors():
    seen = {}

    class Resp(_io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            pass

    def opener(req, timeout):
        seen["auth"], seen["url"] = req.get_header("Authorization"), req.full_url
        seen["key"] = req.get_header("Idempotency-key")
        return Resp(json.dumps({"code": CODE}).encode())

    assert rs.mint_code("https://t/", "tok", opener) == CODE
    assert seen["auth"] == "Bearer tok" and seen["url"] == "https://t/api/v2/enrollments"
    assert seen["key"]  # the server refuses a write without an Idempotency-Key



def _stub_path(tmp_path, curl_body):
    """Fake curl, docker and systemctl first on PATH: synthetic, no network and no Docker."""
    b = tmp_path / "bin"
    b.mkdir()
    stubs = {"curl": curl_body, "docker": "exit 1", "systemctl": "exit 0"}
    for name, body in stubs.items():
        (b / name).write_text("#!/bin/sh\n" + body + "\n")
        (b / name).chmod(0o755)
    return {"PATH": f"{b}:/usr/bin:/bin", "HOME": str(tmp_path)}


FAILING_CURL = 'echo "curl: (6) Could not resolve host" >&2; exit 6'
# Writes an "installer" that records its arguments and exits with $INSTALLER_EXIT.
GOOD_CURL = ('while [ "$#" -gt 0 ]; do [ "$1" = -o ] && out=$2; shift; done; '
             'printf \'echo "ran $*" >> "$HOME/ran"; exit "${INSTALLER_EXIT:-0}"\\n\' > "$out"')


def _join_script(tmp_path):
    env_file = tmp_path / "join.env"
    env_file.write_text(rs.join_env("https://tico.example.com", CODE, "build 1"))
    return "\n".join(["set -eu"] + rs.join_lines("v1", str(env_file))), env_file


@pytest.mark.parametrize("shell", ["sh", "bash"])
def test_a_failed_installer_download_fails_the_join_and_leaves_no_code(tmp_path, shell):
    if not shutil.which(shell):
        pytest.skip(shell)
    script, env_file = _join_script(tmp_path)
    p = subprocess.run([shell, "-c", script], env=_stub_path(tmp_path, FAILING_CURL), capture_output=True, text=True)
    assert p.returncode == 1, p.stdout + p.stderr  # was 0: `curl | sh` fed sh nothing and "succeeded"
    assert "could not download https://github.com/ticoteam/tico/releases/download/v1/install.sh" in p.stderr
    assert not env_file.exists()


def test_a_downloaded_installer_runs_with_the_join_arguments_and_its_exit_code_counts(tmp_path):
    script, _ = _join_script(tmp_path)
    env = _stub_path(tmp_path, GOOD_CURL)
    p = subprocess.run(["sh", "-c", script], env=env, capture_output=True, text=True)
    assert p.returncode == 0, p.stdout + p.stderr
    assert (tmp_path / "ran").read_text() == (
        f"ran --runner --dir /opt/tico-runner --url https://tico.example.com --code {CODE} --label build 1\n")
    script, _ = _join_script(tmp_path)
    p = subprocess.run(["sh", "-c", script], env={**env, "INSTALLER_EXIT": "3"}, capture_output=True, text=True)
    assert p.returncode == 3


@pytest.mark.parametrize("shell", ["sh", "bash"])
def test_a_failed_docker_download_stops_the_bootstrap_and_the_ssh_step(tmp_path, shell):
    if not shutil.which(shell):
        pytest.skip(shell)
    env = _stub_path(tmp_path, FAILING_CURL)
    boot = "\n".join(["set -eu"] + remote.docker_install_lines("ubuntu", need_aws_cli=False) + ["echo reached the end"])
    p = subprocess.run([shell, "-c", boot], env=env, capture_output=True, text=True)
    assert p.returncode == 1 and "reached the end" not in p.stdout, p.stdout + p.stderr
    fake = FakeShell({"docker compose version": Result(1, "")})
    h = Host(fake, lambda *_: None, lambda s: s)
    h.root = True
    with pytest.raises(remote.StepFailed):
        h.docker()
    cmd = next(c for c in fake.cmds() if "get.docker.com" in c)
    assert "| sh" not in cmd
    p = subprocess.run([shell, "-c", cmd], env=env, capture_output=True, text=True)
    assert p.returncode == 1, p.stdout + p.stderr  # was 0: systemctl ran after an empty install


def test_aws_runner_reuses_its_exact_name_boundary_and_private_join(monkeypatch):
    from setup import aws
    monkeypatch.setenv('TICO_ENROLL_CODE', CODE)
    ec2, iam, ssm, clients = fakes.aws_clients()
    ssm.get_command_invocation = lambda **kw: {'Status':'Success','ResponseCode':0,
        'StandardOutputContent':'Up 3 seconds\n','StandardErrorContent':''}
    d = fakes.deps()
    d.aws_clients = clients
    argv = ['runner','--cloud','aws','--aws-region','us-east-2','--aws-instance-type','m8i-flex.xlarge',
            '--name','pv-bot','--aws-permissions-boundary','arn:aws:iam::123456789012:policy/boundary',
            '--server-url','https://acme.tico.dev','--label','pv-bot','--runner-tag','v0.3.71',
            '--non-interactive','--yes']
    for _ in range(2):
        code, out = go(argv, d)
        assert code == 0, out
        assert CODE not in out
    assert len(ec2.called('run_instances')) == 1
    assert iam.called('create_role')[0]['PermissionsBoundary'].endswith(':policy/boundary')
    assert state.load_runners()[0]['name'] == 'pv-bot'
    assert any('cloud-init status --wait' in command for command in ssm.commands)

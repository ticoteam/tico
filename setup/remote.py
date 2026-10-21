"""Doing things on the server: a command runner (SSH or SSM), the bootstrap script, and the install steps."""
from __future__ import annotations

import base64
import gzip
import hashlib
import shlex
import subprocess
import time
from dataclasses import dataclass
from typing import Callable, Protocol

from . import contract


@dataclass
class Result:
    returncode: int
    stdout: str = ""
    stderr: str = ""


class Shell(Protocol):
    def run(self, cmd: str, input: bytes | None = None) -> Result: ...


class SSHShell:
    """Uses the user's own ssh, key and agent; nothing is stored."""

    def __init__(self, host: str, port: int = 22, identity: str = "", timeout: int = 900):
        self.host, self.port, self.identity, self.timeout = host, port, identity, timeout

    def argv(self, cmd: str) -> list[str]:
        a = ["ssh", "-p", str(self.port), "-o", "StrictHostKeyChecking=accept-new", "-o", "ServerAliveInterval=30"]
        if self.identity:
            a += ["-i", self.identity]
        return a + [self.host, cmd]

    def run(self, cmd: str, input: bytes | None = None) -> Result:
        p = subprocess.run(self.argv(cmd), input=input, capture_output=True, timeout=self.timeout)
        return Result(p.returncode, p.stdout.decode(errors="replace"), p.stderr.decode(errors="replace"))


class LocalShell:
    """The machine the wizard runs on (scripts/install.sh): the same steps as over SSH, without the SSH."""

    def run(self, cmd: str, input: bytes | None = None) -> Result:
        p = subprocess.run(["sh", "-c", cmd], input=input, capture_output=True, timeout=900)
        return Result(p.returncode, p.stdout.decode(errors="replace"), p.stderr.decode(errors="replace"))


class SSMShell:
    """Shell over AWS Systems Manager: no SSH key, no port 22. Commands must not carry secrets (SSM logs them)."""

    def __init__(self, ssm, instance_id: str, sleep: Callable[[float], None] = time.sleep):
        self.ssm, self.iid, self.sleep = ssm, instance_id, sleep

    def run(self, cmd: str, input: bytes | None = None, *, ready_timeout: int = 0) -> Result:
        if input is not None:
            raise ValueError("SSMShell does not pass stdin")
        deadline = time.monotonic() + ready_timeout
        while True:
            if ready_timeout and time.monotonic() >= deadline:
                return Result(124, "", "timed out waiting for Systems Manager")
            try:
                online = not ready_timeout or any(
                    row.get("PingStatus") == "Online" for row in self.ssm.describe_instance_information(
                        Filters=[{"Key": "InstanceIds", "Values": [self.iid]}])["InstanceInformationList"])
                if online:
                    sent = self.ssm.send_command(InstanceIds=[self.iid], DocumentName="AWS-RunShellScript",
                                                 Parameters={"commands": [cmd]}, TimeoutSeconds=900)
                    break
            except Exception as exc:
                # Online can precede Run Command registration. Retry only this pre-acceptance refusal,
                # never a failed/uncertain invocation, an explicit access denial, or a transport failure.
                if not ready_timeout or getattr(exc, "response", {}).get("Error", {}).get("Code") != "InvalidInstanceId":
                    raise
            self.sleep(min(5, max(0, deadline - time.monotonic())))
        cid = sent["Command"]["CommandId"]
        for _ in range(450):
            self.sleep(2)
            try:
                inv = self.ssm.get_command_invocation(CommandId=cid, InstanceId=self.iid)
            except self.ssm.exceptions.InvocationDoesNotExist:
                continue
            if inv["Status"] not in ("Pending", "InProgress", "Delayed"):
                code = inv.get("ResponseCode", 0 if inv["Status"] == "Success" else 1)
                return Result(code, inv.get("StandardOutputContent", ""), inv.get("StandardErrorContent", ""))
        return Result(124, "", "timed out waiting for the command")


class StepFailed(RuntimeError):
    pass


def fetch_and_run(url: str, args: str = "") -> str:
    """Downloads an installer to a temp file and runs it with sh, on one line. Never `curl | sh`: the scripts run
    under sh or bash `set -eu` without pipefail, where a failed download feeds sh nothing and "succeeds".
    Each failure exits on its own, since under `set -e` a failure on the left of `&&` does not stop the script."""
    u = shlex.quote(url)
    run = f'sh "$f" {args}'.rstrip()
    return (f'f=$(mktemp) || exit 1; if ! curl -fsSL {u} -o "$f"; then rm -f "$f"; '
            f'echo "tico-setup: could not download {url}" >&2; exit 1; fi; '
            f'{run} || {{ rc=$?; rm -f "$f"; exit "$rc"; }}; rm -f "$f"')


def _sh(cmd: str, root: bool) -> str:
    return f"sudo -n sh -c {shlex.quote(cmd)}" if root else cmd


@dataclass
class Step:
    description: str
    run: Callable[[], None]


DOCKER_OK = "docker compose version >/dev/null 2>&1"


class Host:
    """One SSH host: run as root (sudo when needed), install Docker once."""

    def __init__(self, r: Shell, say: Callable[[str], None] = print, scrub: Callable[[str], str] = lambda s: s):
        self.r, self.say, self.scrub, self.root = r, say, scrub, False

    def ex(self, cmd: str, what: str, input: bytes | None = None) -> Result:
        res = self.r.run(_sh(cmd, not self.root), input)
        if res.returncode:
            raise StepFailed(f"{what} failed (exit {res.returncode}): {self.scrub((res.stderr or res.stdout).strip()[-600:])}")
        return res

    def connect(self) -> None:
        res = self.r.run("id -u")
        if res.returncode and isinstance(self.r, LocalShell):
            raise StepFailed("Could not run commands on this machine: " + self.scrub(res.stderr.strip()[-400:]))
        if res.returncode:
            raise StepFailed("Could not connect over SSH: " + self.scrub(res.stderr.strip()[-400:])
                             + "\nCheck `ssh <host>` works from this terminal with your key or agent.")
        self.root = res.stdout.strip() == "0"
        if not self.root and self.r.run("sudo -n true").returncode:
            raise StepFailed("This user cannot run sudo without a password. Use root, or a user with passwordless sudo.")

    def docker(self) -> None:
        if self.r.run(_sh(DOCKER_OK, not self.root)).returncode == 0:
            self.say("    Docker and the Compose plugin are already installed.")
            return
        self.say("    Installing Docker (get.docker.com) ...")
        self.ex(fetch_and_run("https://get.docker.com") + " && systemctl enable --now docker", "Installing Docker")
        self.ex(DOCKER_OK, "Docker Compose plugin check (needs Compose v2)")

    def write_private(self, path: str, data: bytes, what: str) -> None:
        self.ex(f"umask 077; cat > {path}.new && mv {path}.new {path} && chmod 600 {path}", what, data)


def ssh_steps(r: Shell, *, env_text: str, compose: bytes | None, compose_url: str,
              say: Callable[[str], None] = print, scrub: Callable[[str], str] = lambda s: s) -> list[Step]:
    """Idempotent: each step checks before it changes, so a re-run only does what is missing."""
    d = contract.REMOTE_DIR
    h = Host(r, say, scrub)

    def files() -> None:
        h.ex(f"mkdir -p {d} && chmod 755 {d}", "Creating " + d)
        if compose is not None:
            h.ex(f"cat > {d}/compose.yaml.new && mv {d}/compose.yaml.new {d}/compose.yaml", "Writing compose.yaml", compose)
        else:
            h.ex(f"curl -fsSL {shlex.quote(compose_url)} -o {d}/compose.yaml.new && mv {d}/compose.yaml.new {d}/compose.yaml",
                 "Downloading compose.yaml")
        want = hashlib.sha256(env_text.encode()).hexdigest()
        cur = r.run(_sh(f"sha256sum {d}/.env 2>/dev/null", not h.root)).stdout.split()[:1]
        if cur == [want]:
            say("    .env is already up to date.")
            h.ex(f"chmod 600 {d}/.env", "Setting .env permissions")
            return
        h.write_private(f"{d}/.env", env_text.encode(), "Writing .env")

    def up() -> None:
        h.ex(f"cd {d} && docker compose pull --quiet && docker compose up -d", "docker compose up -d")

    return [
        Step("Check this machine's access (root or sudo)" if isinstance(r, LocalShell) else "Connect over SSH and check sudo", h.connect),
        Step("Install Docker + Compose if missing", h.docker),
        Step(f"Write {d}/compose.yaml and {d}/.env (mode 0600)", files),
        Step("docker compose pull && docker compose up -d", up),
    ]


def _b64gz(data: bytes) -> str:
    return base64.b64encode(gzip.compress(data, 9, mtime=0)).decode()


def docker_install_lines(os_family: str, need_aws_cli: bool) -> list[str]:
    if os_family == "al2023":
        return ["dnf install -y docker awscli >/dev/null", "systemctl enable --now docker",
                "mkdir -p /usr/local/lib/docker/cli-plugins",
                'curl -fsSL "https://github.com/docker/compose/releases/download/v2.29.7/docker-compose-linux-$(uname -m)"'
                " -o /usr/local/lib/docker/cli-plugins/docker-compose", "chmod +x /usr/local/lib/docker/cli-plugins/docker-compose"]
    lines = [f"if ! docker compose version >/dev/null 2>&1; then {fetch_and_run('https://get.docker.com')}; fi",
             "systemctl enable --now docker"]
    return lines + (["command -v aws >/dev/null || snap install aws-cli --classic"] if need_aws_cli else [])


def bootstrap_script(*, os_family: str, domain: str, front_door: str, compose: bytes | None, compose_url: str,
                     env_b64: str = "", env_param: tuple[str, str] | None = None, wait_for_ip: str = "") -> str:
    """One script that installs Docker and starts Tico. Used as EC2 user-data (env from a SecureString parameter)
    and as the pasted install command (env embedded)."""
    d = contract.REMOTE_DIR
    q = shlex.quote
    L = ["#!/bin/bash", "set -eu", "exec > >(tee -a /var/log/tico-setup.log) 2>&1", "echo tico-setup: start $(date -u)"]
    L += docker_install_lines(os_family, need_aws_cli=bool(env_param))
    L += [f"mkdir -p {d}"]
    if compose is not None:
        L += [f"echo {_b64gz(compose)} | base64 -d | gunzip > {d}/compose.yaml"]
    else:
        L += [f"curl -fsSL {q(compose_url)} -o {d}/compose.yaml"]
    if env_param:
        name, region = env_param
        L += [f"cat > {d}/pull-env.sh <<'EOS'", "#!/bin/sh", "set -eu", "umask 077",
              f"aws ssm get-parameter --region {q(region)} --name {q(name)} --with-decryption "
              f"--query Parameter.Value --output text > {d}/.env.new && mv {d}/.env.new {d}/.env", "EOS",
              f"chmod 700 {d}/pull-env.sh", f"{d}/pull-env.sh"]
    else:
        L += ["umask 077", f"echo {env_b64} | base64 -d > {d}/.env"]
    L += [f"chmod 600 {d}/.env"]
    if front_door == "caddy" and wait_for_ip:
        L += ["# Caddy asks for its certificate at first start; wait until the name points here or that fails.",
              f"for i in $(seq 1 80); do getent hosts {q(domain)} | grep -q {q(wait_for_ip)} && break; sleep 15; done"]
    L += [f"cd {d} && docker compose pull --quiet && docker compose up -d", f"touch {d}/.bootstrapped", "echo tico-setup: done"]
    return "\n".join(L) + "\n"


def env_b64(env_text: str) -> str:
    return base64.b64encode(env_text.encode()).decode()


def paste_command(script: str) -> str:
    """The single line the user pastes on the server. The script is inside it, so it holds secrets."""
    return f"echo {_b64gz(script.encode())} | base64 -d | gunzip | sudo bash"

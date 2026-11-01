"""Fixed local operator command for an existing Docker installation's visuals.

Discovery is read-only. Only explicit apply records intent. A private journal
and one process lock fence revisions across bounded calls; provisioning, keys,
provider settings and public application APIs are outside this command.
"""
from __future__ import annotations

import argparse
import base64
from contextlib import contextmanager
import fcntl
import hashlib
import os
from pathlib import Path
import re
import signal
import stat
import subprocess
import sys
import tempfile
import time
import urllib.request

from . import visual_contract as wire
from .envfile import parse

ROOT = Path("/opt/tico")
IDENTITY = ("account_id", "company_id", "workspace_id", "preview_id", "attempt_id", "address",
            "public_hostname", "hostname", "instance_id", "install_id")
JOURNAL = ".visual-config.json"
LOCK = ".visual-config.lock"
MAX_JOURNAL = 1024 * 1024


def read_file(path, limit, *, private=False):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_size > limit
                or (private and (info.st_mode & 0o077 or info.st_uid != os.geteuid()))):
            raise wire.Pending("private_file_invalid")
        raw = os.read(fd, limit + 1)
        if len(raw) > limit:
            raise wire.Pending("private_file_invalid")
        return raw
    finally:
        os.close(fd)


def installer_capability(root):
    root = Path(root)
    value = wire.decode(read_file(root / "installer-manifest.json", 65536), 65536)
    if (not isinstance(value, dict) or set(value) != {"protocol", "revision", "files", "runtime_files"}
            or value["protocol"] != wire.PROTOCOL or not wire.matches(wire.REVISION, value["revision"])
            or not isinstance(value["files"], dict) or not isinstance(value["runtime_files"], dict)
            or set(value["runtime_files"]) != set(wire.RUNTIME_FILES)):
        raise wire.Pending("installer_source_unsupported")
    required = {"setup/__init__.py", "setup/cli.py", "setup/settings.py", "setup/envfile.py",
                "setup/contract.py", "setup/visual_contract.py", "setup/visual_config.py", "compose.yaml"}
    if not required <= set(value["files"]) or len(value["files"]) > 100:
        raise wire.Pending("installer_source_unsupported")
    for name, digest in value["files"].items():
        if (not (name == "compose.yaml" or wire.matches(r"setup/[a-z_][a-z0-9_]*\.py", name))
                or not wire.matches(wire.HEX, digest)
                or hashlib.sha256(read_file(root / name, 2 * 1024 * 1024)).hexdigest() != digest):
            raise wire.Pending("installer_source_modified")
    if any(not wire.matches(wire.HEX, digest) for digest in value["runtime_files"].values()):
        raise wire.Pending("installer_source_unsupported")
    return value


def atomic(path, raw):
    fd, name = tempfile.mkstemp(prefix=".visual-config-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as output:
            os.fchmod(output.fileno(), 0o600)
            output.write(raw)
            output.flush()
            os.fsync(output.fileno())
        os.replace(name, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(name):
            os.unlink(name)


@contextmanager
def guard(root):
    fd = os.open(root / LOCK, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
            raise wire.Pending("lock_invalid")
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise wire.Pending("operation_busy") from None
        yield
    finally:
        os.close(fd)


def load(root):
    try:
        value = wire.decode(read_file(root / JOURNAL, MAX_JOURNAL, private=True), MAX_JOURNAL)
    except FileNotFoundError:
        return {"schema": 1, "identity": None, "current": None, "operations": {}}
    if (not isinstance(value, dict) or set(value) != {"schema", "identity", "current", "operations"}
            or type(value["schema"]) is not int or value["schema"] != 1 or not isinstance(value["operations"], dict)):
        raise wire.Pending("journal_unsupported")
    return value


def save(root, journal):
    raw = wire.encoded(journal)
    if len(raw) > MAX_JOURNAL:
        raise wire.Pending("journal_full")
    atomic(root / JOURNAL, raw)


def patch_environment(raw, configuration):
    if len(raw) > 262144 or any(char < 32 and char not in (9, 10, 13) for char in raw):
        raise wire.Pending("environment_invalid")
    seen, result = set(), []
    for line in raw.splitlines(keepends=True):
        stripped = line.strip()
        if stripped and not stripped.startswith(b"#"):
            assignment = re.fullmatch(rb"([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)", stripped)
            if assignment is None:
                raise wire.Pending("environment_ambiguous")
            value = assignment[2]
            if value.startswith((b"'", b'"')):
                quote, escaped, closed = value[0], False, False
                for index, char in enumerate(value[1:], 1):
                    if char == quote and not escaped:
                        closed = not value[index + 1:].strip() or value[index + 1:].lstrip().startswith(b"#")
                        break
                    escaped = char == 92 and not escaped
                if not closed:
                    raise wire.Pending("environment_ambiguous")
        match = re.match(rb"\s*([A-Za-z_][A-Za-z0-9_]*)\s*=", line)
        key = match[1].decode() if match else ""
        if key in configuration:
            if key in seen:
                raise wire.Pending("environment_ambiguous")
            seen.add(key)
            result.append((key + "=" + configuration[key] + "\n").encode())
        else:
            # Refuse alternative assignments/interpolation of managed settings;
            # preserving an unrecognized override would make a saved claim false.
            if any(re.match(rb"\s*export\s+" + key.encode() + rb"\b", line) for key in configuration):
                raise wire.Pending("environment_ambiguous")
            result.append(line)
    if result and not result[-1].endswith(b"\n"):
        result.append(b"\n")
    result.extend((key + "=" + configuration[key] + "\n").encode() for key in configuration if key not in seen)
    return b"".join(result)


def select(journal, request, raw):
    wire.validate(request)
    identity = {key: request[key] for key in IDENTITY}
    if journal["identity"] not in (None, identity):
        raise wire.Pending("installation_binding_conflict")
    operations = journal["operations"]
    existing = operations.get(request["operation_id"])
    current = operations.get(journal["current"])
    if existing is not None:
        if existing["request"] != request:
            raise wire.Pending("operation_conflict")
        if journal["current"] != request["operation_id"]:
            raise wire.Pending("operation_stale")
        return existing
    if current:
        if request["revision"] <= current["request"]["revision"]:
            raise wire.Pending("operation_stale")
        if current["mutation_pending"] or current["stage"] not in ("applied", "verified"):
            raise wire.Pending("prior_operation_unsettled")
        if hashlib.sha256(raw).hexdigest() != current["desired_sha256"]:
            raise wire.Pending("environment_changed")
        if request["enabled"] and current["request"]["enabled"] and request["lease_id"] != current["request"]["lease_id"]:
            raise wire.Pending("retirement_required")
        if request["enabled"] and not current["request"]["enabled"] and request["lease_id"] == current["request"]["lease_id"]:
            raise wire.Pending("lease_stale")
    desired = patch_environment(raw, wire.configuration(request))
    operation = {"request": request, "original_sha256": hashlib.sha256(raw).hexdigest(),
                 "desired_sha256": hashlib.sha256(desired).hexdigest(), "stage": "intent", "mutation_pending": False,
                 "former_probe": (current.get("probe_fingerprint") if current["request"]["enabled"]
                                  else current.get("former_probe")) if current else None}
    operations[request["operation_id"]] = operation
    journal.update(identity=identity, current=request["operation_id"])
    return operation


def instance_id():
    # IMDSv2 is the supported host identity authority; no environment or request
    # fallback can turn an arbitrary label into evidence of the current EC2.
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs):
            return None
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    token_request = urllib.request.Request("http://169.254.169.254/latest/api/token", method="PUT",
                                          headers={"X-aws-ec2-metadata-token-ttl-seconds": "60"})
    with opener.open(token_request, timeout=1) as response:
        token = response.read(1025).decode("ascii")
    if not token or len(token) > 1024 or any(ord(char) < 33 or ord(char) > 126 for char in token):
        raise wire.Pending("instance_identity_unknown")
    request = urllib.request.Request("http://169.254.169.254/latest/meta-data/instance-id",
                                     headers={"X-aws-ec2-metadata-token": token})
    with opener.open(request, timeout=1) as response:
        identity = response.read(64).decode("ascii")
    if not wire.matches(r"i-[0-9a-f]{8,17}", identity):
        raise wire.Pending("instance_identity_unknown")
    return identity


def process_identity(pid):
    try:
        fields = Path("/proc", str(pid), "stat").read_text().rsplit(") ", 1)[1].split()
        return {"pid": pid, "start": fields[19]}
    except (OSError, IndexError):
        return None


class Docker:
    def __init__(self, root=ROOT):
        self.root = root
        self.environment = {"PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": "/root", "PYTHONDONTWRITEBYTECODE": "1"}
        self.deadline = time.monotonic() + 150

    def run(self, args, *, input=None, timeout=8, limit=65536, on_start=None):
        # Commands and arguments below are code-owned. Never propagate Compose
        # overrides, caller credentials or arbitrary command fragments.
        timeout = min(timeout, self.deadline - time.monotonic())
        if timeout <= 0:
            raise wire.Pending("operation_deadline")
        process = subprocess.Popen(args, cwd=self.root, env=self.environment,
                                   stdin=subprocess.PIPE if input is not None else subprocess.DEVNULL,
                                   stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, start_new_session=True)
        try:
            if on_start:
                on_start(process_identity(process.pid))
            raw, _ = process.communicate(input=input, timeout=timeout)
        finally:
            if process.poll() is None:
                # This exact process group was created above; no shared runner,
                # container or process selected by name is ever signalled.
                os.killpg(process.pid, signal.SIGKILL)
            process.wait()
        if process.returncode or len(raw) > limit:
            raise wire.Pending("runtime_pending")
        return raw

    def compose(self, *args, **kwargs):
        return self.run(["docker", "compose", "--project-directory", str(self.root), "--env-file", str(self.root / ".env"),
                         "-f", str(self.root / "compose.yaml"), *args], **kwargs)

    def container(self, service):
        # An existing stopped Caddy is still the recorded operation's repair
        # target. Absence remains distinct and never activates a new service.
        identity = self.compose("ps", "--all", "-q", service, limit=256).decode().strip()
        if not wire.matches(r"[0-9a-f]{64}", identity):
            raise wire.Pending("existing_service_required")
        return identity

    def runtime(self, verb, payload=None):
        args = ["docker", "exec", "-i", self.container("server"), "python", "-B", "-m", "backend.visual_config", verb]
        return wire.decode(self.run(args, input=wire.encoded(payload) if payload is not None else None,
                                    timeout=12 if verb == "probe" else 8), 65536)

    def caddy(self, expected=None):
        container = self.container("caddy")
        source = self.run(["docker", "exec", container, "sha256sum", "/generated/supervisor.sh"], limit=256).decode().split()[0]
        self.run(["docker", "exec", container, "/bin/sh", "/generated/supervisor.sh", "health"], timeout=4, limit=1024)
        applied = wire.decode(self.run(["docker", "exec", container, "cat", "/tmp/tico-caddy/applied"], limit=1024), 1024)
        if (set(applied) != {"file_sha256", "generation", "pid", "supervisor_pid"}
                or not wire.matches(wire.HEX, applied["generation"]) or not wire.matches(wire.HEX, applied["file_sha256"])
                or type(applied["pid"]) is not int or type(applied["supervisor_pid"]) is not int):
            raise wire.Pending("applied_generation_pending")
        if expected and any(applied[key] != expected[key] for key in ("generation", "file_sha256")):
            raise wire.Pending("applied_generation_pending")
        return source, applied

    def healthy(self, service):
        container = self.container(service)
        state = wire.decode(self.run(["docker", "inspect", "--format", "{{json .State}}", container], limit=8192), 8192)
        if not state.get("Running") or state.get("Restarting") or state.get("Health", {}).get("Status") != "healthy":
            raise wire.Pending("runtime_pending")
        return container


def supported(root, docker):
    selected = installer_capability(root)
    running = docker.runtime("capabilities")
    expected = {"revision": selected["revision"], "runtime_files": selected["runtime_files"]}
    if running != expected:
        raise wire.Pending("selected_runtime_unsupported")
    # observe verifies the generated configuration and shared supervisor bytes
    # against the selected running image, without requiring Caddy to have applied
    # them already. Discovery describes supported code, never applied health.
    # Requiring that health here would prevent an owed same-operation repair.
    observed = docker.runtime("observe")
    if any(observed.get(key) != value for key, value in expected.items()):
        raise wire.Pending("selected_runtime_unsupported")
    docker.container("caddy")
    return {"protocol": wire.PROTOCOL, "installer_revision": selected["revision"],
            "runtime_revision": running["revision"], "initial_flags": True,
            "same_instance_apply": True, "applied_receipt": True}


def bound(request, observed):
    if observed["install_id"] != request["install_id"] or observed["public_hostname"] != request["public_hostname"]:
        raise wire.Pending("installation_identity_conflict")


def reconcile(root, docker, journal, operation):
    request = operation["request"]
    supported(root, docker)
    if instance_id() != request["instance_id"]:
        raise wire.Pending("instance_identity_conflict")
    before = docker.runtime("observe")
    bound(request, before)
    if operation.get("unrelated_digest", before["unrelated_digest"]) != before["unrelated_digest"]:
        raise wire.Pending("unrelated_configuration_changed")
    operation["unrelated_digest"] = before["unrelated_digest"]
    save(root, journal)
    pending = operation["mutation_pending"]
    if pending:
        client = pending.get("client")
        if not client or process_identity(client["pid"]) == client:
            raise wire.Pending("mutation_outcome_unknown")
        # A lost response is recoverable only after its exact client has exited
        # and a fresh healthy container has reached the requested generation.
        if (docker.healthy(pending["service"]) == pending["before"]
                or before["configuration"] != wire.configuration(request)):
            raise wire.Pending("mutation_outcome_unknown")
        if pending["service"] == "caddy":
            docker.caddy(before)
        operation.update(mutation_pending=False,
                         stage="server_started" if pending["service"] == "server" else "applied")
        save(root, journal)
    raw = read_file(root / ".env", 262144, private=True)
    current_sha = hashlib.sha256(raw).hexdigest()
    if current_sha not in (operation["original_sha256"], operation["desired_sha256"]):
        raise wire.Pending("environment_changed")
    if operation["stage"] == "intent":
        desired = patch_environment(raw, wire.configuration(request))
        if hashlib.sha256(desired).hexdigest() != operation["desired_sha256"]:
            raise wire.Pending("environment_changed")
        # The private guard protects this operator; an editor using another
        # mechanism still must not be silently overwritten.
        if read_file(root / ".env", 262144, private=True) != raw:
            raise wire.Pending("environment_changed")
        atomic(root / ".env", desired)
        operation["stage"] = "saved"
        save(root, journal)
    elif current_sha != operation["desired_sha256"]:
        raise wire.Pending("environment_changed")
    for stage, service, following in (("saved", "server", "server_started"), ("server_started", "caddy", "applied")):
        if operation["stage"] != stage:
            continue
        container = docker.container(service)  # Never activate an absent profile/service.
        operation["mutation_pending"] = {"service": service, "before": container, "client": None}
        save(root, journal)
        def started(identity):
            operation["mutation_pending"]["client"] = identity
            save(root, journal)
        # The detached owner retains its flock past the SSM command deadline.
        # A lost Docker response remains durable uncertainty, never a retry.
        docker.compose("up", "-d", "--no-deps", "--force-recreate", "--pull", "never", "--no-build",
                       "--wait", "--wait-timeout", "45", service, timeout=55, limit=8192, on_start=started)
        operation.update(mutation_pending=False, stage=following)
        save(root, journal)
    after = docker.runtime("observe")
    bound(request, after)
    if (after["configuration"] != wire.configuration(request)
            or after["revision"] != before["revision"] or after["runtime_files"] != before["runtime_files"]
            or after["unrelated_digest"] != operation["unrelated_digest"]):
        raise wire.Pending("running_configuration_mismatch")
    source, applied = docker.caddy(after)
    if source != after["runtime_files"]["docker/caddy-supervisor.sh"]:
        raise wire.Pending("supervisor_source_mismatch")
    material = wire.decode(read_file(root / ".visual-probe.json", 4096, private=True))
    fingerprint = wire.digest(material)
    if not request["enabled"] and operation.get("former_probe") != fingerprint:
        raise wire.Pending("former_probe_unproven")
    checked = docker.runtime("probe", {"request": request, "material": material})
    if checked != after:
        raise wire.Pending("runtime_changed")
    docker.caddy(after)
    if (instance_id() != request["instance_id"] or read_file(root / ".env", 262144, private=True) !=
            patch_environment(raw, wire.configuration(request))):
        raise wire.Pending("installation_changed")
    operation["stage"] = "verified"
    operation["probe_fingerprint"] = fingerprint
    save(root, journal)
    return wire.receipt(request, applied["generation"])


def worker(request, root=ROOT):
    with guard(root):
        journal = load(root)
        if journal["current"] != request["operation_id"] or journal["operations"][journal["current"]]["request"] != request:
            raise wire.Pending("operation_missing")
        journal["operations"][journal["current"]]["owner_id"] = {"process": process_identity(os.getpid()),
                                                                 "deadline": int(time.time()) + 150}
        save(root, journal)
        return reconcile(root, Docker(root), journal, journal["operations"][journal["current"]])


def apply(request, root=ROOT, *, docker=None, launch=True):
    docker = docker or Docker(root)
    with guard(root):
        supported(root, docker)
        if instance_id() != request["instance_id"]:
            raise wire.Pending("instance_identity_conflict")
        bound(request, docker.runtime("observe"))
        raw = read_file(root / ".env", 262144, private=True)
        journal = load(root)
        operation = select(journal, request, raw)
        save(root, journal)
        if not launch:
            return reconcile(root, docker, journal, operation)
        if operation["stage"] in ("applied", "verified"):
            # A prior receipt is never cached authority: repeat observations and
            # probes, especially after rollback or token expiry.
            return reconcile(root, docker, journal, operation)
        argument = base64.urlsafe_b64encode(wire.encoded(request)).rstrip(b"=").decode()
        subprocess.Popen([sys.executable, "-B", "-m", "setup.visual_config", "_reconcile", "--request", argument], cwd=root,
                         env=docker.environment, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=True, close_fds=True)
    raise wire.Pending("operation_pending")


def status(root=ROOT):
    journal = load(root)
    operation = journal["operations"].get(journal["current"])
    result = {"protocol": wire.PROTOCOL, "status": "pending", "operation": None}
    if operation:
        result["operation"] = {"operation_id": operation["request"]["operation_id"],
                               "revision": operation["request"]["revision"], "stage": operation["stage"],
                               "mutation_pending": operation["mutation_pending"], "owner_id": operation.get("owner_id"),
                               "saved_configuration": hashlib.sha256(read_file(root / ".env", 262144, private=True)).hexdigest()
                                                      == operation["desired_sha256"]}
        # Current observations are diagnostic only; status never emits a proof
        # receipt, advances the journal, creates a lock or changes runtime state.
        docker = Docker(root)
        try:
            observed = docker.runtime("observe")
            docker.caddy(observed)
            result["observed"] = {key: observed[key] for key in ("revision", "install_id", "generation", "file_sha256", "configuration")}
        except Exception:
            result["observed"] = None
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("operation", choices=("capabilities", "apply", "status", "_reconcile"))
    parser.add_argument("--request")
    args = parser.parse_args(argv)
    try:
        if args.operation == "capabilities" and args.request is None:
            result = supported(ROOT, Docker())
        elif args.operation == "status" and args.request is None and os.geteuid() == 0:
            result = status()
        elif args.operation == "apply" and args.request:
            if os.geteuid() != 0:
                raise wire.Pending("local_operator_required")
            result = apply(wire.from_argument(args.request))
        elif args.operation == "_reconcile" and args.request and os.geteuid() == 0:
            result = worker(wire.from_argument(args.request))
        else:
            raise wire.Pending("operation_unsupported")
        sys.stdout.buffer.write(wire.encoded(result) + b"\n")
        return 0
    except Exception as error:
        reason = str(error) if isinstance(error, wire.Pending) and wire.matches(r"[a-z_]{1,64}", str(error)) else "operation_pending"
        sys.stdout.buffer.write(wire.encoded({"status": "pending", "reason": reason}) + b"\n")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

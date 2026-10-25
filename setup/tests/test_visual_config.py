"""Same-installation configuration fences and environment/crash safety."""
import base64
import copy
import hashlib
import os
from pathlib import Path

import pytest

from setup import visual_config as control, visual_contract as wire

ID = "12345678-1234-4234-8234-123456789abc"


def request(**changes):
    return {"operation_id": "operation_a", "revision": 1, "account_id": ID, "company_id": ID,
            "workspace_id": ID, "preview_id": "preview_1", "attempt_id": "attempt_1",
            "address": "pv-abcdefgh.preview.acme.example", "public_hostname": "acme.tico.dev", "enabled": True,
            "lease_id": ID, "hostname": "acme.ticofiles.com", "receipt": "a" * 32,
            "instance_id": "i-123456789abcdef01", "install_id": ID, "challenge": "b" * 64} | changes


class Docker:
    environment = {}

    def __init__(self, root):
        self.root, self.calls, self.fail, self.ids = root, [], None, {"server": "1" * 64, "caddy": "2" * 64}
        self.running = wire.configuration(request(enabled=False))

    def container(self, service):
        return self.ids[service]

    def healthy(self, service):
        return self.container(service)

    def runtime(self, verb, payload=None):
        self.calls.append(verb)
        if verb == "probe" and self.fail == "probe":
            raise wire.Pending("probe_material_expired")
        return {"install_id": ID, "public_hostname": "acme.tico.dev", "configuration": self.running,
                "unrelated_digest": "f" * 64,
                "generation": wire.digest(self.running), "file_sha256": "c" * 64,
                "revision": "e" * 40, "runtime_files": {"docker/caddy-supervisor.sh": "d" * 64}}

    def caddy(self, expected=None):
        return "d" * 64, {"generation": wire.digest(self.running), "file_sha256": "c" * 64}

    def compose(self, *args, **kwargs):
        self.calls.append(args)
        kwargs["on_start"]({"pid": 99999999, "start": "1"})
        service = args[-1]
        self.ids[service] = ("3" if service == "server" else "4") * 64
        self.running = {k: control.parse((self.root / ".env").read_text()).get(k, "") for k in wire.MANAGED}
        if self.fail == service:
            raise TimeoutError()


@pytest.fixture
def install(tmp_path, monkeypatch):
    raw = b'# private\r\nTICO_DOMAIN=acme.tico.dev\nTICO_VISUAL_SECRET=fictional-secret\nUNRELATED="${KEEP} $$x"\n'
    (tmp_path / ".env").write_bytes(raw)
    (tmp_path / ".env").chmod(0o600)
    (tmp_path / ".visual-probe.json").write_bytes(wire.encoded({"test": "local-only"}))
    (tmp_path / ".visual-probe.json").chmod(0o600)
    monkeypatch.setattr(control, "supported", lambda *args: {})
    monkeypatch.setattr(control, "instance_id", lambda: request()["instance_id"])
    return tmp_path, Docker(tmp_path), raw


def test_same_install_on_off_reenable_duplicate_stale_and_rollback(install):
    root, docker, raw = install
    a = request()
    receipt = control.apply(a, root, docker=docker, launch=False)
    assert receipt == wire.receipt(a, wire.digest(wire.configuration(a)))
    assert raw in (root / ".env").read_bytes()
    mutations = [x for x in docker.calls if isinstance(x, tuple)]
    assert len(mutations) == 2
    control.apply(a, root, docker=docker, launch=False)
    assert [x for x in docker.calls if isinstance(x, tuple)] == mutations
    off = request(operation_id="operation_b", revision=2, challenge="c" * 64, enabled=False)
    assert control.apply(off, root, docker=docker, launch=False)["checks"]["listener_retired"] is True
    b = request(operation_id="operation_c", revision=3, challenge="d" * 64,
                lease_id="12345678-1234-4234-8234-123456789abd")
    control.apply(b, root, docker=docker, launch=False)
    for stale in (a, off):
        with pytest.raises(wire.Pending, match="operation_stale"):
            control.apply(stale, root, docker=docker, launch=False)
    with pytest.raises(wire.Pending, match="operation_conflict"):
        control.apply(b | {"challenge": "e" * 64}, root, docker=docker, launch=False)
    # A previously successful operation cannot replay a cached receipt after a
    # rollback or unrelated editor has replaced the persisted environment.
    (root / ".env").write_bytes(raw)
    with pytest.raises(wire.Pending, match="environment_changed"):
        control.apply(b, root, docker=docker, launch=False)
    assert (root / ".env").read_bytes() == raw


def test_lost_docker_response_recovers_exact_operation_but_newer_cannot_overtake(install):
    root, docker, _ = install
    docker.fail = "server"
    with pytest.raises(TimeoutError):
        control.apply(request(), root, docker=docker, launch=False)
    journal = control.load(root)
    assert journal["operations"]["operation_a"]["mutation_pending"]["service"] == "server"
    with pytest.raises(wire.Pending, match="prior_operation_unsettled"):
        control.apply(request(operation_id="operation_b", revision=2, enabled=False), root, docker=docker, launch=False)
    docker.fail = None
    result = control.apply(request(), root, docker=docker, launch=False)
    assert result["config_digest"] == wire.digest(wire.configuration(request()))
    assert len([x for x in docker.calls if isinstance(x, tuple) and x[-1] == "server"]) == 1


def test_crash_before_or_after_atomic_env_and_unrelated_edit_are_fenced(install):
    root, docker, original = install
    journal = control.load(root)
    operation = control.select(journal, request(), original)
    control.save(root, journal)
    # Intent is durable before the environment rename; both crash cuts recover.
    (root / ".env").write_bytes(control.patch_environment(original, wire.configuration(request())))
    assert control.apply(request(), root, docker=docker, launch=False)["checks"]["saved_configuration"]
    changed = (root / ".env").read_bytes() + b"OTHER=new\n"
    (root / ".env").write_bytes(changed)
    with pytest.raises(wire.Pending, match="environment_changed"):
        control.apply(request(operation_id="operation_b", revision=2, enabled=False), root, docker=docker, launch=False)
    assert (root / ".env").read_bytes() == changed
    operation["mutation_pending"] = {"service": "server", "before": docker.ids["server"], "client": None}
    control.save(root, journal)
    with pytest.raises(wire.Pending, match="mutation_outcome_unknown"):
        control.reconcile(root, docker, journal, operation)


def test_missing_probe_is_pending_after_apply_and_never_restarts_again(install):
    root, docker, _ = install
    (root / ".visual-probe.json").unlink()
    with pytest.raises(FileNotFoundError):
        control.apply(request(), root, docker=docker, launch=False)
    assert control.load(root)["operations"]["operation_a"]["stage"] == "applied"
    (root / ".visual-probe.json").write_bytes(b"{}")
    (root / ".visual-probe.json").chmod(0o600)
    assert control.apply(request(), root, docker=docker, launch=False)["generation"]
    assert len([x for x in docker.calls if isinstance(x, tuple)]) == 2


def test_off_cannot_substitute_unobserved_former_probe(install):
    root, docker, _ = install
    control.apply(request(), root, docker=docker, launch=False)
    (root / ".visual-probe.json").write_bytes(b'{"different":"not the former read"}')
    off = request(operation_id="operation_b", revision=2, enabled=False)
    with pytest.raises(wire.Pending, match="former_probe_unproven"):
        control.apply(off, root, docker=docker, launch=False)
    assert control.load(root)["operations"]["operation_b"]["stage"] == "applied"


def test_private_files_lock_and_wrong_identity_refuse_before_mutation(install):
    root, docker, raw = install
    with control.guard(root):
        with pytest.raises(wire.Pending, match="operation_busy"):
            control.apply(request(), root, docker=docker, launch=False)
    with pytest.raises(wire.Pending, match="installation_identity_conflict"):
        control.apply(request(install_id="12345678-1234-4234-8234-123456789abd"), root, docker=docker, launch=False)
    assert not (root / control.JOURNAL).exists()
    (root / ".env").chmod(0o644)
    with pytest.raises(wire.Pending, match="private_file_invalid"):
        control.apply(request(), root, docker=docker, launch=False)
    assert (root / ".env").read_bytes() == raw
    (root / ".env").unlink()
    (root / ".env").symlink_to(root / ".visual-probe.json")
    with pytest.raises(OSError):
        control.apply(request(), root, docker=docker, launch=False)


def test_wire_rejects_versions_duplicate_fields_wrong_types_and_oversize():
    value = request()
    argument = base64.urlsafe_b64encode(wire.encoded(value)).rstrip(b"=").decode()
    assert wire.from_argument(argument) == value
    for wrong in (value | {"enabled": 1}, value | {"revision": True}, value | {"protocol": "future"}, value | {"hostname": "hub.ticofiles.com"}):
        with pytest.raises(wire.Pending):
            wire.validate(wrong)
    for raw in (b'{"revision":1,"revision":2}', b"x" * 4097):
        with pytest.raises(wire.Pending):
            wire.decode(raw)
    with pytest.raises(wire.Pending):
        wire.from_argument(argument + "=")


def test_multiline_and_duplicate_managed_environment_cannot_change_unrelated_bytes():
    for raw in (b'UNRELATED="multi\nTICO_VISUAL_ORIGIN=still-in-a-secret\n"\n',
                b"TICO_CADDY_VISUALS=0\nTICO_CADDY_VISUALS=1\n"):
        with pytest.raises(wire.Pending, match="environment_ambiguous"):
            control.patch_environment(raw, wire.configuration(request()))


def test_installer_manifest_and_actual_runtime_must_match_not_just_revision(tmp_path):
    names = ("setup/__init__.py", "setup/cli.py", "setup/settings.py", "setup/envfile.py", "setup/contract.py", "setup/route53.py",
             "setup/visual_contract.py", "setup/visual_config.py", "compose.yaml")
    for name in names:
        p = tmp_path / name
        p.parent.mkdir(exist_ok=True)
        p.write_bytes(b"fixture\n")
    manifest = {"protocol": wire.PROTOCOL, "revision": "a" * 40,
                "files": {n: hashlib.sha256((tmp_path / n).read_bytes()).hexdigest() for n in names},
                "runtime_files": {n: "b" * 64 for n in wire.RUNTIME_FILES}}
    (tmp_path / "installer-manifest.json").write_bytes(wire.encoded(manifest))
    assert control.installer_capability(tmp_path) == manifest
    class Runtime:
        def runtime(self, verb):
            return {"revision": "a" * 40, "runtime_files": manifest["runtime_files"]}
        def container(self, service):
            assert service == "caddy"
            return "f" * 64
    assert control.supported(tmp_path, Runtime())["runtime_revision"] == "a" * 40
    manifest["runtime_files"]["backend/visual_config.py"] = "c" * 64
    with pytest.raises(wire.Pending, match="selected_runtime_unsupported"):
        control.supported(tmp_path, Runtime())
    (tmp_path / "setup/cli.py").write_bytes(b"modified\n")
    with pytest.raises(wire.Pending, match="installer_source_modified"):
        control.installer_capability(tmp_path)


@pytest.mark.parametrize('stopped', [False, True])
def test_real_discovery_and_journaled_recovery_do_not_require_caddy_already_healthy(tmp_path, monkeypatch, stopped):
    names = ("setup/__init__.py", "setup/cli.py", "setup/settings.py", "setup/envfile.py", "setup/contract.py",
             "setup/visual_contract.py", "setup/visual_config.py", "compose.yaml")
    for name in names:
        path = tmp_path / name
        path.parent.mkdir(exist_ok=True)
        path.write_bytes(b"selected source\n")
    manifest = {"protocol": wire.PROTOCOL, "revision": "e" * 40,
        "files": {name: hashlib.sha256((tmp_path / name).read_bytes()).hexdigest() for name in names},
        "runtime_files": {name: "d" * 64 for name in wire.RUNTIME_FILES}}
    (tmp_path / "installer-manifest.json").write_bytes(wire.encoded(manifest))
    raw = b'TICO_DOMAIN=acme.tico.dev\nPRIVATE=preserved\n'
    journal = control.load(tmp_path)
    operation = control.select(journal, request(), raw)
    desired = control.patch_environment(raw, wire.configuration(request()))
    (tmp_path / ".env").write_bytes(desired)
    (tmp_path / ".env").chmod(0o600)
    (tmp_path / ".visual-probe.json").write_bytes(b'{}')
    (tmp_path / ".visual-probe.json").chmod(0o600)
    operation.update(stage="server_started", unrelated_digest="f" * 64)
    control.save(tmp_path, journal)
    monkeypatch.setattr(control, "instance_id", lambda: request()["instance_id"])

    class Boundary(control.Docker):
        def __init__(self):
            super().__init__(tmp_path)
            self.caddy_healthy, self.calls, self.absent = False, [], False
            self.stopped = stopped

        def compose(self, *args, **kwargs):
            if args[0] == 'ps':
                assert args in (("ps", "--all", "-q", args[-1]), ("ps", "-q", args[-1]))
                missing = self.absent or (self.stopped and '--all' not in args)
                return b'' if missing and args[-1] == "caddy" else ("1" * 64).encode()
            assert args == ("up", "-d", "--no-deps", "--force-recreate", "--pull", "never", "--no-build",
                            "--wait", "--wait-timeout", "45", "caddy")
            self.calls.append(args)
            kwargs['on_start']({'pid':99999999, 'start':'1'})
            self.caddy_healthy, self.stopped = True, False

        def runtime(self, verb, payload=None):
            capability = {"revision":manifest['revision'], "runtime_files":manifest['runtime_files']}
            if verb == "capabilities":
                return capability
            assert verb in ("observe", "probe")
            return capability | {"install_id":ID, "public_hostname":"acme.tico.dev",
                "configuration":wire.configuration(request()), "unrelated_digest":"f" * 64,
                "generation":wire.digest(wire.configuration(request())), "file_sha256":"c" * 64}

        def run(self, args, **kwargs):
            if args[3:] == ["sha256sum", "/generated/supervisor.sh"]:
                return ("d" * 64 + "  /generated/supervisor.sh\n").encode()
            if args[3:] == ["/bin/sh", "/generated/supervisor.sh", "health"]:
                if not self.caddy_healthy:
                    raise wire.Pending("runtime_pending")
                return b''
            assert args[3:] == ["cat", "/tmp/tico-caddy/applied"]
            return wire.encoded({"generation":wire.digest(wire.configuration(request())),
                                 "file_sha256":"c" * 64, "pid":10, "supervisor_pid":9})

    docker = Boundary()
    # This is the actual discovery command's control flow, also called by the
    # Services adapter before retry. It reports code support, not a receipt.
    capability = control.supported(tmp_path, docker)
    assert capability['same_instance_apply'] and 'checks' not in capability
    assert docker.calls == [] and not docker.caddy_healthy
    # Uncertain dispatch remains fenced even when source discovery succeeds.
    operation['mutation_pending'] = {'service':'caddy', 'before':'1' * 64, 'client':None}
    control.save(tmp_path, journal)
    with pytest.raises(wire.Pending, match="mutation_outcome_unknown"):
        control.apply(request(), tmp_path, docker=docker, launch=False)
    assert docker.calls == []
    with pytest.raises(wire.Pending, match="prior_operation_unsettled"):
        control.apply(request(operation_id="operation_b", revision=2), tmp_path, docker=docker, launch=False)
    operation['mutation_pending'] = False
    control.save(tmp_path, journal)
    result = control.apply(request(), tmp_path, docker=docker, launch=False)
    assert len(docker.calls) == 1 and result == wire.receipt(request(), wire.digest(wire.configuration(request())))
    assert (tmp_path / ".env").read_bytes() == desired
    assert control.load(tmp_path)['operations']['operation_a']['stage'] == 'verified'
    docker.absent = True
    with pytest.raises(wire.Pending, match="existing_service_required"):
        control.supported(tmp_path, docker)
    assert len(docker.calls) == 1

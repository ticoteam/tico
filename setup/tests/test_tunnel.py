"""The Cloudflare tunnel's route ships with the product, and setup notices a tunnel that has none.

A tunnel reused with only its token has no ingress of its own: cloudflared logs "No ingress rules ... will return 503",
stays "running", and every request gets a 503. compose.yaml's cloudflared service therefore always runs with a config the
server writes from TICO_DOMAIN, and `setup` and `setup doctor` fail on the 503 and on that log line.
"""

import importlib.util
import os
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from setup import cloudflare, verify
from setup.remote import Result
from setup.tests.fakes import FakeShell

ROOT = Path(__file__).resolve().parents[2]
ENTRYPOINT = ROOT / "docker" / "entrypoint.sh"
NO_INGRESS_LOG = (
    "2026-09-29T20:14:01Z WRN No ingress rules were defined in provided config (if any) nor from the cli, "
    "cloudflared will return 503 for all incoming HTTP requests\n"
)


def build_install_bundle():
    spec = importlib.util.spec_from_file_location("build_install_bundle", ROOT / "scripts" / "build_install_bundle.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def write_config(directory, domain, **env):
    return subprocess.run(
        ["bash", str(ENTRYPOINT), "tunnel-config"],
        capture_output=True,
        text=True,
        env={**os.environ, "TICO_TUNNEL_DIR": str(directory), "TICO_DOMAIN": domain, **env},
    )


def test_no_domain_or_no_volume_writes_nothing_and_a_bad_domain_is_refused(tmp_path):
    assert write_config(tmp_path, "").returncode == 0 and not list(tmp_path.iterdir())
    assert write_config(tmp_path / "missing", "tico.example.com").returncode == 0
    for bad in ("tico.example.com\n  - service: http://evil", "a b.example.com", ".example.com", "tico.example.com."):
        refused = write_config(tmp_path, bad)
        assert refused.returncode != 0 and "not a plain hostname" in refused.stderr, bad
        assert not list(tmp_path.iterdir())


def test_compose_always_runs_cloudflared_with_that_config_and_the_server_is_what_writes_it():
    compose = yaml.safe_load((ROOT / "compose.yaml").read_text())
    tunnel, server = compose["services"]["cloudflared"], compose["services"]["server"]
    assert tunnel["command"].split() == ["tunnel", "--no-autoupdate", "--config", "/tunnel/ingress.yml", "run"]
    assert tunnel["volumes"] == ["tico-tunnel:/tunnel:ro"] and "tico-tunnel:/tunnel" in server["volumes"]
    assert "tico-tunnel" in compose["volumes"] and "tico-control" not in "".join(
        tunnel["volumes"]
    )  # never the updater's token
    assert tunnel["depends_on"]["server"]["condition"] == "service_healthy"  # the file exists before it starts
    assert "TICO_DOMAIN" in server["environment"]
    assert "install -d -m 0755 -o tico -g tico /tunnel" in (ROOT / "Dockerfile").read_text()
    # The route lives in the image and the compose file, both replaced by an update, not in a file the updater would have to know about.
    assert "compose.yaml" in build_install_bundle().bundle_names(ROOT)


def test_cloudflared_logging_no_ingress_rules_fails_the_tunnel_route_check_even_though_the_container_runs():
    bad = FakeShell({"logs": Result(0, NO_INGRESS_LOG)})
    check = verify.check_tunnel_route(bad, "tico.example.com")
    assert not check.ok and "no ingress rules" in check.detail and "503" in check.detail
    assert "docker compose up -d" in check.hint and "Public Hostname" in check.hint
    assert any("cloudflared" in cmd and "logs" in cmd for cmd in bad.cmds())
    good = FakeShell({"logs": Result(0, "INF Registered tunnel connection connIndex=0\n")})
    assert verify.check_tunnel_route(good, "tico.example.com").ok


def test_verify_all_reports_the_missing_route_for_a_tunnel_and_only_for_a_tunnel(monkeypatch):
    monkeypatch.setattr(verify, "check_dns", lambda *a: verify.Check("DNS", True, "ok"))
    monkeypatch.setattr(verify, "check_tls", lambda d: verify.Check("HTTPS certificate", True, "ok"))
    real = verify.check_health
    monkeypatch.setattr(
        verify, "check_health", lambda d, front_door="": real(d, get=lambda url: (503, {}, ""), front_door=front_door)
    )
    shell = FakeShell(
        {
            "compose ps": Result(0, '{"State": "running", "Health": ""}\n'),
            "logs": Result(0, NO_INGRESS_LOG),
            "stat": Result(0, "600\n"),
        }
    )
    checks = verify.run_all(
        domain="t.example.com",
        provider="none",
        client_id="",
        front_door="cloudflared",
        records=[],
        resolvers=[],
        shell=shell,
        sleep=lambda s: None,
    )
    names = {c.name: c for c in checks}
    assert names["Tunnel connector"].ok  # running is all that check says
    assert not names["Tunnel route"].ok and not names["/healthz"].ok
    caddy = verify.run_all(
        domain="t.example.com",
        provider="none",
        client_id="",
        front_door="caddy",
        records=[],
        resolvers=[],
        shell=shell,
        sleep=lambda s: None,
    )
    assert "Tunnel route" not in {c.name for c in caddy}


def test_local_generated_runtime_and_remote_put_share_complete_alias_and_restricted_ingress(tmp_path, monkeypatch):
    from setup.tests.helpers.cloudflare import Api

    for key in list(os.environ):
        if key.startswith('TICO_'):
            monkeypatch.delenv(key)
    api = Api({('PUT', '/accounts/a/cfd_tunnel/t/configurations'): {}})
    cf = cloudflare.Cloudflare('fictional-token', api)
    for aliases, visual, runner in [
        (
            ' Old.Acme.Example,old.acme.example,app.acme.example ',
            'https://files.acme.example/',
            'https://runner.acme.example',
        ),
        ('', '', 'https://app.acme.example/'),
    ]:
        done = write_config(
            tmp_path, 'app.acme.example', TICO_DOMAIN_ALIASES=aliases, TICO_VISUAL_ORIGIN=visual, TICO_RUNNER_URL=runner
        )
        assert done.returncode == 0, done.stderr
        cf.configure_tunnel(
            'a', 't', 'app.acme.example', runner.split('://')[1].rstrip('/'), aliases=aliases, visual_origin=visual
        )
        assert yaml.safe_load((tmp_path / 'ingress.yml').read_bytes()) == api.calls[-1][2]['config']

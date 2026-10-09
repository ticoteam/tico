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
NO_INGRESS_LOG = ("2026-09-29T20:14:01Z WRN No ingress rules were defined in provided config (if any) nor from the cli, "
                  "cloudflared will return 503 for all incoming HTTP requests\n")


def build_install_bundle():
    spec = importlib.util.spec_from_file_location("build_install_bundle", ROOT / "scripts" / "build_install_bundle.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def write_config(directory, domain, **env):
    return subprocess.run(["bash", str(ENTRYPOINT), "tunnel-config"], capture_output=True, text=True,
                          env={**os.environ, "TICO_TUNNEL_DIR": str(directory), "TICO_DOMAIN": domain, **env})


def test_the_server_writes_the_route_for_its_domain_and_a_404_for_everything_else(tmp_path):
    done = write_config(tmp_path, "tico.example.com")
    assert done.returncode == 0, done.stderr
    written = tmp_path / "cloudflared.yml"
    assert yaml.safe_load(written.read_text()) == cloudflare.ingress_config("tico.example.com")
    assert yaml.safe_load(written.read_text())["ingress"] == [
        {"hostname": "tico.example.com", "service": "http://server:8765"}, {"service": "http_status:404"}]
    assert oct(written.stat().st_mode & 0o777) == "0o644" and not list(tmp_path.glob("*.new"))     # the tunnel container can read it
    # Written on every start: a changed TICO_DOMAIN follows.
    write_config(tmp_path, "hub.example.org")
    assert yaml.safe_load(written.read_text())["ingress"][0]["hostname"] == "hub.example.org"


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
    assert tunnel["command"].split() == ["tunnel", "--no-autoupdate", "--config", "/tunnel/cloudflared.yml", "run"]
    assert tunnel["volumes"] == ["tico-tunnel:/tunnel:ro"] and "tico-tunnel:/tunnel" in server["volumes"]
    assert "tico-tunnel" in compose["volumes"] and "tico-control" not in "".join(tunnel["volumes"])   # never the updater's token
    assert tunnel["depends_on"]["server"]["condition"] == "service_healthy"                          # the file exists before it starts
    assert "TICO_DOMAIN" in server["environment"]
    assert "install -d -m 0755 -o tico -g tico /tunnel" in (ROOT / "Dockerfile").read_text()
    # The route lives in the image and the compose file, both replaced by an update, not in a file the updater would have to know about.
    assert "compose.yaml" in build_install_bundle().bundle_names(ROOT)


@pytest.mark.skipif(not shutil.which("docker") or subprocess.run(
    ["docker", "image", "inspect", "cloudflare/cloudflared:2026.7.3"], capture_output=True).returncode != 0,
    reason="the pinned cloudflared image is not on this machine")
def test_cloudflared_itself_accepts_the_config_and_routes_the_domain_to_the_server(tmp_path):
    assert write_config(tmp_path, "tico.example.com").returncode == 0
    def cloudflared(*args):
        return subprocess.run(["docker", "run", "--rm", "-v", f"{tmp_path}:/tunnel:ro", "cloudflare/cloudflared:2026.7.3", "tunnel",
                               "--config", "/tunnel/cloudflared.yml", "ingress", *args], capture_output=True, text=True).stdout
    assert "OK" in cloudflared("validate")
    assert "service: http://server:8765" in cloudflared("rule", "https://tico.example.com/")
    assert "http_status:404" in cloudflared("rule", "https://other.example.com/")


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
    monkeypatch.setattr(verify, "check_health", lambda d, front_door="": real(d, get=lambda url: (503, {}, ""), front_door=front_door))
    shell = FakeShell({"compose ps": Result(0, '{"State": "running", "Health": ""}\n'), "logs": Result(0, NO_INGRESS_LOG),
                       "stat": Result(0, "600\n")})
    checks = verify.run_all(domain="t.example.com", provider="none", client_id="", front_door="cloudflared", records=[], resolvers=[],
                            shell=shell, sleep=lambda s: None)
    names = {c.name: c for c in checks}
    assert names["Tunnel connector"].ok                      # running is all that check says
    assert not names["Tunnel route"].ok and not names["/healthz"].ok
    caddy = verify.run_all(domain="t.example.com", provider="none", client_id="", front_door="caddy", records=[], resolvers=[],
                           shell=shell, sleep=lambda s: None)
    assert "Tunnel route" not in {c.name for c in caddy}


def test_the_server_and_setup_write_the_same_runner_hostname_route(tmp_path):
    done = write_config(tmp_path, "tico.example.com", TICO_RUNNER_URL="https://tico-runner.example.com")
    assert done.returncode == 0, done.stderr
    written = yaml.safe_load((tmp_path / "cloudflared.yml").read_text())
    assert written == cloudflare.ingress_config("tico.example.com", "tico-runner.example.com")
    assert written["ingress"][1]["path"] == "^/(?:api/v2|download)(?:/.*)?$"
